# -*- coding: utf-8 -*-
"""
Политика прокси: required / preferred / off.

Живые адаптеры при mode=required не стартуют без живого pxpipe.
Mock и явный AGENTIX_PROXY=0 проверку пропускают.
"""

from __future__ import annotations

from pathlib import Path
from typing import FrozenSet, Optional

from memory.proxy.config import effective_mode, load_proxy_config, supervisor_adapter
from memory.proxy.health import probe_pxpipe, start_instructions

# Адаптеры, которым живой прокси не нужен (CI, офлайн-цикл).
PROXY_EXEMPT_ADAPTERS: FrozenSet[str] = frozenset({"mock"})


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
    Перед вызовом живого адаптера.

    mock — всегда no-op. mode=off — no-op. required + мёртвый pxpipe —
    ProxyNotReady. preferred — только если совсем нет запасного URL,
    иначе пропускаем (предупреждение печатает health CLI).
    """
    name = (adapter_name or supervisor_adapter(workdir) or "mock").strip().lower()
    if not adapter_requires_proxy(name):
        return
    cfg = load_proxy_config(workdir)
    mode = effective_mode(cfg)
    if mode == "off":
        return
    probe = probe_pxpipe(cfg)
    if probe.get("ok"):
        return
    if mode == "required":
        raise ProxyNotReady(start_instructions(cfg, probe))
    # preferred: не валимся — вызывающий может уйти в fallback.


def init_should_fail(
    workdir: Optional[Path] = None,
    frontend: Optional[str] = None,
) -> bool:
    """
    Init падает только если выбран живой фронтенд, mode=required
    и pxpipe молчит. Mock / Blackbox / AGENTIX_PROXY=0 — нет.
    """
    name = (frontend or supervisor_adapter(workdir) or "mock").strip().lower()
    if name in {"1", "grok"}:
        name = "grok"
    elif name in {"2", "cursor"}:
        name = "cursor"
    elif name in {"3", "claude", "claude-code"}:
        name = "cursor"
    elif name in {"4", "blackbox"}:
        name = "blackbox"
    if not adapter_requires_proxy(name):
        return False
    cfg = load_proxy_config(workdir)
    if effective_mode(cfg) != "required":
        return False
    return not bool(probe_pxpipe(cfg).get("ok"))
