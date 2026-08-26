# -*- coding: utf-8 -*-
"""Git-операции потоков: стабильный integration-worktree, merge и push.

Путь wt_base / sanitize(ветка), не {cycle}-integration.
ensure бросает IntegrationWorktreeError; merge/push возвращают dict.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MERGE_TIMEOUT_S = 120
PUSH_TIMEOUT_S = 120
_ABORT_TIMEOUT_S = 15
_PROTECTED_BRANCHES = frozenset({"main", "master"})
_DEFAULT_WT_DIR = "agentic-loop-worktrees"
_UNSAFE_IN_PATH = re.compile(r"[^A-Za-z0-9._-]")
_KILL_GRACE_S = 2.0


class IntegrationWorktreeError(RuntimeError):
    """Подготовка integration-worktree не удалась; HEAD хаба не двигаем."""


def _sanitize_branch(name: str) -> str:
    """В путь worktree: «/» и прочие символы → «-», как feature-integration-parallel.

    «.»/«..» оставляем fallback, иначе dest = wt_base / «..» выходит из wt_base.
    """
    cleaned = _UNSAFE_IN_PATH.sub("-", name or "")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned or cleaned in {".", ".."}:
        return "integration"
    return cleaned


def _is_git_checkout(path: Path) -> bool:
    return (path / ".git").exists()


def _git_env() -> Dict[str, str]:
    env = os.environ.copy()
    # иначе «нет слияния / MERGE_HEAD» на русской локали не совпадёт с разбором abort
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env.setdefault("GIT_EDITOR", "true")
    env.setdefault("GIT_MERGE_AUTOEDIT", "no")
    return env


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=_git_env(),
    )


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


def _kill_git_process(proc: subprocess.Popen) -> None:
    """Группа git: pgid один раз, SIGTERM, пауза, всегда SIGKILL по тому же pgid, drain PIPE."""
    if sys.platform == "win32":
        if proc.poll() is None:
            _kill_direct(proc)
        _reap(proc)
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        if proc.poll() is None:
            _kill_direct(proc)
        _reap(proc)
        return
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        _wait_exit(proc, _KILL_GRACE_S)
    # лидер мог выйти по SIGTERM — внуков всё равно снимаем по сохранённому pgid
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    _reap(proc)


def _run_git_timed(
    args: List[str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess:
    """git с лимитом; по TimeoutExpired убиваем группу, drain PIPE, затем пробрасываем."""
    env = _git_env()
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": str(cwd),
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(["git", *args], **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_git_process(proc)
        raise
    return subprocess.CompletedProcess(
        ["git", *args],
        proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _abbrev_head(cwd: Path) -> str:
    r = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return (r.stdout or "").strip()


def _is_dirty(cwd: Path) -> bool:
    r = _run_git(["status", "--porcelain"], cwd)
    return bool((r.stdout or "").strip())


def _err_text(proc: subprocess.CompletedProcess, limit: int = 500) -> str:
    return ((proc.stderr or proc.stdout or "")).strip()[:limit]


def _git_dir(cwd: Path) -> Optional[Path]:
    r = _run_git(["rev-parse", "--git-dir"], cwd)
    raw = (r.stdout or "").strip()
    if r.returncode != 0 or not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (cwd / p)


def _unlink_index_lock(cwd: Path) -> None:
    git_dir = _git_dir(cwd)
    if git_dir is None:
        return
    lock = git_dir / "index.lock"
    try:
        if lock.is_file():
            lock.unlink()
    except OSError:
        pass


def _nothing_to_abort(proc: subprocess.CompletedProcess) -> bool:
    if proc.returncode == 0:
        return True
    text = (proc.stderr or proc.stdout or "").lower()
    return (
        "no merge to abort" in text
        or "merge_head missing" in text
        or "нет слияния" in text
        or "отсутствует файл merge_head" in text
    )


def _abort_merge(cwd: Path) -> Optional[str]:
    """merge --abort с коротким таймаутом; после SIGKILL часто остаётся index.lock."""
    last_err = "merge --abort timeout"
    for attempt in range(2):
        if attempt == 1:
            _unlink_index_lock(cwd)
        try:
            aborted = _run_git_timed(
                ["merge", "--abort"], cwd=cwd, timeout=_ABORT_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            last_err = "merge --abort timeout"
            continue
        if _nothing_to_abort(aborted):
            return None
        last_err = _err_text(aborted)
    return last_err


def _checkout_path_of_branch(repo: Path, branch: str) -> Optional[Path]:
    r = _run_git(["worktree", "list", "--porcelain"], cwd=repo)
    if r.returncode != 0:
        return None
    current: Optional[str] = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("worktree "):
            current = line[9:]
        elif line.startswith("branch ") and current:
            ref = line[7:]
            short = ref[11:] if ref.startswith("refs/heads/") else ref
            if short == branch:
                return Path(current)
    return None


def _is_protected(branch: str, extra: str = "") -> bool:
    name = (branch or "").strip()
    if name in _PROTECTED_BRANCHES:
        return True
    extra_n = (extra or "").strip()
    return bool(extra_n) and name == extra_n


def _recover_hub_if_on_integration(
    repo_root: Path,
    integration_branch: str,
    main_branch: str,
) -> None:
    """Единственный разрешённый checkout хаба: 3.10.1 leftover на integration-ветке."""
    if _abbrev_head(repo_root) != integration_branch:
        return
    if _is_dirty(repo_root):
        raise IntegrationWorktreeError(
            f"hub dirty on {integration_branch}; commit or stash, then checkout {main_branch}"
        )
    c = _run_git(["checkout", main_branch], cwd=repo_root)
    if c.returncode != 0:
        raise IntegrationWorktreeError(
            f"не удалось вернуть хаб на {main_branch}: {_err_text(c)}"
        )
    log.info(
        "хаб был на %s без локальных правок — HEAD возвращён на %s",
        integration_branch,
        main_branch,
    )


def _raise_add_failed(
    repo_root: Path,
    integration_branch: str,
    dest: Path,
    failed: subprocess.CompletedProcess,
) -> None:
    if dest.exists() and not _is_git_checkout(dest):
        try:
            if dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()
        except OSError:
            pass
    other = _checkout_path_of_branch(repo_root, integration_branch)
    if other is not None:
        raise IntegrationWorktreeError(
            f"{integration_branch} already checked out at {other}"
        )
    err = _err_text(failed)
    if "already checked out" in err.lower():
        raise IntegrationWorktreeError(
            f"{integration_branch} already checked out at unknown ({err})"
        )
    raise IntegrationWorktreeError(
        f"git worktree add failed for {integration_branch}: {err}"
    )


def ensure_integration_worktree(
    repo_root: Path,
    *,
    integration_branch: str,
    main_branch: str = "main",
    wt_base: Optional[Path] = None,
) -> Path:
    """Стабильный worktree для integration_branch. В штатном режиме HEAD хаба не трогаем.

    При ошибке — IntegrationWorktreeError, не dict и не Terminal.
    """
    repo_root = Path(repo_root).resolve()
    if wt_base is None:
        wt_base = repo_root.parent / _DEFAULT_WT_DIR
    wt_base = Path(wt_base)
    dest = wt_base / _sanitize_branch(integration_branch)

    _recover_hub_if_on_integration(repo_root, integration_branch, main_branch)

    if dest.exists() and _is_git_checkout(dest):
        return dest.resolve()

    wt_base.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.is_dir() and not any(dest.iterdir()):
            dest.rmdir()
        else:
            raise IntegrationWorktreeError(
                f"worktree path exists but is not a git worktree: {dest}"
            )

    # ветка может уже быть (leftover 3.10.1) — тогда fallback без -b
    added = _run_git(
        ["worktree", "add", "-b", integration_branch, str(dest), main_branch],
        cwd=repo_root,
    )
    if added.returncode != 0:
        added = _run_git(
            ["worktree", "add", str(dest), integration_branch],
            cwd=repo_root,
        )
        if added.returncode != 0:
            _raise_add_failed(repo_root, integration_branch, dest, added)

    return dest.resolve()


def merge_stream_branch(
    *,
    integration_workdir: Path,
    stream_branch: str,
    integration_branch: str,
    main_branch: str = "main",
) -> dict:
    """Локальный merge --no-ff потока в integration. cwd — worktree, не хаб.

    Конфликт и таймаут 120с → git merge --abort в этом worktree.
    """
    wd = Path(integration_workdir)
    if _is_protected(integration_branch, main_branch):
        return {"ok": False, "error": "never merge into main"}

    head = _abbrev_head(wd)
    if head != integration_branch:
        c = _run_git(["checkout", integration_branch], cwd=wd)
        if c.returncode != 0:
            return {"ok": False, "error": _err_text(c)}

    try:
        merged = _run_git_timed(
            [
                "merge",
                "--no-ff",
                stream_branch,
                "-m",
                f"Слил ветку потока {stream_branch}",
            ],
            cwd=wd,
            timeout=MERGE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        abort_err = _abort_merge(wd)
        err = "merge timeout"
        if abort_err:
            err = f"{err}; abort failed: {abort_err}"
        return {"ok": False, "error": err}

    if merged.returncode != 0:
        abort_err = _abort_merge(wd)
        err = _err_text(merged)
        if abort_err:
            err = f"{err}; abort failed: {abort_err}"
        return {"ok": False, "error": err}
    return {"ok": True, "branch": integration_branch}


def push_branch(
    workdir: Path,
    *,
    branch: str,
    remote: str = "origin",
) -> dict:
    """git push -u remote branch. main/master не пушим — это ручной шаг после ревью."""
    name = (branch or "").strip()
    if name in _PROTECTED_BRANCHES:
        return {"ok": False, "error": "refusing to push protected branch"}
    wd = Path(workdir)
    try:
        pushed = _run_git_timed(
            ["push", "-u", remote, name],
            cwd=wd,
            timeout=PUSH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "push timeout"}
    if pushed.returncode != 0:
        return {"ok": False, "error": _err_text(pushed)}
    return {"ok": True, "branch": name, "remote": remote}
