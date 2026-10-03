"""Stockage dans un dossier local (disque, partage réseau, NAS monté...)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from dbbackup.storage.base import Storage, StorageObject


class LocalStorage(Storage):
    def __init__(self, root) -> None:
        super().__init__("")
        self.root = Path(root).expanduser()

    @property
    def location(self) -> str:
        return str(self.root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*key.split("/"))

    def _upload(self, local: Path, key: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            shutil.copyfile(local, part)
            part.replace(dest)  # renommage atomique : jamais de fichier incomplet
        finally:
            part.unlink(missing_ok=True)

    def _download(self, key: str, local: Path) -> None:
        shutil.copyfile(self._path(key), local)

    def _size(self, key: str) -> Optional[int]:
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None

    def _list(self, key_prefix: str) -> List[StorageObject]:
        if not self.root.is_dir():
            return []
        found = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.endswith(".part"):
                name = path.relative_to(self.root).as_posix()
                found.append(StorageObject(name=name, size=path.stat().st_size))
        return found

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
