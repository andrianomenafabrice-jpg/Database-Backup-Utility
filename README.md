# Database Backup Utility

Outil en ligne de commande pour **sauvegarder et restaurer n'importe quelle base de données** (SQLite, PostgreSQL, MySQL, MongoDB) avec compression, stockage local ou cloud, logs et notifications Slack.

> 🚧 Projet en cours de développement, construit étape par étape.

## Fonctionnalités prévues

- Connexion multi-SGBD avec test des identifiants
- Sauvegardes full, incrémentales et différentielles
- Compression gzip et vérification d'intégrité (checksum)
- Stockage local, AWS S3, Google Cloud Storage, Azure Blob
- Logs détaillés (début, fin, durée, statut, erreurs), sans jamais exposer les mots de passe
- Notifications Slack
- Restauration complète ou sélective (tables / collections)
- Planification automatique
- Compatible Windows, Linux et macOS

## Installation (développement)

```bash
git clone https://github.com/andrianomenafabrice-jpg/Database-Backup-Utility.git
cd Database-Backup-Utility
python -m venv .venv
source .venv/Scripts/activate      # Git Bash (Windows) ; Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
```

## Utilisation

```bash
dbbackup --help
dbbackup backup --help
dbbackup restore --help
dbbackup test-connection --help
```

Le mot de passe se passe de préférence via la variable d'environnement `DBBACKUP_PASSWORD`.

## Tests

```bash
pytest
```

## Avancement

- [x] Étape 1 : structure, CLI, logging, tests
- [ ] Étape 2 : adaptateurs + SQLite + compression
- [ ] Étape 3 : restauration (totale et sélective)
- [ ] Étape 4 : PostgreSQL, MySQL, MongoDB
- [ ] Étape 5 : incrémental / différentiel
- [ ] Étape 6 : stockage cloud
- [ ] Étape 7 : Slack + planification
- [ ] Étape 8 : CI et documentation finale

## Licence

MIT
