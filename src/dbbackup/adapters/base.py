"""Interface commune à tous les adaptateurs de bases de données."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO

from dbbackup.config import ConnectionParams
from dbbackup.utils.helpers import safe_name


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
