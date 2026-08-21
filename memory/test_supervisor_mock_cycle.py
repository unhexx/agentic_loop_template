# -*- coding: utf-8 -*-
from pathlib import Path
import json
import os
import threading
import time

from memory.supervisor import run_loop, Terminal, HEARTBEAT_FILENAME


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
    assert not (tmp_path / ".agent" / HEARTBEAT_FILENAME).exists()


def test_three_mock_cycles(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for i in range(3):
        result = run_loop(
            workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
        )
        assert result["exit_code"] == 0, result


def test_maybe_create_pr_success(monkeypatch, tmp_path):
    from memory import supervisor as s

    calls = []

    def fake_which(name):
        return "/usr/bin/gh" if name == "gh" else None

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = "https://github.com/org/repo/pull/1"
            stderr = ""

        return R()

    monkeypatch.setattr(s.shutil, "which", fake_which)
    monkeypatch.setattr(s.subprocess, "run", fake_run)
    term = s.maybe_create_pr(
        tmp_path, {"pr": {"base": "main", "title_prefix": "agentix:"}}
    )
    assert term == s.Terminal.PR_READY
    assert any("pr" in c and "create" in c for c in calls)
    assert not any("merge" in c for c in calls)
    assert any("--base" in c and "main" in c for c in calls)


def test_maybe_create_pr_fail_local(monkeypatch, tmp_path):
    from memory import supervisor as s

    def fake_which(name):
        return "/usr/bin/gh" if name == "gh" else None

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "fail"

        return R()

    monkeypatch.setattr(s.shutil, "which", fake_which)
    monkeypatch.setattr(s.subprocess, "run", fake_run)
    term = s.maybe_create_pr(tmp_path, {"pr": {"base": "main"}})
    assert term == s.Terminal.PR_READY_LOCAL


def test_maybe_create_pr_no_gh(monkeypatch, tmp_path):
    from memory import supervisor as s

    monkeypatch.setattr(s.shutil, "which", lambda _n: None)
    term = s.maybe_create_pr(tmp_path, {"pr": {"base": "main"}})
    assert term == s.Terminal.PR_READY_LOCAL


def test_heartbeat_interval_default_is_20s():
    from memory import supervisor as s

    assert s.HEARTBEAT_INTERVAL_S == 20.0


def test_heartbeat_during_blocking_turn_and_unlinked_after(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from memory import supervisor as s

    monkeypatch.setattr(s, "HEARTBEAT_INTERVAL_S", 0.05)

    class BlockingAdapter:
        name = "mock"

        def __init__(self):
            self.seen_first = None
            self.ts_refreshed = False
            self.daemon_ok = False
            self.file_during = False

        def run_role_turn(self, role, prompt, handoff_in_path, workdir, timeout_s):
            hb = Path(workdir) / ".agent" / HEARTBEAT_FILENAME
            named = [t for t in threading.enumerate() if t.name == "supervisor-heartbeat"]
            self.daemon_ok = bool(named) and all(t.daemon for t in named)
            deadline = time.monotonic() + 3.0
            first_ts = None
            while time.monotonic() < deadline:
                if hb.is_file():
                    self.file_during = True
                    try:
                        data = json.loads(hb.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        time.sleep(0.01)
                        continue
                    if first_ts is None:
                        self.seen_first = data
                        first_ts = data.get("ts")
                    elif data.get("ts") != first_ts:
                        self.ts_refreshed = True
                        break
                time.sleep(0.01)
            out = Path(workdir) / ".agent" / "last_handoff.json"
            payload = {
                "handoff_to": "None",
                "role": role,
                "current_phase": "planning",
                "cycle_number": 1,
                "summary": "blocked in heartbeat test",
                "status": "BLOCKED",
                "confidence": 0.5,
            }
            out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return out

    adapter = BlockingAdapter()
    monkeypatch.setattr("memory.adapters.get_adapter", lambda *a, **k: adapter)

    result = run_loop(
        workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
    )
    assert adapter.file_during
    assert adapter.daemon_ok
    assert adapter.seen_first is not None
    assert adapter.seen_first.get("pid") == os.getpid()
    assert adapter.seen_first.get("role") == "Orchestrator"
    assert "status" in adapter.seen_first
    assert adapter.seen_first.get("ts")
    assert adapter.ts_refreshed
    assert not (tmp_path / ".agent" / HEARTBEAT_FILENAME).exists()
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")


def test_status_heartbeat_optional_and_backward_compatible(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    from memory.supervisor import main

    rc = main(["status", "--workdir", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "state" in out
    assert "heartbeat" not in out

    hb = {
        "pid": 4242,
        "role": "Coder",
        "status": "IN_PROGRESS",
        "ts": "2026-08-21T12:00:00Z",
    }
    (tmp_path / ".agent" / HEARTBEAT_FILENAME).write_text(
        json.dumps(hb), encoding="utf-8"
    )
    rc = main(["status", "--workdir", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["heartbeat"]["pid"] == 4242
    assert out["heartbeat"]["role"] == "Coder"
    assert out.get("state") is not None
