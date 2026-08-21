# -*- coding: utf-8 -*-
"""
JSONL-аудит запросов шлюза. Тело модели не парсим, секреты из заголовков
не пишем. Без корня проекта файл не создаём — шлюз не угадывает cwd.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

REDACT_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
    }
)

EVENTS_NAME = "proxy_events.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def events_path(project_root: Optional[Path]) -> Optional[Path]:
    if project_root is None:
        return None
    return Path(project_root) / ".agent" / EVENTS_NAME


def redact_headers(headers: Mapping[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        if name.lower() in REDACT_HEADERS:
            out[name] = "***"
        else:
            out[name] = str(value)
    return out


def append_event(
    project_root: Optional[Path],
    event: Dict[str, Any],
) -> Optional[Path]:
    path = events_path(project_root)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row.setdefault("ts", _now())
    line = json.dumps(row, ensure_ascii=False)
    # на всякий случай не кладём сырой bearer в строку
    lowered = line.lower()
    if "bearer " in lowered or "x-api-key" in lowered:
        row.pop("headers", None)
        line = json.dumps(row, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path
