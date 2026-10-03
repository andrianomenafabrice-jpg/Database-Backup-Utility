"""Interface en ligne de commande de dbbackup."""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import click

from dbbackup import __version__
from dbbackup.adapters import get_adapter
from dbbackup.catalog import has_full_backup, list_backups
from dbbackup.config import SUPPORTED_DBMS, ConnectionParams
from dbbackup.exceptions import ConfigError, DBBackupError
from dbbackup.jobs import failure_message, restore_message, run_backup_job
from dbbackup.logger import get_logger, setup_logging
from dbbackup.notifications import notify_safely
from dbbackup.restore import run_restore
from dbbackup.scheduler import (
    backup_arguments, choose_mode, cron_entry, parse_interval, run_schedule, windows_task,
)
from dbbackup.storage import is_remote
from dbbackup.transfer import fetch_backup, list_remote_backups
from dbbackup.utils.helpers import human_size


def connection_options(func):
    """Options de connexion communes à toutes les commandes."""
    options = [
        click.option(
            "--type", "db_type",
            type=click.Choice(SUPPORTED_DBMS, case_sensitive=False),
            required=True, help="Type de base de données.",
        ),
        click.option("--host", default="localhost", show_default=True, help="Hôte du serveur."),
        click.option("--port", type=int, default=None, help="Port (défaut selon le SGBD)."),
        click.option("--user", "username", default=None, help="Nom d'utilisateur."),
        click.option(
            "--password", default=None, envvar="DBBACKUP_PASSWORD",
            help="Mot de passe (recommandé : variable d'environnement DBBACKUP_PASSWORD).",
        ),
        click.option(
            "--database", "-d", required=True,
            help="Nom de la base (ou chemin du fichier pour SQLite).",
        ),
        click.option(
            "--docker-container", "docker_container", default=None,
            help="Exécute les outils (pg_dump, mysqldump, mongodump...) dans ce conteneur "
                 "Docker : aucun client à installer. Host et port se rapportent alors "
                 "à l'intérieur du conteneur (gardez les valeurs par défaut).",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def slack_option(func):
    return click.option(
        "--slack-webhook", "slack_webhook", default=None, envvar="DBBACKUP_SLACK_WEBHOOK",
        help="URL d'un webhook Slack : notification de succès / d'échec "
             "(recommandé : variable DBBACKUP_SLACK_WEBHOOK).",
    )(func)


def backup_options(func):
    """Options propres à la sauvegarde (communes à `backup` et `schedule run`)."""
    options = [
        click.option(
            "--mode", type=click.Choice(["full", "incremental", "differential"]),
            default="full", show_default=True,
            help="full : base entière. incremental : tables modifiées depuis la dernière "
                 "sauvegarde. differential : tables modifiées depuis la dernière complète.",
        ),
        click.option(
            "--output", "-o", type=click.Path(file_okay=False), default="./backups",
            show_default=True, help="Dossier de destination des sauvegardes.",
        ),
        click.option("--no-compress", is_flag=True, help="Désactive la compression gzip."),
        click.option(
            "--upload", "upload_to", default=None, metavar="DESTINATION",
            help="Envoie aussi la sauvegarde vers : s3://bucket/dossier, gs://bucket/dossier, "
                 "azure://conteneur/dossier ou un autre dossier. La copie locale est conservée.",
        ),
        slack_option,
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _warn(message: str) -> None:
    click.echo(f"Avertissement : {message}", err=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="dbbackup")
@click.option("--verbose", "-v", is_flag=True, help="Affiche les logs détaillés dans la console.")
@click.option(
    "--log-file", type=click.Path(dir_okay=False), default=None,
    help="Fichier de log (défaut : ~/.dbbackup/logs/dbbackup.log).",
)
def cli(verbose: bool, log_file) -> None:
    """dbbackup : sauvegarde et restauration de bases de données (SQLite, PostgreSQL, MySQL, MongoDB)."""
    setup_logging(verbose=verbose, log_file=log_file)


@cli.command("test-connection")
@connection_options
def test_connection(**conn) -> None:
    """Vérifie les identifiants et la connexion à la base."""
    params = ConnectionParams(**conn)
    get_logger().debug("Test de connexion demandé : %s", params)
    get_adapter(params).test_connection()
    click.echo(f"[OK] Connexion réussie à la base {params.db_type} : {params.database}")


def _print_backup_outcome(outcome) -> None:
    result = outcome.result
    click.echo(f"[OK] Sauvegarde {result.mode} créée : {result.file_path}")
    click.echo(
        f"     Taille : {human_size(result.size_bytes)} | Durée : {result.duration_seconds:.2f} s"
    )
    click.echo(f"     SHA-256 : {result.sha256}")
    if result.parent:
        click.echo(f"     Basée sur : {result.parent}")
    if result.included_tables is not None:
        included = ", ".join(result.included_tables) or "aucun changement"
        click.echo(f"     Tables sauvegardées : {included}")
    if result.dropped_tables:
        click.echo(f"     Tables supprimées depuis la référence : {', '.join(result.dropped_tables)}")
    if outcome.storage is not None:
        click.echo(f"[OK] Envoyée vers : {outcome.storage.location}")


@cli.command()
@connection_options
@backup_options
def backup(mode, output, no_compress, upload_to, slack_webhook, **conn) -> None:
    """Crée une sauvegarde de la base (et l'envoie / la notifie si demandé)."""
    params = ConnectionParams(**conn)
    get_logger().debug("Backup demandé (%s) : %s", mode, params)
    outcome = run_backup_job(
        get_adapter(params), output, mode=mode, compress=not no_compress,
        upload_to=upload_to, webhook=slack_webhook, on_warning=_warn,
    )
    _print_backup_outcome(outcome)


@cli.command()
@connection_options
@click.option(
    "--file", "-f", "backup_file", required=True,
    type=click.Path(dir_okay=False),
    help="Sauvegarde à restaurer : fichier local ou URL (s3://..., gs://..., azure://...).",
)
@click.option(
    "--table", "-t", "tables", multiple=True,
    help="Table/collection à restaurer (option répétable). Sans cette option, la base entière est restaurée.",
)
@click.option(
    "--overwrite", is_flag=True,
    help="Autorise le remplacement d'une base ou de tables existantes.",
)
@click.option(
    "--skip-verify", is_flag=True,
    help="Ne vérifie pas le SHA-256 de la sauvegarde (déconseillé).",
)
@slack_option
def restore(backup_file, tables, overwrite, skip_verify, slack_webhook, **conn) -> None:
    """Restaure la base depuis une sauvegarde (totale, sélective ou chaîne incrémentale)."""
    params = ConnectionParams(**conn)
    get_logger().debug("Restore demandé : %s", params)
    adapter = get_adapter(params)
    try:
        if is_remote(backup_file):
            with tempfile.TemporaryDirectory() as workdir:
                local_file = fetch_backup(backup_file, Path(workdir))
                result = run_restore(
                    adapter, local_file, tables=tables, overwrite=overwrite, verify=not skip_verify
                )
        else:
            result = run_restore(
                adapter, backup_file, tables=tables, overwrite=overwrite, verify=not skip_verify
            )
    except DBBackupError as exc:
        notify_safely(slack_webhook, failure_message("Restauration", adapter, exc), _warn)
        raise
    notify_safely(
        slack_webhook,
        restore_message(adapter, result.target, result.chain, result.duration_seconds),
        _warn,
    )
    scope = ", ".join(result.tables) if result.tables else "base complète"
    details = f"Durée : {result.duration_seconds:.2f} s"
    if result.statements:
        details = f"Instructions exécutées : {result.statements} | {details}"
    click.echo(f"[OK] Restauration terminée : {result.target}")
    click.echo(f"     Contenu restauré : {scope}")
    if result.chain > 1:
        click.echo(f"     Chaîne rejouée : {result.chain} sauvegardes")
    click.echo(f"     {details}")
    click.echo(
        "     Intégrité : vérifiée (SHA-256)" if result.verified else "     Intégrité : NON vérifiée"
    )
    if result.safety_copy:
        click.echo(f"     Ancienne base conservée : {result.safety_copy}")


@cli.command("list")
@click.option(
    "--output", "-o", type=click.Path(file_okay=False), default="./backups",
    show_default=True,
    help="Dossier ou stockage (s3://..., gs://..., azure://...) contenant les sauvegardes.",
)
def list_cmd(output) -> None:
    """Liste les sauvegardes d'un dossier ou d'un stockage cloud (date, type, taille, parent)."""
    backups = list_remote_backups(output) if is_remote(output) else list_backups(output)
    if not backups:
        click.echo(f"Aucune sauvegarde trouvée dans {output}")
        return
    for meta in backups:
        line = (
            f"{str(meta.get('created_at', ''))[:19]}  {meta.get('mode', '?'):<12} "
            f"{human_size(int(meta.get('size_bytes', 0))):>9}  {meta['file']}"
        )
        if meta.get("parent"):
            line += f"   <- {meta['parent']}"
        click.echo(line)


@cli.group()
def schedule() -> None:
    """Planification automatique des sauvegardes."""


@schedule.command("run")
@connection_options
@backup_options
@click.option(
    "--interval", required=True,
    help="Délai entre deux sauvegardes : 30s, 15m, 6h, 1d.",
)
@click.option(
    "--full-every", type=int, default=None, metavar="N",
    help="Avec --mode incremental/differential : une sauvegarde complète toutes les N exécutions.",
)
@click.option(
    "--max-runs", type=int, default=None, metavar="N",
    help="S'arrête après N exécutions (sans cette option : tourne jusqu'à Ctrl+C).",
)
def schedule_run(
    interval, full_every, max_runs, mode, output, no_compress, upload_to, slack_webhook, **conn
) -> None:
    """Lance les sauvegardes à intervalle régulier (processus qui reste actif)."""
    params = ConnectionParams(**conn)
    adapter = get_adapter(params)
    seconds = parse_interval(interval)
    if mode != "full" and not adapter.supports_incremental:
        raise ConfigError(
            f"Le mode '{mode}' n'est pas disponible pour {params.db_type} (SQLite uniquement)."
        )
    if full_every is not None and full_every < 1:
        raise ConfigError("--full-every doit être supérieur ou égal à 1.")

    click.echo(
        f"Planification démarrée : une sauvegarde toutes les {interval} "
        f"(mode {mode}). Ctrl+C pour arrêter."
    )

    def job(run_number: int) -> bool:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        effective = choose_mode(
            mode, run_number, full_every, has_full_backup(output, adapter.identity)
        )
        try:
            outcome = run_backup_job(
                adapter, output, mode=effective, compress=not no_compress,
                upload_to=upload_to, webhook=slack_webhook, on_warning=_warn,
            )
        except DBBackupError as exc:
            get_logger().error("Exécution planifiée %d échouée : %s", run_number, exc)
            click.echo(f"[{stamp}] Exécution {run_number} ({effective}) : Erreur : {exc}", err=True)
            return False
        result = outcome.result
        sent = f" | envoyée vers {outcome.storage.location}" if outcome.storage else ""
        click.echo(
            f"[{stamp}] Exécution {run_number} ({effective}) : [OK] {result.file_path.name} "
            f"({human_size(result.size_bytes)}){sent}"
        )
        return True

    try:
        failures = run_schedule(job, seconds, max_runs=max_runs)
    except KeyboardInterrupt:
        click.echo("\nPlanification arrêtée.")
        return
    if failures:
        click.echo(f"Planification terminée avec {failures} exécution(s) en échec.", err=True)
        sys.exit(1)


@schedule.command("print")
@connection_options
@click.option(
    "--target", type=click.Choice(["cron", "windows"]), required=True,
    help="cron : ligne pour crontab (Linux / macOS). windows : commande schtasks.",
)
@click.option("--cron", "cron_expr", default="0 2 * * *", show_default=True,
              help="Expression cron (cible cron).")
@click.option("--at", default="02:00", show_default=True,
              help="Heure quotidienne HH:MM (cible windows).")
@click.option(
    "--mode", type=click.Choice(["full", "incremental", "differential"]),
    default="full", show_default=True, help="Type de sauvegarde planifiée.",
)
@click.option("--output", "-o", type=click.Path(file_okay=False), default="./backups",
              show_default=True, help="Dossier des sauvegardes.")
@click.option("--upload", "upload_to", default=None, metavar="DESTINATION",
              help="Destination d'envoi (s3://..., gs://..., azure://... ou dossier).")
def schedule_print(target, cron_expr, at, mode, output, upload_to, **conn) -> None:
    """Affiche la commande à ajouter au planificateur du système (rien n'est modifié)."""
    params = ConnectionParams(**conn)
    args = backup_arguments(params, mode, output, upload_to)
    if target == "cron":
        click.echo(cron_entry(cron_expr, args))
    else:
        name = f"dbbackup-{get_adapter(params).label}"
        click.echo(windows_task(at, args, name))


def main() -> None:
    try:
        cli()
    except DBBackupError as exc:
        get_logger().error("%s", exc)
        click.echo(f"Erreur : {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
