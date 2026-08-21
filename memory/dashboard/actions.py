# -*- coding: utf-8 -*-
"""POST оператора: STOP, снятие STOP, resolve вопросов. GET pr-link — только чтение."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from memory.audit_log import append_entry
from memory.dashboard.read_model import DashboardStore
from memory.dashboard.render import render_partial
from memory.dashboard.routes import QUESTION_ID_RE, render_questions_table
from memory.questions_collector import mark_reviewed


_PR_NUM = re.compile(r"/pull/(\d+)")
_GH_TIMEOUT_S = 2.0
_HX_REFRESH = {"HX-Trigger": "ws-refresh"}


def register_actions(app: FastAPI) -> None:
    @app.post("/actions/stop")
    async def action_stop(request: Request) -> Response:
        store: DashboardStore = request.app.state.store
        path = store.write_stop()
        await _after_write(
            request,
            action="dashboard.stop",
            details={"path": str(path)},
            event={"type": "stop:set"},
        )
        return Response(status_code=204, headers=dict(_HX_REFRESH))

    @app.post("/actions/clear-stop")
    async def action_clear_stop(request: Request) -> Response:
        store: DashboardStore = request.app.state.store
        store.clear_stop()
        await _after_write(
            request,
            action="dashboard.clear_stop",
            details={"cleared": True},
            event={"type": "stop:cleared"},
        )
        return Response(status_code=204, headers=dict(_HX_REFRESH))

    @app.post("/actions/questions/{qid}/resolve")
    async def action_resolve(request: Request, qid: str) -> Response:
        if not QUESTION_ID_RE.fullmatch(qid or ""):
            return JSONResponse({"detail": "not found"}, status_code=404)
        notes = (await _form_str(request, "notes")).strip()
        if not notes:
            return JSONResponse({"detail": "notes required"}, status_code=400)
        reviewed_by = (await _form_str(request, "reviewed_by")).strip() or "operator"
        store: DashboardStore = request.app.state.store
        mark_reviewed(
            [qid],
            notes,
            reviewed_by,
            agent_dir=store.agent,
        )
        await _after_write(
            request,
            action="dashboard.question_resolve",
            details={"id": qid, "notes": notes, "reviewed_by": reviewed_by},
            event={"type": "question:resolved", "id": qid},
        )
        return HTMLResponse(
            render_questions_table(store),
            headers=dict(_HX_REFRESH),
        )

    @app.get("/actions/pr-link")
    async def action_pr_link(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        url, reason = _gh_pr_url(store.workdir)
        return HTMLResponse(render_partial("pr_link.html", link_html=_pr_link_html(url, reason)))


def _cycle_number(store: DashboardStore) -> int:
    st = store.loop_state()
    try:
        return int(st.get("cycle_number") or 0)
    except (TypeError, ValueError):
        return 0


async def _after_write(
    request: Request,
    *,
    action: str,
    details: Dict[str, Any],
    event: Dict[str, Any],
) -> None:
    store: DashboardStore = request.app.state.store
    entry = append_entry(
        action=action,
        role="operator",
        cycle=_cycle_number(store),
        details=details,
        approval_required=True,
        approved=True,
        agent_dir=store.agent,
    )
    bc = getattr(request.app.state, "broadcaster", None)
    if bc is None:
        return
    await bc.broadcast(event)
    await bc.broadcast({"type": "audit:appended", "id": entry.get("id")})


async def _form_str(request: Request, key: str, default: str = "") -> str:
    """Поля формы: urlencoded / json. Тело уже прочитано middleware (лимит 64 KiB)."""
    raw = await request.body()
    if not raw:
        return default
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            return default
        if isinstance(data, dict) and key in data and data.get(key) is not None:
            return str(data.get(key))
        return default
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    vals = parsed.get(key)
    if vals:
        return vals[0]
    return default


def _gh_pr_url(workdir: Path) -> Tuple[Optional[str], str]:
    """Только `gh pr view`. Никакого merge, таймаут 2 с, cwd=workdir."""
    gh = shutil.which("gh")
    if not gh:
        return None, "no PR / gh missing"
    try:
        proc = subprocess.run(
            [gh, "pr", "view", "--json", "url", "-q", ".url"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "no PR / gh missing"
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        return None, "no PR / gh missing"
    if not (url.startswith("https://") or url.startswith("http://")):
        return None, "no PR / gh missing"
    return url, ""


def _pr_link_html(url: Optional[str], reason: str) -> str:
    from html import escape

    if url:
        m = _PR_NUM.search(url)
        n = m.group(1) if m else ""
        label = f"Open PR #{n}" if n else "Open PR"
        href = escape(url, quote=True)
        return (
            f'<a href="{href}" target="_blank" rel="noopener" '
            f'class="text-emerald-400 underline">{escape(label, quote=True)}</a>'
        )
    text = reason or "no PR / gh missing"
    return f'<span class="text-amber-400 text-xs">{escape(text, quote=True)}</span>'
