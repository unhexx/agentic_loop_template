# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from .mock import MockAdapter


def get_adapter(name: str, config: Dict[str, Any] | None = None):
    name = (name or "mock").lower()
    if name == "mock":
        return MockAdapter()
    if name == "grok":
        try:
            from .grok import GrokAdapter
        except ImportError as e:
            raise ValueError(f"adapter 'grok' is not available: {e}") from e
        return GrokAdapter((config or {}).get("adapters", {}).get("grok", {}))
    if name == "cursor":
        try:
            from .cursor import CursorAdapter
        except ImportError as e:
            raise ValueError(f"adapter 'cursor' is not available: {e}") from e
        return CursorAdapter((config or {}).get("adapters", {}).get("cursor", {}))
    if name == "blackbox":
        try:
            from .blackbox import BlackboxAdapter
        except ImportError as e:
            raise ValueError(f"adapter 'blackbox' is not available: {e}") from e
        return BlackboxAdapter((config or {}).get("adapters", {}).get("blackbox", {}))
    raise ValueError(f"unknown adapter: {name}")
