"""Exceptions personnalisées de dbbackup."""


class DBBackupError(Exception):
    """Erreur de base de l'application."""


class ConfigError(DBBackupError):
    """Paramètres de configuration invalides ou manquants."""


class DatabaseConnectionError(DBBackupError):
    """Impossible de se connecter à la base de données."""


class BackupError(DBBackupError):
    """Échec pendant une opération de sauvegarde."""


class RestoreError(DBBackupError):
    """Échec pendant une opération de restauration."""


class StorageError(DBBackupError):
    """Échec d'accès au stockage (local ou cloud)."""


class NotificationError(DBBackupError):
    """Échec d'envoi d'une notification (Slack...)."""
