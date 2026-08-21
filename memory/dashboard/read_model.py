# -*- coding: utf-8 -*-
"""Чтение SSOT дашборда по явным путям workdir/.agent, без глобалей модулей."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set


# last_handoff пишется write_text, не tmp+replace — порванный JSON реален.
TORN_RETRIES = 3
TORN_RETRY_S = 0.020
# 2 * 20 с тик пульса + запас; файл опционален (может отсутствовать).
HEARTBEAT_FRESH_S = 45.0
_SSOT_JSON = frozenset({"LOOP_STATE.json", "last_handoff.json"})


class DashboardStore:
    """Проекция LOOP_STATE / last_handoff / STOP. Не раннер, не chdir."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir).resolve()
        self.agent = self.workdir / ".agent"
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._stale: Set[str] = set()

    def loop_state(self) -> Dict[str, Any]:
        return self._read_json(
            self.agent / "LOOP_STATE.json",
            default={"status": "missing"},
        )

    def last_handoff(self) -> Optional[Dict[str, Any]]:
        p = self.agent / "last_handoff.json"
        if not p.is_file():
            self._stale.discard(p.name)
            return None
        data = self._read_json(p, default=None)
        return data if isinstance(data, dict) else None

    def stop_present(self) -> bool:
        return (self.agent / "STOP").is_file()

    def heartbeat(self) -> Dict[str, Any]:
        """Пульс процесса, не статус цикла. Нет файла — liveness unknown."""
        unknown = {"status": "unknown", "label": "liveness unknown"}
        p = self.agent / "supervisor.heartbeat"
        if not p.is_file():
            return dict(unknown)
        data = self._read_json(p, default=None)
        if not isinstance(data, dict):
            return dict(unknown)
        ts_raw = data.get("ts")
        age = _age_s(ts_raw)
        pid = data.get("pid")
        role = data.get("role")
        if age is None:
            return {
                "status": "unknown",
                "label": "liveness unknown",
                "pid": pid,
                "role": role,
                "ts": ts_raw,
            }
        if age <= HEARTBEAT_FRESH_S:
            return {
                "status": "running",
                "label": f"Supervisor running ({pid}, {role})",
                "pid": pid,
                "role": role,
                "ts": ts_raw,
            }
        return {
            "status": "stale",
            "label": "not running / stale",
            "pid": pid,
            "role": role,
            "ts": ts_raw,
        }

    def snapshot(self) -> Dict[str, Any]:
        """Полоса Loop + три ключа last_handoff_*. Не надмножество state.snapshot()."""
        st = self.loop_state()
        ho = self.last_handoff() or {}
        invest = st.get("open_invest") or []
        if not isinstance(invest, list):
            invest = []
        deltas = st.get("recent_deltas") or []
        if not isinstance(deltas, list):
            deltas = []
        git_sync = st.get("git_sync") or {}
        if not isinstance(git_sync, dict):
            git_sync = {}
        gss = ho.get("git_sync_status") or {}
        if not isinstance(gss, dict):
            gss = {}
        metrics = ho.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        return {
            "state": {
                "cycle_number": st.get("cycle_number"),
                "active_role": st.get("active_role"),
                "status": st.get("status"),
                "branch": st.get("branch"),
                "last_commit": st.get("last_commit"),
                "git_sync": git_sync,
                "open_invest": invest[:20],
                "recent_deltas": deltas[-5:],
                "updated_at": st.get("updated_at"),
                "notes": st.get("notes"),
            },
            "last_handoff_summary": ho.get("summary"),
            "last_handoff_status": ho.get("status"),
            "last_handoff_role": ho.get("role"),
            "last_handoff_to": ho.get("handoff_to"),
            # поля карточки — чтобы GET / и /partials/handoff-card не читали файл второй раз
            "last_handoff_confidence": ho.get("confidence"),
            "last_handoff_git_sync": gss,
            "last_handoff_metrics": metrics,
            "stop": self.stop_present(),
            "heartbeat": self.heartbeat(),
            "stale": bool(self._stale.intersection(_SSOT_JSON)),
        }

    def _read_json(self, path: Path, default: Any = None) -> Any:
        key = path.name
        if not path.is_file():
            self._stale.discard(key)
            return _copy_default(default)
        # Повтор 3×20мс только на холодном порванном чтении: last-good уже есть —
        # sleep на event loop не помогает, запись уже прошла или файл мёртвый.
        attempts = 1 if key in self._cache else TORN_RETRIES + 1
        for attempt in range(attempts):
            try:
                text = path.read_text(encoding="utf-8")
                if not text.strip():
                    raise json.JSONDecodeError("empty", text, 0)
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("not object", text, 0)
                self._cache[key] = data
                self._stale.discard(key)
                return dict(data)
            except (json.JSONDecodeError, OSError, UnicodeError):
                if attempt + 1 < attempts:
                    time.sleep(TORN_RETRY_S)
        if key in self._cache:
            self._stale.add(key)
            return dict(self._cache[key])
        self._stale.add(key)
        return _copy_default(default)


def _copy_default(default: Any) -> Any:
    if default is None:
        return None
    if isinstance(default, dict):
        return dict(default)
    return default


def _parse_ts(raw: object) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_s(raw: object) -> Optional[float]:
    dt = _parse_ts(raw)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()
