"""Paramètres de connexion aux bases de données."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dbbackup.exceptions import ConfigError

SUPPORTED_DBMS = ("sqlite", "postgresql", "mysql", "mongodb")

DEFAULT_PORTS = {
    "postgresql": 5432,
    "mysql": 3306,
    "mongodb": 27017,
}


@dataclass
class ConnectionParams:
    """Paramètres de connexion. Le mot de passe n'apparaît jamais dans repr()."""

    db_type: str
    database: str
    host: str = "localhost"
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None

    def __post_init__(self) -> None:
        self.db_type = self.db_type.lower()
        if self.db_type not in SUPPORTED_DBMS:
            raise ConfigError(
                f"SGBD non supporté : {self.db_type}. "
                f"Valeurs possibles : {', '.join(SUPPORTED_DBMS)}"
            )
        if not self.database:
            raise ConfigError("Le nom (ou chemin) de la base est obligatoire.")
        if self.port is None:
            self.port = DEFAULT_PORTS.get(self.db_type)

    def __repr__(self) -> str:
        masked = "***" if self.password else None
        return (
            f"ConnectionParams(db_type={self.db_type!r}, database={self.database!r}, "
            f"host={self.host!r}, port={self.port!r}, "
            f"username={self.username!r}, password={masked!r})"
        )

    __str__ = __repr__
