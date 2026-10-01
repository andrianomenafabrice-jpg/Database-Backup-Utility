"""Exécution de processus externes en streaming (sans shell, sans fuite de secrets)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading
from typing import BinaryIO, Dict, List, Optional, Sequence

_CHUNK = 1024 * 1024
_STDERR_LIMIT = 64 * 1024


class ToolNotFoundError(Exception):
    """L'exécutable demandé est introuvable."""

    def __init__(self, tool: str) -> None:
        super().__init__(tool)
        self.tool = tool


class ProcessError(Exception):
    """Le processus s'est terminé avec une erreur."""

    def __init__(self, returncode: int, stderr: str) -> None:
        super().__init__(f"code de sortie {returncode} : {stderr}")
        self.returncode = returncode
        self.stderr = stderr


def run_process(
    cmd: Sequence[str],
    env: Optional[Dict[str, str]] = None,
    stdin_stream: Optional[BinaryIO] = None,
    stdout_stream: Optional[BinaryIO] = None,
    timeout: Optional[float] = None,
) -> bytes:
    """Exécute `cmd` (sans shell).

    - stdin_stream : flux envoyé à l'entrée standard du processus (en streaming)
    - stdout_stream : flux qui reçoit la sortie standard (en streaming) ;
      sinon la sortie est capturée et retournée (à réserver aux petites sorties)
    - env : variables ajoutées à l'environnement (c'est ici que passent les mots de passe)
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE if stdin_stream is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(str(cmd[0])) from exc
    except OSError as exc:
        raise ProcessError(-1, f"Impossible de lancer {cmd[0]} : {exc}") from exc

    errors: List[Exception] = []
    captured = io.BytesIO()
    sink = stdout_stream if stdout_stream is not None else captured
    stderr_tail = bytearray()

    def pump_out() -> None:
        try:
            shutil.copyfileobj(proc.stdout, sink, _CHUNK)
        except Exception as exc:  # écriture impossible (disque plein, flux fermé...)
            errors.append(exc)
            proc.kill()

    def pump_err() -> None:
        try:
            while True:
                chunk = proc.stderr.read(8192)
                if not chunk:
                    break
                stderr_tail.extend(chunk)
                if len(stderr_tail) > _STDERR_LIMIT:
                    del stderr_tail[:-_STDERR_LIMIT]
        except Exception:
            pass

    def pump_in() -> None:
        try:
            while True:
                try:
                    chunk = stdin_stream.read(_CHUNK)
                except Exception as exc:  # fichier de sauvegarde illisible / corrompu
                    errors.append(exc)
                    proc.kill()
                    return
                if not chunk:
                    return
                try:
                    proc.stdin.write(chunk)
                except (OSError, ValueError):
                    return  # le processus s'est arrêté : son code de sortie dira pourquoi
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    threads = [
        threading.Thread(target=pump_out, daemon=True),
        threading.Thread(target=pump_err, daemon=True),
    ]
    if stdin_stream is not None:
        threads.append(threading.Thread(target=pump_in, daemon=True))
    for thread in threads:
        thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for thread in threads:
            thread.join()
        raise ProcessError(-1, f"délai dépassé ({timeout} s)")
    except BaseException:
        proc.kill()
        raise

    for thread in threads:
        thread.join()
    proc.stdout.close()
    proc.stderr.close()

    if errors:
        raise errors[0]
    if proc.returncode != 0:
        raise ProcessError(proc.returncode, stderr_tail.decode("utf-8", "replace").strip())
    return captured.getvalue()
