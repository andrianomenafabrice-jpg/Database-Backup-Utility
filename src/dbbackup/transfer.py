"""Envoi et récupération des sauvegardes vers / depuis un stockage (local ou cloud)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union

from dbbackup.backup import BackupResult
from dbbackup.catalog import META_SUFFIX, list_backups, read_meta
from dbbackup.exceptions import RestoreError, StorageError
from dbbackup.logger import get_logger
from dbbackup.storage import Storage, open_storage, split_location

log = get_logger()


def upload_backup(result: BackupResult, destination: Union[str, Storage]) -> Storage:
    """Envoie la sauvegarde puis ses métadonnées (en dernier : une sauvegarde n'apparaît
    dans le catalogue distant que lorsqu'elle est complète)."""
    storage = destination if isinstance(destination, Storage) else open_storage(destination)
    name = result.file_path.name
    log.info("Envoi démarré | fichier=%s | destination=%s", name, storage.location)
    try:
        storage.put(result.file_path, name)
        storage.put(result.meta_path, result.meta_path.name)
    except StorageError as exc:
        log.error("Envoi échoué | fichier=%s | destination=%s | erreur=%s", name, storage.location, exc)
        raise
    log.info("Envoi terminé | fichier=%s | destination=%s | statut=SUCCESS", name, storage.location)
    return storage


def fetch_backup(location: str, workdir: Path) -> Path:
    """Télécharge une sauvegarde (et, si elle est incrémentale, toute sa chaîne de parents)
    dans `workdir`. Retourne le chemin local du fichier demandé."""
    directory, name = split_location(location)
    storage = open_storage(directory)
    workdir = Path(workdir)

    if storage.size(name) is None:
        raise StorageError(f"Fichier introuvable dans le stockage : {location}")
    local = workdir / name
    storage.download(name, local)

    seen = {name}
    current = name
    while True:
        meta_name = current + META_SUFFIX
        if storage.size(meta_name) is None:
            if current == name:
                log.warning("Pas de métadonnées pour %s : intégrité non vérifiée", name)
                break
            raise RestoreError(f"Métadonnées introuvables pour la sauvegarde parente {current}.")
        storage.download(meta_name, workdir / meta_name)
        meta = read_meta(workdir / current)
        if meta is None or meta.get("mode", "full") == "full":
            break
        parent = meta.get("parent")
        if not parent or parent in seen:
            break  # build_chain signalera le problème
        if storage.size(parent) is None:
            raise RestoreError(
                f"Sauvegarde parente manquante dans le stockage : {parent} "
                f"(nécessaire pour restaurer {current})."
            )
        seen.add(parent)
        storage.download(parent, workdir / parent)
        current = parent

    log.info("Sauvegarde récupérée | fichier=%s | source=%s", name, storage.location)
    return local


def list_remote_backups(destination: Union[str, Storage]) -> List[Dict[str, Any]]:
    """Liste les sauvegardes d'un stockage en lisant uniquement leurs métadonnées."""
    storage = destination if isinstance(destination, Storage) else open_storage(destination)
    names = [
        o.name for o in storage.list()
        if o.name.endswith(META_SUFFIX) and "/" not in o.name
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            storage.download(name, Path(tmp) / name)
        return list_backups(tmp, require_data=False)
