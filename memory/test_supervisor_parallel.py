# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from memory.streams import StreamPlan
from memory.supervisor import Terminal
from memory.supervisor_parallel import run_parallel


def _stream_workdir(base: Path, name: str) -> Path:
    wd = base / name
    wd.mkdir()
    (wd / "prompts").mkdir()
    for n in ("orchestrator", "coder", "tester", "debugger", "reviewer"):
        (wd / "prompts" / f"short_{n}_prompt.md").write_text(f"# {n}\n", encoding="utf-8")
    (wd / ".agent").mkdir()
    (wd / ".agent" / "project_config.json").write_text(
        json.dumps(
            {
                "supervisor": {
                    "adapter": "mock",
                    "max_cycles": 1,
                    "max_role_retries": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    return wd


def test_run_parallel_two_streams_pr_ready(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agent").mkdir()
    a = _stream_workdir(tmp_path, "wt-a")
    b = _stream_workdir(tmp_path, "wt-b")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
        StreamPlan(name="docs", owned_paths=["docs/"], worktree=str(b), branch="feature/c-docs"),
    ]

    import memory.streams as streams_mod
    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    monkeypatch.setattr(
        "memory.supervisor_parallel.merge_stream_branch",
        lambda **kwargs: {"ok": True, "skipped": True},
    )
    monkeypatch.setattr(
        "memory.supervisor_parallel.maybe_create_integration_pr",
        lambda **kwargs: Terminal.PR_READY_LOCAL,
    )

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=True,
        base_ref="main",
        skip_provision=True,
    )
    assert result["exit_code"] == 0, result
    assert result["terminal"] in (Terminal.PR_READY, Terminal.PR_READY_LOCAL, "PR_READY", "PR_READY_LOCAL")
    assert result["streams"]["harness"]["status"] in ("STREAM_READY", "MERGED")
    assert len(result["streams"]) == 2


def test_run_parallel_blocks_on_ownership(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agent").mkdir()
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod
    monkeypatch.setattr(
        streams_mod,
        "list_changed_files",
        lambda workdir, base_ref="main": ["DEVELOPMENT_STANDARDS.md"],
    )
    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
    )
    assert result["exit_code"] == 1
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")


def test_cli_run_parallel_parses(tmp_path, monkeypatch):
    from memory import supervisor as s
    from memory.supervisor import Terminal
    calls = {}

    def fake_run_parallel(**kwargs):
        calls.update(kwargs)
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0, "streams": {}}

    monkeypatch.setattr(
        "memory.supervisor_parallel.run_parallel", fake_run_parallel
    )
    code = s.main(
        [
            "run-parallel",
            "--stream",
            "harness:memory/",
            "--stream",
            "docs:docs/",
            "--workdir",
            str(tmp_path),
            "--no-pr",
            "--skip-provision",
        ]
    )
    assert code == 0
    assert len(calls.get("plans") or []) == 2
