# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from memory.adapters.blackbox import _child_env
from memory.adapters.cursor import CursorAdapter
from memory.adapters.grok import GrokAdapter
from memory.adapters.persist import persist_role_handoff
from memory.adapters.proc import run_cli
from memory.stream_context import apply_stream_env, use_stream

_STREAM_KEYS = ("AGENTIX_STREAM", "AGENTIX_OWNED_PATHS", "AGENTIX_WORKTREE")


@pytest.fixture
def clean_stream_env(monkeypatch):
    for key in _STREAM_KEYS:
        monkeypatch.delenv(key, raising=False)


def _valid_in_progress(**overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
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


def _stream_snapshot() -> Dict[str, Any]:
    return {k: os.environ.get(k) for k in _STREAM_KEYS}


class _FakeRun:
    def __init__(self, stdout: str, captured: Dict[str, Any]) -> None:
        self._stdout = stdout
        self._captured = captured

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._captured["env"] = kwargs.get("env")
        self._captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stdout = self._stdout
            stderr = ""

        return R()


def test_apply_stream_env_from_contextvar(clean_stream_env):
    before = _stream_snapshot()
    incoming = {"FOO": "bar"}
    with use_stream(
        name="harness",
        owned_paths="memory/,tools/",
        worktree="/tmp/wt-harness",
    ):
        out = apply_stream_env(incoming)
        assert out["AGENTIX_STREAM"] == "harness"
        assert out["AGENTIX_OWNED_PATHS"] == "memory/,tools/"
        assert out["AGENTIX_WORKTREE"] == "/tmp/wt-harness"
        assert out["FOO"] == "bar"
        assert incoming == {"FOO": "bar"}
        assert _stream_snapshot() == before
    assert _stream_snapshot() == before
    assert os.environ.get("AGENTIX_STREAM") is None


def test_apply_stream_env_contextvar_wins(clean_stream_env):
    incoming = {
        "AGENTIX_STREAM": "stale",
        "AGENTIX_OWNED_PATHS": "other/",
        "AGENTIX_WORKTREE": "/old",
    }
    with use_stream(name="harness", owned_paths="memory/", worktree="/tmp/wt"):
        out = apply_stream_env(incoming)
    assert out["AGENTIX_STREAM"] == "harness"
    assert out["AGENTIX_OWNED_PATHS"] == "memory/"
    assert out["AGENTIX_WORKTREE"] == "/tmp/wt"
    assert incoming["AGENTIX_STREAM"] == "stale"
    assert os.environ.get("AGENTIX_STREAM") is None


def test_apply_stream_env_keeps_caller_keys_without_stream(clean_stream_env):
    incoming = {"AGENTIX_STREAM": "from-caller", "FOO": "1"}
    out = apply_stream_env(incoming)
    assert out["AGENTIX_STREAM"] == "from-caller"
    assert "AGENTIX_OWNED_PATHS" not in out
    assert os.environ.get("AGENTIX_STREAM") is None


def test_grok_child_env_includes_stream(tmp_path, monkeypatch, clean_stream_env):
    captured: Dict[str, Any] = {}
    stdout = json.dumps(_valid_in_progress())
    monkeypatch.setattr(
        "memory.adapters.grok.shutil.which", lambda _cmd: "/usr/bin/grok"
    )
    monkeypatch.setattr(
        "memory.adapters.grok.subprocess.run", _FakeRun(stdout, captured)
    )
    monkeypatch.setattr("memory.adapters.grok.assert_ready", lambda *a, **k: None)
    before = _stream_snapshot()
    ad = GrokAdapter({"command": "grok"})
    with use_stream(
        name="harness",
        owned_paths="memory/,tools/",
        worktree=str(tmp_path),
    ):
        out = ad.run_role_turn(
            role="Orchestrator",
            prompt="x",
            handoff_in_path=None,
            workdir=tmp_path,
            timeout_s=5,
        )
    env = captured.get("env")
    assert isinstance(env, dict)
    assert env["AGENTIX_STREAM"] == "harness"
    assert env["AGENTIX_OWNED_PATHS"] == "memory/,tools/"
    assert env["AGENTIX_WORKTREE"] == str(tmp_path)
    assert _stream_snapshot() == before
    saved = json.loads(Path(out).read_text(encoding="utf-8"))
    assert saved["stream"] == "harness"
    assert saved["owned_paths"] == ["memory/", "tools/"]
    assert saved["worktree"] == str(tmp_path)


def test_cursor_passes_env(tmp_path, monkeypatch, clean_stream_env):
    captured: Dict[str, Any] = {}
    stdout = json.dumps(_valid_in_progress())
    monkeypatch.setattr(
        "memory.adapters.cursor.shutil.which", lambda _cmd: "/usr/bin/cursor"
    )
    monkeypatch.setattr(
        "memory.adapters.cursor.subprocess.run", _FakeRun(stdout, captured)
    )
    before = _stream_snapshot()
    ad = CursorAdapter({"command": "cursor"})
    with use_stream(
        name="docs",
        owned_paths="docs/",
        worktree=str(tmp_path),
    ):
        out = ad.run_role_turn(
            role="Orchestrator",
            prompt="x",
            handoff_in_path=None,
            workdir=tmp_path,
            timeout_s=5,
        )
    env = captured.get("env")
    assert isinstance(env, dict)
    assert env["AGENTIX_STREAM"] == "docs"
    assert env["AGENTIX_OWNED_PATHS"] == "docs/"
    assert env["AGENTIX_WORKTREE"] == str(tmp_path)
    assert "env" in (captured.get("kwargs") or {})
    assert _stream_snapshot() == before
    saved = json.loads(Path(out).read_text(encoding="utf-8"))
    assert saved["stream"] == "docs"
    assert saved["owned_paths"] == ["docs/"]
    assert saved["worktree"] == str(tmp_path)


def test_blackbox_child_env_includes_stream(tmp_path, clean_stream_env):
    before = _stream_snapshot()
    with use_stream(
        name="harness",
        owned_paths="memory/",
        worktree=str(tmp_path),
    ):
        env = _child_env(tmp_path)
    assert env["AGENTIX_STREAM"] == "harness"
    assert env["AGENTIX_OWNED_PATHS"] == "memory/"
    assert env["AGENTIX_WORKTREE"] == str(tmp_path)
    assert env["AGENTIX_PROJECT_ROOT"] == str(tmp_path.resolve())
    assert _stream_snapshot() == before


def test_persist_stamps_stream_fields(tmp_path, clean_stream_env):
    before = _stream_snapshot()
    data = _valid_in_progress()
    with use_stream(
        name="harness",
        owned_paths="memory/,tools/",
        worktree=str(tmp_path / "wt"),
    ):
        path = persist_role_handoff(tmp_path, data)
    assert _stream_snapshot() == before
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["stream"] == "harness"
    assert saved["owned_paths"] == ["memory/", "tools/"]
    assert saved["worktree"] == str(tmp_path / "wt")
    assert os.environ.get("AGENTIX_STREAM") is None


def test_persist_overwrites_mismatch(tmp_path, caplog, clean_stream_env):
    data = _valid_in_progress(
        stream="stale",
        owned_paths=["nope/"],
        worktree="/old",
    )
    caplog.set_level(logging.WARNING, logger="memory.adapters")
    with use_stream(
        name="harness",
        owned_paths="memory/",
        worktree="/tmp/wt",
    ):
        path = persist_role_handoff(tmp_path, data)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["stream"] == "harness"
    assert saved["owned_paths"] == ["memory/"]
    assert saved["worktree"] == "/tmp/wt"
    text = caplog.text
    assert "stream mismatch" in text
    assert "owned_paths mismatch" in text
    assert "worktree mismatch" in text
    assert "stale" in text
    assert "harness" in text


def test_persist_without_stream_leaves_keys_absent(tmp_path, clean_stream_env):
    path = persist_role_handoff(tmp_path, _valid_in_progress())
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "stream" not in saved
    assert "owned_paths" not in saved
    assert "worktree" not in saved


def _write_env_script(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(
        "import os\n"
        "print(os.environ.get('AGENTIX_STREAM', 'ABSENT'))\n",
        encoding="utf-8",
    )
    return path


def test_run_cli_applies_when_env_is_none(tmp_path, clean_stream_env):
    script = _write_env_script(tmp_path, "env_none.py")
    before = _stream_snapshot()
    with use_stream(name="harness", owned_paths="memory/", worktree=str(tmp_path)):
        r = run_cli([sys.executable, str(script)], cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0
    assert "harness" in (r.stdout or "")
    assert _stream_snapshot() == before


def test_run_cli_skips_apply_when_env_passed(tmp_path, clean_stream_env):
    script = _write_env_script(tmp_path, "env_passed.py")
    env = os.environ.copy()
    env.pop("AGENTIX_STREAM", None)
    with use_stream(name="harness", owned_paths="memory/", worktree=str(tmp_path)):
        r = run_cli(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout_s=10,
            env=env,
        )
    assert r.returncode == 0
    assert "ABSENT" in (r.stdout or "")
    assert os.environ.get("AGENTIX_STREAM") is None
