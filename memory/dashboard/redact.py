# -*- coding: utf-8 -*-
"""Маскировка DASHBOARD_TOKEN и Authorization — аналог telegrok redact_tokens.

Логи и HTML не должны светить секрет. Пустой токен ничего не режет по значению;
шаблоны query/Bearer/заголовков режем всегда. Короткий токен (< 8 символов)
по значению не подменяем — иначе «new» размажет весь UI.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping, Optional


REDACT_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-token",
        "x-api-key",
        "x-auth-token",
    }
)

# Значение query не захватывает кавычки — иначе сломаем JS `?token=' + encode…`.
_TOKEN_QUERY_RE = re.compile(r"([?&]token=)([^&\"'\s<>]+)", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*)(\S.+)", re.IGNORECASE)
_X_API_RE = re.compile(r"(X-API-Token:\s*)(\S+)", re.IGNORECASE)
_COOKIE_RE = re.compile(r"(agentix_token=)([^;\s\"']+)", re.IGNORECASE)

_MIN_VALUE_LEN = 8
_PLACEHOLDER = "****"
_SCRIPT_RE = re.compile(r"(?is)(<script\b[^>]*>)(.*?)(</script>)")
_FILTER_LOGGERS = (
    "memory.dashboard",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
)


def mask_secret(value: str) -> str:
    """Только для логов: края оставляем. HTML идёт через полный ``****``."""
    v = value or ""
    if len(v) <= _MIN_VALUE_LEN:
        return _PLACEHOLDER
    return f"{v[:4]}{_PLACEHOLDER}{v[-4:]}"


def _env_token(explicit: Optional[str] = None) -> str:
    if explicit is not None:
        return str(explicit).strip()
    return (os.environ.get("DASHBOARD_TOKEN") or "").strip()


def _pattern_redact(text: str) -> str:
    red = text
    red = _TOKEN_QUERY_RE.sub(lambda m: m.group(1) + _PLACEHOLDER, red)
    red = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + _PLACEHOLDER, red)
    red = _BEARER_RE.sub(lambda m: m.group(1) + _PLACEHOLDER, red)
    red = _X_API_RE.sub(lambda m: m.group(1) + _PLACEHOLDER, red)
    red = _COOKIE_RE.sub(lambda m: m.group(1) + _PLACEHOLDER, red)
    return red


def redact_tokens(
    text: Optional[str],
    token: Optional[str] = None,
    *,
    keep_edges: bool = False,
) -> str:
    """Вычищает DASHBOARD_TOKEN, ``?token=``, Bearer и Authorization из строки.

    По умолчанию значение токена целиком ``****`` (HTML). ``keep_edges=True`` —
    только access-логи, не страница.
    """
    if not text:
        return text or ""
    red = str(text)
    tok = _env_token(token)
    if tok and len(tok) >= _MIN_VALUE_LEN and tok in red:
        red = red.replace(tok, mask_secret(tok) if keep_edges else _PLACEHOLDER)
    return _pattern_redact(red)


def redact_html(html: Optional[str], token: Optional[str] = None) -> str:
    """HTML: токен → ``****``, ``<script>`` по значению не трогаем (не ломаем wsUrl)."""
    if not html:
        return html or ""
    raw = str(html)
    tok = _env_token(token)
    if tok and len(tok) >= _MIN_VALUE_LEN:
        parts: list[str] = []
        pos = 0
        for m in _SCRIPT_RE.finditer(raw):
            parts.append(raw[pos : m.start()].replace(tok, _PLACEHOLDER))
            parts.append(m.group(0))
            pos = m.end()
        parts.append(raw[pos:].replace(tok, _PLACEHOLDER))
        raw = "".join(parts)
    return _pattern_redact(raw)


def redact_headers(headers: Mapping[str, Any], token: Optional[str] = None) -> dict[str, str]:
    """Authorization/Cookie/X-API-Token → звёзды; остальное прогоняем через redact_tokens."""
    out: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        if name.lower() in REDACT_HEADER_NAMES:
            out[name] = _PLACEHOLDER
        else:
            out[name] = redact_tokens(str(value), token=token)
    return out


class RedactFilter(logging.Filter):
    """Фильтр stdlib logging: сообщение и args без токена."""

    _ATTRS = (
        "request_line",
        "full_path",
        "path",
        "query_string",
        "authorization",
        "token",
        "scope",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Сначала собираем сообщение, потом маскируем — иначе ?token=%s сломает %.
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact_tokens(rendered, keep_edges=True)
        record.args = ()
        for name in self._ATTRS:
            val = getattr(record, name, None)
            if isinstance(val, str):
                setattr(record, name, redact_tokens(val, keep_edges=True))
        return True


def install_log_redaction(*, ensure_handler: bool = False) -> RedactFilter:
    """Фильтр только на dashboard/uvicorn — не на root (чужие тесты не мажем).

    ``ensure_handler`` — stderr для ``memory.dashboard``, когда uvicorn ещё
    не повесил свои хендлеры. Access пишем в ``uvicorn.error``, баннер uvicorn
    живёт при дефолтном ``log_config``.
    """
    filt = RedactFilter()
    for name in _FILTER_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in lg.filters):
            lg.addFilter(filt)
    if ensure_handler:
        dash = logging.getLogger("memory.dashboard")
        if not dash.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter("%(levelname)s %(name)s %(message)s")
            )
            dash.addHandler(handler)
        if dash.level == logging.NOTSET or dash.level > logging.INFO:
            dash.setLevel(logging.INFO)
    return filt
