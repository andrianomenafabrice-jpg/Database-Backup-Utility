import gzip
import json
import sqlite3

import pytest
from click.testing import CliRunner

from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.catalog import build_chain, list_backups
from dbbackup.cli import cli
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import BackupError, RestoreError
from dbbackup.restore import run_restore


def adapter_for(path):
    return get_adapter(ConnectionParams(db_type="sqlite", database=str(path)))


def execute(db, *statements):
    conn = sqlite3.connect(str(db))
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def state(db):
    conn = sqlite3.connect(str(db))
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND substr(name, 1, 7) != 'sqlite_' ORDER BY name"
            )
        ]
        return {n: conn.execute(f'SELECT * FROM "{n}" ORDER BY 1').fetchall() for n in names}
    finally:
        conn.close()


def read_sql(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def source(tmp_path):
    db = tmp_path / "shop.db"
    execute(
        db,
        "CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT)",
        "CREATE TABLE commandes (id INTEGER PRIMARY KEY, client_id INTEGER, montant REAL)",
        "CREATE TABLE produits (id INTEGER PRIMARY KEY, libelle TEXT)",
        "INSERT INTO clients (nom) VALUES ('Rakoto'), ('Rasoa')",
        "INSERT INTO commandes (client_id, montant) VALUES (1, 10.5)",
        "INSERT INTO produits (libelle) VALUES ('Riz'), ('Sucre')",
    )
    return db


def test_full_backup_records_fingerprints(source, tmp_path):
    result = run_backup(adapter_for(source), tmp_path / "b")
    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert set(meta["tables"]) == {"clients", "commandes", "produits"}
    assert meta["parent"] is None and meta["included_tables"] is None


def test_fingerprints_are_stable_for_unchanged_data(source, tmp_path):
    out = tmp_path / "b"
    first = run_backup(adapter_for(source), out)
    second = run_backup(adapter_for(source), out)
    meta_a = json.loads(first.meta_path.read_text(encoding="utf-8"))
    meta_b = json.loads(second.meta_path.read_text(encoding="utf-8"))
    assert meta_a["tables"] == meta_b["tables"]
    assert first.file_path != second.file_path


def test_incremental_without_changes_includes_nothing(source, tmp_path):
    out = tmp_path / "b"
    full = run_backup(adapter_for(source), out)
    inc = run_backup(adapter_for(source), out, mode="incremental")
    assert inc.included_tables == []
    assert inc.parent == full.file_path.name
    assert "INSERT" not in read_sql(inc.file_path)


def test_incremental_includes_only_changed_tables(source, tmp_path):
    out = tmp_path / "b"
    run_backup(adapter_for(source), out)
    execute(source, "INSERT INTO commandes (client_id, montant) VALUES (2, 99.0)")
    inc = run_backup(adapter_for(source), out, mode="incremental")
    assert inc.included_tables == ["commandes"]
    sql = read_sql(inc.file_path)
    assert "99.0" in sql and "Rakoto" not in sql and "Riz" not in sql


def test_differential_is_cumulative(source, tmp_path):
    out = tmp_path / "b"
    full = run_backup(adapter_for(source), out)
    execute(source, "INSERT INTO commandes (client_id, montant) VALUES (2, 1.0)")
    run_backup(adapter_for(source), out, mode="incremental")
    execute(source, "INSERT INTO produits (libelle) VALUES ('Huile')")
    diff = run_backup(adapter_for(source), out, mode="differential")
    assert diff.included_tables == ["commandes", "produits"]
    assert diff.parent == full.file_path.name

    target = tmp_path / "restored.db"
    result = run_restore(adapter_for(target), diff.file_path)
    assert result.chain == 2
    assert state(target) == state(source)


def test_incremental_chain_restore_with_dropped_table(source, tmp_path):
    out = tmp_path / "b"
    adapter = adapter_for(source)
    run_backup(adapter, out)
    execute(source, "INSERT INTO commandes (client_id, montant) VALUES (2, 99.0)")
    run_backup(adapter, out, mode="incremental")
    execute(source, "INSERT INTO clients (nom) VALUES ('Hery')", "DROP TABLE produits")
    last = run_backup(adapter, out, mode="incremental")
    assert last.included_tables == ["clients"]
    assert last.dropped_tables == ["produits"]

    target = tmp_path / "restored.db"
    result = run_restore(adapter_for(target), last.file_path)
    assert result.chain == 3 and result.verified is True
    assert state(target) == state(source)
    assert "produits" not in state(target)


def test_autoincrement_counter_survives_chain(source, tmp_path):
    out = tmp_path / "b"
    run_backup(adapter_for(source), out)
    # le compteur AUTOINCREMENT avance sans que les lignes changent
    execute(source, "INSERT INTO clients (nom) VALUES ('temp')", "DELETE FROM clients WHERE nom = 'temp'")
    inc = run_backup(adapter_for(source), out, mode="incremental")
    assert "clients" in inc.included_tables

    target = tmp_path / "restored.db"
    run_restore(adapter_for(target), inc.file_path)
    conn = sqlite3.connect(str(target))
    new_id = conn.execute("INSERT INTO clients (nom) VALUES ('x')").lastrowid
    conn.commit()
    conn.close()
    assert new_id == 4


def test_chain_restore_refuses_existing_target_then_keeps_safety_copy(source, tmp_path):
    out = tmp_path / "b"
    run_backup(adapter_for(source), out)
    execute(source, "INSERT INTO produits (libelle) VALUES ('Huile')")
    inc = run_backup(adapter_for(source), out, mode="incremental")

    target = tmp_path / "existing.db"
    execute(target, "CREATE TABLE autre (id INTEGER)")
    with pytest.raises(RestoreError):
        run_restore(adapter_for(target), inc.file_path)
    assert list(state(target)) == ["autre"]

    result = run_restore(adapter_for(target), inc.file_path, overwrite=True)
    assert state(target) == state(source)
    assert result.safety_copy is not None and result.safety_copy.exists()


def test_missing_parent_is_reported(source, tmp_path):
    out = tmp_path / "b"
    full = run_backup(adapter_for(source), out)
    inc = run_backup(adapter_for(source), out, mode="incremental")
    full.file_path.unlink()
    target = tmp_path / "r.db"
    with pytest.raises(RestoreError, match="parente"):
        run_restore(adapter_for(target), inc.file_path)
    assert not target.exists()


def test_tampered_parent_is_rejected(source, tmp_path):
    out = tmp_path / "b"
    full = run_backup(adapter_for(source), out)
    inc = run_backup(adapter_for(source), out, mode="incremental")
    data = bytearray(full.file_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    full.file_path.write_bytes(bytes(data))
    target = tmp_path / "r.db"
    with pytest.raises(RestoreError, match="Intégrité"):
        run_restore(adapter_for(target), inc.file_path)
    assert not target.exists()


def test_incremental_requires_a_full_backup(source, tmp_path):
    with pytest.raises(BackupError, match="complète"):
        run_backup(adapter_for(source), tmp_path / "b", mode="incremental")


def test_incremental_not_available_for_other_dbms(tmp_path):
    adapter = get_adapter(ConnectionParams(db_type="postgresql", database="x"))
    with pytest.raises(BackupError, match="postgresql"):
        run_backup(adapter, tmp_path / "b", mode="incremental")


def test_selective_restore_of_a_chain_is_refused(source, tmp_path):
    out = tmp_path / "b"
    run_backup(adapter_for(source), out)
    inc = run_backup(adapter_for(source), out, mode="incremental")
    with pytest.raises(RestoreError, match="sélective"):
        run_restore(adapter_for(tmp_path / "r.db"), inc.file_path, tables=["clients"])


def test_catalog_lists_backups_in_order_and_builds_chain(source, tmp_path):
    out = tmp_path / "b"
    full = run_backup(adapter_for(source), out)
    inc1 = run_backup(adapter_for(source), out, mode="incremental")
    inc2 = run_backup(adapter_for(source), out, mode="incremental")
    names = [m["file"] for m in list_backups(out)]
    assert names == [full.file_path.name, inc1.file_path.name, inc2.file_path.name]
    chain = build_chain(inc2.file_path)
    assert [m["file"] for m in chain] == names


def test_cli_incremental_and_list(source, tmp_path):
    runner = CliRunner()
    base = ["--log-file", str(tmp_path / "t.log")]
    out = str(tmp_path / "cli_b")
    args = ["backup", "--type", "sqlite", "-d", str(source), "-o", out]

    result = runner.invoke(cli, base + args)
    assert result.exit_code == 0, result.output
    execute(source, "INSERT INTO produits (libelle) VALUES ('Huile')")
    result = runner.invoke(cli, base + args + ["--mode", "incremental"])
    assert result.exit_code == 0, result.output
    assert "Tables sauvegardées : produits" in result.output
    assert "Basée sur" in result.output

    result = runner.invoke(cli, base + ["list", "-o", out])
    assert result.exit_code == 0, result.output
    assert "full" in result.output and "incremental" in result.output and "<-" in result.output
