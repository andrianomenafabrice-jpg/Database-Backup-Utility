# Sauvegardes incrémentales et différentielles

Disponible pour **SQLite**. Pour PostgreSQL, MySQL et MongoDB, seul le mode `full` est géré
(leurs sauvegardes incrémentales natives passent par les journaux WAL / binlog, hors périmètre
de cet outil pour l'instant) : demander un autre mode donne une erreur claire.

## Principe

À chaque sauvegarde, dbbackup calcule une empreinte SHA-256 de chaque table (définition, index,
triggers, compteur AUTOINCREMENT et contenu) et la range dans le fichier `.meta.json`.

- `--mode full` : toute la base.
- `--mode incremental` : seulement les tables modifiées depuis la **dernière sauvegarde**
  (quel que soit son type).
- `--mode differential` : seulement les tables modifiées depuis la **dernière sauvegarde complète**
  (cumulatif : chaque différentielle remplace la précédente).

La granularité est la table : une table modifiée est sauvegardée en entier. Les tables
supprimées sont mémorisées. Les vues ne sont sauvegardées que dans la sauvegarde complète.
La base est lue en entier à chaque sauvegarde pour détecter les changements : le gain porte
sur l'espace de stockage et le transfert, pas sur le temps de lecture.

## Utilisation

```bash
dbbackup backup --type sqlite -d ma_base.db -o ./backups                       # complète
dbbackup backup --type sqlite -d ma_base.db -o ./backups --mode incremental    # incrémentale
dbbackup backup --type sqlite -d ma_base.db -o ./backups --mode differential   # différentielle
dbbackup list -o ./backups                                                     # voir les sauvegardes
```

Restaurer = donner la **dernière** sauvegarde voulue ; toute la chaîne nécessaire est rejouée :

```bash
dbbackup restore --type sqlite -d base_restauree.db -f backups/FICHIER_INCREMENTAL.sql.gz
```

Chaque fichier de la chaîne est vérifié (SHA-256) avant la restauration. La base est
reconstruite dans un fichier temporaire puis mise en place : en cas d'erreur, rien n'est
modifié. Gardez toute la chaîne (complète + incrémentales) dans le même dossier.

## Limites

- La restauration sélective (`--table`) n'est pas disponible pour une chaîne.
- Les sauvegardes complètes faites avant l'étape 5 n'ont pas d'empreintes : refaites une
  sauvegarde `full` avant la première incrémentale.
