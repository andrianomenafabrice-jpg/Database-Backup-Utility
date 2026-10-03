# Changelog

## 0.1.0

- CLI `dbbackup` : `test-connection`, `backup`, `restore`, `list`, `schedule run`, `schedule print`
- SQLite, PostgreSQL, MySQL/MariaDB et MongoDB
- Sauvegardes complètes compressées (gzip) avec SHA-256 et métadonnées
- Sauvegardes incrémentales et différentielles (SQLite), chaînes de restauration vérifiées
- Restauration complète et sélective, avec copie de sécurité de l'ancienne base
- Stockage : dossier local, Amazon S3, Google Cloud Storage, Azure Blob
- Notifications Slack, planification (boucle intégrée, cron, tâche Windows)
- Logs avec rotation et masquage des secrets
