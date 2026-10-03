"""Stockage Amazon S3 (et compatibles : MinIO, Wasabi... via DBBACKUP_S3_ENDPOINT)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dbbackup.exceptions import StorageError
from dbbackup.storage.base import Storage, StorageObject

_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


class S3Storage(Storage):
    """Identifiants : chaîne standard AWS (variables AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY,
    fichier ~/.aws, rôle IAM...). Les gros fichiers sont envoyés en multipart par boto3."""

    def __init__(self, bucket: str, prefix: str = "", client=None) -> None:
        super().__init__(prefix)
        self.bucket = bucket
        self._client = client

    @property
    def location(self) -> str:
        return f"s3://{self.bucket}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise StorageError(
                    'Le paquet boto3 est requis pour S3 : pip install "dbbackup[s3]"'
                ) from exc
            kwargs = {}
            endpoint = os.environ.get("DBBACKUP_S3_ENDPOINT")
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _upload(self, local: Path, key: str) -> None:
        self.client.upload_file(str(local), self.bucket, key)

    def _download(self, key: str, local: Path) -> None:
        self.client.download_file(self.bucket, key, str(local))

    def _size(self, key: str) -> Optional[int]:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            details = getattr(exc, "response", None)
            code = ""
            if isinstance(details, dict):
                code = str(details.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND:
                return None
            raise
        return int(response["ContentLength"])

    def _list(self, key_prefix: str) -> List[StorageObject]:
        paginator = self.client.get_paginator("list_objects_v2")
        found = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                modified = obj.get("LastModified")
                found.append(
                    StorageObject(
                        name=obj["Key"],
                        size=int(obj["Size"]),
                        modified=modified.isoformat() if modified else None,
                    )
                )
        return found

    def _delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
