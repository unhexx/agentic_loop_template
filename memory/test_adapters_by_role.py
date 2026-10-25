# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from memory.supervisor import (
    _maybe_meta_harvest_on_done,
    _maybe_stack_check,
    resolve_adapter_for_role,
    run_loop,
)


def test_resolve_adapter_for_role_defaults():
    assert resolve_adapter_for_role({}, "Coder") == "mock"
    assert resolve_adapter_for_role({"adapter": "grok"}, "Coder") == "grok"


def test_resolve_adapter_for_role_map_and_cli():
    sup = {
        "adapter": "grok",
        "adapters_by_role": {"Coder": "cursor", "Reviewer": "grok"},
    }
    assert resolve_adapter_for_role(sup, "Coder") == "cursor"
    assert resolve_adapter_for_role(sup, "Orchestrator") == "grok"
    assert resolve_adapter_for_role(sup, "Coder", cli_adapter="mock") == "mock"


def test_stack_check_opt_in(tmp_path: Path):
    assert _maybe_stack_check(tmp_path, {}) is None
    assert _maybe_stack_check(tmp_path, {"stack_check_on_start": False}) is None
    err = _maybe_stack_check(tmp_path, {"stack_check_on_start": True})
    assert err is not None
    assert "stack check failed" in err


def test_run_loop_blocks_on_stack_check(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text(
        '{"supervisor": {"adapter": "mock", "stack_check_on_start": true, "max_cycles": 1}}',
        encoding="utf-8",
    )
    result = run_loop(tmp_path, adapter_name="mock", max_cycles=1, create_pr=False)
    assert result["terminal"].value == "BLOCKED"
    assert "stack check" in str(result.get("reason", "")).lower()


def test_meta_harvest_on_done_is_opt_in(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_harvest(path, cycle, outcome="DONE", quality_signals=None, agent_dir=None):
        calls.append((Path(path), cycle, outcome, Path(agent_dir) if agent_dir else None))
        return "T-1"

    monkeypatch.setattr("memory.meta_harvester.harvest_from_handoff", fake_harvest)
    work = tmp_path / "repo"
    agent = work / ".agent"
    agent.mkdir(parents=True)
    handoff = agent / "last_handoff.json"
    handoff.write_text("{}", encoding="utf-8")

    _maybe_meta_harvest_on_done(work, {}, 4)
    assert calls == []

    def _write(body: dict) -> None:
        (agent / "project_config.json").write_text(
            json.dumps({"meta_optimizer": body}), encoding="utf-8"
        )

    _write({"auto_harvest_on_done": False})
    _maybe_meta_harvest_on_done(work, {}, 4)
    _write({"auto_harvest_on_done": "true"})
    _maybe_meta_harvest_on_done(work, {}, 4)
    _write({"enabled": False, "auto_harvest_on_done": True})
    _maybe_meta_harvest_on_done(work, {}, 4)
    assert calls == []

    _write({"auto_harvest_on_done": True})
    _maybe_meta_harvest_on_done(work, {}, 4)
    assert calls == [(handoff, 4, "DONE", agent)]
