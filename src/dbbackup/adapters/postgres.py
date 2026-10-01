"""Adaptateur PostgreSQL : s'appuie sur psql, pg_dump et pg_restore."""

from __future__ import annotations

import re
import shutil
import tempfile
from typing import BinaryIO, Dict, List, Sequence, Set

from dbbackup.adapters.base import RestoreOutcome
from dbbackup.adapters.external import ExternalToolAdapter
from dbbackup.exceptions import BackupError, DatabaseConnectionError, RestoreError

# Ligne de la table des matières de pg_restore --list :
# 210; 1259 16394 TABLE public clients postgres
_TOC_TABLE = re.compile(r"^\d+;\s+\d+\s+\d+\s+TABLE\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse_toc_tables(toc: str) -> Set[str]:
    """Extrait les noms de tables (en minuscules) de la sortie de `pg_restore --list`."""
    names: Set[str] = set()
    for line in toc.splitlines():
        match = _TOC_TABLE.match(line)
        if match:
            names.add(match.group(2).lower())
    return names


class PostgreSQLAdapter(ExternalToolAdapter):
    file_extension = "dump"  # archive au format "custom" de pg_dump
    client_tools = "psql, pg_dump, pg_restore"

    def _env(self) -> Dict[str, str]:
        return {"PGPASSWORD": self.params.password} if self.params.password else {}

    def _conn_args(self) -> List[str]:
        p = self.params
        args = [f"--host={p.host}", f"--port={p.port}"]
        if p.username:
            args.append(f"--username={p.username}")
        args.append("--no-password")  # ne jamais attendre une saisie interactive
        return args

    def test_connection(self) -> None:
        out = self._run(
            DatabaseConnectionError,
            "Connexion PostgreSQL impossible",
            "psql",
            [
                *self._conn_args(),
                f"--dbname={self.params.database}",
                "--no-psqlrc", "--tuples-only", "--no-align",
                "--command=SELECT 1",
            ],
            timeout=30,
        )
        answer = out.decode("utf-8", "replace").strip()
        if answer != "1":
            raise DatabaseConnectionError(f"Réponse inattendue de PostgreSQL : {answer!r}")

    def dump_to(self, out: BinaryIO) -> None:
        self._run(
            BackupError,
            "Échec de pg_dump",
            "pg_dump",
            [
                *self._conn_args(),
                f"--dbname={self.params.database}",
                "--format=custom", "--compress=0",  # la compression gzip est faite par dbbackup
                "--no-owner", "--no-privileges",
            ],
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
        for name in names:
            if "." in name:
                raise RestoreError(
                    f"Indiquez le nom de la table sans schéma (ex : clients), reçu : {name}"
                )

        args = [
            *self._conn_args(),
            f"--dbname={self.params.database}",
            "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--single-transaction",  # tout ou rien
        ]

        if not names:
            self._run(RestoreError, "Échec de pg_restore", "pg_restore", args, stdin_stream=inp)
            return RestoreOutcome(statements=0)

        # Sélectif : on copie l'archive dans un fichier temporaire pour pouvoir
        # vérifier son contenu avant de toucher à la base.
        with tempfile.TemporaryFile() as spool:
            shutil.copyfileobj(inp, spool, 1024 * 1024)
            spool.seek(0)
            toc = self._run(
                RestoreError, "Lecture de la sauvegarde impossible",
                "pg_restore", ["--list"], stdin_stream=spool,
            ).decode("utf-8", "replace")
            known = parse_toc_tables(toc)
            if known:
                missing = [n for n in names if n.lower() not in known]
                if missing:
                    raise RestoreError(
                        f"Table(s) introuvable(s) dans la sauvegarde : {', '.join(missing)}"
                    )
            spool.seek(0)
            self._run(
                RestoreError, "Échec de pg_restore", "pg_restore",
                [*args, *[f"--table={n}" for n in names]], stdin_stream=spool,
            )
        return RestoreOutcome(statements=0)
