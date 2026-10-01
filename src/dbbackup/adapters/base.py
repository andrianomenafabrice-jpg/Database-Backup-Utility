"""Interface commune à tous les adaptateurs de bases de données."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Sequence

from dbbackup.config import ConnectionParams
from dbbackup.utils.helpers import safe_name


@dataclass
class RestoreOutcome:
    """Résultat d'une restauration côté adaptateur."""

    statements: int
    safety_copy: Optional[Path] = None


class DatabaseAdapter(ABC):
    """Contrat que chaque SGBD doit respecter."""

    #: extension du fichier de sauvegarde brut (avant compression)
    file_extension = "sql"

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params

    @property
    def label(self) -> str:
        """Nom court de la base, utilisé dans le nom des fichiers de sauvegarde."""
        return safe_name(self.params.database)

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
