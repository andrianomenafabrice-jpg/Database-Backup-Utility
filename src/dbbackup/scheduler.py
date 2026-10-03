"""Planification des sauvegardes : boucle intégrée, commandes cron / Windows."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError

_INTERVAL = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_CRON = re.compile(r"^\s*\S+(\s+\S+){4}\s*$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_interval(text: str) -> int:
    """'30s', '15m', '6h', '1d' -> secondes."""
    match = _INTERVAL.match(text or "")
    if not match or int(match.group(1)) < 1:
        raise ConfigError(f"Intervalle invalide : {text!r}. Exemples : 30s, 15m, 6h, 1d.")
    return int(match.group(1)) * _UNITS[match.group(2).lower()]


def choose_mode(requested: str, run_number: int, full_every: Optional[int], has_full: bool) -> str:
    """Mode de la prochaine sauvegarde : une complète est forcée si aucune n'existe
    ou toutes les `full_every` exécutions."""
    if requested == "full" or not has_full:
        return "full"
    if full_every and run_number % full_every == 0:
        return "full"
    return requested


def run_schedule(
    job: Callable[[int], bool],
    interval_seconds: int,
    max_runs: Optional[int] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> int:
    """Exécute `job(numéro)` en boucle. Une erreur n'arrête pas la planification.
    Retourne le nombre d'exécutions en échec."""
    sleep = sleep or time.sleep
    runs = failures = 0
    while max_runs is None or runs < max_runs:
        runs += 1
        if not job(runs):
            failures += 1
        if max_runs is not None and runs >= max_runs:
            break
        remaining = float(interval_seconds)
        while remaining > 0:  # petites tranches : Ctrl+C reste réactif
            step = min(remaining, 1.0)
            sleep(step)
            remaining -= step
    return failures


def backup_arguments(
    params: ConnectionParams, mode: str, output: str, upload_to: Optional[str]
) -> List[str]:
    """Arguments de la commande `dbbackup backup` à planifier (sans mot de passe ni webhook)."""
    database = params.database
    if params.db_type == "sqlite":
        database = str(Path(database).expanduser().resolve())
    args = [sys.executable, "-m", "dbbackup", "backup", "--type", params.db_type, "-d", database]
    if params.username:
        args += ["--user", params.username]
    if params.host != "localhost":
        args += ["--host", params.host]
    default_port = ConnectionParams(db_type=params.db_type, database="x").port
    if params.port and params.port != default_port:
        args += ["--port", str(params.port)]
    if params.docker_container:
        args += ["--docker-container", params.docker_container]
    args += ["--mode", mode, "-o", str(Path(output).expanduser().resolve())]
    if upload_to:
        args += ["--upload", upload_to]
    return args


def cron_entry(cron: str, args: List[str]) -> str:
    if not _CRON.match(cron):
        raise ConfigError(f"Expression cron invalide : {cron!r} (5 champs, ex : \"0 2 * * *\").")
    command = " ".join(shlex.quote(a) for a in args)
    return (
        "# Ajoutez la ligne ci-dessous avec : crontab -e\n"
        "# Avant, créez ~/.dbbackup/env (chmod 600) avec vos secrets :\n"
        "#   export DBBACKUP_PASSWORD='...'\n"
        "#   export DBBACKUP_SLACK_WEBHOOK='...'   (optionnel)\n"
        f'{cron.strip()} . "$HOME/.dbbackup/env"; {command} >> "$HOME/.dbbackup/logs/cron.log" 2>&1'
    )


def windows_task(at: str, args: List[str], name: str) -> str:
    if not _TIME.match(at):
        raise ConfigError(f"Heure invalide : {at!r} (format HH:MM, ex : 02:00).")
    command = subprocess.list2cmdline(args)
    escaped = command.replace('"', '\\"')
    lines = [
        "REM Définissez d'abord vos secrets (une seule fois, session utilisateur) :",
        'REM   setx DBBACKUP_PASSWORD "..."',
        'REM   setx DBBACKUP_SLACK_WEBHOOK "..."   (optionnel)',
        f'schtasks /Create /SC DAILY /ST {at} /TN "{name}" /TR "{escaped}" /F',
    ]
    if len(escaped) > 261:
        lines.append("REM ATTENTION : la commande dépasse 261 caractères, limite de schtasks /TR.")
        lines.append("REM Placez la commande dans un fichier .bat et planifiez ce fichier.")
    return "\n".join(lines)
