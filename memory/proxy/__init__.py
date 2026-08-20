# -*- coding: utf-8 -*-
"""
Прокси запросов Agentix: политика, health, (позже) шлюз перед pxpipe.

Пакет stdlib-only. Сам pxpipe в репозиторий не кладём.
"""

from __future__ import annotations

from memory.proxy.config import (
    DEFAULT_INSTALL_CHAT_PROXY,
    DEFAULT_PXPIPE_BASE,
    load_proxy_config,
    effective_mode,
)
from memory.proxy.health import health_report, probe_pxpipe, tcp_ok
from memory.proxy.policy import (
    PROXY_EXEMPT_ADAPTERS,
    ProxyNotReady,
    adapter_requires_proxy,
    assert_ready,
)

__all__ = [
    "DEFAULT_INSTALL_CHAT_PROXY",
    "DEFAULT_PXPIPE_BASE",
    "PROXY_EXEMPT_ADAPTERS",
    "ProxyNotReady",
    "adapter_requires_proxy",
    "assert_ready",
    "effective_mode",
    "health_report",
    "load_proxy_config",
    "probe_pxpipe",
    "tcp_ok",
]
