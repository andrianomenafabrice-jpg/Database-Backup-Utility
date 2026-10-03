import sqlite3
import sys
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.cli import cli
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError, StorageError
from dbbackup.logger import redact
from dbbackup.restore import run_restore
from dbbackup.storage import (
    AzureStorage, GCSStorage, LocalStorage, S3Storage, is_remote, open_storage, split_location,
)
from dbbackup.transfer import fetch_backup, list_remote_backups, upload_backup


# ----------------------------------------------------------------- faux SDK


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    """Imite boto3.client("s3") : upload_file, download_file, head_object, paginator, delete."""

    def __init__(self):
        self.objects = {}

    def upload_file(self, Filename, Bucket, Key):
        with open(Filename, "rb") as handle:
            self.objects[(Bucket, Key)] = handle.read()

    def download_file(self, Bucket, Key, Filename):
        with open(Filename, "wb") as handle:
            handle.write(self.objects[(Bucket, Key)])

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FakeClientError("404")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for (b, k) in client.objects if b == Bucket and k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 2):  # pages de 2 : teste la pagination
                    yield {"Contents": [
                        {"Key": k, "Size": len(client.objects[(Bucket, k)]),
                         "LastModified": datetime(2026, 10, 1, tzinfo=timezone.utc)}
                        for k in keys[i:i + 2]
                    ]} if keys else {}

        return Paginator()

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class TruncatingS3Client(FakeS3Client):
    def upload_file(self, Filename, Bucket, Key):
        with open(Filename, "rb") as handle:
            self.objects[(Bucket, Key)] = handle.read()[:-1]


class FakeBlob:
    def __init__(self, store, key):
        self.store, self.name = store, key

    @property
    def size(self):
        return len(self.store[self.name])

    updated = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def upload_from_filename(self, filename):
        with open(filename, "rb") as handle:
            self.store[self.name] = handle.read()

    def download_to_filename(self, filename):
        with open(filename, "wb") as handle:
            handle.write(self.store[self.name])

    def delete(self):
        del self.store[self.name]


class FakeBucket:
    def __init__(self, store):
        self.store = store

    def blob(self, key):
        return FakeBlob(self.store, key)

    def get_blob(self, key):
        return FakeBlob(self.store, key) if key in self.store else None


class FakeGCSClient:
    def __init__(self):
        self.store = {}

    def bucket(self, name):
        return FakeBucket(self.store)

    def list_blobs(self, bucket, prefix=None):
        return [FakeBlob(self.store, k) for k in sorted(self.store) if k.startswith(prefix or "")]


class ResourceNotFoundError(Exception):
    pass


class FakeAzureBlobClient:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get_blob_properties(self):
        if self.key not in self.store:
            raise ResourceNotFoundError("BlobNotFound")

        class Props:
            size = len(self.store[self.key])

        return Props()


class FakeAzureContainer:
    def __init__(self, store):
        self.store = store

    def upload_blob(self, name, data, overwrite=False):
        self.store[name] = data.read()

    def download_blob(self, key):
        store = self.store

        class Downloader:
            def readinto(self, stream):
                stream.write(store[key])

        return Downloader()

    def get_blob_client(self, key):
        return FakeAzureBlobClient(self.store, key)

    def list_blobs(self, name_starts_with=None):
        class Item:
            def __init__(self, name, size):
                self.name, self.size = name, size
                self.last_modified = datetime(2026, 10, 1, tzinfo=timezone.utc)

        return [Item(k, len(v)) for k, v in sorted(self.store.items())
                if k.startswith(name_starts_with or "")]

    def delete_blob(self, key):
        del self.store[key]


class FakeAzureService:
    def __init__(self):
        self.store = {}

    def get_container_client(self, name):
        return FakeAzureContainer(self.store)


# ----------------------------------------------------------------- helpers


def storages(tmp_path):
    return {
        "local": LocalStorage(tmp_path / "remote"),
        "s3": S3Storage("bucket", "backups/shop", client=FakeS3Client()),
        "gcs": GCSStorage("bucket", "backups/shop", client=FakeGCSClient()),
        "azure": AzureStorage("conteneur", "backups/shop", client=FakeAzureService()),
    }


def make_source(tmp_path):
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, nom TEXT)")
    conn.execute("CREATE TABLE produits (id INTEGER PRIMARY KEY, libelle TEXT)")
    conn.execute("INSERT INTO clients (nom) VALUES ('Rakoto'), ('Rasoa')")
    conn.execute("INSERT INTO produits (libelle) VALUES ('Riz')")
    conn.commit()
    conn.close()
    return db


def state(db):
    conn = sqlite3.connect(str(db))
    try:
        return {
            t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in ("clients", "produits")
        }
    finally:
        conn.close()


def adapter_for(path):
    return get_adapter(ConnectionParams(db_type="sqlite", database=str(path)))


# ----------------------------------------------------------------- tests


def test_every_storage_supports_put_list_download_delete(tmp_path):
    local = tmp_path / "f.bin"
    local.write_bytes(b"hello" * 100)
    for label, storage in storages(tmp_path).items():
        storage.put(local, "f.bin")
        assert storage.size("f.bin") == 500, label
        assert storage.size("absent.bin") is None, label
        assert [(o.name, o.size) for o in storage.list()] == [("f.bin", 500)], label
        back = tmp_path / f"back_{label}.bin"
        storage.download("f.bin", back)
        assert back.read_bytes() == local.read_bytes(), label
        storage.delete("f.bin")
        assert storage.size("f.bin") is None, label


def test_s3_uses_prefix_and_pagination(tmp_path):
    client = FakeS3Client()
    storage = S3Storage("bucket", "backups/shop", client=client)
    local = tmp_path / "x"
    local.write_bytes(b"1")
    for name in ("a", "b", "c", "d", "e"):
        storage.put(local, name)
    assert ("bucket", "backups/shop/a") in client.objects
    assert [o.name for o in storage.list()] == ["a", "b", "c", "d", "e"]


def test_incomplete_upload_is_detected(tmp_path):
    local = tmp_path / "x"
    local.write_bytes(b"abcdef")
    storage = S3Storage("bucket", "p", client=TruncatingS3Client())
    with pytest.raises(StorageError, match="incomplet"):
        storage.put(local, "x")


def test_sdk_errors_become_storage_errors_without_secrets(tmp_path):
    class Boom(FakeS3Client):
        def upload_file(self, Filename, Bucket, Key):
            raise RuntimeError("denied password=hunter2 for user")

    local = tmp_path / "x"
    local.write_bytes(b"1")
    with pytest.raises(StorageError) as exc_info:
        S3Storage("bucket", client=Boom()).put(local, "x")
    assert "hunter2" not in str(exc_info.value) and "denied" in str(exc_info.value)


def test_path_traversal_is_rejected(tmp_path):
    local = tmp_path / "x"
    local.write_bytes(b"1")
    with pytest.raises(StorageError, match="invalide"):
        LocalStorage(tmp_path / "r").put(local, "../evil")


def test_open_storage_dispatch(tmp_path):
    assert isinstance(open_storage("s3://bucket/a/b"), S3Storage)
    assert open_storage("s3://bucket/a/b").prefix == "a/b"
    assert isinstance(open_storage("gs://bucket"), GCSStorage)
    assert isinstance(open_storage("azure://conteneur/x"), AzureStorage)
    assert isinstance(open_storage(str(tmp_path)), LocalStorage)
    assert isinstance(open_storage("file://" + tmp_path.as_posix()), LocalStorage)
    with pytest.raises(ConfigError):
        open_storage("ftp://serveur/dossier")


def test_is_remote_and_split_location():
    assert is_remote("s3://b/x") and is_remote("gs://b/x") and is_remote("azure://c/x")
    assert not is_remote("backups/x.gz") and not is_remote("C:\\backups\\x.gz")
    assert split_location("s3://bucket/dir/sub/f.gz") == ("s3://bucket/dir/sub", "f.gz")
    assert split_location("s3://bucket/f.gz") == ("s3://bucket", "f.gz")
    with pytest.raises(ConfigError):
        split_location("s3://bucket")


def test_missing_sdk_gives_install_hint():
    saved = {k: sys.modules.get(k, "absent") for k in ("boto3", "google.cloud", "azure.storage.blob")}
    try:
        for key in saved:
            sys.modules[key] = None  # force ImportError
        with pytest.raises(StorageError, match="dbbackup\\[s3\\]"):
            S3Storage("b").size("x")
        with pytest.raises(StorageError, match="dbbackup\\[gcs\\]"):
            GCSStorage("b").size("x")
        with pytest.raises(StorageError, match="dbbackup\\[azure\\]"):
            AzureStorage("c").size("x")
    finally:
        for key, value in saved.items():
            if value == "absent":
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def test_azure_requires_connection_string(monkeypatch):
    import os
    os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)
    saved = sys.modules.get("azure.storage.blob", "absent")
    sys.modules["azure.storage.blob"] = type(sys)("azure.storage.blob")
    sys.modules["azure.storage.blob"].BlobServiceClient = object
    try:
        with pytest.raises(StorageError, match="AZURE_STORAGE_CONNECTION_STRING"):
            AzureStorage("c").size("x")
    finally:
        if saved == "absent":
            sys.modules.pop("azure.storage.blob", None)
        else:
            sys.modules["azure.storage.blob"] = saved


def test_redact_hides_azure_keys():
    text = "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=abc123==;EndpointSuffix=x"
    assert "abc123" not in redact(text)
    assert "sig=SECRET" not in redact("https://x/blob?sv=1&sig=SECRET&se=2")


def test_backup_chain_roundtrip_through_each_storage(tmp_path, monkeypatch):
    for label, storage in storages(tmp_path).items():
        work = tmp_path / label
        work.mkdir()
        source = make_source(work)
        out = work / "backups"
        adapter = adapter_for(source)

        full = run_backup(adapter, out)
        upload_backup(full, storage)
        conn = sqlite3.connect(str(source))
        conn.execute("INSERT INTO clients (nom) VALUES ('Hery')")
        conn.commit()
        conn.close()
        inc = run_backup(adapter, out, mode="incremental")
        upload_backup(inc, storage)

        remote = {o.name for o in storage.list()}
        assert inc.file_path.name in remote and inc.meta_path.name in remote, label

        monkeypatch.setattr("dbbackup.transfer.open_storage", lambda dest, s=storage: s)
        remote_url = f"s3://bucket/backups/shop/{inc.file_path.name}"
        fetched = work / "fetched"
        fetched.mkdir()
        local = fetch_backup(remote_url, fetched)
        assert local.name == inc.file_path.name
        assert full.file_path.name in {p.name for p in fetched.iterdir()}, label  # parent récupéré

        target = work / "restored.db"
        result = run_restore(adapter_for(target), local)
        assert result.chain == 2 and result.verified is True, label
        assert state(target) == state(source), label

        listed = list_remote_backups(storage)
        assert [m["mode"] for m in listed] == ["full", "incremental"], label


def test_fetch_reports_missing_file_and_missing_parent(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    out = tmp_path / "backups"
    storage = LocalStorage(tmp_path / "remote")
    full = run_backup(adapter_for(source), out)
    inc = run_backup(adapter_for(source), out, mode="incremental")
    upload_backup(inc, storage)  # le parent n'est PAS envoyé
    monkeypatch.setattr("dbbackup.transfer.open_storage", lambda dest: storage)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(StorageError, match="introuvable"):
        fetch_backup("s3://b/p/absent.sql.gz", work)
    with pytest.raises(Exception, match="parente"):
        fetch_backup(f"s3://b/p/{inc.file_path.name}", work)
    assert full.file_path.exists()


def test_cli_upload_restore_and_list_remote(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    runner = CliRunner()
    base = ["--log-file", str(tmp_path / "t.log")]
    out = str(tmp_path / "local_backups")
    dest = tmp_path / "nas"

    result = runner.invoke(cli, base + [
        "backup", "--type", "sqlite", "-d", str(source), "-o", out, "--upload", str(dest),
    ])
    assert result.exit_code == 0, result.output
    assert "Envoyée vers" in result.output
    uploaded = sorted(p.name for p in dest.iterdir())
    assert len(uploaded) == 2 and uploaded[1].endswith(".meta.json")

    # restauration depuis une URL distante (le stockage est simulé par le dossier "nas")
    monkeypatch.setattr("dbbackup.transfer.open_storage", lambda d: LocalStorage(dest))
    target = tmp_path / "restored.db"
    result = runner.invoke(cli, base + [
        "restore", "--type", "sqlite", "-d", str(target), "-f", f"s3://bucket/nas/{uploaded[0]}",
    ])
    assert result.exit_code == 0, result.output
    assert state(target) == state(source)

    result = runner.invoke(cli, base + ["list", "-o", "s3://bucket/nas"])
    assert result.exit_code == 0, result.output
    assert "full" in result.output
