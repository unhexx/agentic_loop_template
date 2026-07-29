# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from memory.adapters import get_adapter
from memory.adapters.grok import extract_json_object


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
