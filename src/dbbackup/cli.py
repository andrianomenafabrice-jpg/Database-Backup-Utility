"""Interface en ligne de commande de dbbackup."""

from __future__ import annotations

import sys

import click

from dbbackup import __version__
from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.config import SUPPORTED_DBMS, ConnectionParams
from dbbackup.exceptions import DBBackupError
from dbbackup.logger import get_logger, setup_logging
from dbbackup.restore import run_restore
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


@cli.command()
@connection_options
@click.option(
    "--mode", type=click.Choice(["full", "incremental", "differential"]),
    default="full", show_default=True, help="Type de sauvegarde.",
)
@click.option(
    "--output", "-o", type=click.Path(file_okay=False), default="./backups",
    show_default=True, help="Dossier de destination des sauvegardes.",
)
@click.option("--no-compress", is_flag=True, help="Désactive la compression gzip.")
def backup(mode, output, no_compress, **conn) -> None:
    """Crée une sauvegarde de la base."""
    params = ConnectionParams(**conn)
    get_logger().debug("Backup demandé (%s) : %s", mode, params)
    result = run_backup(get_adapter(params), output, mode=mode, compress=not no_compress)
    click.echo(f"[OK] Sauvegarde créée : {result.file_path}")
    click.echo(
        f"     Taille : {human_size(result.size_bytes)} | Durée : {result.duration_seconds:.2f} s"
    )
    click.echo(f"     SHA-256 : {result.sha256}")


@cli.command()
@connection_options
@click.option(
    "--file", "-f", "backup_file", required=True,
    type=click.Path(dir_okay=False), help="Fichier de sauvegarde à restaurer.",
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
def restore(backup_file, tables, overwrite, skip_verify, **conn) -> None:
    """Restaure la base depuis une sauvegarde (totale ou sélective)."""
    params = ConnectionParams(**conn)
    get_logger().debug("Restore demandé : %s", params)
    result = run_restore(
        get_adapter(params), backup_file,
        tables=tables, overwrite=overwrite, verify=not skip_verify,
    )
    scope = ", ".join(result.tables) if result.tables else "base complète"
    details = f"Durée : {result.duration_seconds:.2f} s"
    if result.statements:
        details = f"Instructions exécutées : {result.statements} | {details}"
    click.echo(f"[OK] Restauration terminée : {result.target}")
    click.echo(f"     Contenu restauré : {scope}")
    click.echo(f"     {details}")
    click.echo(
        "     Intégrité : vérifiée (SHA-256)" if result.verified else "     Intégrité : NON vérifiée"
    )
    if result.safety_copy:
        click.echo(f"     Ancienne base conservée : {result.safety_copy}")


def main() -> None:
    try:
        cli()
    except DBBackupError as exc:
        get_logger().error("%s", exc)
        click.echo(f"Erreur : {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
