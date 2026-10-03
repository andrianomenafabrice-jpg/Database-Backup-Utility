"""Interface commune des stockages (local, S3, Google Cloud Storage, Azure Blob)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from dbbackup.exceptions import StorageError
from dbbackup.logger import redact


@dataclass
class StorageObject:
    name: str  # nom relatif au préfixe du stockage
    size: int
    modified: Optional[str] = None


class Storage(ABC):
    """Un emplacement de sauvegardes. Les noms passés aux méthodes publiques sont relatifs
    au préfixe ; les erreurs des SDK sont converties en StorageError (secrets masqués)."""

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix.strip("/")

    # --- à implémenter (clés complètes) -----------------------------------------

    @property
    @abstractmethod
    def location(self) -> str:
        """Description lisible, ex : s3://bucket/dossier"""

    @abstractmethod
    def _upload(self, local: Path, key: str) -> None: ...

    @abstractmethod
    def _download(self, key: str, local: Path) -> None: ...

    @abstractmethod
    def _size(self, key: str) -> Optional[int]:
        """Taille de l'objet, ou None s'il n'existe pas."""

    @abstractmethod
    def _list(self, key_prefix: str) -> List[StorageObject]:
        """Objets dont la clé commence par key_prefix (noms = clés complètes)."""

    @abstractmethod
    def _delete(self, key: str) -> None: ...

    # --- API publique ----------------------------------------------------------

    def _key(self, name: str) -> str:
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        if not parts or ".." in parts:
            raise StorageError(f"Nom d'objet invalide : {name!r}")
        key = "/".join(parts)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _call(self, action: str, func: Callable, *args):
        try:
            return func(*args)
        except StorageError:
            raise
        except Exception as exc:
            detail = redact(str(exc))[:500] or type(exc).__name__
            raise StorageError(f"{action} impossible ({self.location}) : {detail}") from exc

    def put(self, local_path: Path, name: str) -> None:
        """Envoie un fichier puis vérifie que la taille distante correspond."""
        local_path = Path(local_path)
        key = self._key(name)
        expected = local_path.stat().st_size
        self._call(f"Envoi de {name}", self._upload, local_path, key)
        remote = self._call(f"Vérification de {name}", self._size, key)
        if remote != expected:
            raise StorageError(
                f"Envoi de {name} incomplet ({self.location}) : "
                f"{remote} octets reçus au lieu de {expected}."
            )

    def download(self, name: str, local_path: Path) -> None:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._call(f"Téléchargement de {name}", self._download, self._key(name), local_path)

    def size(self, name: str) -> Optional[int]:
        return self._call(f"Lecture de {name}", self._size, self._key(name))

    def delete(self, name: str) -> None:
        self._call(f"Suppression de {name}", self._delete, self._key(name))

    def list(self) -> List[StorageObject]:
        key_prefix = f"{self.prefix}/" if self.prefix else ""
        objects = self._call("Listage", self._list, key_prefix)
        result = []
        for obj in objects:
            name = obj.name[len(key_prefix):] if key_prefix and obj.name.startswith(key_prefix) else obj.name
            if name:
                result.append(StorageObject(name=name, size=obj.size, modified=obj.modified))
        return sorted(result, key=lambda o: o.name)
