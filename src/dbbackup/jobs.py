"""Tâches complètes : sauvegarde + envoi + notification (utilisées par la CLI et le planificateur)."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable, Optional

from dbbackup.adapters.base import DatabaseAdapter
from dbbackup.backup import BackupResult, run_backup
from dbbackup.exceptions import DBBackupError
from dbbackup.logger import redact
from dbbackup.notifications import notify_safely
from dbbackup.storage import Storage
from dbbackup.transfer import upload_backup
from dbbackup.utils.helpers import human_size


@dataclass
class JobOutcome:
    result: BackupResult
    storage: Optional[Storage]


def _host() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "?"


def success_message(adapter: DatabaseAdapter, result: BackupResult, storage: Optional[Storage]) -> str:
    lines = [
        f"✅ *Sauvegarde {result.mode} réussie*",
        f"Base : {adapter.label} ({adapter.params.db_type}) | Serveur : {_host()}",
        f"Fichier : {result.file_path.name}",
        f"Taille : {human_size(result.size_bytes)} | Durée : {result.duration_seconds:.1f} s",
    ]
    if result.included_tables is not None:
        lines.append("Tables : " + (", ".join(result.included_tables) or "aucun changement"))
    if storage is not None:
        lines.append(f"Envoyée vers : {storage.location}")
    return "\n".join(lines)


def failure_message(action: str, adapter: DatabaseAdapter, error: Exception, note: str = "") -> str:
    lines = [
        f"❌ *{action} échouée*",
        f"Base : {adapter.label} ({adapter.params.db_type}) | Serveur : {_host()}",
        f"Erreur : {redact(str(error))[:500]}",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)


def restore_message(adapter: DatabaseAdapter, target: str, chain: int, duration: float) -> str:
    return "\n".join([
        "✅ *Restauration réussie*",
        f"Base : {target} ({adapter.params.db_type}) | Serveur : {_host()}",
        f"Sauvegardes rejouées : {chain} | Durée : {duration:.1f} s",
    ])


def run_backup_job(
    adapter: DatabaseAdapter,
    output_dir,
    mode: str = "full",
    compress: bool = True,
    upload_to: Optional[str] = None,
    webhook: Optional[str] = None,
    on_warning: Optional[Callable[[str], None]] = None,
) -> JobOutcome:
    """Sauvegarde, envoie vers le stockage demandé et notifie (succès comme échec)."""
    result: Optional[BackupResult] = None
    try:
        result = run_backup(adapter, output_dir, mode=mode, compress=compress)
        storage = upload_backup(result, upload_to) if upload_to else None
    except DBBackupError as exc:
        note = f"La sauvegarde locale a été créée : {result.file_path.name}" if result else ""
        notify_safely(webhook, failure_message(f"Sauvegarde {mode}", adapter, exc, note), on_warning)
        raise
    notify_safely(webhook, success_message(adapter, result, storage), on_warning)
    return JobOutcome(result=result, storage=storage)
