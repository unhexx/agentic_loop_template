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


def test_ledger_page_and_partial(dashboard_client, tmp_path: Path):
    _seed_ledger(tmp_path)
    page = dashboard_client.get("/ledger")
    assert page.status_code == 200
    body = page.text
    assert "<title>Ledger — Agentix</title>" in body
    assert 'hx-get="/partials/ledger-rows"' in body
    assert "load, every 20s, ws-refresh" in body
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
    partial = dashboard_client.get("/partials/ledger-rows")
    assert partial.status_code == 200
    assert "12/0" in partial.text
    assert "10/1" in partial.text
    assert "<title>" not in partial.text


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
