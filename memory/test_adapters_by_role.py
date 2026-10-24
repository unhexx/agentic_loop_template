# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from memory.supervisor import (
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
