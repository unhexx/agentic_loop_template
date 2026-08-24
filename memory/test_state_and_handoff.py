# -*- coding: utf-8 -*-
"""Tests for bounded state + handoff validation + experience seeds."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from memory import state as state_mod
from memory.validate_handoff import validate_handoff
from memory.experience_harvester import DEFAULT_SEEDS, dedupe
from memory.context_budget import estimate_tokens, check_files


@pytest.fixture()
def tmp_agent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # rebind module paths
    monkeypatch.setattr(state_mod, "AGENT_DIR", tmp_path / ".agent")
    monkeypatch.setattr(state_mod, "STATE_JSON", tmp_path / ".agent" / "LOOP_STATE.json")
    monkeypatch.setattr(state_mod, "STATE_MD", tmp_path / ".agent" / "LOOP_STATE.md")
    monkeypatch.setattr(state_mod, "HISTORY_DIR", tmp_path / ".agent" / "history")
    monkeypatch.setattr(state_mod, "METRICS_JSONL", tmp_path / ".agent" / "metrics.jsonl")
    return tmp_path


def test_state_init_and_snapshot_small(tmp_agent):
    st = state_mod.default_state()
    state_mod.save_state(st)
    snap = state_mod.snapshot()
    assert snap["cycle_number"] == 0
    assert state_mod.STATE_JSON.exists()
    assert state_mod.STATE_JSON.stat().st_size < 8 * 1024
    assert state_mod.STATE_MD.stat().st_size < 8 * 1024


def test_append_delta_caps(tmp_agent):
    state_mod.save_state(state_mod.default_state())
    for i in range(10):
        state_mod.append_delta(f"delta {i}", role="Coder")
    st = state_mod.load_state()
    assert len(st["recent_deltas"]) <= state_mod.MAX_DELTAS


def test_migrate_large_md(tmp_agent):
    agent = tmp_agent / ".agent"
    agent.mkdir(parents=True)
    bloated = agent / "LOOP_STATE.md"
    bloated.write_text("x" * (20 * 1024) + "\nverified: true\n", encoding="utf-8")
    st = state_mod.load_state()
    assert st["git_sync"]["verified"] is True
    assert (agent / "history").exists()
    assert any(agent.joinpath("history").iterdir())


def test_handoff_done_rules():
    bad = {
        "handoff_to": "None",
        "role": "Reviewer",
        "current_phase": "finalization",
        "cycle_number": 1,
        "summary": "done",
        "status": "DONE",
        "confidence": 0.9,
    }
    ok, errors = validate_handoff(bad)
    assert not ok
    assert any("git_sync" in e or "sync_waived" in e for e in errors)

    good = {
        **bad,
        "sync_waived": "single-repo feature branch dogfood",
        "lessons_learned": ["use state snapshot"],
        "metrics": {"tests_total": 3, "tests_failed": 0, "tool_calls": 2},
    }
    ok2, errors2 = validate_handoff(good)
    assert ok2, errors2


def test_validate_handoff_uses_jsonschema():
    pytest.importorskip("jsonschema")
    from memory import validate_handoff as vh

    good = {
        "handoff_to": "None",
        "role": "Reviewer",
        "current_phase": "finalization",
        "cycle_number": 1,
        "summary": "done",
        "status": "DONE",
        "confidence": 0.9,
        "sync_waived": "single-repo feature branch dogfood",
        "lessons_learned": ["use state snapshot"],
        "metrics": {"tests_total": 3, "tests_failed": 0, "tool_calls": 2},
    }
    ok, errors = vh.validate_handoff(good)
    assert ok, errors
    assert vh._last_backend == "jsonschema"

    bad_role = {
        "handoff_to": "Coder",
        "role": "NotARole",
        "current_phase": "planning",
        "cycle_number": 1,
        "summary": "x",
        "status": "IN_PROGRESS",
        "confidence": 0.5,
    }
    ok_bad, err_bad = vh.validate_handoff(bad_role, strict_done=False)
    assert not ok_bad
    assert err_bad
    assert vh._last_backend == "jsonschema"


def test_log_metrics_agent_dir(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    agent = tmp_path / "work" / ".agent"
    state_mod.log_metrics({"event": "handoff_invalid", "errors": 2}, agent_dir=agent)
    dest = agent / "metrics.jsonl"
    assert dest.is_file()
    row = json.loads(dest.read_text(encoding="utf-8").splitlines()[-1])
    assert row["event"] == "handoff_invalid"
    assert row["errors"] == 2
    assert not (cwd / ".agent" / "metrics.jsonl").exists()


def test_snapshot_agent_dir_without_chdir(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    agent = tmp_path / "work" / ".agent"
    st = state_mod.default_state()
    st["cycle_number"] = 7
    state_mod.save_state(st, agent_dir=agent)
    snap = state_mod.snapshot(agent_dir=agent)
    assert snap["cycle_number"] == 7
    assert snap["history_dir"] == str(agent / "history")
    assert (agent / "LOOP_STATE.json").is_file()
    assert (agent / "LOOP_STATE.md").is_file()
    assert not (cwd / ".agent").exists()


def test_append_delta_agent_dir_writes_history_under_tmp(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    agent = tmp_path / "work" / ".agent"
    state_mod.save_state(state_mod.default_state(), agent_dir=agent)
    state_mod.append_delta("hello from elsewhere", role="Coder", agent_dir=agent)
    hist = list((agent / "history").glob("loop_state-*.jsonl"))
    assert hist
    blob = hist[0].read_text(encoding="utf-8")
    assert "hello from elsewhere" in blob
    assert not (cwd / ".agent").exists()


def test_load_state_corrupt_json_logs_error(tmp_path, monkeypatch, caplog):
    import logging

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    agent = tmp_path / "work" / ".agent"
    agent.mkdir(parents=True)
    (agent / "LOOP_STATE.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="memory.state"):
        st = state_mod.load_state(agent_dir=agent)
    assert st["status"] == "READY"
    assert any("load_state" in rec.getMessage() for rec in caplog.records)


def test_experience_seeds_dedupe():
    rows = dedupe(DEFAULT_SEEDS + DEFAULT_SEEDS)
    assert len(rows) == len(DEFAULT_SEEDS)
    assert len(rows) >= 30


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 100) >= 50


def test_budget_check(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello " * 100, encoding="utf-8")
    report = check_files([f], budget=10)
    assert report["total_tokens"] >= 1
    assert "within_budget" in report
