# Stockage : local et cloud

`dbbackup backup` écrit toujours la sauvegarde dans le dossier local `--output` (nécessaire pour
les chaînes incrémentales). `--upload DESTINATION` l'envoie en plus vers un autre stockage.

| Destination | Exemple | Identifiants |
|---|---|---|
| Dossier local / NAS | `--upload /mnt/nas/backups` | aucun |
| Amazon S3 | `--upload s3://mon-bucket/dossier` | variables `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (ou `~/.aws`, rôle IAM) |
| S3 compatible (MinIO...) | idem + `DBBACKUP_S3_ENDPOINT=https://...` | idem |
| Google Cloud Storage | `--upload gs://mon-bucket/dossier` | `GOOGLE_APPLICATION_CREDENTIALS` (ou `gcloud auth application-default login`) |
| Azure Blob | `--upload azure://conteneur/dossier` | `AZURE_STORAGE_CONNECTION_STRING` |

Les SDK cloud sont optionnels :

```bash
pip install -e ".[s3]"      # ou .[gcs], .[azure], .[cloud] pour les trois
```

## Utilisation

```bash
dbbackup backup --type sqlite -d ma_base.db -o ./backups --upload s3://mon-bucket/shop
dbbackup list -o s3://mon-bucket/shop
dbbackup restore --type sqlite -d restauree.db -f s3://mon-bucket/shop/FICHIER.sql.gz
```

- L'envoi du fichier est suivi d'une vérification de taille ; les métadonnées sont envoyées en
  dernier (une sauvegarde n'apparaît dans `list` que lorsqu'elle est complète).
- Restaurer depuis une URL télécharge la sauvegarde et, si elle est incrémentale, toute sa
  chaîne de parents, puis vérifie les SHA-256 comme pour une restauration locale.
- Les mots de passe, clés et signatures n'apparaissent jamais dans les logs ni les messages d'erreur.
