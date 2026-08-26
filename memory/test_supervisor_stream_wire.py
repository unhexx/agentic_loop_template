# -*- coding: utf-8 -*-
"""Проводка забора потока, STOP fan-out и --push в CLI супервизора."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from memory.stream_context import use_stream
from memory.stream_fence import FENCE_OVERHEAD_CHARS, fence_block
from memory.stream_stop import STOP_BODY
from memory.supervisor import (
    Terminal,
    build_role_prompt,
    maybe_create_pr,
    run_loop,
)

_STREAM_ENV = ("AGENTIX_STREAM", "AGENTIX_OWNED_PATHS", "AGENTIX_WORKTREE")
_FENCE_MARK = "## Stream fence (mandatory)"


@pytest.fixture
def no_stream_env(monkeypatch):
    for key in _STREAM_ENV:
        monkeypatch.delenv(key, raising=False)


def _write_coder_prompt(workdir: Path, body: str = "# Coder\nDo code.\n") -> None:
    prompts = workdir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "short_coder_prompt.md").write_text(body, encoding="utf-8")
    (workdir / ".agent").mkdir(parents=True, exist_ok=True)


def _setup_mock_cycle(tmp_path: Path, monkeypatch) -> Path:
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
                    "max_cycles": 1,
                    "max_role_retries": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_build_role_prompt_appends_fence(tmp_path: Path, no_stream_env) -> None:
    _write_coder_prompt(tmp_path)
    wt = "/tmp/wt-harness"
    with use_stream(name="harness", owned_paths="memory/,tools/", worktree=wt):
        prompt = build_role_prompt("Coder", None, tmp_path)
        fence = fence_block()
    assert _FENCE_MARK in prompt
    assert "`harness`" in prompt
    assert "memory/,tools/" in prompt
    assert wt in prompt
    assert prompt.endswith(fence)
    assert "You are the **Coder**" in prompt
    assert len(fence) <= FENCE_OVERHEAD_CHARS


def test_build_role_prompt_without_stream_has_no_fence(
    tmp_path: Path, no_stream_env
) -> None:
    _write_coder_prompt(tmp_path)
    prompt = build_role_prompt("Coder", None, tmp_path)
    assert "You are the **Coder**" in prompt
    assert _FENCE_MARK not in prompt
    assert "Stream fence" not in prompt
    assert fence_block() == ""


def test_build_role_prompt_appends_fence_after_compress(
    tmp_path: Path, monkeypatch, no_stream_env
) -> None:
    _write_coder_prompt(tmp_path)
    compressed = "COMPRESSED_BODY_NO_FENCE"
    monkeypatch.setattr(
        "memory.supervisor._maybe_compress_prompt",
        lambda text, workdir: compressed,
    )
    with use_stream(name="docs", owned_paths="docs/", worktree="/tmp/wt-docs"):
        fence = fence_block()
        prompt = build_role_prompt("Coder", None, tmp_path)
    assert prompt.startswith(compressed)
    assert prompt == compressed + fence
    assert _FENCE_MARK in prompt
    assert "`docs`" in prompt
    assert prompt.index(_FENCE_MARK) > prompt.index(compressed)
    extra = len(prompt) - len(compressed)
    assert extra == len(fence)
    assert extra <= FENCE_OVERHEAD_CHARS


def test_mock_cycle_without_stream_has_no_fence(
    tmp_path: Path, monkeypatch, no_stream_env
) -> None:
    from memory import supervisor as s

    _setup_mock_cycle(tmp_path, monkeypatch)
    captured: list[str] = []
    real = s.build_role_prompt

    def wrapped(role, handoff_in, workdir):
        prompt = real(role, handoff_in, workdir)
        captured.append(prompt)
        return prompt

    monkeypatch.setattr(s, "build_role_prompt", wrapped)
    result = run_loop(
        workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
    )
    assert result["exit_code"] == 0, result
    assert captured
    for prompt in captured:
        assert "You are the **" in prompt
        assert _FENCE_MARK not in prompt
        assert "Stream fence" not in prompt


def test_stop_cli_fanout(tmp_path: Path) -> None:
    from memory import supervisor as s

    hub = tmp_path / "hub"
    wt = tmp_path / "agentic-loop-worktrees" / "wt-a"
    (hub / ".agent").mkdir(parents=True)
    (wt / ".agent").mkdir(parents=True)
    (hub / ".agent" / "streams_state.json").write_text(
        json.dumps(
            {
                "streams": {
                    "harness": {
                        "worktree": str(wt.resolve()),
                        "status": "RUNNING",
                    }
                },
                "terminal": "IN_PROGRESS",
            }
        ),
        encoding="utf-8",
    )
    code = s.main(["stop", "--workdir", str(hub)])
    assert code == 0
    hub_stop = hub / ".agent" / "STOP"
    wt_stop = wt / ".agent" / "STOP"
    assert hub_stop.is_file()
    assert wt_stop.is_file()
    assert hub_stop.read_text(encoding="utf-8") == STOP_BODY
    assert wt_stop.read_text(encoding="utf-8") == "1"


def test_cli_parses_push(tmp_path: Path, monkeypatch) -> None:
    from memory import supervisor as s

    calls: dict = {}

    def fake_run_parallel(**kwargs):
        calls.update(kwargs)
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0, "streams": {}}

    monkeypatch.setattr("memory.supervisor_parallel.run_parallel", fake_run_parallel)
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
            "--push",
        ]
    )
    assert code == 0
    assert calls.get("push") is True
    assert len(calls.get("plans") or []) == 2


def test_cli_push_default_is_false(tmp_path: Path, monkeypatch) -> None:
    from memory import supervisor as s

    calls: dict = {}

    def fake_run_parallel(**kwargs):
        calls.update(kwargs)
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0, "streams": {}}

    monkeypatch.setattr("memory.supervisor_parallel.run_parallel", fake_run_parallel)
    code = s.main(
        [
            "run-parallel",
            "--stream",
            "harness:memory/",
            "--workdir",
            str(tmp_path),
            "--no-pr",
            "--skip-provision",
        ]
    )
    assert code == 0
    assert "push" in calls
    assert calls.get("push") is False


def test_maybe_create_pr_signature_unchanged() -> None:
    params = list(inspect.signature(maybe_create_pr).parameters)
    assert params == ["workdir", "sup"]
