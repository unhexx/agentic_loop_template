# -*- coding: utf-8 -*-
"""Раздача кооперативного STOP с хаба на worktree disjoint-потоков.

Читает пути из streams_state.json и stream_leases.json. Нет файлов — только хаб.
Не импортирует supervisor: разводка CLI/дашборда живёт в других PR.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from memory.logutil import get_logger

log = get_logger("memory.stream_stop")

STOP_BODY = "1"
_STREAMS_STATE = "streams_state.json"
_LEASES = "stream_leases.json"
_DEFAULT_WT_DIR = "agentic-loop-worktrees"


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
        # UnicodeError: бинарный/latin-1 мусор не должен валить весь fan-out.
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        log.warning("stream_stop: cannot read %s: %s", path, exc)
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("stream_stop: cannot parse %s: %s", path, exc)
        return None


def _default_wt_base(hub: Path) -> Path:
    return Path(hub).resolve().parent / _DEFAULT_WT_DIR


def _allowed_roots(hub: Path, extra_roots: Sequence[Path] = ()) -> list[Path]:
    hub_r = Path(hub).resolve()
    roots = [hub_r, _default_wt_base(hub_r)]
    for raw in extra_roots:
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if _is_fs_root(p):
            continue
        roots.append(p)
    return roots


def _is_allowed(path: Path, hub: Path, extra_roots: Sequence[Path] = ()) -> bool:
    """Хаб, внутри хаба, default sibling wt_base, либо extra_roots (wt_base из конфига)."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if _is_fs_root(resolved):
        return False
    for root in _allowed_roots(hub, extra_roots):
        try:
            if resolved == root or resolved.is_relative_to(root):
                return True
        except (ValueError, TypeError, OSError):
            continue
    return False


def _normalize_worktree(
    raw: Any,
    hub: Path,
    extra_roots: Sequence[Path] = (),
) -> Optional[Path]:
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
    if not _is_allowed(p, hub, extra_roots):
        log.warning("STOP fan-out skip worktree outside allowlist %s", p)
        return None
    return p


def _records(container: Any) -> Iterable[Any]:
    if isinstance(container, dict):
        return container.values()
    if isinstance(container, list):
        return container
    return ()


def _worktrees_from_container(
    container: Any,
    hub: Path,
    extra_roots: Sequence[Path] = (),
) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for rec in _records(container):
        if not isinstance(rec, dict):
            continue
        p = _normalize_worktree(rec.get("worktree"), hub, extra_roots)
        if p is None:
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def stream_worktrees_from_hub(
    hub: Path,
    *,
    extra_roots: Sequence[Path] = (),
) -> list[Path]:
    """Пути worktree из streams_state.json и stream_leases.json. Нет файлов → []."""
    hub = Path(hub).expanduser().resolve()
    agent = _agent(hub)
    seen: set[str] = set()
    out: list[Path] = []
    for filename, key in ((_STREAMS_STATE, "streams"), (_LEASES, "leases")):
        data = _load_json(agent / filename)
        if not isinstance(data, dict):
            continue
        for p in _worktrees_from_container(data.get(key), hub, extra_roots):
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


def _discover_worktrees(hub: Path, extra_roots: Sequence[Path] = ()) -> list[Path]:
    """Сбой чтения JSON не должен блокировать хаб: тогда список пустой."""
    try:
        return stream_worktrees_from_hub(hub, extra_roots=extra_roots)
    except Exception as exc:
        log.warning("STOP fan-out discovery failed: %s", exc)
        return []


def fanout_stop(
    hub: Path,
    *,
    extra_roots: Sequence[Path] = (),
) -> list[Path]:
    """Пишет hub/.agent/STOP и STOP каждого известного worktree (байты ``1``).

    Хаб пишется первым, до разбора JSON: битый state не отменяет STOP на хабе.
    """
    hub = Path(hub).expanduser().resolve()
    written: list[Path] = []
    try:
        written.append(_write_stop(hub))
    except OSError as exc:
        log.warning("STOP fan-out failed for %s: %s", hub, exc)

    seen = {str(hub)}
    for root in _discover_worktrees(hub, extra_roots):
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if not root.is_dir():
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


def clear_fanout(
    hub: Path,
    *,
    extra_roots: Sequence[Path] = (),
) -> int:
    """Снимает STOP на хабе и известных worktree. Возвращает число удалённых файлов."""
    hub = Path(hub).expanduser().resolve()
    removed = 0
    seen = {str(hub)}
    if _unlink_stop(hub):
        removed += 1
    for root in _discover_worktrees(hub, extra_roots):
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if _unlink_stop(root):
            removed += 1
    return removed
