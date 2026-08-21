# -*- coding: utf-8 -*-
"""Опрос mtime файлов .agent и сигналы в broadcaster. LOOP_STATE.md не смотрим."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from memory.dashboard.broadcaster import WSBroadcaster

# JSON — SSOT; md-проекцию не трогаем, иначе debounce «оправдали» бы лишним файлом.
WATCHED_FILES: Tuple[str, ...] = (
    "LOOP_STATE.json",
    "last_handoff.json",
    "STOP",
    "AUDIT_LOG.json",
    "PERFORMANCE_LEDGER.json",
    "PLAYBOOKS.json",
    "HUB_INDEX.json",
    "QUESTIONS_POOL.json",
    "PLAN.md",
    "TODO.md",
    "supervisor.heartbeat",
)

POLL_INTERVAL_S = 1.0
DEBOUNCE_S = 0.150

_Stat = Tuple[bool, int, int]  # exists, mtime_ns, size


class Watcher:
    """Раз в секунду stat; 150 мс схлопывают пачку записей в .agent."""

    def __init__(
        self,
        workdir: Path,
        broadcaster: WSBroadcaster,
        *,
        poll_s: Optional[float] = None,
        debounce_s: Optional[float] = None,
        watched: Optional[Iterable[str]] = None,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.agent = self.workdir / ".agent"
        self.broadcaster = broadcaster
        self.poll_s = POLL_INTERVAL_S if poll_s is None else poll_s
        self.debounce_s = DEBOUNCE_S if debounce_s is None else debounce_s
        self.watched: Tuple[str, ...] = tuple(watched) if watched is not None else WATCHED_FILES
        self._prev: Dict[str, _Stat] = {}

    def prime(self) -> None:
        """Запомнить текущие stat без рассылки — иначе первый тик взорвёт UI."""
        self._prev = self._scan()

    async def run(self) -> None:
        self.prime()
        try:
            while True:
                await asyncio.sleep(self.poll_s)
                await self.tick()
        except asyncio.CancelledError:
            raise

    async def tick(self) -> int:
        """Один проход: при грязи — debounce, потом сигналы. Возвращает число событий."""
        snap = self._scan()
        changed = _changed_names(self._prev, snap)
        if not changed:
            self._prev = snap
            return 0
        if self.debounce_s > 0:
            await asyncio.sleep(self.debounce_s)
            snap = self._scan()
            changed = _changed_names(self._prev, snap)
        self._prev = snap
        n = 0
        for name in self.watched:
            if name not in changed:
                continue
            msg = self._event(name, snap.get(name, (False, 0, 0)))
            if msg is None:
                continue
            await self.broadcaster.broadcast(msg)
            n += 1
        return n

    def _scan(self) -> Dict[str, _Stat]:
        out: Dict[str, _Stat] = {}
        for name in self.watched:
            out[name] = _stat(self.agent / name)
        return out

    def _event(self, name: str, st: _Stat) -> Optional[dict[str, Any]]:
        exists, _, _ = st
        if name == "LOOP_STATE.json":
            data = self._peek_json(name)
            return {
                "type": "state:changed",
                "path": name,
                "loop_status": data.get("status"),
                "role": data.get("active_role"),
                "cycle": data.get("cycle_number"),
            }
        if name == "last_handoff.json":
            data = self._peek_json(name)
            return {
                "type": "handoff:changed",
                "path": name,
                "handoff_status": data.get("status"),
                "role": data.get("role"),
            }
        if name == "STOP":
            return {"type": "stop:set" if exists else "stop:cleared"}
        if name == "AUDIT_LOG.json":
            msg: dict[str, Any] = {"type": "audit:appended"}
            data = self._peek_json(name)
            entries = data.get("entries") if isinstance(data.get("entries"), list) else []
            if entries:
                last = entries[-1]
                if isinstance(last, dict) and last.get("id"):
                    msg["id"] = last["id"]
            return msg
        if name == "QUESTIONS_POOL.json":
            return {"type": "questions:changed", "path": name}
        if name == "PERFORMANCE_LEDGER.json":
            return {"type": "ledger:changed"}
        if name in {"PLAYBOOKS.json", "HUB_INDEX.json"}:
            return {"type": "playbooks:changed", "path": name}
        if name in {"PLAN.md", "TODO.md"}:
            return {"type": "plan:changed", "path": name}
        if name == "supervisor.heartbeat":
            return {"type": "liveness:changed", "path": name}
        return {"type": "file:changed", "path": name}

    def _peek_json(self, name: str) -> dict[str, Any]:
        p = self.agent / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def _stat(path: Path) -> _Stat:
    try:
        st = path.stat()
        return (True, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (False, 0, 0)


def _changed_names(old: Mapping[str, _Stat], new: Mapping[str, _Stat]) -> set[str]:
    names = set(old) | set(new)
    return {n for n in names if old.get(n) != new.get(n)}
