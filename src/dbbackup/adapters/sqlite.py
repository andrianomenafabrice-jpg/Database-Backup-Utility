"""Adaptateur SQLite (bibliothèque standard, aucune dépendance externe)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import BinaryIO

from dbbackup.adapters.base import DatabaseAdapter
from dbbackup.exceptions import BackupError, DatabaseConnectionError
from dbbackup.logger import get_logger
from dbbackup.utils.helpers import safe_name

log = get_logger()


class SQLiteAdapter(DatabaseAdapter):
    file_extension = "sql"

    @property
    def label(self) -> str:
        return safe_name(Path(self.params.database).stem)

    def _path(self) -> Path:
        return Path(self.params.database).expanduser()

    def _connect(self) -> sqlite3.Connection:
        """Ouvre la base en lecture seule : un backup ne doit jamais la modifier."""
        path = self._path()
        if not path.is_file():
            raise DatabaseConnectionError(f"Fichier SQLite introuvable : {path}")
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            return sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise DatabaseConnectionError(
                f"Impossible d'ouvrir la base SQLite {path} : {exc}"
            ) from exc

    def test_connection(self) -> None:
        conn = self._connect()
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            raise DatabaseConnectionError(
                f"Le fichier {self._path()} n'est pas une base SQLite valide : {exc}"
            ) from exc
        finally:
            conn.close()
        log.debug("Connexion SQLite validée : %s", self._path())

    def dump_to(self, out: BinaryIO) -> None:
        """Exporte schéma + données en SQL, ligne par ligne (faible empreinte mémoire)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")  # lecture cohérente (snapshot) pendant tout l'export
            for line in conn.iterdump():
                out.write(f"{line}\n".encode("utf-8"))
            conn.rollback()
        except sqlite3.Error as exc:
            raise BackupError(f"Erreur pendant l'export SQLite : {exc}") from exc
        finally:
            conn.close()
