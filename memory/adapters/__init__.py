# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from .mock import MockAdapter


def _adapter_section(config: Dict[str, Any] | None, name: str) -> dict:
    """Resolve adapters.<name> from full project config or supervisor slice."""
    config = config or {}
    sup = config.get("supervisor")
    if isinstance(sup, dict):
        adapters = sup.get("adapters")
        if isinstance(adapters, dict) and name in adapters:
            entry = adapters.get(name)
            return entry if isinstance(entry, dict) else {}
    adapters = config.get("adapters")
    if isinstance(adapters, dict):
        entry = adapters.get(name)
        return entry if isinstance(entry, dict) else {}
    return {}


def get_adapter(name: str, config: Dict[str, Any] | None = None):
    name = (name or "mock").lower()
    if name == "mock":
        return MockAdapter()
    if name == "grok":
        from .grok import GrokAdapter

        return GrokAdapter(_adapter_section(config, "grok"))
    if name == "cursor":
        from .cursor import CursorAdapter

        return CursorAdapter(_adapter_section(config, "cursor"))
    if name == "blackbox":
        from .blackbox import BlackboxAdapter

        return BlackboxAdapter(_adapter_section(config, "blackbox"))
    raise ValueError(f"unknown adapter: {name}")
