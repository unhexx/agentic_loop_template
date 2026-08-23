# -*- coding: utf-8 -*-
"""Политика прокси: required / preferred / off. Mock и AGENTIX_PROXY=0 — no-op."""

from __future__ import annotations

from pathlib import Path
from typing import FrozenSet, Optional

from memory.proxy.config import effective_mode, load_proxy_config, supervisor_adapter
from memory.proxy.health import local_hops_ok, start_instructions

# Прокси нужен только тем, кто реально ходит в GROK_CLI_CHAT_PROXY_BASE_URL.
PROXY_EXEMPT_ADAPTERS: FrozenSet[str] = frozenset(
    {"mock", "cursor", "claude", "claude-code", "blackbox"}
)


def normalize_frontend(name: Optional[str]) -> str:
    raw = (name or "mock").strip().lower()
    if raw in {"1", "grok"}:
        return "grok"
    if raw in {"2", "cursor"}:
        return "cursor"
    if raw in {"3", "claude", "claude-code"}:
        return "claude"
    if raw in {"4", "blackbox"}:
        return "blackbox"
    return raw or "mock"


class ProxyNotReady(RuntimeError):
    """pxpipe недоступен, а режим required — запрос блокируем."""


def adapter_requires_proxy(adapter_name: str) -> bool:
    name = (adapter_name or "").strip().lower()
    if not name:
        return False
    return name not in PROXY_EXEMPT_ADAPTERS


def assert_ready(
    workdir: Optional[Path] = None,
    adapter_name: Optional[str] = None,
) -> None:
    """
    Перед живым адаптером.

    mock / mode=off — no-op. required — ProxyNotReady, если молчит pxpipe
    или (другой порт) URL, на который ходит Grok. preferred никогда не
    бросает: публичный fallback есть только у уже запущенного шлюза.
    """
    name = normalize_frontend(adapter_name or supervisor_adapter(workdir) or "mock")
    if not adapter_requires_proxy(name):
        return
    cfg = load_proxy_config(workdir)
    mode = effective_mode(cfg)
    if mode == "off":
        return
    ok, probe, hop = local_hops_ok(cfg)
    if ok:
        return
    if mode == "required":
        raise ProxyNotReady(start_instructions(cfg, probe, hop))


def init_should_fail(
    workdir: Optional[Path] = None,
    frontend: Optional[str] = None,
) -> bool:
    """Init падает только для grok + required, если локальный хоп молчит."""
    name = normalize_frontend(frontend or supervisor_adapter(workdir) or "mock")
    if not adapter_requires_proxy(name):
        return False
    cfg = load_proxy_config(workdir)
    if effective_mode(cfg) != "required":
        return False
    ok, _, _ = local_hops_ok(cfg)
    return not ok
