# Guide d'utilisation

## Tester la connexion

```bash
dbbackup test-connection --type sqlite -d ma_base.db
```

## Sauvegarde complète compressée (SQLite)

```bash
dbbackup backup --type sqlite -d ma_base.db -o ./backups
```

Fichiers produits dans le dossier de destination :

- `ma_base_sqlite_full_AAAAMMJJ_HHMMSS.sql.gz` : la sauvegarde compressée
- `ma_base_sqlite_full_AAAAMMJJ_HHMMSS.sql.gz.meta.json` : métadonnées (taille, SHA-256, durée, date)

Options utiles :

- `--no-compress` : sauvegarde en `.sql` brut
- `-v` : affiche les logs détaillés dans la console

## Logs

Par défaut : `~/.dbbackup/logs/dbbackup.log` (rotation automatique, mots de passe masqués).

## Restaurer une sauvegarde

Restauration complète dans un nouveau fichier (le SHA-256 est vérifié automatiquement) :

```bash
dbbackup restore --type sqlite -d base_restauree.db -f backups/ma_base_sqlite_full_XXXX.sql.gz
```

Remplacer une base existante (l'ancienne est conservée en `*.before-restore-<date>`) :

```bash
dbbackup restore --type sqlite -d ma_base.db -f backups/FICHIER.sql.gz --overwrite
```

Restauration sélective (une ou plusieurs tables, avec leurs index et triggers) :

```bash
dbbackup restore --type sqlite -d ma_base.db -f backups/FICHIER.sql.gz -t clients -t commandes --overwrite
```

Si la base cible existe, seules les tables demandées sont remplacées, en une seule
transaction : en cas d'erreur, rien n'est modifié.

Options :

- `--overwrite` : autorise le remplacement d'une base ou de tables existantes
- `--skip-verify` : ignore la vérification d'intégrité (déconseillé)
