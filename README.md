# Database Backup Utility

[![CI](https://github.com/andrianomenafabrice-jpg/Database-Backup-Utility/actions/workflows/ci.yml/badge.svg)](https://github.com/andrianomenafabrice-jpg/Database-Backup-Utility/actions/workflows/ci.yml)

Outil en ligne de commande pour **sauvegarder et restaurer des bases de données** (SQLite,
PostgreSQL, MySQL/MariaDB, MongoDB) : compression, vérification d'intégrité, sauvegardes
incrémentales, stockage local ou cloud, notifications Slack et planification.

Fonctionne sur Windows, Linux et macOS (Python 3.9+).

## Installation

```bash
git clone https://github.com/andrianomenafabrice-jpg/Database-Backup-Utility.git
cd Database-Backup-Utility
python -m venv .venv
source .venv/Scripts/activate        # Git Bash (Windows) ; Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
```

SDK cloud (optionnels) : `pip install -e ".[s3]"`, `".[gcs]"`, `".[azure]"` ou `".[cloud]"`.

## Démarrage rapide

```bash
dbbackup test-connection --type sqlite -d ma_base.db
dbbackup backup --type sqlite -d ma_base.db -o ./backups
dbbackup list -o ./backups
dbbackup restore --type sqlite -d restauree.db -f backups/FICHIER.sql.gz
```

Aide : `dbbackup --help`, `dbbackup backup --help`...

## Commandes

| Commande | Rôle |
|---|---|
| `test-connection` | Vérifie les identifiants et la connexion |
| `backup` | Crée une sauvegarde (`--mode full\|incremental\|differential`, `--upload`, `--slack-webhook`) |
| `restore` | Restaure (complète ou sélective avec `-t table`, depuis un fichier ou une URL cloud) |
| `list` | Liste les sauvegardes d'un dossier ou d'un stockage cloud |
| `schedule run` | Lance des sauvegardes à intervalle régulier |
| `schedule print` | Affiche la commande cron / `schtasks` à installer |

Le mot de passe se passe par la variable `DBBACKUP_PASSWORD` (jamais dans les logs).

## SGBD pris en charge

| SGBD | Outil utilisé | Incrémental / différentiel | Restauration sélective |
|---|---|---|---|
| SQLite | bibliothèque standard Python | oui (par table) | oui |
| PostgreSQL | `psql`, `pg_dump`, `pg_restore` | non | oui (`pg_restore --table`) |
| MySQL / MariaDB | `mysql`, `mysqldump` | non | oui (filtrage du dump) |
| MongoDB | `mongosh`, `mongodump`, `mongorestore` | non | oui (collections) |

Pour PostgreSQL, MySQL et MongoDB, `--docker-container NOM` exécute les outils dans un conteneur
Docker : aucun client à installer. Voir [docs/servers.md](docs/servers.md).

## Sauvegardes complètes, incrémentales, différentielles

- `full` : toute la base. `incremental` : tables modifiées depuis la dernière sauvegarde.
  `differential` : tables modifiées depuis la dernière sauvegarde complète.
- Restaurer la dernière sauvegarde d'une chaîne rejoue automatiquement toute la chaîne,
  vérifiée par SHA-256.

Détails et limites : [docs/incremental.md](docs/incremental.md).

## Stockage

`--upload` envoie la sauvegarde vers un dossier, `s3://`, `gs://` ou `azure://` (la copie locale
est conservée). Voir [docs/storage.md](docs/storage.md).

## Notifications et planification

Webhook Slack (`DBBACKUP_SLACK_WEBHOOK`) et planification : [docs/automation.md](docs/automation.md).

## Sécurité et fiabilité

- Sauvegarde écrite dans un fichier `.part` puis renommée : jamais de fichier incomplet.
- SHA-256 de chaque sauvegarde, vérifié avant toute restauration.
- Restauration atomique : en cas d'erreur la base d'origine n'est pas modifiée ; l'ancienne
  base est conservée (`*.before-restore-*`) quand elle est remplacée.
- Mots de passe transmis aux outils par variable d'environnement ; masqués dans les logs et erreurs.
- Données traitées en streaming : pas de chargement complet en mémoire.
- Logs (début, fin, durée, statut, erreurs) : `~/.dbbackup/logs/dbbackup.log`, avec rotation.

## Architecture

```
src/dbbackup/
├── cli.py            commandes (Click)
├── backup.py         service de sauvegarde
├── restore.py        service de restauration
├── catalog.py        métadonnées, chaînes de sauvegardes
├── jobs.py           sauvegarde + envoi + notification
├── scheduler.py      planification
├── transfer.py       envoi / récupération vers un stockage
├── adapters/         un adaptateur par SGBD (sqlite, postgres, mysql, mongodb)
├── storage/          local, S3, Google Cloud Storage, Azure Blob
├── notifications/    Slack
└── utils/            hachage, exécution de processus externes
```

## Limites connues

- Incrémental / différentiel : SQLite uniquement ; granularité = table entière ; la base est
  lue en entier à chaque sauvegarde (le gain porte sur le stockage, pas sur le temps de lecture).
- Restauration sélective impossible sur une chaîne incrémentale.
- PostgreSQL : la restauration sélective ne restaure pas index, contraintes ni séquences.
- MongoDB : le mot de passe transite dans les arguments du processus (limite des outils MongoDB).
- Les adaptateurs PostgreSQL/MySQL/MongoDB et les stockages cloud sont testés avec des outils
  et SDK simulés ; des tests avec de vrais serveurs sont fournis (voir ci-dessous).

## Tests

```bash
pytest                                                   # tests unitaires (sans serveur ni Docker)
bash scripts/test-dbs.sh up                              # bases de test PostgreSQL / MySQL / MongoDB (Docker)
DBBACKUP_IT=1 pytest tests/test_integration_servers.py   # tests avec de vrais serveurs
bash scripts/test-dbs.sh down
```

## Licence

MIT
