# -*- coding: utf-8 -*-
"""GET-страницы и HTMX-частичные; POST живут в actions.py."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from memory.dashboard.read_model import PLAYBOOK_ID_RE, DashboardStore
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

# Поля last_handoff в порядке схемы, не сырой dump.
_HANDOFF_FIELDS = (
    "role",
    "handoff_to",
    "current_phase",
    "cycle_number",
    "summary",
    "context_delta",
    "status",
    "confidence",
    "git_sync_status",
    "metrics",
    "issues_found",
    "process_tags",
    "clarification_questions",
    "artifacts",
    "next_input_files",
)

_LEDGER_COLS = (
    "cycle",
    "timestamp",
    "outcome",
    "elapsed_min",
    "tool_calls",
    "tokens_est",
    "confidence",
    "tests",
    "violations",
    "meta_applied",
)


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
            stop_banner_html=render_stop_banner(store),
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

    @app.get("/handoff")
    async def handoff_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        ho = store.last_handoff()
        html = render_page(
            "handoff.html",
            **_chrome(request.app, title="Handoff"),
            fields_html=render_handoff_fields(ho),
            handoff_json=_handoff_json_text(ho),
            history_html=render_history_list(store.history_tail()),
        )
        return HTMLResponse(html)

    @app.get("/ledger")
    async def ledger_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "ledger.html",
            **_chrome(request.app, title="Ledger"),
            ledger_rows_html=render_ledger_rows(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/ledger-rows")
    async def ledger_rows(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_ledger_rows(store))

    @app.get("/playbooks")
    async def playbooks_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "playbooks.html",
            **_chrome(request.app, title="Playbooks"),
            playbooks_list_html=render_playbooks_list(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/playbooks-list")
    async def playbooks_list(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_playbooks_list(store))

    @app.get("/partials/playbook/{playbook_id}")
    async def playbook_one(request: Request, playbook_id: str) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        detail = store.playbook_detail(playbook_id)
        if detail is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(render_playbook_detail(detail))

    @app.get("/audit")
    async def audit_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "audit.html",
            **_chrome(request.app, title="Audit"),
            audit_rows_html=render_audit_rows(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/audit-rows")
    async def audit_rows(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_audit_rows(store))

    @app.get("/plan")
    async def plan_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "plan.html",
            **_chrome(request.app, title="Plan"),
            plan_body_html=render_plan_body(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/plan-body")
    async def plan_body(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_plan_body(store))

    @app.get("/memory")
    async def memory_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "memory.html",
            **_chrome(request.app, title="Memory"),
            memory_excerpt_html=render_memory_excerpt(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/memory-excerpt")
    async def memory_excerpt(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_memory_excerpt(store))

    @app.get("/questions")
    async def questions_page(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        html = render_page(
            "questions.html",
            **_chrome(request.app, title="Questions"),
            questions_table_html=render_questions_table(store),
        )
        return HTMLResponse(html)

    @app.get("/partials/questions-table")
    async def questions_table(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_questions_table(store))

    @app.get("/partials/stop-banner")
    async def stop_banner(request: Request) -> HTMLResponse:
        store: DashboardStore = request.app.state.store
        return HTMLResponse(render_stop_banner(store))


def _chrome(app: FastAPI, title: str = "Loop") -> Dict[str, str]:
    wd = app.state.workdir
    return {
        "title": title,
        "csrf": str(getattr(app.state, "csrf_token", "") or ""),
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


def _handoff_json_text(ho: Optional[Dict[str, Any]]) -> str:
    if not ho:
        return ""
    return json.dumps(ho, ensure_ascii=False, indent=2)


def _fmt_field_value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def render_handoff_fields(ho: Optional[Dict[str, Any]]) -> str:
    if not ho:
        return '<p class="text-zinc-500 text-sm">no last_handoff.json</p>'
    parts = ['<dl class="grid grid-cols-1 gap-2 text-sm">']
    for key in _HANDOFF_FIELDS:
        if key in ho:
            val = _fmt_field_value(ho.get(key))
        else:
            val = "—"
        parts.append(
            '<div class="bg-zinc-900 border border-zinc-800 rounded p-2">'
            f'<dt class="text-[10px] text-zinc-400">{escape(key, quote=True)}</dt>'
            f'<dd class="whitespace-pre-wrap text-zinc-200">{escape(val, quote=True)}</dd>'
            "</div>"
        )
    parts.append("</dl>")
    return "".join(parts)


def _fmt_history_row(item: Dict[str, Any]) -> str:
    ts = item.get("ts") or item.get("timestamp") or ""
    kind = item.get("type") or item.get("role") or ""
    text = item.get("summary") or item.get("text") or item.get("notes") or ""
    if not text and item.get("raw"):
        text = str(item["raw"])
    if not text:
        skip = {"ts", "timestamp", "type", "role", "summary", "text", "notes", "raw"}
        rest = {k: v for k, v in item.items() if k not in skip}
        if rest:
            text = json.dumps(rest, ensure_ascii=False)[:200]
    bits = [str(ts), str(kind), str(text)]
    return " ".join(b for b in bits if b).strip() or json.dumps(
        item, ensure_ascii=False
    )[:200]


def render_history_list(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return '<li class="text-zinc-500">(none)</li>'
    parts = []
    for row in rows:
        if not isinstance(row, dict):
            row = {"raw": str(row)[:200]}
        parts.append(f"<li>{escape(_fmt_history_row(row), quote=True)}</li>")
    return "".join(parts)


def _ledger_cell(cycle: Dict[str, Any], col: str) -> str:
    if col == "elapsed_min":
        v = cycle.get("elapsed_minutes")
        if v is None:
            v = cycle.get("elapsed_min")
        return _str(v) if v is not None else ""
    if col == "tests":
        total = cycle.get("tests_total")
        failed = cycle.get("tests_failed")
        if total is None and failed is None:
            return ""
        return f"{_str(total if total is not None else '—')}/{_str(failed if failed is not None else '—')}"
    v = cycle.get(col)
    return _str(v) if v is not None else ""


def render_ledger_rows(store: DashboardStore) -> str:
    cycles = store.ledger_cycles()
    summary = store.ledger_summary(cycles)
    if not cycles:
        rows_html = (
            '<tr><td colspan="10" class="py-3 text-zinc-500">'
            "No cycles recorded yet.</td></tr>"
        )
    else:
        parts = []
        for c in reversed(cycles):
            cells = []
            for col in _LEDGER_COLS:
                cells.append(
                    f'<td class="py-1 pr-3 whitespace-nowrap">'
                    f"{escape(_ledger_cell(c, col), quote=True)}</td>"
                )
            parts.append(
                '<tr class="border-b border-zinc-800/80">' + "".join(cells) + "</tr>"
            )
        rows_html = "".join(parts)
    return render_partial(
        "ledger_rows.html",
        count=_str(summary.get("count")),
        avg_elapsed_min=_str(summary.get("avg_elapsed_min")),
        avg_confidence=_str(summary.get("avg_confidence")),
        total_meta_applied=_str(summary.get("total_meta_applied")),
        rows_html=rows_html,
    )


_PLAYBOOK_COLS = (
    "id",
    "scope",
    "name",
    "bullet_count",
    "avg_effectiveness",
    "last_curated",
    "install_path",
)

_AUDIT_COLS = (
    "id",
    "ts",
    "action",
    "role",
    "cycle",
    "approval_required",
    "approved",
    "signature",
)

# id вопроса в форме: без слэшей, как playbook allowlist.
QUESTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

_QUESTION_COLS = (
    "id",
    "priority",
    "question",
    "context",
    "source_role",
    "created_cycle",
    "suggested_recipient",
)


def render_playbooks_list(store: DashboardStore) -> str:
    items = store.playbooks()
    hub = store.hub_index_header()
    if hub:
        hub_header_html = (
            '<div class="text-xs text-zinc-400 mb-3" data-hub-header>'
            f"version {escape(_str(hub.get('version')), quote=True)} · "
            f"generated_at {escape(_str(hub.get('generated_at')), quote=True)} · "
            f"item_count {escape(_str(hub.get('item_count')), quote=True)}"
            "</div>"
        )
    else:
        hub_header_html = ""
    if not items:
        rows_html = (
            '<tr><td colspan="8" class="py-3 text-zinc-500">'
            "No playbooks.</td></tr>"
        )
    else:
        parts = []
        for item in items:
            cells = []
            for col in _PLAYBOOK_COLS:
                cells.append(
                    f'<td class="py-1 pr-3 whitespace-nowrap">'
                    f"{escape(_str(item.get(col)), quote=True)}</td>"
                )
            pid = _str(item.get("id"))
            if PLAYBOOK_ID_RE.fullmatch(pid):
                pid_attr = escape(pid, quote=True)
                # цель — соседняя панель #playbook-detail, не #pb-{id}:
                # точка и двоеточие ломают CSS-селектор, слот вне poll innerHTML.
                expand = (
                    f'<button type="button" class="text-emerald-400 underline text-xs" '
                    f'data-pb-id="{pid_attr}" '
                    f'hx-get="/partials/playbook/{pid_attr}" '
                    f'hx-target="#playbook-detail" hx-swap="innerHTML">expand</button>'
                )
            else:
                expand = ""
            cells.append(f'<td class="py-1 pr-3">{expand}</td>')
            parts.append(
                '<tr class="border-b border-zinc-800/80 align-top">'
                + "".join(cells)
                + "</tr>"
            )
        rows_html = "".join(parts)
    return render_partial(
        "playbooks_list.html",
        hub_header_html=hub_header_html,
        rows_html=rows_html,
    )


def render_playbook_detail(detail: Dict[str, Any]) -> str:
    bullets = detail.get("bullets") or []
    parts = []
    for b in bullets:
        if not isinstance(b, dict):
            continue
        bid = _str(b.get("id"))
        content = _str(b.get("content"))
        eff = b.get("effectiveness")
        eff_s = _str(eff) if eff is not None else ""
        parts.append(
            "<li>"
            f'<span class="text-zinc-500">{escape(bid, quote=True)}</span> '
            f'<span class="text-zinc-400">({escape(eff_s, quote=True)})</span> '
            f"{escape(content, quote=True)}"
            "</li>"
        )
    bullets_html = (
        "".join(parts) if parts else '<li class="text-zinc-500">No bullets.</li>'
    )
    return render_partial(
        "playbook.html",
        playbook_id=_str(detail.get("id")),
        playbook_name=_str(detail.get("name")),
        playbook_scope=_str(detail.get("scope")),
        bullets_html=bullets_html,
    )


def render_audit_rows(store: DashboardStore) -> str:
    entries = store.audit_entries()
    if not entries:
        rows_html = (
            '<tr><td colspan="8" class="py-3 text-zinc-500">'
            "No audit entries.</td></tr>"
        )
    else:
        parts = []
        for entry in reversed(entries):
            cells = []
            for col in _AUDIT_COLS:
                val = entry.get(col)
                if col in ("approval_required", "approved"):
                    cell = _bool_label(val)
                else:
                    cell = _str(val) if val is not None else ""
                cells.append(
                    f'<td class="py-1 pr-3 whitespace-nowrap">'
                    f"{escape(cell, quote=True)}</td>"
                )
            parts.append(
                '<tr class="border-b border-zinc-800/80">' + "".join(cells) + "</tr>"
            )
        rows_html = "".join(parts)
    return render_partial("audit_rows.html", rows_html=rows_html)


def _md_block(title: str, info: Optional[Dict[str, Any]]) -> str:
    heading = f'<h2 class="text-sm font-medium mb-2">{escape(title, quote=True)}</h2>'
    if info is None:
        body = (
            '<p class="text-zinc-500 text-sm">not present in this workdir.</p>'
        )
    else:
        body = (
            '<pre class="whitespace-pre-wrap text-xs bg-zinc-900 border '
            'border-zinc-800 rounded p-3 overflow-x-auto">'
            f"{escape(_str(info.get('text')), quote=True)}</pre>"
        )
        if info.get("truncated"):
            body += (
                '<p class="text-[10px] text-zinc-500" data-truncated>(truncated)</p>'
            )
    return f'<div class="space-y-2">{heading}{body}</div>'


def render_plan_body(store: DashboardStore) -> str:
    return render_partial(
        "plan_body.html",
        plan_html=_md_block("PLAN.md", store.plan_text()),
        todo_html=_md_block("TODO.md", store.todo_text()),
    )


def render_memory_excerpt(store: DashboardStore) -> str:
    info = store.memory_excerpt()
    if info.get("present"):
        body_html = (
            '<pre class="whitespace-pre-wrap text-xs bg-zinc-900 border '
            'border-zinc-800 rounded p-3 overflow-x-auto">'
            f"{escape(_str(info.get('excerpt')), quote=True)}</pre>"
        )
        if info.get("truncated"):
            body_html += (
                '<p class="text-[10px] text-zinc-500" data-truncated>(truncated)</p>'
            )
    else:
        body_html = (
            '<p class="text-zinc-500 text-sm">no institutional memory file yet</p>'
        )
    return render_partial(
        "memory_excerpt.html",
        workspace_id=_str(info.get("workspace_id")),
        body_html=body_html,
    )


def render_stop_banner(store: DashboardStore) -> str:
    present = store.stop_present()
    if present:
        stop_class = "bg-amber-900 text-amber-300 border-amber-800"
        stop_label = "present"
        stop_flag = "present"
    else:
        stop_class = "bg-zinc-900 text-zinc-400 border-zinc-800"
        stop_label = "absent"
        stop_flag = "absent"
    return render_partial(
        "stop_banner.html",
        stop_class=stop_class,
        stop_label=stop_label,
        stop_flag=stop_flag,
    )


def render_questions_table(store: DashboardStore) -> str:
    cadence = store.questions_cadence()
    escalate = "yes" if cadence.get("escalate") else "no"
    cadence_html = (
        '<div class="text-xs text-zinc-400 mb-3" data-cadence '
        f'data-escalate="{escape(escalate, quote=True)}">'
        f'frequency {escape(_str(cadence.get("frequency")), quote=True)}'
        f' (N={escape(_str(cadence.get("N")), quote=True)})'
        f' · open {escape(_str(cadence.get("open_count")), quote=True)}'
        f' · escalate {escape(escalate, quote=True)}'
        f' · {escape(_str(cadence.get("reason")), quote=True)}'
        "</div>"
    )
    items = store.open_questions()
    if not items:
        rows_html = (
            '<tr><td colspan="8" class="py-3 text-zinc-500">'
            "No open questions.</td></tr>"
        )
    else:
        parts = []
        for item in items:
            cells = []
            for col in _QUESTION_COLS:
                cells.append(
                    f'<td class="py-1 pr-3 align-top">'
                    f"{escape(_str(item.get(col)), quote=True)}</td>"
                )
            qid = _str(item.get("id"))
            if QUESTION_ID_RE.fullmatch(qid):
                qid_attr = escape(qid, quote=True)
                form = (
                    f'<form class="flex flex-col gap-1 min-w-[10rem]" '
                    f'hx-post="/actions/questions/{qid_attr}/resolve" '
                    f'hx-target="#questions-table" hx-swap="innerHTML">'
                    f'<input type="text" name="notes" required placeholder="notes" '
                    f'class="bg-zinc-800 border border-zinc-700 rounded px-1.5 py-0.5 text-xs">'
                    f'<input type="text" name="reviewed_by" value="operator" '
                    f'class="bg-zinc-800 border border-zinc-700 rounded px-1.5 py-0.5 text-xs">'
                    f'<button type="submit" class="bg-zinc-800 hover:bg-zinc-700 '
                    f'px-2 py-0.5 rounded border border-zinc-700 text-xs">Resolve</button>'
                    f"</form>"
                )
            else:
                form = ""
            cells.append(f'<td class="py-1 pr-3 align-top">{form}</td>')
            parts.append(
                '<tr class="border-b border-zinc-800/80 align-top">'
                + "".join(cells)
                + "</tr>"
            )
        rows_html = "".join(parts)
    return render_partial(
        "questions_table.html",
        cadence_html=cadence_html,
        rows_html=rows_html,
    )
