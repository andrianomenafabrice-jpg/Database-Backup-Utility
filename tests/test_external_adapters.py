import gzip
import io

import pytest

from dbbackup.adapters import get_adapter
from dbbackup.adapters.mysql import filter_mysql_dump
from dbbackup.adapters.postgres import parse_toc_tables
from dbbackup.backup import run_backup
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import (
    BackupError,
    ConfigError,
    DatabaseConnectionError,
    RestoreError,
)
from dbbackup.restore import run_restore
from dbbackup.utils.process import ProcessError, ToolNotFoundError


class FakeRunner:
    """Remplace run_process : enregistre les appels, renvoie ce que dit le handler."""

    def __init__(self, handler=None):
        self.handler = handler or (lambda cmd, data: b"")
        self.calls = []

    def __call__(self, cmd, env=None, stdin_stream=None, stdout_stream=None, timeout=None):
        data = stdin_stream.read() if stdin_stream is not None else None
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "stdin": data})
        result = self.handler(cmd, data)
        if isinstance(result, BaseException):
            raise result
        if stdout_stream is not None:
            stdout_stream.write(result)
            return b""
        return result


@pytest.fixture
def fake(monkeypatch):
    def install(handler=None):
        runner = FakeRunner(handler)
        monkeypatch.setattr("dbbackup.adapters.external.run_process", runner)
        return runner

    return install


def tool_of(cmd):
    if cmd[0] == "docker":  # docker exec -i [-e NOM]... CONTENEUR OUTIL ...
        i = 3
        while cmd[i] == "-e":
            i += 2
        return cmd[i + 1]
    return cmd[0]


def pg(**kw):
    return ConnectionParams(
        db_type="postgresql", database="testdb", username="postgres", password="secret", **kw
    )


def my(**kw):
    return ConnectionParams(
        db_type="mysql", database="testdb", username="root", password="secret", **kw
    )


def mongo(**kw):
    return ConnectionParams(
        db_type="mongodb", database="testdb", username="root", password="secret", **kw
    )


def backup_file(tmp_path, content, name="b.sql.gz"):
    path = tmp_path / name
    path.write_bytes(gzip.compress(content))
    return path


# ------------------------------------------------------------------ configuration


def test_docker_container_not_allowed_for_sqlite():
    with pytest.raises(ConfigError):
        ConnectionParams(db_type="sqlite", database="x.db", docker_container="c")


def test_invalid_docker_container_name():
    with pytest.raises(ConfigError):
        ConnectionParams(db_type="mysql", database="x", docker_container="--privileged")


def test_server_database_cannot_start_with_dash():
    with pytest.raises(ConfigError):
        ConnectionParams(db_type="postgresql", database="-oops")


# ------------------------------------------------------------------ PostgreSQL


def test_postgres_connection_local_keeps_password_out_of_args(fake):
    runner = fake(lambda cmd, data: b"1\n")
    get_adapter(pg()).test_connection()
    call = runner.calls[0]
    assert call["cmd"][0] == "psql"
    assert "--no-password" in call["cmd"]
    assert call["env"] == {"PGPASSWORD": "secret"}
    assert not any("secret" in arg for arg in call["cmd"])


def test_postgres_connection_in_docker(fake):
    runner = fake(lambda cmd, data: b"1\n")
    get_adapter(pg(docker_container="dbbackup-pg")).test_connection()
    cmd = runner.calls[0]["cmd"]
    assert cmd[:6] == ["docker", "exec", "-i", "-e", "PGPASSWORD", "dbbackup-pg"]
    assert cmd[6] == "psql"
    assert not any("secret" in arg for arg in cmd)


def test_postgres_unexpected_answer(fake):
    fake(lambda cmd, data: b"nope")
    with pytest.raises(DatabaseConnectionError):
        get_adapter(pg()).test_connection()


def test_postgres_backup_then_restore(fake, tmp_path):
    archive = b"PGDMP-fake-archive" * 100

    def handler(cmd, data):
        tool = tool_of(cmd)
        if tool == "psql":
            return b"1\n"
        if tool == "pg_dump":
            return archive
        return b""

    runner = fake(handler)
    adapter = get_adapter(pg())
    result = run_backup(adapter, tmp_path / "out")
    assert result.file_path.name.endswith(".dump.gz")
    with gzip.open(result.file_path, "rb") as handle:
        assert handle.read() == archive

    run_restore(adapter, result.file_path, overwrite=True)
    last = runner.calls[-1]
    assert tool_of(last["cmd"]) == "pg_restore"
    assert last["stdin"] == archive
    assert "--clean" in last["cmd"] and "--single-transaction" in last["cmd"]


def test_postgres_restore_requires_overwrite(fake, tmp_path):
    runner = fake()
    with pytest.raises(RestoreError, match="overwrite"):
        run_restore(get_adapter(pg()), backup_file(tmp_path, b"x", "b.dump.gz"))
    assert runner.calls == []


TOC = """;
; Archive created at 2026-10-01
210; 1259 16394 TABLE public clients postgres
211; 0 16394 TABLE DATA public clients postgres
212; 1259 16400 TABLE public commandes postgres
"""


def test_parse_toc_tables():
    assert parse_toc_tables(TOC) == {"clients", "commandes"}


def test_postgres_selective_restore(fake, tmp_path):
    runner = fake(lambda cmd, data: TOC.encode() if "--list" in cmd else b"")
    path = backup_file(tmp_path, b"ARCHIVE", "b.dump.gz")
    run_restore(get_adapter(pg()), path, tables=["clients"], overwrite=True)
    list_call, restore_call = runner.calls
    assert list_call["stdin"] == b"ARCHIVE"
    assert "--table=clients" in restore_call["cmd"]
    assert restore_call["stdin"] == b"ARCHIVE"


def test_postgres_selective_unknown_table_runs_no_restore(fake, tmp_path):
    runner = fake(lambda cmd, data: TOC.encode() if "--list" in cmd else b"")
    path = backup_file(tmp_path, b"ARCHIVE", "b.dump.gz")
    with pytest.raises(RestoreError, match="fantome"):
        run_restore(get_adapter(pg()), path, tables=["clients", "fantome"], overwrite=True)
    assert len(runner.calls) == 1  # seul "pg_restore --list" a été exécuté


def test_postgres_rejects_schema_qualified_table(fake, tmp_path):
    fake()
    path = backup_file(tmp_path, b"ARCHIVE", "b.dump.gz")
    with pytest.raises(RestoreError, match="schéma"):
        run_restore(get_adapter(pg()), path, tables=["public.clients"], overwrite=True)


# ------------------------------------------------------------------ MySQL

MYSQL_DUMP = b"""-- MySQL dump 10.13
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;

--
-- Table structure for table `clients`
--

DROP TABLE IF EXISTS `clients`;
CREATE TABLE `clients` (`id` int NOT NULL, `nom` varchar(50) DEFAULT NULL) ENGINE=InnoDB;
INSERT INTO `clients` VALUES (1,'Rakoto'),(2,'Rasoa');

--
-- Table structure for table `commandes`
--

DROP TABLE IF EXISTS `commandes`;
CREATE TABLE `commandes` (`id` int NOT NULL) ENGINE=InnoDB;
INSERT INTO `commandes` VALUES (1);

--
-- Temporary view structure for view `v_clients`
--

CREATE VIEW `v_clients` AS SELECT 1;

--
-- Dumping routines for database 'testdb'
--

/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
-- Dump completed on 2026-10-01
"""


def test_filter_mysql_dump_keeps_only_selected_tables():
    out = io.BytesIO()
    found = filter_mysql_dump(io.BytesIO(MYSQL_DUMP), out, {"clients"})
    text = out.getvalue().decode("utf-8")
    assert found == {"clients"}
    assert "CREATE TABLE `clients`" in text and "Rakoto" in text
    assert "commandes" not in text
    assert "v_clients" not in text
    assert "Dumping routines" not in text
    assert "FOREIGN_KEY_CHECKS=0" in text  # en-tête conservé
    assert "OLD_TIME_ZONE" in text  # pied de page conservé


def test_mysql_connection_uses_env_for_password(fake):
    runner = fake(lambda cmd, data: b"1\n")
    get_adapter(my()).test_connection()
    call = runner.calls[0]
    assert call["cmd"][0] == "mysql"
    assert "--protocol=TCP" in call["cmd"]
    assert call["env"] == {"MYSQL_PWD": "secret"}
    assert not any("secret" in arg for arg in call["cmd"])


def test_mysql_backup_then_restore(fake, tmp_path):
    def handler(cmd, data):
        tool = tool_of(cmd)
        if tool == "mysql" and data is None:
            return b"1\n"
        if tool == "mysqldump":
            return MYSQL_DUMP
        return b""

    runner = fake(handler)
    adapter = get_adapter(my())
    result = run_backup(adapter, tmp_path / "out")
    assert result.file_path.name.endswith(".sql.gz")
    run_restore(adapter, result.file_path, overwrite=True)
    last = runner.calls[-1]
    assert tool_of(last["cmd"]) == "mysql"
    assert last["stdin"] == MYSQL_DUMP


def test_mysql_selective_restore(fake, tmp_path):
    runner = fake()
    path = backup_file(tmp_path, MYSQL_DUMP)
    run_restore(get_adapter(my()), path, tables=["Clients"], overwrite=True)
    assert len(runner.calls) == 1
    sent = runner.calls[0]["stdin"].decode("utf-8")
    assert "Rakoto" in sent and "commandes" not in sent


def test_mysql_selective_unknown_table_runs_nothing(fake, tmp_path):
    runner = fake()
    path = backup_file(tmp_path, MYSQL_DUMP)
    with pytest.raises(RestoreError, match="fantome"):
        run_restore(get_adapter(my()), path, tables=["clients", "fantome"], overwrite=True)
    assert runner.calls == []


def test_mysql_restore_requires_overwrite(fake, tmp_path):
    runner = fake()
    with pytest.raises(RestoreError, match="overwrite"):
        run_restore(get_adapter(my()), backup_file(tmp_path, MYSQL_DUMP))
    assert runner.calls == []


# ------------------------------------------------------------------ MongoDB


def test_mongodb_backup_then_selective_restore(fake, tmp_path):
    archive = b"MONGO-ARCHIVE" * 50

    def handler(cmd, data):
        tool = tool_of(cmd)
        if tool == "mongosh":
            return b'["users"]\n'
        if tool == "mongodump":
            return archive
        return b""

    runner = fake(handler)
    adapter = get_adapter(mongo())
    result = run_backup(adapter, tmp_path / "out")
    assert result.file_path.name.endswith(".archive.gz")

    run_restore(adapter, result.file_path, tables=["users"], overwrite=True)
    last = runner.calls[-1]
    assert tool_of(last["cmd"]) == "mongorestore"
    assert "--nsInclude=testdb.users" in last["cmd"]
    assert "--drop" in last["cmd"]
    assert last["stdin"] == archive


def test_mongodb_full_restore_namespace(fake, tmp_path):
    runner = fake()
    path = backup_file(tmp_path, b"A", "b.archive.gz")
    run_restore(get_adapter(mongo()), path, overwrite=True)
    assert "--nsInclude=testdb.*" in runner.calls[0]["cmd"]


def test_mongodb_restore_requires_overwrite(fake, tmp_path):
    runner = fake()
    with pytest.raises(RestoreError, match="overwrite"):
        run_restore(get_adapter(mongo()), backup_file(tmp_path, b"A", "b.archive.gz"))
    assert runner.calls == []


# ------------------------------------------------------------------ erreurs


def test_missing_tool_message(fake):
    fake(lambda cmd, data: ToolNotFoundError("psql"))
    with pytest.raises(DatabaseConnectionError, match="Outil introuvable"):
        get_adapter(pg()).test_connection()


def test_missing_docker_message(fake):
    fake(lambda cmd, data: ToolNotFoundError("docker"))
    with pytest.raises(DatabaseConnectionError, match="Docker est introuvable"):
        get_adapter(pg(docker_container="c")).test_connection()


def test_process_error_message(fake):
    fake(lambda cmd, data: ProcessError(2, "FATAL: password authentication failed for user x"))
    with pytest.raises(DatabaseConnectionError, match="authentication failed"):
        get_adapter(pg()).test_connection()


def test_failed_dump_leaves_no_file(fake, tmp_path):
    def handler(cmd, data):
        if tool_of(cmd) == "psql":
            return b"1\n"
        return ProcessError(1, "pg_dump: boom")

    fake(handler)
    out = tmp_path / "out"
    with pytest.raises(BackupError, match="boom"):
        run_backup(get_adapter(pg()), out)
    assert list(out.iterdir()) == []
