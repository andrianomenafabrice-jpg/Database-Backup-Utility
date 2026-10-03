"""Notifications de fin d'opération."""

from dbbackup.notifications.slack import notify_safely, send_slack

__all__ = ["send_slack", "notify_safely"]
