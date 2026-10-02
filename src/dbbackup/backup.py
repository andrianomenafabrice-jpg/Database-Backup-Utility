"""Service de sauvegarde : orchestre adaptateur, compression, intégrité et logs."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dbbackup import __version__
from dbbackup.adapters.base import DatabaseAdapter, SnapshotResult
from dbbackup.catalog import find_reference
from dbbackup.exceptions import BackupError
from dbbackup.logger import get_logger
from dbbackup.utils.helpers import human_size, sha256_file

log = get_logger()


@dataclass
class BackupResult:
    file_path: Path
    meta_path: Path
    size_bytes: int
    sha256: str
    duration_seconds: float
    started_at: datetime
    finished_at: datetime
    mode: str
    parent: Optional[str] = None
    included_tables: Optional[List[str]] = None  # None = base entière
    dropped_tables: List[str] = field(default_factory=list)


def _remove(*paths: Optional[Path]) -> None:
    for path in paths:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _unique_path(directory: Path, base: str, extension: str) -> Path:
    """Nom de fichier libre : ajoute _2, _3... si deux sauvegardes tombent dans la même seconde."""
    candidate = directory / f"{base}.{extension}"
    counter = 2
    while candidate.exists() or candidate.with_name(candidate.name + ".meta.json").exists():
        candidate = directory / f"{base}_{counter}.{extension}"
        counter += 1
    return candidate


def run_backup(
    adapter: DatabaseAdapter,
    output_dir,
    mode: str = "full",
    compress: bool = True,
) -> BackupResult:
    """Crée une sauvegarde (full, incremental ou differential) et retourne ses informations."""
    db_type = adapter.params.db_type
    if mode != "full" and not adapter.supports_incremental:
        raise BackupError(
            f"Le mode '{mode}' n'est pas disponible pour {db_type} : seul 'full' est géré "
            "(incrémental / différentiel : SQLite uniquement pour l'instant)."
        )

    reference = None
    if mode != "full":
        reference = find_reference(output_dir, adapter.identity, mode)

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    label = adapter.label

    extension = adapter.file_extension + (".gz" if compress else "")
    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    final_path = _unique_path(Path(output_dir), f"{label}_{db_type}_{mode}_{stamp}", extension)
    tmp_path = final_path.with_name(final_path.name + ".part")
    meta_path = final_path.with_name(final_path.name + ".meta.json")

    log.info(
        "Backup démarré | base=%s | type=%s | mode=%s | compression=%s | parent=%s",
        label, db_type, mode, "gzip" if compress else "non",
        reference["file"] if reference else "-",
    )

    snapshot: Optional[SnapshotResult] = None
    try:
        adapter.test_connection()
        final_path.parent.mkdir(parents=True, exist_ok=True)

        def write_dump(out) -> None:
            nonlocal snapshot
            if adapter.supports_incremental:
                snapshot = adapter.dump_snapshot(
                    out, reference=reference["tables"] if reference else None
                )
            else:
                adapter.dump_to(out)

        if compress:
            with gzip.open(tmp_path, "wb", compresslevel=6) as out:
                write_dump(out)
        else:
            with open(tmp_path, "wb") as out:
                write_dump(out)
        tmp_path.replace(final_path)  # renommage atomique : jamais de fichier incomplet

        size_bytes = final_path.stat().st_size
        checksum = sha256_file(final_path)
        finished_at = datetime.now(timezone.utc)
        duration = time.perf_counter() - t0

        included = snapshot.included if snapshot else None
        dropped = snapshot.dropped if snapshot else []
        meta = {
            "file": final_path.name,
            "database": adapter.params.database,
            "source_id": adapter.identity,
            "db_type": db_type,
            "mode": mode,
            "created_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "compressed": compress,
            "size_bytes": size_bytes,
            "sha256": checksum,
            "duration_seconds": round(duration, 3),
            "tool_version": __version__,
            "parent": reference["file"] if reference else None,
            "parent_sha256": reference["sha256"] if reference else None,
            "tables": snapshot.fingerprints if snapshot else None,
            "included_tables": included,
            "dropped_tables": dropped,
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        _remove(tmp_path, final_path, meta_path)
        log.error(
            "Backup échoué | base=%s | statut=FAILED | durée=%.2fs | erreur=%s",
            label, time.perf_counter() - t0, exc,
        )
        if isinstance(exc, OSError):
            raise BackupError(f"Erreur d'écriture de la sauvegarde : {exc}") from exc
        raise

    log.info(
        "Backup terminé | base=%s | statut=SUCCESS | mode=%s | fichier=%s | taille=%s | sha256=%s "
        "| tables=%s | supprimées=%s | début=%s | fin=%s | durée=%.2fs",
        label, mode, final_path, human_size(size_bytes), checksum,
        ",".join(included) if included is not None else "toutes",
        ",".join(dropped) or "-",
        started_at.isoformat(), finished_at.isoformat(), duration,
    )
    return BackupResult(
        file_path=final_path,
        meta_path=meta_path,
        size_bytes=size_bytes,
        sha256=checksum,
        duration_seconds=duration,
        started_at=started_at,
        finished_at=finished_at,
        mode=mode,
        parent=reference["file"] if reference else None,
        included_tables=included,
        dropped_tables=dropped,
    )
