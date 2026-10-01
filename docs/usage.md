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
