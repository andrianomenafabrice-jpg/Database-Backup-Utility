"""Registre des adaptateurs de bases de données."""

from __future__ import annotations

from dbbackup.adapters.base import DatabaseAdapter
from dbbackup.adapters.mongodb import MongoDBAdapter
from dbbackup.adapters.mysql import MySQLAdapter
from dbbackup.adapters.postgres import PostgreSQLAdapter
from dbbackup.adapters.sqlite import SQLiteAdapter
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError

ADAPTERS = {
    "sqlite": SQLiteAdapter,
    "postgresql": PostgreSQLAdapter,
    "mysql": MySQLAdapter,
    "mongodb": MongoDBAdapter,
}


def get_adapter(params: ConnectionParams) -> DatabaseAdapter:
    adapter_cls = ADAPTERS.get(params.db_type)
    if adapter_cls is None:
        raise ConfigError(f"Aucun adaptateur pour le type : {params.db_type}")
    return adapter_cls(params)


__all__ = [
    "DatabaseAdapter", "SQLiteAdapter", "PostgreSQLAdapter",
    "MySQLAdapter", "MongoDBAdapter", "get_adapter", "ADAPTERS",
]
