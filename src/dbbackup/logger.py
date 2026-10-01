"""Logging avec rotation de fichiers et masquage automatique des secrets."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

LOGGER_NAME = "dbbackup"
DEFAULT_LOG_FILE = Path.home() / ".dbbackup" / "logs" / "dbbackup.log"

_SECRET_PATTERNS = (
    re.compile(r"(?i)((?:password|passwd|pwd|secret|token)\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(--password\s+)\S+"),
)


def redact(text: str) -> str:
    """Remplace les mots de passe et secrets présents dans un texte par ***."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class RedactingFilter(logging.Filter):
    """Empêche les secrets d'être écrits dans les logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(
    verbose: bool = False,
    log_file: Optional[Union[str, Path]] = None,
) -> logging.Logger:
    """Configure le logger : console (stderr) + fichier avec rotation."""
    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    redactor = RedactingFilter()
    file_format = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    console.addFilter(redactor)
    logger.addHandler(console)

    path = Path(log_file) if log_file else DEFAULT_LOG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_format)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Impossible d'écrire le fichier de log %s : %s", path, exc)

    return logger
