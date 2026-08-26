# -*- coding: utf-8 -*-
"""Integration-worktree, merge --abort и запрет push main/master."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memory.stream_git import (
    IntegrationWorktreeError,
    ensure_integration_worktree,
    merge_stream_branch,
    push_branch,
)
from memory import stream_git


INTEGRATION = "feature/integration-parallel"
SANITIZED = "feature-integration-parallel"


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
    (root / "memory").mkdir()
    (root / "memory" / "x.py").write_text("#\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")


def _abbrev(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _sha(cwd: Path, ref: str = "HEAD") -> str:
    return _git(cwd, "rev-parse", ref).stdout.strip()


def _setup_hub(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo, tmp_path / "wts"


def test_ensure_wt_recovers_hub_on_integration_branch(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    _git(repo, "checkout", "-b", INTEGRATION)
    assert _abbrev(repo) == INTEGRATION

    dest = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    assert dest == (wt_base / SANITIZED).resolve()
    assert (dest / ".git").exists()
    assert _abbrev(repo) == "main"
    assert _abbrev(dest) == INTEGRATION
    assert dest.name == SANITIZED  # sanitize(ветка), не {cycle}-integration


def test_ensure_wt_dirty_hub_raises(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    _git(repo, "checkout", "-b", INTEGRATION)
    hub_sha = _sha(repo)
    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(IntegrationWorktreeError, match="hub dirty") as exc:
        ensure_integration_worktree(
            repo, integration_branch=INTEGRATION, wt_base=wt_base
        )
    assert INTEGRATION in str(exc.value)
    assert "main" in str(exc.value)
    assert _abbrev(repo) == INTEGRATION
    assert _sha(repo) == hub_sha
    dest = wt_base / SANITIZED
    assert not dest.exists()


def test_ensure_wt_add_collision_raises(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    _git(repo, "branch", INTEGRATION)
    other = tmp_path / "other-checkout"
    _git(repo, "worktree", "add", str(other), INTEGRATION)
    hub_sha = _sha(repo)
    hub_branch = _abbrev(repo)
    assert hub_branch == "main"

    with pytest.raises(IntegrationWorktreeError, match="already checked out") as exc:
        ensure_integration_worktree(
            repo, integration_branch=INTEGRATION, wt_base=wt_base
        )
    msg = str(exc.value)
    assert str(other.resolve()) in msg or str(other) in msg
    assert _abbrev(repo) == hub_branch
    assert _sha(repo) == hub_sha
    dest = wt_base / SANITIZED
    assert not dest.exists()


def test_ensure_wt_reuses_existing(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    first = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    hub_sha = _sha(repo)
    second = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    assert first == second
    assert (first / ".git").exists()
    assert _sha(repo) == hub_sha
    assert _abbrev(repo) == "main"


def test_merge_does_not_move_hub_head(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    integ = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    stream_wt = tmp_path / "stream"
    _git(repo, "worktree", "add", "-b", "feature/stream", str(stream_wt), "main")
    (stream_wt / "stream.txt").write_text("from-stream\n", encoding="utf-8")
    _git(stream_wt, "add", "-A")
    _git(stream_wt, "commit", "-m", "stream change")

    hub_sha = _sha(repo)
    hub_branch = _abbrev(repo)
    result = merge_stream_branch(
        integration_workdir=integ,
        stream_branch="feature/stream",
        integration_branch=INTEGRATION,
    )
    assert result["ok"] is True, result
    assert result["branch"] == INTEGRATION
    assert _sha(repo) == hub_sha
    assert _abbrev(repo) == hub_branch == "main"
    parents = _git(integ, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert len(parents) >= 3  # merge-коммит: сам + два родителя
    assert _abbrev(integ) == INTEGRATION
    assert (integ / "stream.txt").is_file()


def test_merge_conflict_aborts(tmp_path: Path) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    integ = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    (integ / "README.md").write_text("from-integ\n", encoding="utf-8")
    _git(integ, "add", "-A")
    _git(integ, "commit", "-m", "integ edit")

    stream_wt = tmp_path / "stream"
    _git(repo, "worktree", "add", "-b", "feature/stream", str(stream_wt), "main")
    (stream_wt / "README.md").write_text("from-stream\n", encoding="utf-8")
    _git(stream_wt, "add", "-A")
    _git(stream_wt, "commit", "-m", "stream edit")

    integ_sha = _sha(integ)
    result = merge_stream_branch(
        integration_workdir=integ,
        stream_branch="feature/stream",
        integration_branch=INTEGRATION,
    )
    assert result["ok"] is False, result
    assert result.get("error")
    assert _sha(integ) == integ_sha
    merge_head = _git(integ, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
    assert merge_head.returncode != 0
    porcelain = _git(integ, "status", "--porcelain").stdout
    assert porcelain.strip() == ""


def test_merge_timeout_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    integ = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )
    stream_wt = tmp_path / "stream"
    _git(repo, "worktree", "add", "-b", "feature/stream", str(stream_wt), "main")
    (stream_wt / "stream.txt").write_text("x\n", encoding="utf-8")
    _git(stream_wt, "add", "-A")
    _git(stream_wt, "commit", "-m", "stream")

    abort_cwds: list[Path] = []
    orig_timed = stream_git._run_git_timed

    def spy_timed(args, cwd, timeout):
        argv = list(args)
        if argv[:2] == ["merge", "--abort"]:
            abort_cwds.append(Path(cwd).resolve())
            return orig_timed(args, cwd, timeout)
        raise subprocess.TimeoutExpired(cmd=["git", *argv], timeout=timeout)

    monkeypatch.setattr(stream_git, "_run_git_timed", spy_timed)

    result = merge_stream_branch(
        integration_workdir=integ,
        stream_branch="feature/stream",
        integration_branch=INTEGRATION,
    )
    assert result["ok"] is False
    assert result["error"] == "merge timeout"
    assert integ.resolve() in abort_cwds


def test_merge_timeout_surfaces_abort_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, wt_base = _setup_hub(tmp_path)
    integ = ensure_integration_worktree(
        repo, integration_branch=INTEGRATION, wt_base=wt_base
    )

    abort_cwds: list[Path] = []

    def spy_timed(args, cwd, timeout):
        argv = list(args)
        if argv[:2] == ["merge", "--abort"]:
            abort_cwds.append(Path(cwd).resolve())
            return subprocess.CompletedProcess(
                ["git", *argv], 128, "", "fatal: Unable to write new index file"
            )
        raise subprocess.TimeoutExpired(cmd=["git", *argv], timeout=timeout)

    monkeypatch.setattr(stream_git, "_run_git_timed", spy_timed)

    result = merge_stream_branch(
        integration_workdir=integ,
        stream_branch="feature/stream",
        integration_branch=INTEGRATION,
    )
    assert result["ok"] is False
    assert result["error"].startswith("merge timeout")
    assert "abort failed" in result["error"]
    assert "Unable to write new index file" in result["error"]
    assert integ.resolve() in abort_cwds
    assert len(abort_cwds) >= 2  # первая попытка + повтор после index.lock


def test_merge_refuses_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timed_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    def boom_timed(args, cwd, timeout):
        timed_calls.append(list(args))
        raise AssertionError("git merge не должен вызываться для protected branch")

    def boom_run(args, cwd):
        run_calls.append(list(args))
        raise AssertionError("git не должен вызываться для protected branch")

    monkeypatch.setattr(stream_git, "_run_git_timed", boom_timed)
    monkeypatch.setattr(stream_git, "_run_git", boom_run)
    cases = (
        ("main", "main"),
        ("master", "main"),
        ("develop", "develop"),
    )
    for integration_branch, main_branch in cases:
        timed_calls.clear()
        run_calls.clear()
        result = merge_stream_branch(
            integration_workdir=tmp_path,
            stream_branch="feature/stream",
            integration_branch=integration_branch,
            main_branch=main_branch,
        )
        assert result["ok"] is False, (integration_branch, result)
        assert result["error"] == "never merge into main"
        assert timed_calls == []
        assert run_calls == []


def test_sanitize_dotdot_stays_under_wt_base() -> None:
    assert stream_git._sanitize_branch("..") == "integration"
    assert stream_git._sanitize_branch(".") == "integration"
    assert stream_git._sanitize_branch("feature/integration-parallel") == SANITIZED


def test_push_refuses_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timed_calls: list[list[str]] = []

    def boom(args, cwd, timeout):
        timed_calls.append(list(args))
        raise AssertionError("git push не должен вызываться для protected branch")

    monkeypatch.setattr(stream_git, "_run_git_timed", boom)
    for name in ("main", "master"):
        result = push_branch(tmp_path, branch=name)
        assert result["ok"] is False
        assert result["error"] == "refusing to push protected branch"
    assert timed_calls == []
