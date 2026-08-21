# -*- coding: utf-8 -*-
"""
Host и peer только loopback: 127/8 и ::1 через ipaddress.

Prefix 127. не используем — иначе 127.0.0.1.nip.io прошёл бы как свой.
"""

from __future__ import annotations

import ipaddress
from typing import Optional


def _host_no_port(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("[") and "]" in h:
        return h[1 : h.index("]")]
    if h.count(":") == 1 and not h.startswith("["):
        return h.split(":")[0]
    return h


def is_loopback_address(address: Optional[str]) -> bool:
    if not address:
        return False
    a = address.strip().lower()
    if a.startswith("::ffff:"):
        a = a[7:]
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return False
    return ip.is_loopback


def is_loopback_host(host: str) -> bool:
    h = _host_no_port(host)
    if h in {"localhost", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback
