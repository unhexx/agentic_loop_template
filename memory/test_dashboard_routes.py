# -*- coding: utf-8 -*-
"""Страница Loop и HTMX-частичные: полоса, карточка, дельты."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.render import _CHROME_KEYS


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _seed(tmp_path: Path, summary: str = "Implemented parser. Tests pending.") -> None:
    _write_json(
        tmp_path / ".agent" / "LOOP_STATE.json",
        {
            "cycle_number": 12,
            "active_role": "Coder",
            "status": "IN_PROGRESS",
            "branch": "feat-x",
            "last_commit": "abc123",
            "git_sync": {"verified": True},
            "open_invest": ["T-12 parser"],
            "recent_deltas": [
                {"ts": "2026-08-21T12:00:00Z", "role": "Coder", "text": "wired parser"}
            ],
            "updated_at": "2026-08-21T12:00:01Z",
            "notes": "keep going",
        },
    )
    _write_json(
        tmp_path / ".agent" / "last_handoff.json",
        {
            "role": "Coder",
            "handoff_to": "Tester",
            "current_phase": "implementation",
            "cycle_number": 12,
            "summary": summary,
            "status": "IN_PROGRESS",
            "confidence": 0.86,
            "git_sync_status": {"verified": True},
            "metrics": {"tests_total": 12, "tests_failed": 0},
        },
    )


def test_loop_page_shows_loop_and_handoff_fields(dashboard_client, tmp_path: Path):
    _seed(tmp_path)
    r = dashboard_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "loop.status" in body
    assert "handoff.status" in body
    assert "IN_PROGRESS" in body
    assert "Coder" in body
    assert "12" in body
    assert "feat-x" in body
    assert "git_sync.verified" in body
    assert "keep going" in body
    assert "wired parser" in body
    assert "T-12 parser" in body
    assert 'hx-get="/partials/loop-strip"' in body
    assert 'hx-get="/partials/handoff-card"' in body
    assert 'hx-get="/partials/deltas"' in body
    assert "every 5s" in body
    assert "Agentix Control" in body


def test_partials_loop_strip_handoff_deltas(dashboard_client, tmp_path: Path):
    _seed(tmp_path)
    strip = dashboard_client.get("/partials/loop-strip")
    assert strip.status_code == 200
    assert "loop.status" in strip.text
    assert "handoff.status" in strip.text
    assert "Coder" in strip.text
    assert "STOP:" in strip.text

    card = dashboard_client.get("/partials/handoff-card")
    assert card.status_code == 200
    assert "Last handoff" in card.text
    assert "Implemented parser. Tests pending." in card.text
    assert "Coder" in card.text
    assert "Tester" in card.text

    deltas = dashboard_client.get("/partials/deltas")
    assert deltas.status_code == 200
    assert "wired parser" in deltas.text
    assert "T-12 parser" in deltas.text
    assert "Recent deltas" in deltas.text
    assert "Open INVEST" in deltas.text


def test_summary_literal_title_placeholder_does_not_mutate_chrome(
    dashboard_client, tmp_path: Path
):
    _seed(tmp_path, summary="see {{title}}")
    r = dashboard_client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "<title>Loop — Agentix</title>" in html
    assert "see {{title}}" in html
    # литерал живёт в теле handoff, а не только в <title>
    title_end = html.find("</title>")
    assert title_end != -1
    assert "see {{title}}" in html[title_end:]
    assert "Last handoff" in html

    card = dashboard_client.get("/partials/handoff-card")
    assert card.status_code == 200
    assert "see {{title}}" in card.text
    assert "<title>" not in card.text


def test_xss_summary_escaped(dashboard_client, tmp_path: Path):
    _seed(tmp_path, summary="<script>alert(1)</script>")
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    card = dashboard_client.get("/partials/handoff-card")
    assert "<script>" not in card.text
    assert "&lt;script&gt;" in card.text


def test_torn_handoff_partial_is_not_500(dashboard_client, tmp_path: Path, monkeypatch):
    from memory.dashboard import read_model

    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _seed(tmp_path, summary="stable summary")
    first_card = dashboard_client.get("/partials/handoff-card")
    assert first_card.status_code == 200
    assert "stable summary" in first_card.text
    first_strip = dashboard_client.get("/partials/loop-strip")
    assert first_strip.status_code == 200
    assert 'data-stale="false"' in first_strip.text
    assert "data-stale-banner" not in first_strip.text

    (tmp_path / ".agent" / "last_handoff.json").write_text("{", encoding="utf-8")
    second = dashboard_client.get("/partials/handoff-card")
    assert second.status_code == 200
    assert "stable summary" in second.text
    strip = dashboard_client.get("/partials/loop-strip")
    assert strip.status_code == 200
    assert 'data-stale="true"' in strip.text
    assert "data-stale-banner" in strip.text


def test_missing_files_partials_200(dashboard_client, tmp_path: Path):
    r = dashboard_client.get("/partials/loop-strip")
    assert r.status_code == 200
    assert "loop.status" in r.text
    assert dashboard_client.get("/partials/handoff-card").status_code == 200
    assert dashboard_client.get("/partials/deltas").status_code == 200
    page = dashboard_client.get("/")
    assert page.status_code == 200
    assert "Loop" in page.text


def test_render_page_chrome_keys_only():
    assert "body_html" in _CHROME_KEYS
    assert "title" in _CHROME_KEYS
    assert "summary" not in _CHROME_KEYS
    assert "last_handoff_summary" not in _CHROME_KEYS
    assert "notes" not in _CHROME_KEYS


def test_dashboard_sources_do_not_import_runner():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "memory/dashboard/read_model.py",
        "memory/dashboard/routes.py",
        "memory/dashboard/server.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "run_loop" not in text
        assert "get_adapter" not in text
        assert "performance_ledger" not in text
        assert "generate_report" not in text
        assert "get_recent" not in text
        assert "tail_history" not in text
        assert "list_playbooks" not in text
        assert "memory_paths" not in text
        assert "export_hub_index" not in text


def _current_ym() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}{now.month:02d}"


def _seed_history(tmp_path: Path, text: str = "hist-ok") -> None:
    hist = tmp_path / ".agent" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    path = hist / f"loop_state-{_current_ym()}.jsonl"
    path.write_text(
        json.dumps({"ts": "2026-08-21T12:00:00Z", "type": "delta", "text": text})
        + "\n",
        encoding="utf-8",
    )


def _seed_ledger(tmp_path: Path, outcome: str = "DONE") -> None:
    _write_json(
        tmp_path / ".agent" / "PERFORMANCE_LEDGER.json",
        {
            "cycles": [
                {
                    "cycle": 12,
                    "timestamp": "2026-08-21T12:00:00Z",
                    "outcome": outcome,
                    "elapsed_minutes": 4.0,
                    "tool_calls": 8,
                    "tokens_est": 1200,
                    "confidence": 0.8,
                    "tests_total": 12,
                    "tests_failed": 0,
                    "violations": 1,
                    "meta_applied": 2,
                },
                {
                    "cycle": 13,
                    "timestamp": "2026-08-21T13:00:00Z",
                    "outcome": "DONE",
                    "elapsed_minutes": 6.0,
                    "tool_calls": 3,
                    "tokens_est": 900,
                    "confidence": 1.0,
                    "tests_total": 10,
                    "tests_failed": 1,
                    "violations": 0,
                    "meta_applied": 0,
                },
            ]
        },
    )


def test_handoff_page_dl_and_history_tail(dashboard_client, tmp_path: Path):
    _seed(tmp_path)
    _seed_history(tmp_path, "wired parser")
    r = dashboard_client.get("/handoff")
    assert r.status_code == 200
    body = r.text
    assert "<title>Handoff — Agentix</title>" in body
    assert "Last 20" in body or "History tail" in body
    assert "View JSON" in body
    assert "handoff_to" in body
    assert "current_phase" in body
    assert "Coder" in body
    assert "Tester" in body
    assert "Implemented parser. Tests pending." in body
    assert "wired parser" in body
    assert "History tail" in body
    # определение, не сырой dump как основной вид: есть dt/dd
    assert "<dt" in body
    assert "<dd" in body


def test_handoff_summary_literal_title_placeholder(dashboard_client, tmp_path: Path):
    _seed(tmp_path, summary="see {{title}}")
    r = dashboard_client.get("/handoff")
    assert r.status_code == 200
    html = r.text
    assert "<title>Handoff — Agentix</title>" in html
    assert "see {{title}}" in html
    title_end = html.find("</title>")
    assert title_end != -1
    assert "see {{title}}" in html[title_end:]
    assert "summary" in html
    loop = dashboard_client.get("/")
    assert loop.status_code == 200
    assert "<title>Loop — Agentix</title>" in loop.text
    assert "see {{title}}" in loop.text


def test_handoff_xss_escaped(dashboard_client, tmp_path: Path):
    _seed(tmp_path, summary="<script>alert(1)</script>")
    _seed_history(tmp_path, "<script>hist()</script>")
    r = dashboard_client.get("/handoff")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "<script>hist()</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_handoff_malformed_jsonl_not_500(dashboard_client, tmp_path: Path):
    _seed(tmp_path)
    hist = tmp_path / ".agent" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"loop_state-{_current_ym()}.jsonl").write_text(
        '{not-json\n'
        + json.dumps({"ts": "t", "text": "good-line"})
        + "\n<script>raw()</script>\n",
        encoding="utf-8",
    )
    r = dashboard_client.get("/handoff")
    assert r.status_code == 200
    assert "{not-json" in r.text
    assert "good-line" in r.text
    assert "<script>raw()</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_ledger_page_and_partial(dashboard_client, tmp_path: Path):
    _seed_ledger(tmp_path)
    page = dashboard_client.get("/ledger")
    assert page.status_code == 200
    body = page.text
    assert "<title>Ledger — Agentix</title>" in body
    assert 'hx-get="/partials/ledger-rows"' in body
    assert "load, every 20s, ws-refresh from:body" in body
    assert "cycle" in body
    assert "elapsed_min" in body
    assert "meta_applied" in body
    assert "12" in body
    assert "13" in body
    assert "DONE" in body
    assert "avg elapsed" in body
    assert "avg confidence" in body
    #  (4+6)/2 = 5.0 , (0.8+1.0)/2 = 0.9 , meta 2+0 = 2
    assert "5.0" in body
    assert "0.9" in body
    # newest first: цикл 13 выше 12 (не только оба числа где-то на странице)
    assert body.find(">13</td>") < body.find(">12</td>")
    partial = dashboard_client.get("/partials/ledger-rows")
    assert partial.status_code == 200
    assert "12/0" in partial.text
    assert "10/1" in partial.text
    assert "<title>" not in partial.text
    assert partial.text.find(">13</td>") < partial.text.find(">12</td>")


def test_ledger_empty_message(dashboard_client, tmp_path: Path):
    r = dashboard_client.get("/ledger")
    assert r.status_code == 200
    assert "No cycles recorded yet." in r.text
    assert "<title>Ledger — Agentix</title>" in r.text
    rows = dashboard_client.get("/partials/ledger-rows")
    assert rows.status_code == 200
    assert "No cycles recorded yet." in rows.text


def test_handoff_ledger_missing_files_200(dashboard_client, tmp_path: Path):
    h = dashboard_client.get("/handoff")
    assert h.status_code == 200
    assert "no last_handoff.json" in h.text
    assert "(none)" in h.text
    assert dashboard_client.get("/ledger").status_code == 200
    assert dashboard_client.get("/partials/ledger-rows").status_code == 200


def test_handoff_ledger_are_read_only(dashboard_client, tmp_path: Path):
    _seed(tmp_path)
    _seed_history(tmp_path)
    _seed_ledger(tmp_path)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    mtimes = {p: p.stat().st_mtime_ns for p in files}
    snapshot = {p: p.read_bytes() for p in files}
    assert dashboard_client.get("/handoff").status_code == 200
    assert dashboard_client.get("/ledger").status_code == 200
    assert dashboard_client.get("/partials/ledger-rows").status_code == 200
    after = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert set(after) == set(files)
    for p in files:
        assert p.stat().st_mtime_ns == mtimes[p]
        assert p.read_bytes() == snapshot[p]
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_ledger_torn_partial_not_500(dashboard_client, tmp_path: Path, monkeypatch):
    from memory.dashboard import read_model

    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _seed_ledger(tmp_path)
    first = dashboard_client.get("/partials/ledger-rows")
    assert first.status_code == 200
    assert "12" in first.text
    (tmp_path / ".agent" / "PERFORMANCE_LEDGER.json").write_text("{", encoding="utf-8")
    second = dashboard_client.get("/partials/ledger-rows")
    assert second.status_code == 200
    assert "12" in second.text


def test_handoff_chrome_not_overwritten_by_json_placeholder(
    dashboard_client, tmp_path: Path
):
    _seed(tmp_path, summary="see {{title}}")
    r = dashboard_client.get("/handoff")
    html = r.text
    assert html.count("<title>Handoff — Agentix</title>") == 1
    # литерал в dl/summary, не только в <title>
    dl_at = html.find("id=\"handoff-dl\"")
    assert dl_at != -1
    assert "see {{title}}" in html[dl_at:]


def _seed_playbooks(tmp_path: Path, content: str = "Always start with git.") -> None:
    _write_json(
        tmp_path / ".agent" / "PLAYBOOKS.json",
        {
            "playbooks": {
                "global-dev": {
                    "scope": "global",
                    "name": "Global Dev — see {{title}}",
                    "bullets": [
                        {"id": "b-0001", "content": content, "effectiveness": 0.95},
                        {
                            "id": "b-0002",
                            "content": "see {{title}}",
                            "effectiveness": 0.5,
                        },
                    ],
                    "last_curated": "2026-08-21T12:00:00Z",
                }
            }
        },
    )


def _seed_audit(tmp_path: Path, action: str = "git.sync") -> None:
    _write_json(
        tmp_path / ".agent" / "AUDIT_LOG.json",
        {
            "entries": [
                {
                    "id": "A-0001",
                    "ts": "2026-08-21T12:00:00Z",
                    "action": action,
                    "role": "Coder",
                    "cycle": 12,
                    "approval_required": True,
                    "approved": False,
                    "signature": "abcdef0123456789ffff",
                },
                {
                    "id": "A-0002",
                    "ts": "2026-08-21T13:00:00Z",
                    "action": "dashboard.stop",
                    "role": "operator",
                    "cycle": 12,
                    "approval_required": True,
                    "approved": True,
                    "signature": "fff111222333444555",
                },
            ]
        },
    )


def test_playbooks_page_and_partial(dashboard_client, tmp_path: Path):
    _seed_playbooks(tmp_path)
    _write_json(
        tmp_path / ".agent" / "HUB_INDEX.json",
        {
            "version": "1.0",
            "generated_at": "2026-08-21T12:00:00Z",
            "item_count": 1,
        },
    )
    page = dashboard_client.get("/playbooks")
    assert page.status_code == 200
    body = page.text
    assert "<title>Playbooks — Agentix</title>" in body
    assert 'hx-get="/partials/playbooks-list"' in body
    assert "load, every 20s, ws-refresh from:body" in body
    assert 'hx-swap="innerHTML"' in body
    assert "global-dev" in body
    assert "Global Dev" in body
    assert ".agent/PLAYBOOKS/global-dev.md" in body
    assert "0.725" in body
    assert "version 1.0" in body
    assert "item_count 1" in body
    assert "data-hub-header" in body
    assert 'hx-get="/partials/playbook/global-dev"' in body
    assert 'hx-target="#playbook-detail"' in body
    assert 'id="playbook-detail"' in body
    assert "hx-target=\"#pb-" not in body
    assert "hx-post" not in body
    assert "see {{title}}" in body
    partial = dashboard_client.get("/partials/playbooks-list")
    assert partial.status_code == 200
    assert "<title>" not in partial.text
    assert "global-dev" in partial.text
    # фрагмент списка сам по себе не poll'ит outerHTML
    assert "every 20s" not in partial.text
    # слот раскрытия живёт на странице, не внутри poll innerHTML
    assert 'id="playbook-detail"' not in partial.text
    assert 'hx-target="#playbook-detail"' in partial.text


def test_playbooks_empty_no_hub_header(dashboard_client, tmp_path: Path):
    r = dashboard_client.get("/playbooks")
    assert r.status_code == 200
    assert "No playbooks." in r.text
    assert "data-hub-header" not in r.text
    assert dashboard_client.get("/partials/playbooks-list").status_code == 200


def test_playbook_expand_escapes_and_literal_title(dashboard_client, tmp_path: Path):
    _seed_playbooks(tmp_path, content="<script>alert(1)</script>")
    r = dashboard_client.get("/partials/playbook/global-dev")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert "see {{title}}" in r.text
    assert "<title>" not in r.text
    page = dashboard_client.get("/playbooks")
    assert page.status_code == 200
    assert "<title>Playbooks — Agentix</title>" in page.text
    title_end = page.text.find("</title>")
    assert "see {{title}}" in page.text[title_end:]


def test_playbook_expand_target_dot_and_colon(dashboard_client, tmp_path: Path):
    _write_json(
        tmp_path / ".agent" / "PLAYBOOKS.json",
        {
            "playbooks": {
                "tool.git": {
                    "scope": "tool:git",
                    "name": "Git dots",
                    "bullets": [{"id": "b-d", "content": "dot-id", "effectiveness": 1}],
                },
                "role:coder": {
                    "scope": "role:coder",
                    "name": "Coder colon",
                    "bullets": [{"id": "b-c", "content": "colon-id", "effectiveness": 1}],
                },
            }
        },
    )
    page = dashboard_client.get("/playbooks")
    assert page.status_code == 200
    body = page.text
    assert 'hx-target="#playbook-detail"' in body
    assert 'hx-target="#pb-tool.git"' not in body
    assert 'hx-target="#pb-role:coder"' not in body
    assert 'id="pb-tool.git"' not in body
    list_at = body.find('id="playbooks-list"')
    detail_at = body.find('id="playbook-detail"')
    assert list_at != -1 and detail_at != -1
    assert detail_at > list_at
    assert dashboard_client.get("/partials/playbook/tool.git").status_code == 200
    colon = dashboard_client.get("/partials/playbook/role:coder")
    assert colon.status_code == 200
    assert "colon-id" in colon.text
    partial = dashboard_client.get("/partials/playbooks-list")
    assert 'id="playbook-detail"' not in partial.text


def test_playbook_unknown_and_traversal_404(dashboard_client, tmp_path: Path):
    _seed_playbooks(tmp_path)
    assert dashboard_client.get("/partials/playbook/missing-id").status_code == 404
    assert dashboard_client.get("/partials/playbook/%2e%2e%2fetc%2fpasswd").status_code == 404
    assert dashboard_client.get("/partials/playbook/%2e%2e").status_code == 404
    nested = dashboard_client.get("/partials/playbook/foo/bar")
    assert nested.status_code == 404


def test_audit_page_and_partial(dashboard_client, tmp_path: Path):
    _seed_audit(tmp_path)
    page = dashboard_client.get("/audit")
    assert page.status_code == 200
    body = page.text
    assert "<title>Audit — Agentix</title>" in body
    assert 'hx-get="/partials/audit-rows"' in body
    assert "load, every 15s, ws-refresh from:body" in body
    assert 'hx-swap="innerHTML"' in body
    assert "A-0001" in body
    assert "A-0002" in body
    assert "git.sync" in body
    assert "dashboard.stop" in body
    assert "abcdef012345" in body
    assert "abcdef0123456789ffff" not in body
    assert "approval_required" in body
    # новее сверху
    assert body.find("A-0002") < body.find("A-0001")
    partial = dashboard_client.get("/partials/audit-rows")
    assert partial.status_code == 200
    assert "<title>" not in partial.text
    assert "every 15s" not in partial.text


def test_audit_empty_and_xss(dashboard_client, tmp_path: Path):
    empty = dashboard_client.get("/audit")
    assert empty.status_code == 200
    assert "No audit entries." in empty.text
    _seed_audit(tmp_path, action="<script>audit()</script>")
    r = dashboard_client.get("/audit")
    assert "<script>audit()</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_plan_page_missing_and_present(dashboard_client, tmp_path: Path):
    missing = dashboard_client.get("/plan")
    assert missing.status_code == 200
    assert "<title>Plan — Agentix</title>" in missing.text
    assert missing.text.count("not present in this workdir.") == 2
    assert 'hx-get="/partials/plan-body"' in missing.text
    assert "load, every 20s, ws-refresh from:body" in missing.text
    agent = tmp_path / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "PLAN.md").write_text("Do {{title}}\n<script>x</script>\n", encoding="utf-8")
    (agent / "TODO.md").write_text("- ship it\n", encoding="utf-8")
    page = dashboard_client.get("/plan")
    assert page.status_code == 200
    html = page.text
    assert "<title>Plan — Agentix</title>" in html
    title_end = html.find("</title>")
    assert "Do {{title}}" in html[title_end:]
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert "- ship it" in html
    assert "<pre" in html
    assert "whitespace-pre-wrap" in html
    partial = dashboard_client.get("/partials/plan-body")
    assert partial.status_code == 200
    assert "Do {{title}}" in partial.text
    assert "<title>" not in partial.text
    assert "data-truncated" not in page.text

    from memory.dashboard.read_model import PLAN_MAX_BYTES

    (agent / "PLAN.md").write_text("Q" * (PLAN_MAX_BYTES + 8), encoding="utf-8")
    big = dashboard_client.get("/plan")
    assert big.status_code == 200
    assert "data-truncated" in big.text
    assert "(truncated)" in big.text
    assert "Q" * 16 in big.text


def test_memory_page_missing_and_excerpt(dashboard_client, tmp_path: Path, monkeypatch):
    from memory.dashboard import read_model
    from memory.workspace import get_workspace_id

    root = tmp_path / "memroot"
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    missing = dashboard_client.get("/memory")
    assert missing.status_code == 200
    assert "<title>Memory — Agentix</title>" in missing.text
    assert "no institutional memory file yet" in missing.text
    assert "Institutional memory is off-workdir" in missing.text
    assert "shared across worktrees" in missing.text
    assert 'hx-get="/partials/memory-excerpt"' in missing.text
    assert "load, every 30s, ws-refresh from:body" in missing.text
    assert not root.exists()
    root.mkdir()
    wid = get_workspace_id(cwd=tmp_path)
    (root / f"{wid}.md").write_text(
        "hello {{title}}\n<script>mem()</script>\n", encoding="utf-8"
    )
    (root / "other-project.md").write_text("SECRET-OTHER", encoding="utf-8")
    page = dashboard_client.get("/memory")
    assert page.status_code == 200
    html = page.text
    title_end = html.find("</title>")
    assert "hello {{title}}" in html[title_end:]
    assert "<script>mem()</script>" not in html
    assert "&lt;script&gt;" in html
    assert "SECRET-OTHER" not in html
    assert not any(p.name.endswith(".lock") for p in root.iterdir())
    partial = dashboard_client.get("/partials/memory-excerpt")
    assert partial.status_code == 200
    assert "hello {{title}}" in partial.text
    assert "<title>" not in partial.text
    assert "data-truncated" not in page.text

    long_lines = "\n".join(f"line-{i}" for i in range(100))
    (root / f"{wid}.md").write_text(long_lines, encoding="utf-8")
    truncated_page = dashboard_client.get("/memory")
    assert truncated_page.status_code == 200
    assert "data-truncated" in truncated_page.text
    assert "(truncated)" in truncated_page.text
    assert "line-0" in truncated_page.text
    assert "line-80" not in truncated_page.text


def test_playbooks_audit_plan_memory_are_read_only(dashboard_client, tmp_path: Path, monkeypatch):
    from memory.dashboard import read_model
    from memory.workspace import get_workspace_id

    _seed_playbooks(tmp_path)
    _seed_audit(tmp_path)
    agent = tmp_path / ".agent"
    (agent / "PLAN.md").write_text("plan", encoding="utf-8")
    (agent / "TODO.md").write_text("todo", encoding="utf-8")
    _write_json(
        agent / "HUB_INDEX.json",
        {"version": "1.0", "generated_at": "t", "item_count": 1},
    )
    root = tmp_path / "memroot"
    root.mkdir()
    wid = get_workspace_id(cwd=tmp_path)
    (root / f"{wid}.md").write_text("mem", encoding="utf-8")
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    mtimes = {p: p.stat().st_mtime_ns for p in files}
    snapshot = {p: p.read_bytes() for p in files}
    for path in (
        "/playbooks",
        "/partials/playbooks-list",
        "/partials/playbook/global-dev",
        "/audit",
        "/partials/audit-rows",
        "/plan",
        "/partials/plan-body",
        "/memory",
        "/partials/memory-excerpt",
    ):
        assert dashboard_client.get(path).status_code == 200
    after = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert set(after) == set(files)
    for p in files:
        assert p.stat().st_mtime_ns == mtimes[p]
        assert p.read_bytes() == snapshot[p]
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_playbooks_does_not_export_hub(dashboard_client, tmp_path: Path):
    _seed_playbooks(tmp_path)
    hub = tmp_path / ".agent" / "HUB_INDEX.json"
    assert not hub.exists()
    assert dashboard_client.get("/playbooks").status_code == 200
    assert not hub.exists()
