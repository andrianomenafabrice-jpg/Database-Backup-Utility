"""Service de restauration : intégrité, décompression, orchestration et logs."""

from __future__ import annotations

import gzip
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from dbbackup.adapters.base import ChainStep, DatabaseAdapter
from dbbackup.catalog import build_chain, meta_path_for, read_meta
from dbbackup.exceptions import RestoreError
from dbbackup.logger import get_logger
from dbbackup.utils.helpers import sha256_file

log = get_logger()


@dataclass
class RestoreResult:
    target: str
    tables: List[str]
    statements: int
    duration_seconds: float
    verified: bool
    safety_copy: Optional[Path]
    started_at: datetime
    finished_at: datetime
    chain: int = 1  # nombre de sauvegardes rejouées


def verify_backup(backup_file: Path) -> bool:
    """Compare le SHA-256 du fichier à celui du .meta.json.

    Retourne True si vérifié, False si aucun .meta.json n'est disponible.
    Lève RestoreError si le fichier a été altéré.
    """
    meta_path = meta_path_for(backup_file)
    if not meta_path.is_file():
        log.warning("Aucun fichier de métadonnées pour %s : intégrité non vérifiée", backup_file)
        return False
    meta = read_meta(backup_file)
    if meta is None:
        raise RestoreError(f"Fichier de métadonnées illisible : {meta_path}")

    expected = meta.get("sha256")
    if not expected:
        raise RestoreError(f"Le fichier de métadonnées {meta_path} ne contient pas de SHA-256.")

    actual = sha256_file(backup_file)
    if actual != expected:
        raise RestoreError(
            "Intégrité compromise : le SHA-256 de la sauvegarde ne correspond pas "
            f"(attendu {expected[:16]}…, obtenu {actual[:16]}…). Restauration annulée."
        )
    return True


def run_restore(
    adapter: DatabaseAdapter,
    backup_file,
    tables: Sequence[str] = (),
    overwrite: bool = False,
    verify: bool = True,
) -> RestoreResult:
    """Restaure une sauvegarde (complète, sélective ou chaîne incrémentale) via l'adaptateur."""
    path = Path(backup_file)
    if not path.is_file():
        raise RestoreError(f"Fichier de sauvegarde introuvable : {path}")

    names = [t.strip() for t in tables if t and t.strip()]
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    target = adapter.params.database

    meta = read_meta(path)
    mode = meta.get("mode", "full") if meta else "full"

    log.info(
        "Restauration démarrée | fichier=%s | mode=%s | cible=%s | type=%s | tables=%s | overwrite=%s",
        path, mode, target, adapter.params.db_type, ",".join(names) or "toutes", overwrite,
    )

    chain_length = 1
    try:
        if mode != "full":
            if names:
                raise RestoreError(
                    "La restauration sélective (--table) n'est pas disponible pour une "
                    "sauvegarde incrémentale ou différentielle : restaurez la chaîne complète."
                )
            chain = build_chain(path)
            chain_length = len(chain)
            steps = [
                ChainStep(
                    path=path.parent / m["file"],
                    mode=m.get("mode", "full"),
                    included=None if m.get("mode") == "full" else (m.get("included_tables") or []),
                    dropped=m.get("dropped_tables") or [],
                )
                for m in chain
            ]
            verified = bool(verify)
            if verify:
                for step in steps:
                    verify_backup(step.path)
            outcome = adapter.restore_chain(steps, overwrite=overwrite)
        else:
            verified = verify_backup(path) if verify else False
            with open(path, "rb") as raw:
                magic = raw.read(2)
                raw.seek(0)
                stream = gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw
                outcome = adapter.restore_from(stream, tables=names, overwrite=overwrite)
    except Exception as exc:
        log.error(
            "Restauration échouée | cible=%s | statut=FAILED | durée=%.2fs | erreur=%s",
            target, time.perf_counter() - t0, exc,
        )
        if isinstance(exc, (OSError, EOFError, zlib.error, UnicodeDecodeError)):
            raise RestoreError(
                f"Lecture de la sauvegarde impossible (fichier corrompu ?) : {exc}"
            ) from exc
        raise

    finished_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - t0
    log.info(
        "Restauration terminée | cible=%s | statut=SUCCESS | sauvegardes=%d | tables=%s "
        "| instructions=%d | intégrité=%s | début=%s | fin=%s | durée=%.2fs",
        target, chain_length, ",".join(names) or "toutes", outcome.statements,
        "vérifiée" if verified else "non vérifiée",
        started_at.isoformat(), finished_at.isoformat(), duration,
    )
    return RestoreResult(
        target=target,
        tables=names,
        statements=outcome.statements,
        duration_seconds=duration,
        verified=verified,
        safety_copy=outcome.safety_copy,
        started_at=started_at,
        finished_at=finished_at,
        chain=chain_length,
    )
