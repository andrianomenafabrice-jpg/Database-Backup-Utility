"""Paramètres de connexion aux bases de données."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from dbbackup.exceptions import ConfigError

SUPPORTED_DBMS = ("sqlite", "postgresql", "mysql", "mongodb")

DEFAULT_PORTS = {
    "postgresql": 5432,
    "mysql": 3306,
    "mongodb": 27017,
}

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass
class ConnectionParams:
    """Paramètres de connexion. Le mot de passe n'apparaît jamais dans repr()."""

    db_type: str
    database: str
    host: str = "localhost"
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    docker_container: Optional[str] = None

    def __post_init__(self) -> None:
        self.db_type = self.db_type.lower()
        if self.db_type not in SUPPORTED_DBMS:
            raise ConfigError(
                f"SGBD non supporté : {self.db_type}. "
                f"Valeurs possibles : {', '.join(SUPPORTED_DBMS)}"
            )
        if not self.database:
            raise ConfigError("Le nom (ou chemin) de la base est obligatoire.")
        if self.db_type != "sqlite" and self.database.startswith("-"):
            raise ConfigError("Le nom de la base ne peut pas commencer par « - ».")

        self.docker_container = self.docker_container or None
        if self.docker_container is not None:
            if self.db_type == "sqlite":
                raise ConfigError("--docker-container n'est pas utilisable avec SQLite.")
            if not _CONTAINER_RE.match(self.docker_container):
                raise ConfigError(f"Nom de conteneur Docker invalide : {self.docker_container}")

        if self.port is None:
            self.port = DEFAULT_PORTS.get(self.db_type)

    def __repr__(self) -> str:
        masked = "***" if self.password else None
        return (
            f"ConnectionParams(db_type={self.db_type!r}, database={self.database!r}, "
            f"host={self.host!r}, port={self.port!r}, username={self.username!r}, "
            f"password={masked!r}, docker_container={self.docker_container!r})"
        )

    __str__ = __repr__
