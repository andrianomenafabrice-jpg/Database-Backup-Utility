"""Interface commune à tous les adaptateurs de bases de données."""

from __future__ import annotations

import gzip
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, List, Optional, Sequence

from dbbackup.config import ConnectionParams
from dbbackup.exceptions import BackupError, RestoreError
from dbbackup.utils.helpers import safe_name


@dataclass
class RestoreOutcome:
    """Résultat d'une restauration côté adaptateur."""

    statements: int
    safety_copy: Optional[Path] = None


@dataclass
class SnapshotResult:
    """Résultat d'un export pris sur un instantané cohérent de la base."""

    fingerprints: Dict[str, str]  # empreinte de chaque table à cet instant
    included: Optional[List[str]] = None  # tables écrites ; None = tout (sauvegarde complète)
    dropped: List[str] = field(default_factory=list)  # tables supprimées depuis la référence


@dataclass
class ChainStep:
    """Une sauvegarde de la chaîne (complète, puis incrémentales / différentielle)."""

    path: Path
    mode: str
    included: Optional[List[str]]
    dropped: List[str]

    @contextmanager
    def stream(self) -> Iterator[BinaryIO]:
        """Ouvre la sauvegarde en lecture, en décompressant si nécessaire."""
        with open(self.path, "rb") as raw:
            magic = raw.read(2)
            raw.seek(0)
            yield gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw


class DatabaseAdapter(ABC):
    """Contrat que chaque SGBD doit respecter."""

    #: extension du fichier de sauvegarde brut (avant compression)
    file_extension = "sql"
    #: True si l'adaptateur sait faire des sauvegardes incrémentales / différentielles
    supports_incremental = False

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params

    @property
    def label(self) -> str:
        """Nom court de la base, utilisé dans le nom des fichiers de sauvegarde."""
        return safe_name(self.params.database)

    @property
    def identity(self) -> str:
        """Identifiant stable de la base source (relie les sauvegardes d'une même base)."""
        p = self.params
        return f"{p.db_type}://{p.host}:{p.port}/{p.database}"

    @abstractmethod
    def test_connection(self) -> None:
        """Vérifie la connexion. Lève DatabaseConnectionError en cas d'échec."""

    @abstractmethod
    def dump_to(self, out: BinaryIO) -> None:
        """Écrit un export complet de la base dans `out` (flux binaire, en streaming)."""

    @abstractmethod
    def restore_from(
        self,
        inp: BinaryIO,
        tables: Sequence[str] = (),
        overwrite: bool = False,
    ) -> RestoreOutcome:
        """Restaure la base depuis le flux `inp` (déjà décompressé).

        Si `tables` est non vide, seules ces tables sont restaurées.
        Lève RestoreError en cas d'échec.
        """

    def dump_snapshot(
        self,
        out: BinaryIO,
        reference: Optional[Dict[str, str]] = None,
    ) -> SnapshotResult:
        """Export + empreintes des tables, sur le même instantané.

        reference=None : export complet. Sinon : seules les tables dont l'empreinte
        diffère de `reference` sont écrites. À implémenter si supports_incremental.
        """
        raise BackupError(
            f"Les sauvegardes incrémentales/différentielles ne sont pas disponibles "
            f"pour {self.params.db_type} (SQLite uniquement pour l'instant)."
        )

    def restore_chain(self, steps: Sequence[ChainStep], overwrite: bool = False) -> RestoreOutcome:
        """Restaure une chaîne de sauvegardes (complète puis incrémentales)."""
        raise RestoreError(
            f"La restauration de sauvegardes incrémentales/différentielles n'est pas "
            f"disponible pour {self.params.db_type} (SQLite uniquement pour l'instant)."
        )
