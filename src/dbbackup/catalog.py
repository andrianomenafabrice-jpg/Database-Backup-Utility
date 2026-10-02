"""Catalogue des sauvegardes d'un dossier : métadonnées, référence, chaînes de restauration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dbbackup.exceptions import BackupError, RestoreError

META_SUFFIX = ".meta.json"


def meta_path_for(backup_file: Path) -> Path:
    return backup_file.with_name(backup_file.name + META_SUFFIX)


def read_meta(backup_file: Path) -> Optional[Dict[str, Any]]:
    """Retourne les métadonnées d'une sauvegarde, ou None si absentes / illisibles."""
    try:
        data = json.loads(meta_path_for(backup_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _created_at(meta: Dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(meta["created_at"])
    except (KeyError, TypeError, ValueError):
        return datetime.min


def list_backups(output_dir) -> List[Dict[str, Any]]:
    """Liste les sauvegardes d'un dossier (les plus anciennes d'abord).

    Seules les sauvegardes dont le fichier de données existe encore sont retournées.
    """
    directory = Path(output_dir)
    if not directory.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for meta_file in directory.glob("*" + META_SUFFIX):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or not meta.get("file"):
            continue
        if not (directory / meta["file"]).is_file():
            continue
        found.append(meta)
    found.sort(key=_created_at)
    return found


def find_reference(output_dir, source_id: str, mode: str) -> Dict[str, Any]:
    """Trouve la sauvegarde de référence pour une incrémentale ou une différentielle.

    - incremental  : la sauvegarde la plus récente (de n'importe quel type)
    - differential : la dernière sauvegarde complète
    Seules les sauvegardes de la même base, avec des empreintes de tables, sont éligibles.
    """
    candidates = [
        m for m in list_backups(output_dir)
        if m.get("source_id") == source_id and m.get("tables") is not None
    ]
    if mode == "differential":
        candidates = [m for m in candidates if m.get("mode") == "full"]
    if not candidates or not any(m.get("mode") == "full" for m in candidates):
        raise BackupError(
            "Aucune sauvegarde complète exploitable trouvée dans "
            f"{output_dir} pour cette base. Faites d'abord une sauvegarde avec --mode full."
        )
    return candidates[-1]


def build_chain(backup_file: Path) -> List[Dict[str, Any]]:
    """Reconstitue la chaîne à restaurer : [complète, ..., backup_file].

    Vérifie que chaque sauvegarde parente existe et correspond à celle référencée.
    """
    directory = backup_file.parent
    chain: List[Dict[str, Any]] = []
    seen = set()

    current = read_meta(backup_file)
    if current is None:
        raise RestoreError(
            f"Métadonnées introuvables pour {backup_file.name} : "
            "elles sont indispensables pour restaurer une sauvegarde incrémentale."
        )

    while True:
        name = current.get("file") or ""
        if name in seen:
            raise RestoreError("Chaîne de sauvegardes invalide (boucle détectée).")
        seen.add(name)
        chain.append(current)
        if current.get("mode") == "full":
            break

        parent_name = current.get("parent")
        if not parent_name:
            raise RestoreError(f"La sauvegarde {name} n'indique pas de sauvegarde parente.")
        parent_path = directory / parent_name
        if not parent_path.is_file():
            raise RestoreError(
                f"Sauvegarde parente manquante : {parent_name} "
                f"(nécessaire pour restaurer {name}). Gardez toute la chaîne dans le même dossier."
            )
        parent_meta = read_meta(parent_path)
        if parent_meta is None:
            raise RestoreError(f"Métadonnées introuvables pour la sauvegarde parente {parent_name}.")
        expected = current.get("parent_sha256")
        if expected and expected != parent_meta.get("sha256"):
            raise RestoreError(
                f"La sauvegarde parente {parent_name} ne correspond plus à celle utilisée "
                f"pour créer {name}."
            )
        current = parent_meta

    chain.reverse()
    return chain
