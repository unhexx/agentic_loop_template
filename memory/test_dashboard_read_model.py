# -*- coding: utf-8 -*-
"""DashboardStore: явные пути, порванный JSON, snapshot не надмножество CLI."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard import read_model
from memory.dashboard.read_model import DashboardStore


# Ключи memory.state.snapshot(), которых нет в проекции полосы Loop.
_CLI_ONLY = {
    "workspace_hint",
    "template_version",
    "working_bytes",
    "history_dir",
    "rule",
}


@pytest.fixture
def cwd_guard():
    prev = os.getcwd()
    try:
        yield Path(prev)
    finally:
        os.chdir(prev)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _loop_payload(**overrides):
    data = {
        "cycle_number": 12,
        "active_role": "Coder",
        "status": "IN_PROGRESS",
        "branch": "feat-x",
        "last_commit": "abc123",
        "git_sync": {"verified": True, "feature_pushed": True},
        "open_invest": ["T-12 parser"],
        "recent_deltas": [
            {"ts": "2026-08-21T12:00:00Z", "role": "Coder", "text": "parser ok"}
        ],
        "updated_at": "2026-08-21T12:00:01Z",
        "notes": "all good",
        "template_version": "3.7.0",
        "workspace_hint": "should-not-leak",
    }
    data.update(overrides)
    return data


def _handoff_payload(**overrides):
    data = {
        "role": "Coder",
        "handoff_to": "Tester",
        "current_phase": "implementation",
        "cycle_number": 12,
        "summary": "Implemented parser. Tests pending.",
        "status": "IN_PROGRESS",
        "confidence": 0.86,
        "git_sync_status": {"verified": True},
        "metrics": {"tests_total": 12, "tests_failed": 0},
    }
    data.update(overrides)
    return data


def test_snapshot_loop_strip_fields_and_handoff_keys(tmp_path: Path, cwd_guard):
    agent = tmp_path / ".agent"
    _write_json(agent / "LOOP_STATE.json", _loop_payload())
    _write_json(agent / "last_handoff.json", _handoff_payload())
    (agent / "STOP").write_text("1", encoding="utf-8")

    store = DashboardStore(tmp_path)
    snap = store.snapshot()
    st = snap["state"]

    assert st["cycle_number"] == 12
    assert st["active_role"] == "Coder"
    assert st["status"] == "IN_PROGRESS"
    assert st["branch"] == "feat-x"
    assert st["last_commit"] == "abc123"
    assert st["git_sync"]["verified"] is True
    assert st["notes"] == "all good"
    assert st["updated_at"] == "2026-08-21T12:00:01Z"
    assert snap["last_handoff_summary"] == "Implemented parser. Tests pending."
    assert snap["last_handoff_status"] == "IN_PROGRESS"
    assert snap["last_handoff_role"] == "Coder"
    assert snap["last_handoff_to"] == "Tester"
    assert snap["last_handoff_confidence"] == 0.86
    assert snap["last_handoff_metrics"]["tests_total"] == 12
    assert snap["stop"] is True
    assert "heartbeat" in snap
    assert Path.cwd() == cwd_guard


def test_snapshot_is_not_cli_key_superset(tmp_path: Path, cwd_guard):
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    snap = DashboardStore(tmp_path).snapshot()
    # Не проверяем snap.keys() >= state.snapshot().keys() — это другой контракт.
    assert _CLI_ONLY.isdisjoint(snap.keys())
    assert _CLI_ONLY.isdisjoint(snap["state"].keys())
    assert "notes" in snap["state"]
    assert "workspace_hint" not in snap["state"]
    assert "last_handoff_summary" not in snap["state"]


def test_open_invest_cap_20_deltas_last_5(tmp_path: Path, cwd_guard):
    invest = [f"T-{i}" for i in range(25)]
    deltas = [{"ts": str(i), "role": "Coder", "text": f"d{i}"} for i in range(8)]
    _write_json(
        tmp_path / ".agent" / "LOOP_STATE.json",
        _loop_payload(open_invest=invest, recent_deltas=deltas),
    )
    st = DashboardStore(tmp_path).snapshot()["state"]
    assert st["open_invest"] == invest[:20]
    assert st["recent_deltas"] == deltas[-5:]


def test_explicit_paths_ignore_cwd_agent(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    _write_json(
        cwd_wd / ".agent" / "LOOP_STATE.json",
        _loop_payload(status="BLOCKED", active_role="Tester"),
    )
    _write_json(
        real_wd / ".agent" / "LOOP_STATE.json",
        _loop_payload(status="IN_PROGRESS", active_role="Coder"),
    )
    monkeypatch.chdir(cwd_wd)
    snap = DashboardStore(real_wd).snapshot()
    assert snap["state"]["status"] == "IN_PROGRESS"
    assert snap["state"]["active_role"] == "Coder"
    assert Path.cwd() == cwd_wd


def test_store_does_not_chdir(tmp_path: Path, cwd_guard):
    prev = Path.cwd()
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    DashboardStore(tmp_path).snapshot()
    assert Path.cwd() == prev == cwd_guard


def test_missing_loop_state_status_missing(tmp_path: Path, cwd_guard):
    snap = DashboardStore(tmp_path).snapshot()
    assert snap["state"]["status"] == "missing"
    assert snap["last_handoff_summary"] is None
    assert snap["last_handoff_status"] is None
    assert snap["last_handoff_role"] is None
    assert snap["stop"] is False
    assert snap["stale"] is False


def test_torn_handoff_uses_last_good_and_stale(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    agent = tmp_path / ".agent"
    _write_json(agent / "LOOP_STATE.json", _loop_payload())
    _write_json(agent / "last_handoff.json", _handoff_payload(summary="good summary"))
    store = DashboardStore(tmp_path)
    first = store.snapshot()
    assert first["last_handoff_summary"] == "good summary"
    assert first["stale"] is False

    sleeps = []
    monkeypatch.setattr(read_model.time, "sleep", lambda s: sleeps.append(s))
    (agent / "last_handoff.json").write_text("{", encoding="utf-8")
    second = store.snapshot()
    assert second["last_handoff_summary"] == "good summary"
    assert second["stale"] is True
    assert second["state"]["status"] == "IN_PROGRESS"
    assert sleeps == []


def test_torn_loop_state_uses_last_good(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    p = tmp_path / ".agent" / "LOOP_STATE.json"
    _write_json(p, _loop_payload(status="IN_PROGRESS", notes="keep me"))
    store = DashboardStore(tmp_path)
    store.snapshot()
    p.write_text("", encoding="utf-8")
    snap = store.snapshot()
    assert snap["state"]["status"] == "IN_PROGRESS"
    assert snap["state"]["notes"] == "keep me"
    assert snap["stale"] is True


def test_torn_without_cache_does_not_raise(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    sleeps = []
    monkeypatch.setattr(read_model.time, "sleep", lambda s: sleeps.append(s))
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "last_handoff.json").write_text("{not-json", encoding="utf-8")
    store = DashboardStore(tmp_path)
    snap = store.snapshot()
    assert snap["last_handoff_summary"] is None
    assert snap["stale"] is True
    assert sleeps == [0, 0, 0]


def test_stop_absent_and_present(tmp_path: Path, cwd_guard):
    store = DashboardStore(tmp_path)
    assert store.stop_present() is False
    stop = tmp_path / ".agent" / "STOP"
    stop.parent.mkdir(parents=True)
    stop.write_text("1", encoding="utf-8")
    assert store.stop_present() is True
    assert store.snapshot()["stop"] is True


def test_heartbeat_missing_is_unknown(tmp_path: Path, cwd_guard):
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "unknown"
    assert hb["label"] == "liveness unknown"


def test_heartbeat_fresh_running(tmp_path: Path, cwd_guard):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 4242, "role": "Coder", "status": "IN_PROGRESS", "ts": ts},
    )
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "running"
    assert "4242" in hb["label"]
    assert "Coder" in hb["label"]


def test_torn_heartbeat_does_not_flip_strip_stale(
    tmp_path: Path, monkeypatch, cwd_guard
):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 4242, "role": "Coder", "status": "IN_PROGRESS", "ts": ts},
    )
    store = DashboardStore(tmp_path)
    first = store.snapshot()
    assert first["stale"] is False
    assert first["heartbeat"]["status"] == "running"

    (tmp_path / ".agent" / "supervisor.heartbeat").write_text("{", encoding="utf-8")
    snap = store.snapshot()
    assert snap["stale"] is False
    hb = store.heartbeat()
    assert hb["status"] in ("running", "unknown")
    if hb["status"] == "running":
        assert hb.get("pid") == 4242

    cold = DashboardStore(tmp_path).snapshot()
    assert cold["stale"] is False
    assert cold["heartbeat"]["status"] == "unknown"

    (tmp_path / ".agent" / "last_handoff.json").write_text("{", encoding="utf-8")
    torn_ho = store.snapshot()
    assert torn_ho["stale"] is True


def test_heartbeat_stale_file(tmp_path: Path, cwd_guard):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace(
        "+00:00", "Z"
    )
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 7, "role": "Tester", "status": "IN_PROGRESS", "ts": ts},
    )
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "stale"
    assert hb["label"] == "not running / stale"
