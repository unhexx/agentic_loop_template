# -*- coding: utf-8 -*-
"""
Пробы pxpipe и (позже) шлюза. TCP достаточно: HTTP-парсинг не нужен.

Тесты поднимают локальный слушатель — живой pxpipe в CI не требуется.
"""

from __future__ import annotations

import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from memory.proxy.config import (
    DEFAULT_GATEWAY_BASE,
    DEFAULT_PXPIPE_BASE,
    GATEWAY_PORT,
    PXPIPE_PORT,
    load_proxy_config,
    split_host_port,
)


def tcp_ok(host: str, port: int, timeout: float = 1.0) -> bool:
    """Короткое TCP-подключение. Закрытый порт → False, без исключения."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_url(url: str, default_port: int, timeout: float = 1.0) -> Dict[str, Any]:
    host, port = split_host_port(url, default_port)
    ok = tcp_ok(host, port, timeout=timeout)
    return {
        "ok": ok,
        "host": host,
        "port": port,
        "url": url,
    }


def probe_pxpipe(
    cfg: Optional[Dict[str, Any]] = None, timeout: float = 1.0
) -> Dict[str, Any]:
    cfg = cfg or load_proxy_config()
    url = str(cfg.get("pxpipe_base") or DEFAULT_PXPIPE_BASE)
    return _probe_url(url, PXPIPE_PORT, timeout=timeout)


def probe_gateway(
    cfg: Optional[Dict[str, Any]] = None, timeout: float = 1.0
) -> Dict[str, Any]:
    cfg = cfg or load_proxy_config()
    url = str(cfg.get("gateway_base") or DEFAULT_GATEWAY_BASE)
    return _probe_url(url, GATEWAY_PORT, timeout=timeout)


def start_instructions(
    cfg: Optional[Dict[str, Any]] = None,
    probe: Optional[Dict[str, Any]] = None,
) -> str:
    cfg = cfg or load_proxy_config()
    base = cfg.get("pxpipe_base") or DEFAULT_PXPIPE_BASE
    host = (probe or {}).get("host") or urlparse(str(base)).hostname or "127.0.0.1"
    port = (probe or {}).get("port") or PXPIPE_PORT
    return (
        f"pxpipe не слушает {host}:{port} (proxy.mode=required).\n"
        "Запуск:\n"
        "  systemctl --user enable --now pxpipe.service\n"
        "  # или: npx pxpipe-proxy\n"
        "  # шаблон юнита: scripts/systemd/pxpipe.service.example\n"
        "Явный отказ: export AGENTIX_PROXY=0  (или proxy.mode=off).\n"
        "Не направляйте GROK_CLI_CHAT_PROXY_BASE_URL на публичный cli-chat-proxy "
        "в обход локального прокси."
    )


def health_report(
    workdir=None,
    *,
    strict: bool = False,
    frontend: Optional[str] = None,
) -> Dict[str, Any]:
    """Сводка для CLI и Init: pxpipe + шлюз + режим."""
    from memory.proxy.config import effective_mode, supervisor_adapter
    from memory.proxy.policy import adapter_requires_proxy, normalize_frontend

    cfg = load_proxy_config(workdir)
    px = probe_pxpipe(cfg)
    gw = probe_gateway(cfg)
    mode = effective_mode(cfg)
    adapter = normalize_frontend(frontend or supervisor_adapter(workdir) or "mock")
    exempt = not adapter_requires_proxy(adapter)
    ok = bool(px.get("ok")) or mode == "off" or exempt
    if strict and mode != "off":
        ok = bool(px.get("ok"))
    report: Dict[str, Any] = {
        "ok": ok,
        "mode": mode,
        "adapter": adapter,
        "adapter_exempt": exempt,
        "pxpipe_ok": bool(px.get("ok")),
        "pxpipe": px,
        "gateway_ok": bool(gw.get("ok")),
        "gateway": gw,
        "pxpipe_base": cfg.get("pxpipe_base"),
        "chat_proxy": cfg.get("chat_proxy"),
        "strict": strict,
    }
    if not px.get("ok") and mode == "required" and not exempt:
        report["instructions"] = start_instructions(cfg, px)
    return report
