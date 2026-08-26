# -*- coding: utf-8 -*-
"""Раздача кооперативного STOP с хаба на worktree disjoint-потоков.

Читает пути из streams_state.json и stream_leases.json. Нет файлов — только хаб.
Не импортирует supervisor: разводка CLI/дашборда живёт в других PR.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from memory.logutil import get_logger

log = get_logger("memory.stream_stop")

STOP_BODY = "1"
_STREAMS_STATE = "streams_state.json"
_LEASES = "stream_leases.json"


def _agent(hub: Path) -> Path:
    return Path(hub) / ".agent"


def _stop_path(root: Path) -> Path:
    return Path(root) / ".agent" / "STOP"


def _is_fs_root(path: Path) -> bool:
    """True для '/' / 'C:\\' — отравленный JSON не должен писать в корень ФС."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        return resolved == Path(resolved.anchor)
    except (OSError, ValueError):
        return str(resolved) in ("/", "\\")


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("stream_stop: cannot read %s: %s", path, exc)
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("stream_stop: cannot parse %s: %s", path, exc)
        return None


def _normalize_worktree(raw: Any, hub: Path) -> Optional[Path]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = Path(hub) / p
    try:
        p = p.resolve()
    except OSError:
        pass
    if _is_fs_root(p):
        return None
    return p


def _records(container: Any) -> Iterable[Any]:
    if isinstance(container, dict):
        return container.values()
    if isinstance(container, list):
        return container
    return ()


def _worktrees_from_container(container: Any, hub: Path) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for rec in _records(container):
        if not isinstance(rec, dict):
            continue
        p = _normalize_worktree(rec.get("worktree"), hub)
        if p is None:
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def stream_worktrees_from_hub(hub: Path) -> list[Path]:
    """Пути worktree из streams_state.json и stream_leases.json. Нет файлов → []."""
    hub = Path(hub).expanduser().resolve()
    agent = _agent(hub)
    seen: set[str] = set()
    out: list[Path] = []
    for filename, key in ((_STREAMS_STATE, "streams"), (_LEASES, "leases")):
        data = _load_json(agent / filename)
        if not isinstance(data, dict):
            continue
        for p in _worktrees_from_container(data.get(key), hub):
            k = str(p)
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
    return out


def _write_stop(root: Path) -> Path:
    agent = Path(root) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "STOP"
    path.write_text(STOP_BODY, encoding="utf-8")
    return path


def _unlink_stop(root: Path) -> bool:
    path = _stop_path(root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("clear_fanout failed for %s: %s", path, exc)
        return False


def _iter_targets(hub: Path) -> list[Path]:
    """Хаб первым, затем уникальные worktree (тот же путь в state и leases — один раз)."""
    hub = Path(hub).expanduser().resolve()
    seen = {str(hub)}
    targets = [hub]
    for wt in stream_worktrees_from_hub(hub):
        key = str(wt)
        if key in seen:
            continue
        seen.add(key)
        targets.append(wt)
    return targets


def fanout_stop(hub: Path) -> list[Path]:
    """Пишет hub/.agent/STOP и STOP каждого известного worktree (байты ``1``)."""
    hub = Path(hub).expanduser().resolve()
    written: list[Path] = []
    for root in _iter_targets(hub):
        if root != hub and not root.is_dir():
            log.warning("STOP fan-out skip missing worktree %s", root)
            continue
        try:
            written.append(_write_stop(root))
        except OSError as exc:
            log.warning("STOP fan-out failed for %s: %s", root, exc)
    log.info(
        "STOP fan-out: wrote %d files: %s",
        len(written),
        [str(p) for p in written],
    )
    return written


def clear_fanout(hub: Path) -> int:
    """Снимает STOP на хабе и известных worktree. Возвращает число удалённых файлов."""
    hub = Path(hub).expanduser().resolve()
    removed = 0
    for root in _iter_targets(hub):
        if _unlink_stop(root):
            removed += 1
    return removed
