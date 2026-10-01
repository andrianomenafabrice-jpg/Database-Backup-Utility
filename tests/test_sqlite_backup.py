import gzip
import json
import sqlite3

import pytest

from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import BackupError, ConfigError, DatabaseConnectionError
from dbbackup.utils.helpers import human_size, sha256_file


@pytest.fixture
def sample_db(tmp_path):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany(
        "INSERT INTO users (name) VALUES (?)", [("Rado",), ("Voahangy",), ("Tiana",)]
    )
    conn.commit()
    conn.close()
    return db


def make_adapter(path):
    return get_adapter(ConnectionParams(db_type="sqlite", database=str(path)))


def test_connection_ok(sample_db):
    make_adapter(sample_db).test_connection()


def test_connection_missing_file(tmp_path):
    with pytest.raises(DatabaseConnectionError):
        make_adapter(tmp_path / "absent.db").test_connection()


def test_connection_invalid_file(tmp_path):
    fake = tmp_path / "fake.db"
    fake.write_bytes(b"x" * 200)
    with pytest.raises(DatabaseConnectionError):
        make_adapter(fake).test_connection()


def test_compressed_backup_is_restorable(sample_db, tmp_path):
    result = run_backup(make_adapter(sample_db), tmp_path / "out")
    assert result.file_path.name.endswith(".sql.gz")

    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert meta["sha256"] == sha256_file(result.file_path)
    assert meta["mode"] == "full"
    assert "password" not in json.dumps(meta).lower()

    with gzip.open(result.file_path, "rt", encoding="utf-8") as handle:
        sql = handle.read()
    restored = sqlite3.connect(":memory:")
    restored.executescript(sql)
    rows = restored.execute("SELECT name FROM users ORDER BY id").fetchall()
    restored.close()
    assert rows == [("Rado",), ("Voahangy",), ("Tiana",)]


def test_uncompressed_backup(sample_db, tmp_path):
    result = run_backup(make_adapter(sample_db), tmp_path / "out", compress=False)
    assert result.file_path.name.endswith(".sql")
    assert "CREATE TABLE" in result.file_path.read_text(encoding="utf-8")


def test_no_partial_file_left(sample_db, tmp_path):
    out = tmp_path / "out"
    run_backup(make_adapter(sample_db), out)
    assert list(out.glob("*.part")) == []


def test_failed_backup_leaves_nothing(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(DatabaseConnectionError):
        run_backup(make_adapter(tmp_path / "absent.db"), out)
    assert not out.exists() or list(out.iterdir()) == []


def test_unsupported_mode(sample_db, tmp_path):
    with pytest.raises(BackupError):
        run_backup(make_adapter(sample_db), tmp_path / "out", mode="incremental")


def test_adapter_not_available_yet():
    with pytest.raises(ConfigError):
        get_adapter(ConnectionParams(db_type="mysql", database="x"))


def test_human_size():
    assert human_size(500) == "500 B"
    assert human_size(2048) == "2.0 KB"
