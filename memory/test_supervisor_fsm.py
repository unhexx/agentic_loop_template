# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from memory.adapters.mock import MockAdapter
from memory.validate_handoff import validate_handoff


def test_mock_orchestrator_handoff_valid(tmp_path: Path):
    ad = MockAdapter()
    out = ad.run_role_turn(
        role="Orchestrator",
        prompt="plan",
        handoff_in_path=None,
        workdir=tmp_path,
        timeout_s=5,
    )
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    ok, errors = validate_handoff(data, strict_done=False)
    assert ok, errors
    assert data["role"] == "Orchestrator"
    assert data["handoff_to"] == "Coder"


from memory.supervisor import next_role, Terminal, SupervisorStatus


def test_fsm_happy_path_to_reviewer():
    assert next_role("Orchestrator", {"status": "IN_PROGRESS", "handoff_to": "Coder", "metrics": {}}) == "Coder"
    assert next_role("Coder", {"status": "IN_PROGRESS", "handoff_to": "Tester", "metrics": {}}) == "Tester"
    h = {"status": "IN_PROGRESS", "handoff_to": "Reviewer", "metrics": {"tests_failed": 0}}
    assert next_role("Tester", h) == "Reviewer"


def test_fsm_tester_failed_goes_debugger():
    h = {"status": "IN_PROGRESS", "handoff_to": "Reviewer", "metrics": {"tests_failed": 2}}
    assert next_role("Tester", h) == "Debugger"


def test_fsm_reviewer_done_is_pr_ready():
    h = {"status": "DONE", "handoff_to": "None", "metrics": {"tests_failed": 0}}
    assert next_role("Reviewer", h) == Terminal.PR_READY


def test_fsm_blocked():
    h = {"status": "BLOCKED", "handoff_to": "None", "summary": "x"}
    assert next_role("Coder", h) == Terminal.BLOCKED

