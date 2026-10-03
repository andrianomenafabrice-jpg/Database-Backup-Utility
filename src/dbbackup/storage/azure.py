"""Stockage Azure Blob Storage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dbbackup.exceptions import StorageError
from dbbackup.storage.base import Storage, StorageObject


class AzureStorage(Storage):
    """Identifiants : variable d'environnement AZURE_STORAGE_CONNECTION_STRING."""

    def __init__(self, container: str, prefix: str = "", client=None) -> None:
        super().__init__(prefix)
        self.container_name = container
        self._client = client  # BlobServiceClient

    @property
    def location(self) -> str:
        return f"azure://{self.container_name}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def container(self):
        if self._client is None:
            try:
                from azure.storage.blob import BlobServiceClient
            except ImportError as exc:
                raise StorageError(
                    'Le paquet azure-storage-blob est requis pour Azure : pip install "dbbackup[azure]"'
                ) from exc
            connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
            if not connection_string:
                raise StorageError(
                    "Définissez la variable d'environnement AZURE_STORAGE_CONNECTION_STRING."
                )
            self._client = BlobServiceClient.from_connection_string(connection_string)
        return self._client.get_container_client(self.container_name)

    def _upload(self, local: Path, key: str) -> None:
        with open(local, "rb") as handle:
            self.container.upload_blob(name=key, data=handle, overwrite=True)

    def _download(self, key: str, local: Path) -> None:
        with open(local, "wb") as handle:
            self.container.download_blob(key).readinto(handle)

    def _size(self, key: str) -> Optional[int]:
        blob = self.container.get_blob_client(key)
        try:
            properties = blob.get_blob_properties()
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            raise
        return int(properties.size)

    def _list(self, key_prefix: str) -> List[StorageObject]:
        found = []
        for blob in self.container.list_blobs(name_starts_with=key_prefix or None):
            modified = getattr(blob, "last_modified", None)
            found.append(
                StorageObject(
                    name=blob.name,
                    size=int(blob.size or 0),
                    modified=modified.isoformat() if modified else None,
                )
            )
        return found

    def _delete(self, key: str) -> None:
        self.container.delete_blob(key)
