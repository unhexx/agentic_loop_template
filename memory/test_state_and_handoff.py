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
