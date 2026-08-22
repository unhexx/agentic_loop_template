# -*- coding: utf-8 -*-
"""HTML без Jinja: один проход {{name}} и html.escape, не цепочка replace."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Dict, Match

from memory.dashboard.redact import redact_html


TEMPLATES = Path(__file__).parent / "templates"
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

# Только ключи оболочки: поля страницы не должны попадать во второй проход.
_CHROME_KEYS = (
    "body_html",
    "title",
    "csrf",
    "year",
    "conn_dot",
    "workdir_name",
    "workdir_path",
)


def _sub(raw: str, ctx: Dict[str, object]) -> str:
    def repl(m: Match[str]) -> str:
        k = m.group(1)
        if k not in ctx:
            return m.group(0)
        v = ctx[k]
        if k.endswith("_html"):
            return str(v if v is not None else "")
        return escape(str(v if v is not None else ""), quote=True)

    return _PLACEHOLDER.sub(repl, raw)


def render_partial(name: str, **ctx: object) -> str:
    raw = (TEMPLATES / "partials" / name).read_text(encoding="utf-8")
    return redact_html(_sub(raw, ctx))


def render_page(name: str, **ctx: object) -> str:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    page = (TEMPLATES / "pages" / name).read_text(encoding="utf-8")
    body = _sub(page, ctx)
    chrome = {k: ctx[k] for k in _CHROME_KEYS if k in ctx}
    chrome["body_html"] = body
    chrome.setdefault("title", ctx.get("title") or "Agentix")
    return redact_html(_sub(base, chrome))
