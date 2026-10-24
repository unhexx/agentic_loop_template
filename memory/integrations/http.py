# -*- coding: utf-8 -*-
"""JSON-запросы через stdlib urllib. Без сторонних HTTP-клиентов."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional, Tuple

from memory.logutil import get_logger

log = get_logger("memory.integrations")

DEFAULT_TIMEOUT = 15.0


class HttpError(RuntimeError):
    """Ответ не JSON или транспорт упал. Тело в лог не кладём, если похоже на секрет."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    body: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[int, Any]:
    """Возвращает (status, parsed). HTTPError urllib — не исключение, а код+тело."""
    hdrs: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        hdrs.update(dict(headers))
    data: Optional[bytes] = None
    if body is not None:
        if not isinstance(body, (bytes, bytearray)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = bytes(body)
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    raw = ""
    status = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 0)
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
    except urllib.error.URLError as exc:
        raise HttpError(f"transport {type(exc).__name__}", status=None) from exc
    except TimeoutError as exc:
        raise HttpError("timeout", status=None) from exc
    if not raw.strip():
        return status, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Slack incoming webhook отвечает телом "ok", не JSON.
        if status < 400:
            return status, {"ok": True, "raw": raw[:80]}
        raise HttpError("non-json", status=status) from exc
    return status, parsed
