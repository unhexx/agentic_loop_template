# -*- coding: utf-8 -*-
"""
Конфиг дашборда: workdir, bind, порт, опциональный токен.

Env перекрывает дефолты. Слушать можно только 127.0.0.1 / localhost / ::1 —
иначе процесс не стартует (SR-04).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8110
LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class DashboardConfig:
    workdir: Path
    host: str
    port: int
    token: str


def bind_host_allowed(host: str) -> bool:
    """Bind, не Host-заголовок: только три явных loopback-имени."""
    return (host or "").strip().lower() in LOOPBACK_BIND_HOSTS


def load_config(
    *,
    workdir: Optional[Path] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    token: Optional[str] = None,
) -> DashboardConfig:
    if workdir is not None:
        wd = Path(workdir)
    else:
        env_wd = os.environ.get("AGENTIX_DASHBOARD_WORKDIR", "").strip()
        wd = Path(env_wd) if env_wd else Path.cwd()
    h = (host if host is not None else os.environ.get("AGENTIX_DASHBOARD_HOST", "")).strip()
    if not h:
        h = DEFAULT_HOST
    if port is not None:
        p = int(port)
    else:
        raw_p = os.environ.get("AGENTIX_DASHBOARD_PORT", "").strip()
        p = int(raw_p) if raw_p else DEFAULT_PORT
    if token is not None:
        tok = token
    else:
        tok = os.environ.get("DASHBOARD_TOKEN", "")
    return DashboardConfig(
        workdir=wd.resolve(),
        host=h,
        port=p,
        token=tok,
    )
