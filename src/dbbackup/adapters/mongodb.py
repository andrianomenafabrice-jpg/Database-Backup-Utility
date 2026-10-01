"""Adaptateur MongoDB : s'appuie sur mongosh, mongodump et mongorestore."""

from __future__ import annotations

import json
import os
from typing import BinaryIO, List, Sequence

from dbbackup.adapters.base import RestoreOutcome
from dbbackup.adapters.external import ExternalToolAdapter
from dbbackup.exceptions import BackupError, DatabaseConnectionError, RestoreError


class MongoDBAdapter(ExternalToolAdapter):
    file_extension = "archive"  # archive native de mongodump
    client_tools = "mongosh, mongodump, mongorestore"

    def _auth_args(self) -> List[str]:
        # Limite connue : les outils MongoDB n'acceptent pas le mot de passe par variable
        # d'environnement ; il transite donc dans les arguments du processus.
        p = self.params
        args = [f"--host={p.host}", f"--port={p.port}"]
        if p.username:
            auth_db = os.environ.get("DBBACKUP_MONGO_AUTH_DB", "admin")
            args += [
                f"--username={p.username}",
                f"--password={p.password or ''}",
                f"--authenticationDatabase={auth_db}",
            ]
        return args

    def test_connection(self) -> None:
        db = json.dumps(self.params.database)
        out = self._run(
            DatabaseConnectionError,
            "Connexion MongoDB impossible",
            "mongosh",
            [
                *self._auth_args(),
                "--quiet",
                f"--eval=print(JSON.stringify(db.getSiblingDB({db}).getCollectionNames()))",
            ],
            timeout=30,
        )
        answer = out.decode("utf-8", "replace")
        if "[" not in answer:
            raise DatabaseConnectionError(f"Réponse inattendue de MongoDB : {answer.strip()!r}")

    def dump_to(self, out: BinaryIO) -> None:
        self._run(
            BackupError,
            "Échec de mongodump",
            "mongodump",
            [*self._auth_args(), f"--db={self.params.database}", "--archive", "--quiet"],
            stdout_stream=out,
        )

    def restore_from(
        self,
        inp: BinaryIO,
        tables: Sequence[str] = (),
        overwrite: bool = False,
    ) -> RestoreOutcome:
        self._require_overwrite(overwrite)
        names = self._table_names(tables)
        db = self.params.database
        namespaces = [f"--nsInclude={db}.{n}" for n in names] or [f"--nsInclude={db}.*"]
        self._run(
            RestoreError,
            "Échec de mongorestore",
            "mongorestore",
            [*self._auth_args(), "--archive", "--drop", "--quiet", *namespaces],
            stdin_stream=inp,
        )
        return RestoreOutcome(statements=0)
