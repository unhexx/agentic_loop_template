# -*- coding: utf-8 -*-
"""Конфиг прокси: env бьёт файл; нет ключа proxy → mode=required."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from memory.logutil import get_logger

log = get_logger("memory.proxy.config")


MODES = ("required", "preferred", "off")

PXPIPE_HOST = "127.0.0.1"
PXPIPE_PORT = 8100
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 8110

DEFAULT_PXPIPE_BASE = f"http://{PXPIPE_HOST}:{PXPIPE_PORT}"
DEFAULT_GATEWAY_BASE = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
DEFAULT_UPSTREAM_FALLBACK = "https://cli-chat-proxy.grok.com"

# Init и Grok CLI ходят на шлюз :8110; шлюз сам фронтит pxpipe :8100.
CHAT_PROXY_VIA_PXPIPE = f"{DEFAULT_PXPIPE_BASE}/v1"
CHAT_PROXY_VIA_GATEWAY = f"{DEFAULT_GATEWAY_BASE}/v1"
DEFAULT_INSTALL_CHAT_PROXY = CHAT_PROXY_VIA_GATEWAY

DEFAULT_MODE = "required"
DEFAULT_TIMEOUT_S = 900
DEFAULT_BODY_BUDGET_TOKENS = 24000
DEFAULT_KEEP_RECENT_TURNS = 2


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def is_public_upstream(url: str) -> bool:
    raw = (url or "").strip().lower()
    return "cli-chat-proxy.grok.com" in raw or "api.x.ai" in raw


def project_root(workdir: Optional[Path] = None) -> Path:
    """Явный workdir важнее AGENTIX_PROJECT_ROOT (тот — для шлюза без cwd)."""
    if workdir is not None:
        return Path(workdir)
    env = os.environ.get("AGENTIX_PROJECT_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.cwd()


def load_project_config(workdir: Optional[Path] = None) -> Dict[str, Any]:
    """Читаем .agent/project_config.json, иначе example, иначе {}."""
    root = project_root(workdir)
    for name in ("project_config.json", "project_config.example.json"):
        p = root / ".agent" / name
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                log.warning("load_project_config failed for %s: %s", p, exc)
    return {}


def _file_proxy_section(workdir: Optional[Path] = None) -> Dict[str, Any]:
    cfg = load_project_config(workdir)
    section = cfg.get("proxy")
    return dict(section) if isinstance(section, dict) else {}


def _env_mode_override() -> Optional[str]:
    raw = os.environ.get("AGENTIX_PROXY", "").strip().lower()
    if raw in {"0", "off", "false", "no"}:
        return "off"
    mode = os.environ.get("AGENTIX_PROXY_MODE", "").strip().lower()
    if mode in MODES:
        return mode
    return None


def effective_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Итоговый режим: env бьёт файл, неизвестное значение → required."""
    env_mode = _env_mode_override()
    if env_mode is not None:
        return env_mode
    file_mode = (cfg or {}).get("mode")
    if isinstance(file_mode, str) and file_mode.strip().lower() in MODES:
        return file_mode.strip().lower()
    return DEFAULT_MODE


def _url(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")
    return default


def load_proxy_config(workdir: Optional[Path] = None) -> Dict[str, Any]:
    """Собранный конфиг прокси со всеми дефолтами."""
    section = _file_proxy_section(workdir)
    plugins = section.get("plugins") if isinstance(section.get("plugins"), dict) else {}

    mode = effective_mode(section)
    pxpipe_base = _url(
        os.environ.get("AGENTIX_PXPIPE_URL") or section.get("pxpipe_base"),
        DEFAULT_PXPIPE_BASE,
    )
    listen = str(section.get("listen") or f"{GATEWAY_HOST}:{GATEWAY_PORT}")
    gateway_env = os.environ.get("AGENTIX_GATEWAY_URL", "").strip()
    if gateway_env:
        gateway_base = gateway_env.rstrip("/")
    else:
        if "://" in listen:
            gateway_base = listen.rstrip("/")
        else:
            gateway_base = f"http://{listen}"

    default_chat = f"{gateway_base}/v1"
    chat_env = os.environ.get("GROK_CLI_CHAT_PROXY_BASE_URL", "").strip()
    if mode == "required":
        # leftover public URL must not bypass the local hop
        if chat_env and not is_public_upstream(chat_env):
            chat_proxy = chat_env.rstrip("/")
        else:
            chat_proxy = default_chat
    else:
        chat_proxy = chat_env.rstrip("/") if chat_env else default_chat

    cfg = {
        "mode": mode,
        "listen": listen,
        "pxpipe_base": pxpipe_base,
        "gateway_base": gateway_base,
        "chat_proxy": chat_proxy,
        "upstream_fallback": _url(
            section.get("upstream_fallback"), DEFAULT_UPSTREAM_FALLBACK
        ),
        "bind": str(section.get("bind") or "loopback"),
        "timeout_s": _as_int(section.get("timeout_s"), DEFAULT_TIMEOUT_S),
        "compress_body": _as_bool(section.get("compress_body"), True),
        "body_budget_tokens": _as_int(
            section.get("body_budget_tokens"), DEFAULT_BODY_BUDGET_TOKENS
        ),
        "keep_recent_turns": _as_int(
            section.get("keep_recent_turns"), DEFAULT_KEEP_RECENT_TURNS
        ),
        "exact_cache": _as_bool(section.get("exact_cache"), True),
        "fidelity": _as_bool(section.get("fidelity"), True),
        "plugins": {
            "llmlingua": _as_bool(plugins.get("llmlingua"), False),
            "sqlite_vec": _as_bool(plugins.get("sqlite_vec"), False),
        },
        "workdir": str(project_root(workdir)),
    }
    cfg["mode"] = effective_mode(cfg)
    return cfg


def split_host_port(url_or_listen: str, default_port: int) -> tuple[str, int]:
    """host, port из URL. https без порта → 443, иначе default_port."""
    raw = (url_or_listen or "").strip()
    if not raw:
        return PXPIPE_HOST, default_port
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or PXPIPE_HOST
    if parsed.port:
        return host, int(parsed.port)
    if parsed.scheme == "https":
        return host, 443
    return host, int(default_port)


def host_header(host: str, port: int, scheme: str) -> str:
    """Host без стандартного порта (80/443)."""
    if scheme == "https" and int(port) == 443:
        return host
    if scheme == "http" and int(port) == 80:
        return host
    return f"{host}:{int(port)}"


def supervisor_adapter(workdir: Optional[Path] = None) -> str:
    cfg = load_project_config(workdir)
    sup = cfg.get("supervisor")
    if isinstance(sup, dict):
        name = sup.get("adapter")
        if isinstance(name, str) and name.strip():
            return name.strip().lower()
    return "mock"
