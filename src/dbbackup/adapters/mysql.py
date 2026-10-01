"""Adaptateur MySQL / MariaDB : s'appuie sur mysql et mysqldump."""

from __future__ import annotations

import re
import tempfile
from typing import BinaryIO, Dict, List, Sequence, Set

from dbbackup.adapters.base import RestoreOutcome
from dbbackup.adapters.external import ExternalToolAdapter
from dbbackup.exceptions import BackupError, DatabaseConnectionError, RestoreError

# Marqueurs de sections écrits par mysqldump (commentaires activés par défaut)
_BLOCK = re.compile(
    rb"^-- (Table structure for table|Temporary table structure for view|"
    rb"Temporary view structure for view|Final view structure for view) `(.+)`\s*$"
)
_ROUTINES = re.compile(rb"^-- Dumping (?:events|routines) for database ")
_FOOTER = re.compile(rb"^/\*!40103 SET TIME_ZONE=@OLD_TIME_ZONE \*/;")


def filter_mysql_dump(inp: BinaryIO, out: BinaryIO, selected: Set[str]) -> Set[str]:
    """Copie dans `out` l'en-tête, le pied de page et les sections des tables `selected`.

    Les vues, routines et événements sont ignorés. Retourne les tables trouvées (minuscules).
    """
    found: Set[str] = set()
    state = "header"  # header | table | skip | footer
    for line in inp:
        if state != "footer":
            if _FOOTER.match(line):
                state = "footer"
            else:
                match = _BLOCK.match(line)
                if match:
                    key = match.group(2).decode("utf-8", "replace").lower()
                    if match.group(1) == b"Table structure for table" and key in selected:
                        found.add(key)
                        state = "table"
                    else:
                        state = "skip"
                elif _ROUTINES.match(line):
                    state = "skip"
        if state != "skip":
            out.write(line)
    return found


class MySQLAdapter(ExternalToolAdapter):
    file_extension = "sql"
    client_tools = "mysql, mysqldump"

    def _env(self) -> Dict[str, str]:
        return {"MYSQL_PWD": self.params.password} if self.params.password else {}

    def _conn_args(self) -> List[str]:
        p = self.params
        args = [f"--host={p.host}", f"--port={p.port}", "--protocol=TCP"]
        if p.username:
            args.append(f"--user={p.username}")
        return args

    def test_connection(self) -> None:
        out = self._run(
            DatabaseConnectionError,
            "Connexion MySQL impossible",
            "mysql",
            [
                *self._conn_args(),
                "--batch", "--skip-column-names", "--execute=SELECT 1",
                self.params.database,
            ],
            timeout=30,
        )
        answer = out.decode("utf-8", "replace").strip()
        if answer != "1":
            raise DatabaseConnectionError(f"Réponse inattendue de MySQL : {answer!r}")

    def dump_to(self, out: BinaryIO) -> None:
        self._run(
            BackupError,
            "Échec de mysqldump",
            "mysqldump",
            [
                *self._conn_args(),
                "--single-transaction", "--routines", "--triggers", "--hex-blob",
                "--default-character-set=utf8mb4",
                self.params.database,
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
        args = [*self._conn_args(), "--default-character-set=utf8mb4", self.params.database]

        if not names:
            self._run(RestoreError, "Échec de mysql", "mysql", args, stdin_stream=inp)
            return RestoreOutcome(statements=0)

        # Sélectif : on filtre le dump dans un fichier temporaire et on vérifie
        # que toutes les tables existent AVANT de toucher à la base.
        with tempfile.TemporaryFile() as spool:
            found = filter_mysql_dump(inp, spool, {n.lower() for n in names})
            missing = [n for n in names if n.lower() not in found]
            if missing:
                raise RestoreError(
                    f"Table(s) introuvable(s) dans la sauvegarde : {', '.join(missing)}"
                )
            spool.seek(0)
            self._run(RestoreError, "Échec de mysql", "mysql", args, stdin_stream=spool)
        return RestoreOutcome(statements=0)
