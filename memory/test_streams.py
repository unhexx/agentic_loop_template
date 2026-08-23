# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from memory.adapters.mock import MockAdapter
from memory.streams import (
    StreamPlan,
    check_owned_paths_gate,
    files_outside_owned,
    list_changed_files,
    parse_stream_specs,
    provision_stream_worktrees,
    validate_stream_plans,
)


def test_files_outside_owned_accepts_prefix():
    changed = ["memory/streams.py", "memory/supervisor.py", "tools/select.py"]
    owned = ["memory/", "tools/"]
    assert files_outside_owned(changed, owned) == []


def test_files_outside_owned_flags_hot_files():
    changed = ["memory/streams.py", "DEVELOPMENT_STANDARDS.md"]
    owned = ["memory/"]
    assert files_outside_owned(changed, owned) == ["DEVELOPMENT_STANDARDS.md"]


def test_files_outside_owned_exact_file_token():
    changed = ["docs/README.md"]
    owned = ["docs/README.md"]
    assert files_outside_owned(changed, owned) == []


def test_parse_stream_specs():
    plans = parse_stream_specs(["harness:memory/,tools/", "docs:docs/"])
    assert len(plans) == 2
    assert plans[0].name == "harness"
    assert plans[0].owned_paths == ["memory/", "tools/"]
    assert plans[1].name == "docs"
    assert plans[1].owned_paths == ["docs/"]


def test_validate_stream_plans_rejects_overlap():
    a = StreamPlan(name="a", owned_paths=["memory/"])
    b = StreamPlan(name="b", owned_paths=["memory/state.py"])
    with pytest.raises(ValueError, match="overlap"):
        validate_stream_plans([a, b])


def test_validate_stream_plans_ok_disjoint():
    a = StreamPlan(name="a", owned_paths=["memory/"])
    b = StreamPlan(name="b", owned_paths=["docs/"])
    validate_stream_plans([a, b])  # no raise


def test_dotfile_not_stripped_as_outside_memory():
    """``.gitignore`` must not become ``gitignore`` via lstrip('./')."""
    changed = [".gitignore"]
    owned = ["memory/"]
    assert files_outside_owned(changed, owned) == [".gitignore"]


def test_dot_agent_path_owned_when_prefix_owned():
    """``.agent/LOOP_STATE.json`` stays under owned ``.agent/``."""
    changed = [".agent/LOOP_STATE.json"]
    owned = [".agent/"]
    assert files_outside_owned(changed, owned) == []


def test_dot_slash_prefix_still_accepted_under_memory():
    """``./memory/x.py`` still counts as under ``memory/``."""
    changed = ["./memory/x.py"]
    owned = ["memory/"]
    assert files_outside_owned(changed, owned) == []


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "x.py").write_text("#\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("#\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def test_provision_stream_worktrees(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    wt_base = tmp_path / "wts"
    plans = [
        StreamPlan(name="harness", owned_paths=["memory/"]),
        StreamPlan(name="docs", owned_paths=["docs/"]),
    ]
    out = provision_stream_worktrees(
        repo_root=repo,
        plans=plans,
        cycle_id="c1",
        wt_base=wt_base,
        main_branch="main",
    )
    assert len(out) == 2
    assert (wt_base / "c1-harness").is_dir()
    assert (wt_base / "c1-docs").is_dir()
    assert out[0].branch == "feature/c1-harness"
    assert out[0].worktree is not None
    assert Path(out[0].worktree).is_dir()
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=out[0].worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "feature/c1-harness"


def test_check_owned_paths_gate_flags_outside_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    wt_base = tmp_path / "wts"
    plans = provision_stream_worktrees(
        repo_root=repo,
        plans=[StreamPlan(name="docs", owned_paths=["docs/"])],
        cycle_id="c2",
        wt_base=wt_base,
        main_branch="main",
    )
    wt = Path(plans[0].worktree)
    # Change outside owned_paths
    (wt / "README.md").write_text("changed\n", encoding="utf-8")
    (wt / "docs" / "a.md").write_text("# ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "stream edit"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    changed = list_changed_files(wt, base_ref="main")
    assert "README.md" in changed
    assert "docs/a.md" in changed
    violations = check_owned_paths_gate(wt, ["docs/"], base_ref="main")
    assert violations == ["README.md"]


def test_mock_includes_stream_fields_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIX_STREAM", "harness")
    monkeypatch.setenv("AGENTIX_OWNED_PATHS", "memory/,tools/")
    monkeypatch.setenv("AGENTIX_WORKTREE", str(tmp_path))
    ad = MockAdapter()
    out = ad.run_role_turn("Orchestrator", "p", None, tmp_path, 5)
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["stream"] == "harness"
    assert data["owned_paths"] == ["memory/", "tools/"]
    assert data["worktree"] == str(tmp_path)
