# PostgreSQL, MySQL et MongoDB

Ces SGBD sont gérés via leurs outils officiels (`psql`/`pg_dump`/`pg_restore`, `mysql`/`mysqldump`,
`mongosh`/`mongodump`/`mongorestore`). Deux possibilités :

1. installer ces outils clients sur la machine ;
2. ou utiliser `--docker-container NOM` : les outils sont exécutés dans le conteneur
   (`docker exec`), sans rien installer. Host et port se rapportent alors à l'intérieur du
   conteneur : gardez les valeurs par défaut.

Le mot de passe se passe par la variable `DBBACKUP_PASSWORD` (jamais dans les logs).

```bash
export DBBACKUP_PASSWORD=secret
dbbackup backup --type postgresql -d testdb --user postgres --docker-container dbbackup-pg
dbbackup restore --type postgresql -d testdb --user postgres --docker-container dbbackup-pg \
  -f backups/FICHIER.dump.gz --overwrite
```

Bases de test jetables : `bash scripts/test-dbs.sh up` (puis `down`).

## Particularités

- La restauration sur un serveur remplace les objets existants : `--overwrite` est obligatoire.
- **PostgreSQL** : archive au format custom ; restauration sélective via `pg_restore --table`
  (structure et données de la table, sans index, contraintes ni séquences : comportement de
  pg_restore). Indiquer le nom de la table sans schéma.
- **MySQL** : restauration sélective par filtrage du dump (tables, avec leurs triggers) ; vues,
  routines et événements ne sont pas restaurés en mode sélectif.
- **MongoDB** : la restauration se fait dans la base du même nom que celle sauvegardée. Une
  collection absente de la sauvegarde est ignorée par mongorestore. Les outils MongoDB
  n'acceptent pas le mot de passe par variable d'environnement : il transite dans les
  arguments du processus (utilisez un compte dédié aux sauvegardes).
- `DBBACKUP_MONGO_AUTH_DB` : base d'authentification MongoDB (défaut : `admin`).
