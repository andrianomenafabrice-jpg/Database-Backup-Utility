"""Fonctions utilitaires partagées."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 Mo


def human_size(num_bytes: int) -> str:
    """Convertit une taille en octets en texte lisible (ex: 2.0 KB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def sha256_file(path: Path) -> str:
    """Calcule le SHA-256 d'un fichier par blocs (adapté aux gros fichiers)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name: str) -> str:
    """Nettoie un nom pour l'utiliser dans un nom de fichier."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return cleaned or "database"
