"""Adaptateur SQLite (bibliothèque standard, aucune dépendance externe)."""

from __future__ import annotations

import io
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Dict, Optional, Sequence, Set, Tuple

from dbbackup.adapters.base import DatabaseAdapter, RestoreOutcome
from dbbackup.exceptions import BackupError, DatabaseConnectionError, RestoreError
from dbbackup.logger import get_logger
from dbbackup.utils.helpers import safe_name

log = get_logger()

_SIDECARS = ("-wal", "-shm", "-journal")

# Identifiant SQL : "nom", `nom`, [nom] ou nom
_IDENT = r'(?:"((?:[^"]|"")+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_$]*))'

_RE_TX = re.compile(r"\s*(?:BEGIN|COMMIT|END|ROLLBACK)\b", re.IGNORECASE)
_RE_SEQ_DELETE = re.compile(r'\s*DELETE\s+FROM\s+"sqlite_sequence"', re.IGNORECASE)
_RE_SEQ_VALUE = re.compile(r"VALUES\s*\(\s*'((?:[^']|'')*)'", re.IGNORECASE)
_RE_INSERT = re.compile(r"\s*INSERT\s+INTO\s+" + _IDENT, re.IGNORECASE)
_RE_CREATE_TABLE = re.compile(
    r"\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _IDENT,
    re.IGNORECASE,
)
_RE_CREATE_INDEX = re.compile(
    r"\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _IDENT + r"\s+ON\s+" + _IDENT,
    re.IGNORECASE,
)
_RE_CREATE_TRIGGER = re.compile(r"\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TRIGGER\b", re.IGNORECASE)
_RE_TRIGGER_ON = re.compile(r"\bON\s+" + _IDENT, re.IGNORECASE)
_RE_CREATE_VIEW = re.compile(r"\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?VIEW\b", re.IGNORECASE)


def _ident(match: "re.Match[str]", offset: int = 0) -> str:
    quoted, backtick, bracket, bare = match.group(offset + 1, offset + 2, offset + 3, offset + 4)
    if quoted is not None:
        return quoted.replace('""', '"')
    return backtick or bracket or bare


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _classify(statement: str) -> Tuple[str, Optional[str]]:
    """Retourne (type d'instruction, table concernée)."""
    if _RE_TX.match(statement):
        return "tx", None
    if _RE_SEQ_DELETE.match(statement):
        return "sequence_delete", None
    match = _RE_INSERT.match(statement)
    if match:
        name = _ident(match)
        if name.lower() == "sqlite_sequence":
            value = _RE_SEQ_VALUE.search(statement)
            return "sequence_insert", (value.group(1).replace("''", "'") if value else None)
        return "insert", name
    match = _RE_CREATE_TABLE.match(statement)
    if match:
        return "create_table", _ident(match)
    match = _RE_CREATE_INDEX.match(statement)
    if match:
        return "index", _ident(match, 4)
    if _RE_CREATE_TRIGGER.match(statement):
        header = re.split(r"\bBEGIN\b", statement, maxsplit=1, flags=re.IGNORECASE)[0]
        match = _RE_TRIGGER_ON.search(header)
        return "trigger", (_ident(match) if match else None)
    if _RE_CREATE_VIEW.match(statement):
        return "view", None
    return "other", None


class SQLiteAdapter(DatabaseAdapter):
    file_extension = "sql"

    @property
    def label(self) -> str:
        return safe_name(Path(self.params.database).stem)

    def _path(self) -> Path:
        return Path(self.params.database).expanduser()

    # ------------------------------------------------------------------ backup

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

    # ----------------------------------------------------------------- restore

    @staticmethod
    def _remove_files(path: Path) -> None:
        for candidate in [path] + [Path(str(path) + s) for s in _SIDECARS]:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _move_with_sidecars(src: Path, dst: Path) -> None:
        try:
            src.replace(dst)
            for suffix in _SIDECARS:
                side = Path(str(src) + suffix)
                if side.exists():
                    side.replace(Path(str(dst) + suffix))
        except OSError as exc:
            raise RestoreError(
                f"Impossible de déplacer {src} vers {dst} : {exc}. "
                "Fermez les programmes qui utilisent cette base puis réessayez."
            ) from exc

    def restore_from(
        self,
        inp: BinaryIO,
        tables: Sequence[str] = (),
        overwrite: bool = False,
    ) -> RestoreOutcome:
        target = self._path()
        names = [t.strip() for t in tables if t and t.strip()]
        requested: Dict[str, str] = {t.lower(): t for t in names}
        selected: Optional[Set[str]] = set(requested) or None

        if target.exists():
            if not target.is_file():
                raise RestoreError(f"La cible n'est pas un fichier : {target}")
            if not overwrite:
                raise RestoreError(
                    f"La base cible existe déjà : {target}. "
                    "Utilisez --overwrite pour autoriser son remplacement."
                )

        if selected is not None and target.exists():
            return self._restore_in_place(inp, target, selected, requested)
        return self._restore_to_new_file(inp, target, selected, requested)

    def _restore_to_new_file(
        self,
        inp: BinaryIO,
        target: Path,
        selected: Optional[Set[str]],
        requested: Dict[str, str],
    ) -> RestoreOutcome:
        """Reconstruit la base dans un fichier temporaire puis la met en place."""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RestoreError(f"Impossible de créer le dossier {target.parent} : {exc}") from exc

        tmp = target.with_name(target.name + ".restore.tmp")
        self._remove_files(tmp)
        try:
            conn = sqlite3.connect(str(tmp), isolation_level=None)
        except sqlite3.Error as exc:
            raise RestoreError(f"Impossible de créer la base temporaire {tmp} : {exc}") from exc

        try:
            statements = self._run(conn, inp, selected, requested, drop_existing=False)
        except Exception:
            conn.close()
            self._remove_files(tmp)
            raise
        conn.close()

        safety: Optional[Path] = None
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safety = target.with_name(f"{target.name}.before-restore-{stamp}")
            try:
                self._move_with_sidecars(target, safety)
            except RestoreError:
                self._remove_files(tmp)
                raise

        try:
            tmp.replace(target)
        except OSError as exc:
            if safety is not None:
                try:
                    self._move_with_sidecars(safety, target)
                except RestoreError:
                    pass
            self._remove_files(tmp)
            raise RestoreError(f"Impossible de mettre la base restaurée en place : {exc}") from exc

        return RestoreOutcome(statements=statements, safety_copy=safety)

    def _restore_in_place(
        self,
        inp: BinaryIO,
        target: Path,
        selected: Set[str],
        requested: Dict[str, str],
    ) -> RestoreOutcome:
        """Remplace uniquement les tables demandées, dans une seule transaction."""
        try:
            conn = sqlite3.connect(str(target), isolation_level=None)
        except sqlite3.Error as exc:
            raise RestoreError(f"Impossible d'ouvrir la base cible {target} : {exc}") from exc

        try:
            statements = self._run(conn, inp, selected, requested, drop_existing=True)
        except Exception:
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            conn.close()
            raise
        conn.close()
        return RestoreOutcome(statements=statements, safety_copy=None)

    def _run(
        self,
        conn: sqlite3.Connection,
        inp: BinaryIO,
        selected: Optional[Set[str]],
        requested: Dict[str, str],
        drop_existing: bool,
    ) -> int:
        """Rejoue le dump SQL en streaming, dans une transaction unique."""
        found: Set[str] = set()
        executed = 0
        # newline="" : préserve exactement les retours à la ligne présents dans les données
        text = io.TextIOWrapper(inp, encoding="utf-8", newline="")
        try:
            conn.execute("BEGIN")
            buffer = ""
            for line in text:
                buffer += line
                if not sqlite3.complete_statement(buffer):
                    continue
                statement, buffer = buffer, ""
                if self._apply(conn, statement, selected, drop_existing, found):
                    executed += 1
            if buffer.strip():
                raise RestoreError(
                    "La sauvegarde est tronquée ou corrompue (instruction SQL incomplète)."
                )
            if selected is not None:
                missing = sorted(selected - found)
                if missing:
                    names = ", ".join(requested[key] for key in missing)
                    raise RestoreError(f"Table(s) introuvable(s) dans la sauvegarde : {names}")
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise RestoreError(f"Erreur SQLite pendant la restauration : {exc}") from exc
        finally:
            text.detach()
        return executed

    @staticmethod
    def _apply(
        conn: sqlite3.Connection,
        statement: str,
        selected: Optional[Set[str]],
        drop_existing: bool,
        found: Set[str],
    ) -> bool:
        """Exécute une instruction si elle est concernée. Retourne True si exécutée."""
        kind, table = _classify(statement)
        if kind == "tx":
            return False  # la transaction est gérée par _run
        key = table.lower() if table else None

        if selected is not None:
            if kind == "create_table":
                if key not in selected:
                    return False
                found.add(key)
                if drop_existing:
                    conn.execute(f"DROP TABLE IF EXISTS {_quote(table)}")
            elif kind in ("insert", "index", "trigger", "sequence_insert"):
                if key not in selected:
                    return False
            else:
                # sequence_delete, vues et autres : ignorés en mode sélectif
                return False
        elif kind == "create_table" and key:
            found.add(key)

        if kind == "sequence_delete":
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                return False  # pas de table sqlite_sequence : rien à faire
            return True

        if kind == "sequence_insert":
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
                conn.execute(statement)
            except sqlite3.OperationalError:
                return False
            return True

        conn.execute(statement)
        return True
