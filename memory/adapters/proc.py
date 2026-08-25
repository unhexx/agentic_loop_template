# -*- coding: utf-8 -*-
"""Запуск CLI-адаптера: timeout + группа процессов, без логов тел промпта."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

# пауза SIGTERM → SIGKILL; бюджет роли уже исчерпан timeout_s
_KILL_GRACE_S = 2.0

Cmd = Sequence[str]


class CliTimeoutError(RuntimeError):
    """Подпроцесс (и группа) убиты по timeout_s."""


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _wait_exit(proc: subprocess.Popen, grace_s: float) -> bool:
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return proc.poll() is not None


def _kill_direct(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    if not _wait_exit(proc, _KILL_GRACE_S):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _kill_posix_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        _kill_direct(proc)
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    _wait_exit(proc, _KILL_GRACE_S)
    # лидер мог выйти по SIGTERM, внуки с handler/SIG_IGN — нет
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _kill_after_timeout(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        _kill_direct(proc)
    else:
        _kill_posix_group(proc)
    _reap(proc)


def run_cli(
    cmd: Cmd,
    *,
    cwd: Path,
    timeout_s: int,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Popen; stdin=DEVNULL; захват stdout/stderr текстом.

    POSIX: start_new_session=True; по timeout — os.killpg(SIGTERM), затем SIGKILL.
    win32: CREATE_NEW_PROCESS_GROUP; по timeout — proc.terminate(), затем proc.kill().
    Убийство группы — только Unix. На Windows — только прямой потомок,
    без гарантии для внуков Node.
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s должен быть положительным")
    child_env = os.environ.copy() if env is None else dict(env)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,  # иначе Node CLI зависает на запросе ключа
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "env": child_env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(cmd), **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _kill_after_timeout(proc)
        raise CliTimeoutError(f"{cmd[0]} timed out after {timeout_s}s") from exc
    return subprocess.CompletedProcess(
        list(cmd),
        proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )
