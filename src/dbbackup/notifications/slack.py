"""Notifications Slack via webhook entrant (bibliothèque standard, aucune dépendance)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlparse

from dbbackup.exceptions import NotificationError
from dbbackup.logger import get_logger, redact

log = get_logger()

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _validate(webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    secure = parsed.scheme == "https"
    local_test = parsed.scheme == "http" and (parsed.hostname or "") in _LOCAL_HOSTS
    if not parsed.netloc or not (secure or local_test):
        raise NotificationError("URL de webhook invalide : une URL https:// est requise.")


def send_slack(webhook_url: str, text: str, timeout: float = 10.0) -> None:
    """Envoie un message. L'URL du webhook est un secret : elle n'apparaît dans aucune erreur."""
    _validate(webhook_url)
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(200)
    except urllib.error.HTTPError as exc:
        raise NotificationError(f"Slack a refusé la notification (HTTP {exc.code}).") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = redact(str(getattr(exc, "reason", exc)))[:200]
        raise NotificationError(f"Impossible de joindre Slack : {reason}") from None


def notify_safely(
    webhook_url: Optional[str],
    text: str,
    on_warning: Optional[Callable[[str], None]] = None,
) -> bool:
    """Envoie une notification sans jamais faire échouer l'opération principale.

    Retourne True si le message est parti. Sans webhook configuré, ne fait rien.
    """
    if not webhook_url:
        return False
    try:
        send_slack(webhook_url, text)
        log.info("Notification Slack envoyée")
        return True
    except NotificationError as exc:
        log.warning("Notification Slack non envoyée : %s", exc)
        if on_warning:
            on_warning(f"notification Slack non envoyée : {exc}")
        return False
