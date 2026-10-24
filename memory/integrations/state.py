# -*- coding: utf-8 -*-
"""Идемпотентное соответствие TODO → issue (Linear identifier / Jira key)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from memory.agent_lock import agent_lock
from memory.logutil import get_logger

log = get_logger("memory.integrations")

STATE_NAME = "integrations-issues-state.json"


def state_path(agent_dir: Path) -> Path:
    return Path(agent_dir) / STATE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_state(provider: str, project_id: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "provider": provider,
        "project_id": project_id,
        "team_id": None,
        "done_state_id": None,
        "updated_at": _now(),
        "items": {},
    }


def load_state(
    agent_dir: Path,
    *,
    provider: str,
    project_id: str,
) -> Dict[str, Any]:
    path = state_path(agent_dir)
    if not path.is_file():
        return empty_state(provider, project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        log.warning("integrations state unreadable: %s", exc)
        return empty_state(provider, project_id)
    if not isinstance(data, dict):
        return empty_state(provider, project_id)
    if data.get("provider") != provider or data.get("project_id") != project_id:
        fresh = empty_state(provider, project_id)
        fresh["reset_from"] = {
            "provider": data.get("provider"),
            "project_id": data.get("project_id"),
        }
        return fresh
    items = data.get("items")
    if not isinstance(items, dict):
        data["items"] = {}
    return data


def save_state(agent_dir: Path, data: Dict[str, Any]) -> None:
    path = state_path(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with agent_lock(path.parent, name="integrations"):
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        # небольшой retry на replace — Windows и параллельные тесты
        for attempt in range(3):
            try:
                tmp.replace(path)
                return
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.02)


def get_item(state: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    items = state.get("items") or {}
    row = items.get(item_id)
    return dict(row) if isinstance(row, dict) else None


def put_item(state: Dict[str, Any], item_id: str, row: Dict[str, Any]) -> None:
    items = state.setdefault("items", {})
    if not isinstance(items, dict):
        items = {}
        state["items"] = items
    items[item_id] = dict(row)
