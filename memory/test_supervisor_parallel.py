# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from memory.stream_git import IntegrationWorktreeError
from memory.stream_lease import status as lease_status
from memory.streams import StreamPlan
from memory.supervisor import Terminal
from memory.supervisor_parallel import run_parallel


INTEGRATION = "feature/integration-parallel"
SANITIZED = "feature-integration-parallel"


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


def _write_hub_config(hub: Path, parallel: dict | None = None) -> None:
    agent = hub / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"supervisor": {"adapter": "mock", "max_cycles": 1}}
    if parallel is not None:
        payload["supervisor"]["parallel"] = parallel
    (agent / "project_config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _stub_integration_git(monkeypatch, integ: Path | None = None) -> Path:
    """Юниты без git-хаба: иначе ensure падает и success-тесты уезжают в BLOCKED."""
    dest = integ if integ is not None else Path("/tmp/fake-integ-wt")
    monkeypatch.setattr(
        "memory.supervisor_parallel.merge_stream_branch",
        lambda **kwargs: {"ok": True, "skipped": True},
    )
    monkeypatch.setattr(
        "memory.supervisor_parallel.ensure_integration_worktree",
        lambda *a, **k: dest,
    )
    monkeypatch.setattr(
        "memory.supervisor_parallel.maybe_create_integration_pr",
        lambda **kwargs: Terminal.PR_READY_LOCAL,
    )
    return dest


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} rc={r.returncode}: {r.stderr or r.stdout}")
    return r


def _init_git_repo(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    # глобально включён gpgsign — иначе commit в tmp повиснет на pinentry
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")


def _abbrev(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _sha(cwd: Path, ref: str = "HEAD") -> str:
    return _git(cwd, "rev-parse", ref).stdout.strip()


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
    _stub_integration_git(monkeypatch)

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


def test_cli_run_parallel_concurrent_flag(tmp_path, monkeypatch):
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
            "--concurrent",
        ]
    )
    assert code == 0
    assert calls.get("concurrent") is True


def test_run_parallel_concurrent_overlap(tmp_path, monkeypatch):
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
    _stub_integration_git(monkeypatch)

    barrier = threading.Barrier(2, timeout=5)
    started = []

    def fake_run_loop(**kwargs):
        started.append(kwargs.get("workdir"))
        barrier.wait()
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0}

    monkeypatch.setattr("memory.supervisor_parallel.run_loop", fake_run_loop)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        base_ref="main",
        skip_provision=True,
        concurrent=True,
    )
    assert result["exit_code"] == 0, result
    assert result["mode"] == "concurrent"
    assert result["streams"]["harness"]["status"] in ("STREAM_READY", "MERGED")
    assert result["streams"]["docs"]["status"] in ("STREAM_READY", "MERGED")
    assert len(started) == 2


def test_run_parallel_concurrent_blocks_skips_merge(tmp_path, monkeypatch):
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
    merge_called = {"n": 0}

    def fake_merge(**kwargs):
        merge_called["n"] += 1
        return {"ok": True, "skipped": True}

    monkeypatch.setattr("memory.supervisor_parallel.merge_stream_branch", fake_merge)

    barrier = threading.Barrier(2, timeout=5)

    def fake_run_loop(**kwargs):
        barrier.wait()
        wd = str(kwargs.get("workdir") or "")
        if Path(wd).name == "wt-a":
            return {"terminal": Terminal.BLOCKED, "exit_code": 1}
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0}

    monkeypatch.setattr("memory.supervisor_parallel.run_loop", fake_run_loop)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
        concurrent=True,
    )
    assert merge_called["n"] == 0
    assert result["exit_code"] == 1
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
    assert result["mode"] == "concurrent"
    assert set(result["streams"]) == {"harness", "docs"}
    # wait-all: предзаполнение RUNNING не должно маскировать fail-fast
    assert result["streams"]["harness"]["status"] == "BLOCKED"
    assert result["streams"]["docs"]["status"] == "STREAM_READY"
    assert "loop" in result["streams"]["docs"]
    assert "loop" in result["streams"]["harness"]


def test_stream_context_isolated_per_thread():
    from memory.stream_context import stream_name, use_stream

    seen = {}
    barrier = threading.Barrier(2, timeout=5)

    def worker(name: str) -> None:
        with use_stream(name=name, owned_paths=name + "/", worktree="/tmp/" + name):
            barrier.wait()
            seen[name] = stream_name()

    t1 = threading.Thread(target=worker, args=("alpha",))
    t2 = threading.Thread(target=worker, args=("beta",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert seen["alpha"] == "alpha"
    assert seen["beta"] == "beta"
    assert stream_name() == os.environ.get("AGENTIX_STREAM")


def test_require_owned_paths_false_skips_gate(tmp_path, monkeypatch, caplog):
    hub = tmp_path / "hub"
    hub.mkdir()
    _write_hub_config(hub, {"require_owned_paths": False})
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
    _stub_integration_git(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="memory.supervisor_parallel"):
        result = run_parallel(
            hub_workdir=hub,
            plans=plans,
            adapter_name="mock",
            max_cycles_per_stream=1,
            create_pr=False,
            skip_provision=True,
        )
    assert result["exit_code"] == 0, result
    assert result["terminal"] not in (Terminal.BLOCKED, "BLOCKED")
    assert result["streams"]["harness"]["status"] in ("STREAM_READY", "MERGED")
    assert "require_owned_paths disabled for stream harness" in caplog.text


def test_concurrent_midflight_hub_state(tmp_path, monkeypatch):
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
    import memory.supervisor_parallel as sp

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    _stub_integration_git(monkeypatch)

    writes: list[dict] = []
    orig_write = sp._write_hub_streams_state

    def spy_write(hub_path, payload):
        writes.append(json.loads(json.dumps(payload, default=str)))
        orig_write(hub_path, payload)

    monkeypatch.setattr(sp, "_write_hub_streams_state", spy_write)

    barrier = threading.Barrier(2, timeout=5)
    midflight: list[dict] = []

    def fake_run_loop(**kwargs):
        state_path = hub / ".agent" / "streams_state.json"
        midflight.append(json.loads(state_path.read_text(encoding="utf-8")))
        barrier.wait()
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0}

    monkeypatch.setattr("memory.supervisor_parallel.run_loop", fake_run_loop)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
        concurrent=True,
    )
    assert result["exit_code"] == 0, result
    n = len(plans)
    assert len(writes) >= n
    assert len(midflight) == 2
    for snap in midflight:
        assert snap.get("terminal") == "IN_PROGRESS"
        assert snap["streams"]["harness"]["status"] == "RUNNING"
        assert snap["streams"]["docs"]["status"] == "RUNNING"
    assert any(w.get("terminal") == "IN_PROGRESS" for w in writes)


def test_maybe_create_pr_uses_integration_workdir(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_git_repo(hub)
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    merge_seen: dict = {}

    def spy_merge(**kwargs):
        merge_seen.update(kwargs)
        return {"ok": True, "skipped": True}

    monkeypatch.setattr("memory.supervisor_parallel.merge_stream_branch", spy_merge)
    monkeypatch.setattr(
        "memory.supervisor_parallel.run_loop",
        lambda **kwargs: {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0},
    )

    seen: dict = {}

    def spy_pr(workdir, sup):
        seen["cwd"] = Path(workdir).resolve()
        seen["sup"] = sup
        return Terminal.PR_READY_LOCAL

    monkeypatch.setattr("memory.supervisor_parallel.maybe_create_pr", spy_pr)

    wt_base = tmp_path / "wts"
    hub_head = _abbrev(hub)
    hub_sha = _sha(hub)
    assert hub_head == "main"

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=True,
        skip_provision=True,
        wt_base=wt_base,
    )
    assert result["exit_code"] == 0, result
    expected = (wt_base / SANITIZED).resolve()
    assert seen.get("cwd") == expected
    assert seen["cwd"] != hub.resolve()
    assert _abbrev(hub) == hub_head == "main"
    assert _sha(hub) == hub_sha
    assert Path(result["integration_worktree"]).resolve() == expected
    assert Path(merge_seen["integration_workdir"]).resolve() == expected
    assert Path(merge_seen["hub_workdir"]).resolve() == hub.resolve()
    assert merge_seen["integration_workdir"] != merge_seen["hub_workdir"]


def test_create_pr_push_precondition(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agent").mkdir()
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    monkeypatch.setattr(
        "memory.supervisor_parallel.run_loop",
        lambda **kwargs: {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0},
    )

    merge_n = {"n": 0}

    def fake_merge(**kwargs):
        merge_n["n"] += 1
        return {"ok": True, "skipped": True}

    monkeypatch.setattr("memory.supervisor_parallel.merge_stream_branch", fake_merge)

    pr_n = {"n": 0}

    def spy_pr(workdir, sup):
        pr_n["n"] += 1
        return Terminal.PR_READY

    monkeypatch.setattr("memory.supervisor_parallel.maybe_create_pr", spy_pr)

    integ = tmp_path / "integ"
    ensure_n = {"n": 0}

    def fake_ensure(*a, **k):
        ensure_n["n"] += 1
        return integ

    monkeypatch.setattr("memory.supervisor_parallel.ensure_integration_worktree", fake_ensure)

    push_calls: list[tuple[str, str]] = []

    def fake_push(workdir, *, branch, remote="origin"):
        push_calls.append((str(Path(workdir).resolve()), branch))
        if branch == INTEGRATION or Path(workdir).resolve() == integ.resolve():
            return {"ok": False, "error": "auth failed"}
        return {"ok": True, "branch": branch, "remote": remote}

    monkeypatch.setattr("memory.supervisor_parallel.push_branch", fake_push)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=True,
        skip_provision=True,
        push=True,
    )
    assert result["exit_code"] == 1
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
    reason = str(result.get("reason"))
    assert "push failed" in reason
    assert INTEGRATION in reason
    assert pr_n["n"] == 0
    assert merge_n["n"] == 1
    assert ensure_n["n"] == 1
    assert any(branch == "feature/c-harness" for _, branch in push_calls)
    assert any(branch == INTEGRATION for _, branch in push_calls)


def test_ensure_wt_error_maps_to_blocked(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_git_repo(hub)
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    monkeypatch.setattr(
        "memory.supervisor_parallel.run_loop",
        lambda **kwargs: {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0},
    )
    merge_n = {"n": 0}
    monkeypatch.setattr(
        "memory.supervisor_parallel.merge_stream_branch",
        lambda **kwargs: merge_n.__setitem__("n", merge_n["n"] + 1) or {"ok": True},
    )

    def boom(*a, **k):
        raise IntegrationWorktreeError("hub dirty on feature/integration-parallel; commit or stash, then checkout main")

    monkeypatch.setattr("memory.supervisor_parallel.ensure_integration_worktree", boom)

    hub_head = _abbrev(hub)
    hub_sha = _sha(hub)
    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
    )
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
    assert "hub dirty" in str(result.get("reason"))
    assert merge_n["n"] == 0
    assert _abbrev(hub) == hub_head == "main"
    assert _sha(hub) == hub_sha


def test_ensure_wt_dirty_hub_maps_to_blocked(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_git_repo(hub)
    _git(hub, "checkout", "-b", INTEGRATION)
    (hub / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    monkeypatch.setattr(
        "memory.supervisor_parallel.run_loop",
        lambda **kwargs: {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0},
    )
    merge_n = {"n": 0}
    monkeypatch.setattr(
        "memory.supervisor_parallel.merge_stream_branch",
        lambda **kwargs: merge_n.__setitem__("n", merge_n["n"] + 1) or {"ok": True},
    )

    wt_base = tmp_path / "wts"
    hub_sha = _sha(hub)
    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
        wt_base=wt_base,
    )
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
    assert "hub dirty" in str(result.get("reason"))
    assert merge_n["n"] == 0
    assert _abbrev(hub) == INTEGRATION
    assert _sha(hub) == hub_sha
    assert not (wt_base / SANITIZED).exists()


def test_ensure_wt_add_collision_maps_to_blocked(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_git_repo(hub)
    _git(hub, "branch", INTEGRATION)
    other = tmp_path / "other-checkout"
    _git(hub, "worktree", "add", str(other), INTEGRATION)
    a = _stream_workdir(tmp_path, "wt-a")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    monkeypatch.setattr(
        "memory.supervisor_parallel.run_loop",
        lambda **kwargs: {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0},
    )
    merge_n = {"n": 0}
    monkeypatch.setattr(
        "memory.supervisor_parallel.merge_stream_branch",
        lambda **kwargs: merge_n.__setitem__("n", merge_n["n"] + 1) or {"ok": True},
    )

    wt_base = tmp_path / "wts"
    hub_head = _abbrev(hub)
    hub_sha = _sha(hub)
    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
        wt_base=wt_base,
    )
    assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
    reason = str(result.get("reason"))
    assert "already checked out" in reason
    assert str(other.resolve()) in reason or str(other) in reason
    assert merge_n["n"] == 0
    assert _abbrev(hub) == hub_head == "main"
    assert _sha(hub) == hub_sha
    assert not (wt_base / SANITIZED).exists()


def test_run_parallel_renews_leases(tmp_path, monkeypatch):
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
    import memory.supervisor_parallel as sp

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    _stub_integration_git(monkeypatch)

    renews: list[str] = []
    orig_renew = sp.renew_lease

    def spy_renew(hub_path, name, **kwargs):
        renews.append(name)
        return orig_renew(hub_path, name, **kwargs)

    monkeypatch.setattr(sp, "renew_lease", spy_renew)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
    )
    assert result["exit_code"] == 0, result
    assert "harness" in renews
    assert "docs" in renews
    assert len(renews) >= 2
    st = lease_status(hub)
    assert st.get("leases") == {}


def test_live_foreign_lease_blocks_skips_provision(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agent").mkdir()
    a = _stream_workdir(tmp_path, "wt-a")
    b = _stream_workdir(tmp_path, "wt-b")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
        StreamPlan(name="docs", owned_paths=["docs/"], worktree=str(b), branch="feature/c-docs"),
    ]
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        (hub / ".agent" / "stream_leases.json").write_text(
            json.dumps(
                {
                    "leases": {
                        "other": {
                            "owned_paths": ["docs/"],
                            "worktree": "/abs/other",
                            "pid": proc.pid,
                            "claimed_at": "2026-08-26T12:00:00Z",
                            "expires_at": "2099-01-01T00:00:00Z",
                            "branch": "feature/other",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        prov = {"n": 0}

        def fake_provision(**kwargs):
            prov["n"] += 1
            return kwargs.get("plans") or []

        monkeypatch.setattr(
            "memory.supervisor_parallel.provision_stream_worktrees", fake_provision
        )
        result = run_parallel(
            hub_workdir=hub,
            plans=plans,
            adapter_name="mock",
            max_cycles_per_stream=1,
            create_pr=False,
            skip_provision=False,
        )
        assert result["terminal"] in (Terminal.BLOCKED, "BLOCKED")
        reason = str(result.get("reason"))
        assert "lease overlap" in reason
        assert "overlap between streams" in reason
        assert prov["n"] == 0
        leases = lease_status(hub).get("leases") or {}
        assert "harness" not in leases
        assert "docs" not in leases
        assert leases["other"]["pid"] == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_config_concurrent_without_flag(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    _write_hub_config(hub, {"concurrent": True, "push": False})
    a = _stream_workdir(tmp_path, "wt-a")
    b = _stream_workdir(tmp_path, "wt-b")
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"], worktree=str(a), branch="feature/c-harness"),
        StreamPlan(name="docs", owned_paths=["docs/"], worktree=str(b), branch="feature/c-docs"),
    ]
    import memory.streams as streams_mod

    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    _stub_integration_git(monkeypatch)

    barrier = threading.Barrier(2, timeout=5)

    def fake_run_loop(**kwargs):
        barrier.wait()
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0}

    monkeypatch.setattr("memory.supervisor_parallel.run_loop", fake_run_loop)

    result = run_parallel(
        hub_workdir=hub,
        plans=plans,
        adapter_name="mock",
        max_cycles_per_stream=1,
        create_pr=False,
        skip_provision=True,
    )
    assert result["exit_code"] == 0, result
    assert result["mode"] == "concurrent"
    assert result["push"] is False
