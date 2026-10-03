"""Stockages des sauvegardes : local, Amazon S3, Google Cloud Storage, Azure Blob."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse
from urllib.request import url2pathname

from dbbackup.exceptions import ConfigError
from dbbackup.storage.azure import AzureStorage
from dbbackup.storage.base import Storage, StorageObject
from dbbackup.storage.gcs import GCSStorage
from dbbackup.storage.local import LocalStorage
from dbbackup.storage.s3 import S3Storage

_REMOTE = re.compile(r"^(s3|gs|gcs|azure|az)://([^/]+)/?(.*)$", re.IGNORECASE)
_ANY_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+://")


def is_remote(value: str) -> bool:
    """True pour s3://, gs://, gcs://, azure://, az://."""
    return bool(_REMOTE.match(str(value)))


def open_storage(destination: str) -> Storage:
    """Ouvre un stockage depuis une destination :
    s3://bucket/dossier, gs://bucket/dossier, azure://conteneur/dossier, file:///chemin
    ou un simple dossier local."""
    destination = str(destination)
    match = _REMOTE.match(destination)
    if match:
        scheme, bucket, prefix = match.group(1).lower(), match.group(2), match.group(3)
        if scheme == "s3":
            return S3Storage(bucket, prefix)
        if scheme in ("gs", "gcs"):
            return GCSStorage(bucket, prefix)
        return AzureStorage(bucket, prefix)
    if destination.lower().startswith("file://"):
        return LocalStorage(Path(url2pathname(urlparse(destination).path)))
    if _ANY_SCHEME.match(destination):
        raise ConfigError(
            f"Destination non supportée : {destination}. "
            "Utilisez s3://, gs://, azure:// ou un dossier local."
        )
    return LocalStorage(Path(destination))


def split_location(value: str) -> Tuple[str, str]:
    """Sépare « dossier ou préfixe » et « nom du fichier » : s3://b/p/f.gz -> (s3://b/p, f.gz)."""
    value = str(value)
    if is_remote(value):
        head, _, name = value.rstrip("/").rpartition("/")
        if not name or head.endswith(":/"):
            raise ConfigError(f"Il manque le nom du fichier dans : {value}")
        return head, name
    path = Path(value)
    return str(path.parent), path.name


__all__ = [
    "Storage", "StorageObject", "LocalStorage", "S3Storage", "GCSStorage", "AzureStorage",
    "open_storage", "split_location", "is_remote",
]
