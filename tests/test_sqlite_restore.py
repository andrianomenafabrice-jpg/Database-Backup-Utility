import sqlite3

import pytest
from click.testing import CliRunner

from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.cli import cli
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import RestoreError
from dbbackup.restore import run_restore

SOURCE_CLIENTS = ["Rakoto", "O'Brien", "Ligne1\r\nLigne2 é", "temporaire"]
EXPECTED_CLIENTS = [(1, "Rakoto"), (2, "O'Brien"), (3, "Ligne1\r\nLigne2 é")]


def adapter_for(path):
    return get_adapter(ConnectionParams(db_type="sqlite", database=str(path)))


def query(db, sql):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def make_db(path, clients, commandes):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT);
        CREATE TABLE commandes (id INTEGER PRIMARY KEY, client_id INTEGER, montant REAL);
        CREATE INDEX idx_commandes_client ON commandes(client_id);
        CREATE TRIGGER trg_montant_positif BEFORE INSERT ON commandes
        WHEN NEW.montant < 0
        BEGIN
            SELECT RAISE(ABORT, 'montant négatif');
        END;
        """
    )
    conn.executemany("INSERT INTO clients (nom) VALUES (?)", [(n,) for n in clients])
    conn.executemany("INSERT INTO commandes (client_id, montant) VALUES (?, ?)", commandes)
    conn.commit()
    conn.close()


@pytest.fixture
def source(tmp_path):
    db = tmp_path / "source.db"
    make_db(db, SOURCE_CLIENTS, [(1, 10.5), (2, 20.0)])
    conn = sqlite3.connect(str(db))
    # la ligne supprimée fait que le compteur AUTOINCREMENT (4) dépasse l'id max (3)
    conn.execute("DELETE FROM clients WHERE nom = 'temporaire'")
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def backup_file(source, tmp_path):
    return run_backup(adapter_for(source), tmp_path / "backups").file_path


def test_full_restore_roundtrip(backup_file, tmp_path):
    target = tmp_path / "restored.db"
    result = run_restore(adapter_for(target), backup_file)
    assert result.verified is True
    assert query(target, "SELECT id, nom FROM clients ORDER BY id") == EXPECTED_CLIENTS
    assert query(target, "SELECT client_id, montant FROM commandes ORDER BY id") == [
        (1, 10.5),
        (2, 20.0),
    ]
    conn = sqlite3.connect(str(target))
    new_id = conn.execute("INSERT INTO clients (nom) VALUES ('nouveau')").lastrowid
    conn.commit()
    conn.close()
    assert new_id == 5  # le compteur AUTOINCREMENT a bien été restauré


def test_restore_refuses_existing_target(backup_file, tmp_path):
    target = tmp_path / "existing.db"
    make_db(target, ["x"], [])
    with pytest.raises(RestoreError):
        run_restore(adapter_for(target), backup_file)
    assert query(target, "SELECT nom FROM clients") == [("x",)]


def test_overwrite_keeps_safety_copy(backup_file, tmp_path):
    target = tmp_path / "existing.db"
    make_db(target, ["ancien"], [])
    result = run_restore(adapter_for(target), backup_file, overwrite=True)
    assert query(target, "SELECT id, nom FROM clients ORDER BY id") == EXPECTED_CLIENTS
    assert result.safety_copy is not None and result.safety_copy.exists()
    assert query(result.safety_copy, "SELECT nom FROM clients") == [("ancien",)]


def test_selective_restore_new_file(backup_file, tmp_path):
    target = tmp_path / "partial.db"
    run_restore(adapter_for(target), backup_file, tables=["commandes"])
    names = {row[0] for row in query(target, "SELECT name FROM sqlite_master")}
    assert {"commandes", "idx_commandes_client", "trg_montant_positif"} <= names
    assert "clients" not in names
    assert query(target, "SELECT COUNT(*) FROM commandes") == [(2,)]


def test_selective_restore_in_place(backup_file, tmp_path):
    target = tmp_path / "live.db"
    make_db(target, ["autre"], [(9, 99.0)])
    run_restore(adapter_for(target), backup_file, tables=["Clients"], overwrite=True)
    assert query(target, "SELECT id, nom FROM clients ORDER BY id") == EXPECTED_CLIENTS
    assert query(target, "SELECT client_id, montant FROM commandes") == [(9, 99.0)]
    names = {row[0] for row in query(target, "SELECT name FROM sqlite_master")}
    assert "idx_commandes_client" in names


def test_selective_restore_on_existing_target_requires_overwrite(backup_file, tmp_path):
    target = tmp_path / "live.db"
    make_db(target, ["autre"], [])
    with pytest.raises(RestoreError):
        run_restore(adapter_for(target), backup_file, tables=["clients"])
    assert query(target, "SELECT nom FROM clients") == [("autre",)]


def test_selective_restore_unknown_table_changes_nothing(backup_file, tmp_path):
    target = tmp_path / "live.db"
    make_db(target, ["autre"], [])
    with pytest.raises(RestoreError, match="introuvable"):
        run_restore(
            adapter_for(target), backup_file, tables=["clients", "inexistante"], overwrite=True
        )
    assert query(target, "SELECT nom FROM clients") == [("autre",)]


def test_corrupted_backup_is_rejected(backup_file, tmp_path):
    data = bytearray(backup_file.read_bytes())
    data[len(data) // 2] ^= 0xFF
    backup_file.write_bytes(bytes(data))
    target = tmp_path / "restored.db"
    with pytest.raises(RestoreError, match="Intégrité"):
        run_restore(adapter_for(target), backup_file)
    assert not target.exists()


def test_missing_metadata_restores_unverified(backup_file, tmp_path):
    backup_file.with_name(backup_file.name + ".meta.json").unlink()
    result = run_restore(adapter_for(tmp_path / "r.db"), backup_file)
    assert result.verified is False


def test_missing_backup_file(tmp_path):
    with pytest.raises(RestoreError, match="introuvable"):
        run_restore(adapter_for(tmp_path / "r.db"), tmp_path / "absent.sql.gz")


def test_truncated_dump_is_rejected(tmp_path):
    dump = tmp_path / "broken.sql"
    dump.write_text(
        'BEGIN TRANSACTION;\nCREATE TABLE a (id INTEGER);\nINSERT INTO "a" VALUES(1',
        encoding="utf-8",
    )
    target = tmp_path / "r.db"
    with pytest.raises(RestoreError, match="tronquée"):
        run_restore(adapter_for(target), dump)
    assert not target.exists()


def test_cli_backup_then_restore(source, tmp_path):
    runner = CliRunner()
    log_file = str(tmp_path / "t.log")
    out = tmp_path / "cli_backups"
    result = runner.invoke(
        cli,
        ["--log-file", log_file, "backup", "--type", "sqlite", "-d", str(source), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    bfile = next(out.glob("*.sql.gz"))

    target = tmp_path / "cli_restored.db"
    result = runner.invoke(
        cli,
        ["--log-file", log_file, "restore", "--type", "sqlite", "-d", str(target), "-f", str(bfile)],
    )
    assert result.exit_code == 0, result.output
    assert "[OK]" in result.output
    assert query(target, "SELECT COUNT(*) FROM clients") == [(3,)]
