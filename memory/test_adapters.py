# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from memory.adapters import get_adapter
from memory.adapters.grok import (
    HandoffExtractError,
    extract_handoff,
    extract_json_object,
)
from memory.adapters.persist import persist_role_handoff


def test_extract_json_object_from_prose():
    text = (
        'Here you go:\n{"handoff_to":"Coder","role":"Orchestrator",'
        '"current_phase":"planning","cycle_number":1,"summary":"x",'
        '"status":"IN_PROGRESS","confidence":0.9}\nthanks'
    )
    data = extract_json_object(text)
    assert data["role"] == "Orchestrator"
    assert data["handoff_to"] == "Coder"


def test_extract_json_object_picks_last():
    text = '{"a": 1}\nmore text\n{"role": "Tester", "b": 2}'
    data = extract_json_object(text)
    assert data["role"] == "Tester"
    assert data["b"] == 2


def test_extract_json_object_missing_raises():
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json_object("no braces here")


def test_cursor_not_configured_raises():
    ad = get_adapter(
        "cursor",
        {"supervisor": {"adapters": {"cursor": {"command": None}}}},
    )
    with pytest.raises(RuntimeError, match="not configured"):
        ad.run_role_turn(
            role="Coder",
            prompt="x",
            handoff_in_path=None,
            workdir=Path("."),
            timeout_s=5,
        )


def test_blackbox_not_configured_raises():
    ad = get_adapter(
        "blackbox",
        {"supervisor": {"adapters": {"blackbox": {"command": None}}}},
    )
    with pytest.raises(RuntimeError, match="not configured"):
        ad.run_role_turn(
            role="Coder",
            prompt="x",
            handoff_in_path=None,
            workdir=Path("."),
            timeout_s=5,
        )


def test_get_adapter_grok_from_supervisor_section():
    ad = get_adapter(
        "grok",
        {"supervisor": {"adapters": {"grok": {"command": "grok"}}}},
    )
    assert ad.name == "grok"
    assert ad.command == "grok"


@pytest.mark.skipif(not shutil.which("grok"), reason="grok not installed")
def test_grok_smoke_on_path():
    """Live smoke only when grok binary exists; does not require network success."""
    ad = get_adapter("grok", {"supervisor": {"adapters": {"grok": {"command": "grok"}}}})
    assert shutil.which(ad.command)
    # Do not invoke network-backed role turn in unit suite.
    assert ad.name == "grok"


def _valid_in_progress(**overrides):
    data = {
        "handoff_to": "Coder",
        "role": "Orchestrator",
        "current_phase": "planning",
        "cycle_number": 1,
        "summary": "plan",
        "status": "IN_PROGRESS",
        "confidence": 0.9,
    }
    data.update(overrides)
    return data


def _invalid_done():
    return {
        "handoff_to": "None",
        "role": "Reviewer",
        "current_phase": "finalization",
        "cycle_number": 1,
        "summary": "done",
        "status": "DONE",
        "confidence": 0.9,
    }


def test_extract_json_object_nested_braces():
    inner = _valid_in_progress(summary="see {nested} and {more}")
    text = "prose " + json.dumps(inner) + " tail"
    data = extract_json_object(text)
    assert data["summary"] == "see {nested} and {more}"
    assert extract_handoff(text)["role"] == "Orchestrator"


def test_extract_handoff_skips_trailing_non_handoff():
    text = json.dumps(_valid_in_progress()) + '\n{"ok": true}'
    assert extract_handoff(text)["role"] == "Orchestrator"
    assert extract_json_object(text) == {"ok": True}


def test_extract_handoff_prefers_in_progress_over_invalid_done(tmp_path):
    text = json.dumps(_valid_in_progress()) + "\n" + json.dumps(_invalid_done())
    got = extract_handoff(text)
    assert got["status"] == "IN_PROGRESS"
    assert got["role"] == "Orchestrator"
    p = persist_role_handoff(tmp_path, got)
    assert p.is_file()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["status"] == "IN_PROGRESS"


def test_persist_invalid_does_not_write(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    workdir = tmp_path / "work"
    workdir.mkdir()
    target = workdir / ".agent" / "last_handoff.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"keep": true}', encoding="utf-8")
    with pytest.raises(HandoffExtractError):
        persist_role_handoff(workdir, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}
    metrics = (workdir / ".agent" / "metrics.jsonl").read_text(encoding="utf-8")
    assert "handoff_invalid" in metrics
    assert not (cwd / ".agent" / "metrics.jsonl").exists()


def test_greedy_garbage_raises_handoff_extract_error():
    text = "noise {not json, trailing {braces} too} end"
    with pytest.raises(HandoffExtractError):
        extract_handoff(text)
    try:
        extract_handoff(text)
    except json.JSONDecodeError:
        pytest.fail("raw JSONDecodeError")
    except HandoffExtractError:
        pass
