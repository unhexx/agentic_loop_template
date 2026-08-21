# -*- coding: utf-8 -*-
"""
Host и peer только loopback: 127/8 и ::1 через ipaddress.

Prefix 127. не используем — иначе 127.0.0.1.nip.io прошёл бы как свой.

CSRF — синхронизатор: cookie HttpOnly + тот же токен в hx-headers.
DASHBOARD_TOKEN: заголовок / Bearer / cookie / ?token=, пустой — проверка выключена.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


MAX_BODY_BYTES = 64 * 1024
CSRF_COOKIE = "agentix_csrf"
TOKEN_COOKIE = "agentix_token"
CSRF_HEADER = "x-csrf-token"


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


def generate_csrf_token() -> str:
    """32 байта urlsafe: кладём и в cookie, и в HTML."""
    return secrets.token_urlsafe(32)


def origin_tuple(url: str) -> Optional[tuple[str, str, int]]:
    try:
        p = urlparse(url)
    except Exception:
        return None
    if not p.scheme or not p.hostname:
        return None
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.scheme.lower(), p.hostname.lower(), int(port))


def is_same_origin(request: Any) -> bool:
    """POST: Sec-Fetch-Site=cross-site или чужой Origin — отказ. Нет Origin — curl."""
    headers = request.headers
    if headers.get("sec-fetch-site") == "cross-site":
        return False
    origin = headers.get("origin")
    if origin is None:
        return True
    got = origin_tuple(origin)
    if got is None:
        return False
    exp = origin_tuple(str(request.base_url))
    if exp is None:
        return False
    return got == exp


def token_ok(expected: str, provided: Optional[str]) -> bool:
    exp = (expected or "").strip()
    if not exp:
        return True
    if not provided:
        return False
    return hmac.compare_digest(str(provided), exp)


def extract_token(
    headers: Mapping[str, str],
    cookies: Optional[Mapping[str, str]] = None,
    query_params: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """X-API-Token → Bearer → cookie agentix_token → ?token=."""
    x = headers.get("x-api-token") if headers is not None else None
    if x:
        s = str(x).strip()
        if s:
            return s
    auth = headers.get("authorization") if headers is not None else None
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        s = auth[7:].strip()
        if s:
            return s
    if cookies is not None:
        cookie = cookies.get(TOKEN_COOKIE)
        if cookie:
            s = str(cookie).strip()
            if s:
                return s
    if query_params is not None:
        q = query_params.get("token")
        if q:
            s = str(q).strip()
            if s:
                return s
    return None


def extract_request_token(request: Any) -> Optional[str]:
    return extract_token(request.headers, request.cookies, request.query_params)


def csrf_ok(request: Any, expected: str) -> bool:
    """Заголовок X-CSRF-Token должен совпасть с cookie и с токеном процесса."""
    exp = (expected or "").strip()
    cookie = (request.cookies.get(CSRF_COOKIE) or "").strip()
    header = (request.headers.get(CSRF_HEADER) or "").strip()
    if not exp or not cookie or not header:
        return False
    return hmac.compare_digest(cookie, exp) and hmac.compare_digest(header, exp)


def set_csrf_cookie(response: Any, token: str) -> None:
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        path="/",
    )


def set_token_cookie(response: Any, token: str) -> None:
    response.set_cookie(
        key=TOKEN_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        path="/",
    )


def content_length_too_large(headers: Mapping[str, str], limit: int = MAX_BODY_BYTES) -> bool:
    raw = headers.get("content-length")
    if raw is None:
        return False
    try:
        return int(raw) > limit
    except (TypeError, ValueError):
        return False
