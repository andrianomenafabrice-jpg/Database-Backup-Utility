"""Interface en ligne de commande de dbbackup."""

from __future__ import annotations

import sys

import click

from dbbackup import __version__
from dbbackup.config import SUPPORTED_DBMS, ConnectionParams
from dbbackup.exceptions import DBBackupError
from dbbackup.logger import get_logger, setup_logging


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
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _build_params(db_type, host, port, username, password, database) -> ConnectionParams:
    return ConnectionParams(
        db_type=db_type, database=database, host=host,
        port=port, username=username, password=password,
    )


def _not_ready(step: int) -> None:
    raise click.ClickException(
        f"Cette commande sera disponible à l'étape {step} du développement."
    )


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
def test_connection(db_type, host, port, username, password, database) -> None:
    """Vérifie les identifiants et la connexion à la base."""
    params = _build_params(db_type, host, port, username, password, database)
    get_logger().debug("Test de connexion demandé : %s", params)
    _not_ready(2)


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
def backup(db_type, host, port, username, password, database, mode, output, no_compress) -> None:
    """Crée une sauvegarde de la base."""
    params = _build_params(db_type, host, port, username, password, database)
    get_logger().debug("Backup demandé (%s) : %s", mode, params)
    _not_ready(2)


@cli.command()
@connection_options
@click.option(
    "--file", "-f", "backup_file", required=True,
    type=click.Path(dir_okay=False), help="Fichier de sauvegarde à restaurer.",
)
@click.option(
    "--table", "-t", "tables", multiple=True,
    help="Table/collection à restaurer (option répétable).",
)
def restore(db_type, host, port, username, password, database, backup_file, tables) -> None:
    """Restaure la base depuis une sauvegarde (totale ou sélective)."""
    params = _build_params(db_type, host, port, username, password, database)
    get_logger().debug("Restore demandé : %s", params)
    _not_ready(3)


def main() -> None:
    try:
        cli()
    except DBBackupError as exc:
        get_logger().error("%s", exc)
        click.echo(f"Erreur : {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
