"""Base commune des adaptateurs qui pilotent des outils en ligne de commande."""

from __future__ import annotations

from typing import BinaryIO, Dict, List, Optional, Sequence, Type

from dbbackup.adapters.base import DatabaseAdapter
from dbbackup.exceptions import DBBackupError, RestoreError
from dbbackup.logger import get_logger, redact
from dbbackup.utils.process import ProcessError, ToolNotFoundError, run_process

log = get_logger()

_MAX_DETAIL = 1000


class ExternalToolAdapter(DatabaseAdapter):
    """Adaptateur qui exécute des outils clients (localement ou via `docker exec`)."""

    #: texte d'aide affiché si un outil est introuvable
    client_tools = ""

    def _env(self) -> Dict[str, str]:
        """Variables d'environnement (mots de passe) : jamais dans la ligne de commande."""
        return {}

    def _command(self, tool: str, *args: str) -> List[str]:
        container = self.params.docker_container
        if not container:
            return [tool, *args]
        cmd = ["docker", "exec", "-i"]
        for name in self._env():
            cmd += ["-e", name]  # sans valeur : Docker lit la variable dans notre environnement
        cmd += [container, tool, *args]
        return cmd

    def _run(
        self,
        error_cls: Type[DBBackupError],
        action: str,
        tool: str,
        args: Sequence[str],
        stdin_stream: Optional[BinaryIO] = None,
        stdout_stream: Optional[BinaryIO] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        container = self.params.docker_container
        log.debug("Exécution de %s%s", tool, f" dans le conteneur {container}" if container else "")
        try:
            return run_process(
                self._command(tool, *args),
                env=self._env(),
                stdin_stream=stdin_stream,
                stdout_stream=stdout_stream,
                timeout=timeout,
            )
        except ToolNotFoundError as exc:
            if exc.tool == "docker":
                message = "Docker est introuvable : installez-le ou retirez --docker-container."
            else:
                message = (
                    f"Outil introuvable : {exc.tool}. Installez les outils clients "
                    f"({self.client_tools}) ou utilisez --docker-container NOM."
                )
            raise error_cls(message) from exc
        except ProcessError as exc:
            detail = redact(exc.stderr)[-_MAX_DETAIL:] or f"code de sortie {exc.returncode}"
            raise error_cls(f"{action} : {detail}") from exc

    def _require_overwrite(self, overwrite: bool) -> None:
        if not overwrite:
            raise RestoreError(
                f"La restauration remplace les objets existants de la base "
                f"« {self.params.database} ». Ajoutez --overwrite pour confirmer."
            )

    @staticmethod
    def _table_names(tables: Sequence[str]) -> List[str]:
        return [t.strip() for t in tables if t and t.strip()]
