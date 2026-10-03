# Notifications Slack et planification

## Notifications Slack

Créez un « Incoming Webhook » dans Slack, puis :

```bash
export DBBACKUP_SLACK_WEBHOOK='https://hooks.slack.com/services/...'
dbbackup backup --type sqlite -d ma_base.db -o ./backups
```

ou `--slack-webhook URL`. Un message part à la fin de chaque sauvegarde et restauration,
en cas de succès comme d'échec (base, serveur, fichier, taille, durée, erreur).
Une notification qui échoue n'interrompt jamais la sauvegarde : un avertissement est affiché.
L'URL du webhook est un secret : elle n'apparaît dans aucun log ni message d'erreur.

## Planification

### Option 1 : boucle intégrée (tous systèmes)

```bash
dbbackup schedule run --type sqlite -d ma_base.db -o ./backups --interval 6h
dbbackup schedule run --type sqlite -d ma_base.db -o ./backups --interval 1h \
  --mode incremental --full-every 24
```

`--interval` : `30s`, `15m`, `6h`, `1d`. Avec `--mode incremental` ou `differential`, une
sauvegarde complète est faite en premier, puis toutes les `--full-every` exécutions.
Une exécution en échec n'arrête pas la boucle (une alerte Slack part). `--max-runs N` limite
le nombre d'exécutions. Ctrl+C arrête proprement.

### Option 2 : planificateur du système

`dbbackup schedule print` affiche la commande à ajouter (il ne modifie rien) :

```bash
dbbackup schedule print --target cron --cron "0 2 * * *" --type sqlite -d ma_base.db -o ./backups
dbbackup schedule print --target windows --at 02:00 --type sqlite -d ma_base.db -o ./backups
```

- Linux / macOS : collez la ligne avec `crontab -e`. Mettez vos secrets dans `~/.dbbackup/env`
  (`chmod 600`) ; la ligne le charge avant la sauvegarde.
- Windows : lancez la commande `schtasks` affichée. Définissez d'abord vos secrets avec `setx`.

Aucun mot de passe ni webhook n'est écrit dans la commande générée.
