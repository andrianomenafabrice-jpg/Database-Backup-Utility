import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from click.testing import CliRunner

from dbbackup.cli import cli
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError, NotificationError
from dbbackup.logger import redact
from dbbackup.notifications import notify_safely, send_slack
from dbbackup.scheduler import (
    backup_arguments, choose_mode, cron_entry, parse_interval, run_schedule, windows_task,
)


class SlackRecorder:
    """Vrai petit serveur HTTP local qui joue le rôle du webhook Slack."""

    def __init__(self, status=200):
        self.bodies = []
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.bodies.append(json.loads(self.rfile.read(length)))
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/services/T000/B000/SECRETTOKEN"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def make_source(tmp_path):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, nom TEXT)")
    conn.execute("INSERT INTO clients (nom) VALUES ('Rakoto')")
    conn.commit()
    conn.close()
    return db


def invoke(args, tmp_path, env=None):
    base = ["--log-file", str(tmp_path / "t.log")]
    return CliRunner().invoke(cli, base + args, env=env)


# ------------------------------------------------------------------ Slack


def test_send_slack_posts_json_text():
    with SlackRecorder() as rec:
        send_slack(rec.url, "Bonjour ✅")
        assert rec.bodies == [{"text": "Bonjour ✅"}]


def test_slack_http_error_is_reported_without_url():
    with SlackRecorder(status=500) as rec:
        with pytest.raises(NotificationError) as exc_info:
            send_slack(rec.url, "x")
    assert "500" in str(exc_info.value) and "SECRETTOKEN" not in str(exc_info.value)


def test_slack_unreachable_does_not_leak_url():
    with pytest.raises(NotificationError) as exc_info:
        send_slack("http://127.0.0.1:1/services/T000/B000/SECRETTOKEN", "x", timeout=2)
    assert "SECRETTOKEN" not in str(exc_info.value)


def test_slack_requires_https_except_localhost():
    with pytest.raises(NotificationError, match="https"):
        send_slack("http://example.com/hook", "x")
    with pytest.raises(NotificationError, match="https"):
        send_slack("pas-une-url", "x")


def test_notify_safely_never_raises_and_warns():
    warnings = []
    ok = notify_safely("http://127.0.0.1:1/hook", "x", warnings.append)
    assert ok is False and len(warnings) == 1
    assert notify_safely(None, "x") is False


def test_redact_hides_slack_webhook():
    text = "POST https://hooks.slack.com/services/T000/B000/SECRETTOKEN"
    assert "SECRETTOKEN" not in redact(text)


# ------------------------------------------------------------------ CLI + Slack


def test_backup_sends_success_notification(tmp_path):
    source = make_source(tmp_path)
    with SlackRecorder() as rec:
        result = invoke(
            ["backup", "--type", "sqlite", "-d", str(source), "-o", str(tmp_path / "b"),
             "--slack-webhook", rec.url],
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        text = rec.bodies[0]["text"]
    assert "réussie" in text and "shop" in text and "SECRETTOKEN" not in text


def test_webhook_from_environment_variable(tmp_path):
    source = make_source(tmp_path)
    with SlackRecorder() as rec:
        result = invoke(
            ["backup", "--type", "sqlite", "-d", str(source), "-o", str(tmp_path / "b")],
            tmp_path, env={"DBBACKUP_SLACK_WEBHOOK": rec.url},
        )
        assert result.exit_code == 0, result.output
        assert len(rec.bodies) == 1


def test_failed_backup_sends_failure_notification(tmp_path):
    with SlackRecorder() as rec:
        result = invoke(
            ["backup", "--type", "sqlite", "-d", str(tmp_path / "absent.db"),
             "-o", str(tmp_path / "b"), "--slack-webhook", rec.url],
            tmp_path,
        )
        assert result.exit_code == 1
        text = rec.bodies[0]["text"]
    assert "échouée" in text and "introuvable" in text


def test_unreachable_slack_does_not_fail_the_backup(tmp_path):
    source = make_source(tmp_path)
    result = invoke(
        ["backup", "--type", "sqlite", "-d", str(source), "-o", str(tmp_path / "b"),
         "--slack-webhook", "http://127.0.0.1:1/hook"],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "Avertissement" in result.output and "[OK]" in result.output


def test_upload_failure_is_notified_and_reported(tmp_path):
    source = make_source(tmp_path)
    blocker = tmp_path / "bloc"
    blocker.write_text("je suis un fichier, pas un dossier")
    with SlackRecorder() as rec:
        result = invoke(
            ["backup", "--type", "sqlite", "-d", str(source), "-o", str(tmp_path / "b"),
             "--upload", str(blocker / "sous"), "--slack-webhook", rec.url],
            tmp_path,
        )
        assert result.exit_code == 1
        text = rec.bodies[0]["text"]
    assert "échouée" in text and "sauvegarde locale a été créée" in text


def test_restore_sends_notification(tmp_path):
    source = make_source(tmp_path)
    out = tmp_path / "b"
    assert invoke(["backup", "--type", "sqlite", "-d", str(source), "-o", str(out)], tmp_path).exit_code == 0
    file = next(out.glob("*.sql.gz"))
    with SlackRecorder() as rec:
        result = invoke(
            ["restore", "--type", "sqlite", "-d", str(tmp_path / "r.db"), "-f", str(file),
             "--slack-webhook", rec.url],
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        assert "Restauration réussie" in rec.bodies[0]["text"]


# ------------------------------------------------------------------ planification


def test_parse_interval():
    assert parse_interval("30s") == 30
    assert parse_interval("15m") == 900
    assert parse_interval("6h") == 21600
    assert parse_interval("1d") == 86400
    for bad in ("", "abc", "0m", "10", "-5s"):
        with pytest.raises(ConfigError):
            parse_interval(bad)


def test_choose_mode():
    assert choose_mode("full", 5, 3, True) == "full"
    assert choose_mode("incremental", 1, None, False) == "full"  # aucune complète
    assert choose_mode("incremental", 2, 3, True) == "incremental"
    assert choose_mode("incremental", 3, 3, True) == "full"  # complète périodique
    assert choose_mode("differential", 4, None, True) == "differential"


def test_run_schedule_keeps_going_after_a_failure():
    sleeps = []
    results = iter([True, False, True])
    failures = run_schedule(lambda n: next(results), 2, max_runs=3, sleep=sleeps.append)
    assert failures == 1
    assert sleeps == [1.0, 1.0, 1.0, 1.0]  # 2 pauses de 2 s, par tranches d'1 s


def test_schedule_run_cli_full_incremental_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    source = make_source(tmp_path)
    out = str(tmp_path / "b")
    result = invoke(
        ["schedule", "run", "--type", "sqlite", "-d", str(source), "-o", out,
         "--mode", "incremental", "--full-every", "3", "--interval", "1s", "--max-runs", "3"],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "Exécution 1 (full)" in result.output
    assert "Exécution 2 (incremental)" in result.output
    assert "Exécution 3 (full)" in result.output


def test_schedule_run_reports_failures_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with SlackRecorder() as rec:
        result = invoke(
            ["schedule", "run", "--type", "sqlite", "-d", str(tmp_path / "absent.db"),
             "-o", str(tmp_path / "b"), "--interval", "1s", "--max-runs", "2",
             "--slack-webhook", rec.url],
            tmp_path,
        )
        assert result.exit_code == 1
        assert len(rec.bodies) == 2  # une alerte par échec, la boucle a continué
    assert "2 exécution(s) en échec" in result.output


def test_schedule_run_rejects_incremental_for_other_dbms(tmp_path):
    result = invoke(
        ["schedule", "run", "--type", "postgresql", "-d", "x", "--mode", "incremental",
         "--interval", "1h", "--max-runs", "1"],
        tmp_path,
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, ConfigError) and "SQLite" in str(result.exception)


def test_cron_entry_has_no_secrets_and_absolute_paths(tmp_path):
    params = ConnectionParams(
        db_type="postgresql", database="shop", username="backup", password="TOPSECRET",
        docker_container="dbbackup-pg",
    )
    args = backup_arguments(params, "full", str(tmp_path / "b"), "s3://bucket/shop")
    line = cron_entry("0 2 * * *", args)
    assert "TOPSECRET" not in line and "DBBACKUP_PASSWORD" in line
    assert "-m dbbackup backup" in line and "--docker-container dbbackup-pg" in line
    assert str(tmp_path) in line and "--upload s3://bucket/shop" in line
    assert line.splitlines()[-1].startswith("0 2 * * * ")
    with pytest.raises(ConfigError):
        cron_entry("tous les jours", args)


def test_windows_task_command(tmp_path):
    params = ConnectionParams(db_type="sqlite", database=str(tmp_path / "shop.db"))
    args = backup_arguments(params, "incremental", str(tmp_path / "b"), None)
    text = windows_task("03:30", args, "dbbackup-shop")
    assert 'schtasks /Create /SC DAILY /ST 03:30 /TN "dbbackup-shop"' in text
    assert "--mode incremental" in text
    with pytest.raises(ConfigError):
        windows_task("25:99", args, "x")


def test_schedule_print_cli(tmp_path):
    source = make_source(tmp_path)
    result = invoke(
        ["schedule", "print", "--target", "cron", "--cron", "30 1 * * 0",
         "--type", "sqlite", "-d", str(source), "-o", str(tmp_path / "b")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "30 1 * * 0 " in result.output and "crontab -e" in result.output
    result = invoke(
        ["schedule", "print", "--target", "windows", "--at", "02:15",
         "--type", "sqlite", "-d", str(source)],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "schtasks /Create" in result.output and "dbbackup-shop" in result.output
