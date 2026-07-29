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
