"""Stockage Google Cloud Storage."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from dbbackup.exceptions import StorageError
from dbbackup.storage.base import Storage, StorageObject


class GCSStorage(Storage):
    """Identifiants : Application Default Credentials (variable GOOGLE_APPLICATION_CREDENTIALS,
    `gcloud auth application-default login`, compte de service...)."""

    def __init__(self, bucket: str, prefix: str = "", client=None) -> None:
        super().__init__(prefix)
        self.bucket = bucket
        self._client = client

    @property
    def location(self) -> str:
        return f"gs://{self.bucket}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def client(self):
        if self._client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise StorageError(
                    'Le paquet google-cloud-storage est requis pour GCS : pip install "dbbackup[gcs]"'
                ) from exc
            self._client = storage.Client()
        return self._client

    def _blob(self, key: str):
        return self.client.bucket(self.bucket).blob(key)

    def _upload(self, local: Path, key: str) -> None:
        self._blob(key).upload_from_filename(str(local))

    def _download(self, key: str, local: Path) -> None:
        self._blob(key).download_to_filename(str(local))

    def _size(self, key: str) -> Optional[int]:
        blob = self.client.bucket(self.bucket).get_blob(key)
        return None if blob is None else int(blob.size)

    def _list(self, key_prefix: str) -> List[StorageObject]:
        found = []
        for blob in self.client.list_blobs(self.bucket, prefix=key_prefix):
            updated = getattr(blob, "updated", None)
            found.append(
                StorageObject(
                    name=blob.name,
                    size=int(blob.size or 0),
                    modified=updated.isoformat() if updated else None,
                )
            )
        return found

    def _delete(self, key: str) -> None:
        self._blob(key).delete()
