# -*- coding: utf-8 -*-
from pathlib import Path
import json

from memory.supervisor import run_loop, Terminal


def _setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    for name in ("orchestrator", "coder", "tester", "debugger", "reviewer"):
        (tmp_path / "prompts" / f"short_{name}_prompt.md").write_text(
            f"# {name}\n", encoding="utf-8"
        )
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "project_config.json").write_text(
        json.dumps(
            {
                "supervisor": {
                    "adapter": "mock",
                    "max_cycles": 2,
                    "max_role_retries": 1,
                }
            }
        ),
        encoding="utf-8",
    )


def test_mock_full_cycle_pr_ready(tmp_path: Path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_loop(
        workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
    )
    assert result["terminal"] in (
        Terminal.PR_READY,
        Terminal.PR_READY_LOCAL,
        "PR_READY",
        "PR_READY_LOCAL",
    )
    assert (tmp_path / ".agent" / "last_handoff.json").is_file()
    data = json.loads(
        (tmp_path / ".agent" / "last_handoff.json").read_text(encoding="utf-8")
    )
    assert data.get("status") == "DONE"
    assert result.get("exit_code") == 0


def test_three_mock_cycles(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for i in range(3):
        result = run_loop(
            workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
        )
        assert result["exit_code"] == 0, result
