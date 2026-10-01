"""Registre des adaptateurs de bases de données."""

from __future__ import annotations

from dbbackup.adapters.base import DatabaseAdapter
from dbbackup.adapters.sqlite import SQLiteAdapter
from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError

ADAPTERS = {
    "sqlite": SQLiteAdapter,
}


def get_adapter(params: ConnectionParams) -> DatabaseAdapter:
    adapter_cls = ADAPTERS.get(params.db_type)
    if adapter_cls is None:
        raise ConfigError(
            f"Le support de {params.db_type} n'est pas encore disponible (étape 4)."
        )
    return adapter_cls(params)


__all__ = ["DatabaseAdapter", "SQLiteAdapter", "get_adapter", "ADAPTERS"]
