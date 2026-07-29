"""Идентификатор workspace и пути к файлам институциональной памяти."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def _run_git(args: list[str], cwd: Path | None = None) -> str | None:
    """Безопасный вызов git; None если недоступен."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None
    except Exception:
        return None


def _canonicalize_remote(url: str) -> str:
    """Приводит remote URL к стабильному ключу (github.com/org/repo)."""
    u = url.strip()
    u = re.sub(r"\.git$", "", u, flags=re.IGNORECASE)

    # git@github.com:org/repo -> github.com/org/repo
    ssh_match = re.match(r"git@([^:]+):(.+)", u)
    if ssh_match:
        host, path = ssh_match.groups()
        return f"{host.lower()}/{path.strip('/')}"

    # https://github.com/org/repo
    https_match = re.match(r"https?://([^/]+)/(.+)", u)
    if https_match:
        host, path = https_match.groups()
        return f"{host.lower()}/{path.strip('/')}"

    return u.lower()


def get_workspace_id(cwd: Path | None = None) -> str:
    """
    Стабильный ID workspace по git remote (или fallback на cwd).

    Формат: <repo-short-name>-<sha256-prefix-12hex>
    Пример: eegent-b5ba3fe3655e
    """
    base = cwd or Path.cwd()
    remote = _run_git(["remote", "get-url", "origin"], cwd=base)
    if not remote:
        remote = _run_git(["rev-parse", "--git-common-dir"], cwd=base)
        if remote:
            remote = f"local-gitdir:{Path(remote).resolve()}"
        else:
            remote = f"local-cwd:{base.resolve()}"

    canonical = _canonicalize_remote(remote)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    short_name = canonical.rstrip("/").split("/")[-1] or "workspace"
    short_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", short_name).strip("-") or "workspace"
    return f"{short_name}-{digest}"


def memory_paths(cwd: Path | None = None) -> dict[str, Any]:
    """Возвращает директорию, файл памяти и lock-файл для текущего workspace."""
    wid = get_workspace_id(cwd=cwd)
    mem_dir = Path.home() / ".grok" / "agentic-loop-memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_file = mem_dir / f"{wid}.md"
    lock_file = mem_dir / f"{wid}.lock"
    return {
        "workspace_id": wid,
        "dir": mem_dir,
        "file": mem_file,
        "lock": lock_file,
    }