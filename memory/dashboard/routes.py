# -*- coding: utf-8 -*-
"""GET-страницы и HTMX-частичные; без POST и без WebSocket."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from memory.dashboard.read_model import DashboardStore
from memory.dashboard.render import render_page, render_partial


_LOOP_STATUS_CLASS = {
    "IN_PROGRESS": "bg-emerald-900 text-emerald-300",
    "PR_READY": "bg-emerald-900 text-emerald-300",
    "PR_READY_LOCAL": "bg-amber-900 text-amber-300",
    "BLOCKED": "bg-red-900 text-red-300",
    "STOPPED": "bg-amber-900 text-amber-300",
    "STOPPED_LIMIT": "bg-amber-900 text-amber-300",
    "READY": "bg-zinc-800 text-zinc-300",
    "DONE": "bg-zinc-800 text-zinc-300",
}

# Отдельная палитра: loop.status и handoff.status не красим одним цветом.
_HANDOFF_STATUS_CLASS = {
    "IN_PROGRESS": "bg-zinc-800 text-zinc-200",
    "BLOCKED": "bg-red-900 text-red-300",
    "DONE": "bg-emerald-900 text-emerald-300",
}

_LOOP_STATUS_NOTE = {
    "PR_READY_LOCAL": "local only — gh missing or failed",
}


def register_routes(app: FastAPI) -> None:
    @app.get("/")
    async def loop_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        snap = store.snapshot()
        html = render_page(
            "loop.html",
            **_chrome(request.app),
            loop_strip_html=render_loop_strip(snap),
            handoff_card_html=render_handoff_card(snap),
            deltas_html=render_deltas(snap),
        )
        return HTMLResponse(html)

    @app.get("/partials/loop-strip")
    async def loop_strip(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_loop_strip(store.snapshot()))

    @app.get("/partials/handoff-card")
    async def handoff_card(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_handoff_card(store.snapshot()))

    @app.get("/partials/deltas")
    async def deltas(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_deltas(store.snapshot()))


def _chrome(app: FastAPI) -> Dict[str, str]:
    wd = app.state.workdir
    return {
        "title": "Loop",
        "csrf": "",
        "year": str(datetime.now(timezone.utc).year),
        "conn_dot": "WS: polling",
        "workdir_name": wd.name,
        "workdir_path": str(wd),
    }


def render_loop_strip(snap: Dict[str, Any]) -> str:
    st = snap.get("state") or {}
    git_sync = st.get("git_sync") or {}
    if not isinstance(git_sync, dict):
        git_sync = {}
    loop_status = _str(st.get("status"))
    handoff_status = _str(snap.get("last_handoff_status"))
    hb = snap.get("heartbeat") or {}
    stale = bool(snap.get("stale"))
    note = _LOOP_STATUS_NOTE.get(loop_status, "")
    stale_html = (
        '<div class="text-amber-400 text-xs mb-2" data-stale-banner>stale</div>'
        if stale
        else ""
    )
    note_html = (
        f'<div class="text-[10px] text-amber-400 mt-1">{escape(note, quote=True)}</div>'
        if note
        else ""
    )
    return render_partial(
        "loop_strip.html",
        loop_status=loop_status or "missing",
        loop_status_class=_LOOP_STATUS_CLASS.get(loop_status, "bg-zinc-800 text-zinc-300"),
        loop_status_note_html=note_html,
        active_role=_str(st.get("active_role")),
        cycle_number=_str(st.get("cycle_number")),
        branch=_str(st.get("branch")),
        git_sync_verified=_bool_label(git_sync.get("verified")),
        handoff_status=handoff_status or "—",
        handoff_status_class=_HANDOFF_STATUS_CLASS.get(
            handoff_status, "bg-zinc-800 text-zinc-300"
        ),
        heartbeat_label=_str((hb or {}).get("label") or "liveness unknown"),
        stop_label="present" if snap.get("stop") else "absent",
        updated_at=_str(st.get("updated_at")),
        notes=_str(st.get("notes")),
        stale="true" if stale else "false",
        stale_html=stale_html,
    )


def render_handoff_card(snap: Dict[str, Any]) -> str:
    gss = snap.get("last_handoff_git_sync")
    if not isinstance(gss, dict):
        gss = {}
    metrics = snap.get("last_handoff_metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    conf = snap.get("last_handoff_confidence")
    if conf is None:
        conf_s = "—"
    else:
        conf_s = str(conf)
    tests_total = metrics.get("tests_total")
    tests_failed = metrics.get("tests_failed")
    return render_partial(
        "handoff_card.html",
        last_handoff_role=_str(snap.get("last_handoff_role")),
        last_handoff_to=_str(snap.get("last_handoff_to")),
        last_handoff_summary=_str(snap.get("last_handoff_summary")),
        last_handoff_status=_str(snap.get("last_handoff_status")),
        confidence=conf_s,
        git_sync_verified=_bool_label(gss.get("verified")),
        tests_total=_str(tests_total if tests_total is not None else "—"),
        tests_failed=_str(tests_failed if tests_failed is not None else "—"),
    )


def render_deltas(snap: Dict[str, Any]) -> str:
    st = snap.get("state") or {}
    return render_partial(
        "deltas.html",
        deltas_html=_list_html(st.get("recent_deltas") or [], _fmt_delta),
        invest_html=_list_html(st.get("open_invest") or [], _fmt_invest),
    )


def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _bool_label(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return "—"


def _fmt_delta(item: Any) -> str:
    if isinstance(item, dict):
        ts = item.get("ts") or ""
        role = item.get("role") or ""
        text = item.get("text") or ""
        return f"[{ts}] {role}: {text}".strip()
    return str(item)


def _fmt_invest(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("title") or item)
    return str(item)


def _list_html(items: List[Any], fmt) -> str:
    if not items:
        return '<li class="text-zinc-500">(none)</li>'
    parts = []
    for it in items:
        parts.append(f"<li>{escape(fmt(it), quote=True)}</li>")
    return "".join(parts)
