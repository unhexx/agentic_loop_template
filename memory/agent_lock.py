# -*- coding: utf-8 -*-
"""Межпроцессная блокировка файлов в каталоге .agent (stdlib, O_EXCL)."""
from __future__ import annotations

import errno
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_RETRY_SLEEP = 0.05
# O_EXCL сверяет PID: два потока одного процесса снимают чужой lock-файл.
_THREAD_GUARDS: dict[str, threading.Lock] = {}
_THREAD_GUARDS_MU = threading.Lock()


def _thread_guard(path: Path) -> threading.Lock:
    """Один threading.Lock на путь — сериализация внутри процесса."""
    key = str(path)
    with _THREAD_GUARDS_MU:
        guard = _THREAD_GUARDS.get(key)
        if guard is None:
            guard = threading.Lock()
            _THREAD_GUARDS[key] = guard
        return guard


def lock_path(agent_dir: Path, name: str = "agent") -> Path:
    """Вернуть agent_dir / '{name}.lock'; в имени оставляем только [A-Za-z0-9._-]."""
    cleaned = _SAFE_NAME.sub("", str(name))
    if not cleaned:
        cleaned = "agent"
    return Path(agent_dir) / f"{cleaned}.lock"


def _pid_dead(pid: int) -> bool:
    """True, если процесса с таким PID нет."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ESRCH:
            return True
        # Windows: OpenProcess на несуществующий PID даёт EINVAL, не ESRCH
        if os.name == "nt" and getattr(exc, "errno", None) in (errno.EINVAL, errno.ENOENT):
            return True
        return False
    return False


def _stale_lock(path: Path) -> bool:
    """True, если файл блокировки пустой, битый или PID уже мёртв."""
    try:
        raw = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return False
    if not raw:
        return True
    token = raw.split()[0]
    try:
        pid = int(token)
    except ValueError:
        return True
    return _pid_dead(pid)


def _unlink_if_owner(path: Path, pid: int) -> None:
    """Снять файл только если в нём наш PID — чужой не трогаем."""
    try:
        raw = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return
    token = raw.split()[0] if raw else ""
    if token != str(pid):
        return
    try:
        path.unlink()
    except OSError:
        pass


@contextmanager
def agent_lock(
    agent_dir: Path | str,
    *,
    name: str = "agent",
    timeout: float = 30.0,
) -> Iterator[None]:
    """Эксклюзивная блокировка: os.open(O_CREAT|O_EXCL|O_WRONLY), в файл пишется PID.

    Если файл есть и PID мёртв (ProcessLookupError / OSError ESRCH) — удаляем и
    повторяем. Пауза 0.05 с между попытками. По таймауту — TimeoutError с путём.
    В finally закрываем fd и снимаем только свой файл (сверка PID).
    Каталог agent_dir создаётся при необходимости.
    """
    root = Path(agent_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = lock_path(root, name).resolve()
    my_pid = os.getpid()
    deadline = time.monotonic() + float(timeout)
    fd: int | None = None
    owned = False
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    guard = _thread_guard(path)
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not guard.acquire(timeout=max(0.0, remaining)):
        raise TimeoutError(f"таймаут блокировки: {path}")
    try:
        while True:
            try:
                fd = os.open(str(path), flags)
                os.write(fd, f"{my_pid}\n".encode("ascii"))
                owned = True
                break
            except FileExistsError:
                if _stale_lock(path):
                    try:
                        path.unlink()
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"таймаут блокировки: {path}") from None
                time.sleep(_RETRY_SLEEP)
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if owned:
            _unlink_if_owner(path, my_pid)
        guard.release()
