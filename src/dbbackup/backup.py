"""Service de sauvegarde : orchestre adaptateur, compression, intégrité et logs."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dbbackup import __version__
from dbbackup.adapters.base import DatabaseAdapter
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


def _remove(*paths: Optional[Path]) -> None:
    for path in paths:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def run_backup(
    adapter: DatabaseAdapter,
    output_dir,
    mode: str = "full",
    compress: bool = True,
) -> BackupResult:
    """Crée une sauvegarde et retourne ses informations."""
    if mode != "full":
        raise BackupError(
            f"Le mode '{mode}' sera disponible à l'étape 5 (seul 'full' est géré pour l'instant)."
        )

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    label = adapter.label
    db_type = adapter.params.db_type

    extension = adapter.file_extension + (".gz" if compress else "")
    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    final_path = Path(output_dir) / f"{label}_{db_type}_{mode}_{stamp}.{extension}"
    tmp_path = final_path.with_name(final_path.name + ".part")
    meta_path = final_path.with_name(final_path.name + ".meta.json")

    if final_path.exists():
        raise BackupError(f"Le fichier {final_path} existe déjà, réessayez dans une seconde.")

    log.info(
        "Backup démarré | base=%s | type=%s | mode=%s | compression=%s",
        label, db_type, mode, "gzip" if compress else "non",
    )

    try:
        adapter.test_connection()
        final_path.parent.mkdir(parents=True, exist_ok=True)

        if compress:
            with gzip.open(tmp_path, "wb", compresslevel=6) as out:
                adapter.dump_to(out)
        else:
            with open(tmp_path, "wb") as out:
                adapter.dump_to(out)
        tmp_path.replace(final_path)  # renommage atomique : jamais de fichier incomplet

        size_bytes = final_path.stat().st_size
        checksum = sha256_file(final_path)
        finished_at = datetime.now(timezone.utc)
        duration = time.perf_counter() - t0

        meta = {
            "file": final_path.name,
            "database": adapter.params.database,
            "db_type": db_type,
            "mode": mode,
            "created_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "compressed": compress,
            "size_bytes": size_bytes,
            "sha256": checksum,
            "duration_seconds": round(duration, 3),
            "tool_version": __version__,
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
        "Backup terminé | base=%s | statut=SUCCESS | fichier=%s | taille=%s | sha256=%s "
        "| début=%s | fin=%s | durée=%.2fs",
        label, final_path, human_size(size_bytes), checksum,
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
    )
