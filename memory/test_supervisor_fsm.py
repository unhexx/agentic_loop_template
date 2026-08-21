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


from memory.supervisor import build_role_prompt, load_config, load_last_handoff, save_handoff


def test_build_role_prompt_includes_knowledge_when_db_seeded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "short_coder_prompt.md").write_text(
        "# Coder\nDo code.\n", encoding="utf-8"
    )
    (tmp_path / ".agent").mkdir()
    from memory.knowledge import db_path, upsert

    db = db_path(cwd=tmp_path)
    upsert(
        source="docs/git.md",
        title="Git preflight",
        content="Always run preflight_git.sh before worktree sync.",
        category="playbook",
        db=db,
        provenance="test",
    )
    prompt = build_role_prompt(
        "Coder",
        handoff_in={"summary": "git preflight before sync"},
        workdir=tmp_path,
    )
    assert "Local knowledge" in prompt
    assert "Git preflight" in prompt
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "prompts").mkdir()
    (empty / "prompts" / "short_coder_prompt.md").write_text("# Coder\n", encoding="utf-8")
    (empty / ".agent").mkdir()
    bare = build_role_prompt("Coder", handoff_in=None, workdir=empty)
    assert "Local knowledge" not in bare


def test_build_role_prompt_mentions_role_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "short_coder_prompt.md").write_text(
        "# Coder\nDo code.\n", encoding="utf-8"
    )
    (tmp_path / ".agent").mkdir()
    prompt = build_role_prompt("Coder", handoff_in=None, workdir=tmp_path)
    assert "Coder" in prompt or "code" in prompt.lower()
    assert "last_handoff" in prompt or "handoff" in prompt.lower() or "JSON" in prompt
    assert ".agent/history" not in prompt or "Do NOT" in prompt or "never" in prompt.lower()
    assert "tools/select.py" in prompt


def test_save_and_load_last_handoff_roundtrip(tmp_path: Path):
    data = {
        "role": "Coder",
        "handoff_to": "Tester",
        "status": "IN_PROGRESS",
        "summary": "implemented feature",
        "context_delta": "added parser",
        "metrics": {"tests_failed": 0},
    }
    path = save_handoff(tmp_path, data)
    assert path == tmp_path / ".agent" / "last_handoff.json"
    assert path.is_file()
    loaded = load_last_handoff(tmp_path)
    assert loaded is not None
    assert loaded["role"] == "Coder"
    assert loaded["handoff_to"] == "Tester"
    assert loaded["summary"] == "implemented feature"
    assert load_last_handoff(tmp_path / "missing") is None


def test_load_config_reads_project_config(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    cfg = {
        "supervisor": {
            "adapter": "mock",
            "max_cycles": 3,
        }
    }
    (agent / "project_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    loaded = load_config(tmp_path)
    assert loaded.get("supervisor", {}).get("adapter") == "mock"
    assert loaded["supervisor"]["max_cycles"] == 3


def test_load_config_falls_back_to_example(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    example = {"supervisor": {"adapter": "grok", "max_cycles": 1}}
    (agent / "project_config.example.json").write_text(
        json.dumps(example), encoding="utf-8"
    )
    loaded = load_config(tmp_path)
    assert loaded["supervisor"]["adapter"] == "grok"


def test_build_role_prompt_includes_handoff_delta(tmp_path: Path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "short_tester_prompt.md").write_text(
        "# Tester\nRun tests.\n", encoding="utf-8"
    )
    (tmp_path / ".agent").mkdir()
    handoff = {
        "role": "Coder",
        "summary": "shipped module X",
        "context_delta": "new file a.py",
        "status": "IN_PROGRESS",
        "handoff_to": "Tester",
    }
    prompt = build_role_prompt("Tester", handoff_in=handoff, workdir=tmp_path)
    assert "shipped module X" in prompt
    assert "new file a.py" in prompt
    assert "Tester" in prompt

