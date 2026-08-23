# -*- coding: utf-8 -*-
"""Parallel stream plans, path ownership, worktree helpers."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass
class StreamPlan:
    name: str
    owned_paths: List[str]
    worktree: Optional[str] = None
    branch: Optional[str] = None
    status: str = "PENDING"  # PENDING | RUNNING | STREAM_READY | BLOCKED | MERGED
    merge_gate: str = "after-tests-green"


def _norm_owned(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    if not p:
        raise ValueError("empty owned path")
    return p


def _norm_rel(rel_path: str) -> str:
    """Normalize relative path: backslashes → slashes, strip only ``./`` prefixes."""
    rel = rel_path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def path_is_owned(rel_path: str, owned_paths: Sequence[str]) -> bool:
    """True if rel_path is exactly an owned token or under an owned directory prefix.

    Rules:
    - owned ending with ``/`` → directory prefix (covers the dir and children)
    - owned with a file extension → exact match only
    - owned without trailing slash and without extension → directory prefix too
    """
    rel = _norm_rel(rel_path)
    for raw in owned_paths:
        own = _norm_owned(raw)
        if own.endswith("/"):
            base = own.rstrip("/")
            if rel == base or rel.startswith(own) or rel.startswith(base + "/"):
                return True
            continue
        # Exact file (or exact path) match
        if rel == own:
            return True
        # No file extension → treat as directory prefix
        if not Path(own).suffix and rel.startswith(own + "/"):
            return True
    return False


def files_outside_owned(changed: Sequence[str], owned_paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    for f in changed:
        rel = _norm_rel(f)
        if not rel or rel == ".":
            continue
        if not path_is_owned(rel, owned_paths):
            out.append(rel)
    return out


def parse_stream_specs(specs: Sequence[str]) -> List[StreamPlan]:
    """
    Parse CLI specs: ``name:path1,path2`` → StreamPlan.
    Example: ``harness:memory/,tools/``
    """
    plans: List[StreamPlan] = []
    for raw in specs:
        raw = (raw or "").strip()
        if not raw:
            continue
        if ":" not in raw:
            raise ValueError(f"stream spec needs name:paths, got {raw!r}")
        name, paths_s = raw.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"empty stream name in {raw!r}")
        paths = [_norm_owned(p) for p in paths_s.split(",") if p.strip()]
        if not paths:
            raise ValueError(f"stream {name!r} has no owned_paths")
        plans.append(StreamPlan(name=name, owned_paths=paths))
    if not plans:
        raise ValueError("no streams specified")
    return plans


def _owned_covers(a: str, b: str) -> bool:
    """True if path token a covers path token b (same or parent dir)."""
    a_n = _norm_owned(a)
    b_n = _norm_owned(b)
    if a_n == b_n:
        return True
    # Directory form: trailing slash, or no file extension → prefix dir
    if a_n.endswith("/"):
        a_dir = a_n
    elif not Path(a_n).suffix:
        a_dir = a_n + "/"
    else:
        return False
    if b_n == a_dir.rstrip("/") or b_n.startswith(a_dir):
        return True
    return False


def validate_stream_plans(plans: Sequence[StreamPlan]) -> None:
    """Reject overlapping owned_paths across streams (hard fail)."""
    if len(plans) < 1:
        raise ValueError("need at least one stream")
    names = [p.name for p in plans]
    if len(names) != len(set(names)):
        raise ValueError("duplicate stream names")
    for i, pa in enumerate(plans):
        for pb in plans[i + 1 :]:
            for oa in pa.owned_paths:
                for ob in pb.owned_paths:
                    if _owned_covers(oa, ob) or _owned_covers(ob, oa):
                        raise ValueError(
                            f"overlap between streams {pa.name!r} and {pb.name!r}: "
                            f"{oa!r} vs {ob!r}"
                        )


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _is_git_checkout(path: Path) -> bool:
    """True if path looks like a git worktree/repo (.git file or dir)."""
    return (path / ".git").exists()


def provision_stream_worktrees(
    repo_root: Path,
    plans: List[StreamPlan],
    cycle_id: Optional[str] = None,
    wt_base: Optional[Path] = None,
    main_branch: str = "main",
) -> List[StreamPlan]:
    """
    Create git worktrees for each plan under wt_base/<cycle>-<name>.
    Idempotent if worktree path already exists with .git.
    """
    repo_root = Path(repo_root).resolve()
    if cycle_id is None:
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if wt_base is None:
        wt_base = repo_root.parent / "agentic-loop-worktrees"
    wt_base = Path(wt_base)
    wt_base.mkdir(parents=True, exist_ok=True)

    result: List[StreamPlan] = []
    for plan in plans:
        branch = plan.branch or f"feature/{cycle_id}-{plan.name}"
        wt = Path(plan.worktree) if plan.worktree else wt_base / f"{cycle_id}-{plan.name}"

        if wt.exists():
            if _is_git_checkout(wt):
                plan.worktree = str(wt.resolve())
                plan.branch = branch
                plan.status = "PENDING"
                result.append(plan)
                continue
            if wt.is_dir() and not any(wt.iterdir()):
                wt.rmdir()
            else:
                raise RuntimeError(f"worktree path exists but is not a git worktree: {wt}")

        r = _run_git(
            ["worktree", "add", "-b", branch, str(wt), main_branch],
            cwd=repo_root,
        )
        if r.returncode != 0:
            # branch may already exist — try without -b
            r2 = _run_git(["worktree", "add", str(wt), branch], cwd=repo_root)
            if r2.returncode != 0:
                raise RuntimeError(
                    f"git worktree add failed for {plan.name}: {r.stderr or r.stdout} "
                    f"| fallback: {r2.stderr or r2.stdout}"
                )
        plan.worktree = str(wt.resolve())
        plan.branch = branch
        plan.status = "PENDING"
        result.append(plan)
    return result


def list_changed_files(workdir: Path, base_ref: str = "main") -> List[str]:
    """Files changed on current branch vs base_ref (name-only)."""
    workdir = Path(workdir)
    r = _run_git(["diff", "--name-only", f"{base_ref}...HEAD"], cwd=workdir)
    if r.returncode != 0:
        r = _run_git(["diff", "--name-only", base_ref], cwd=workdir)
    if r.returncode != 0:
        r = _run_git(["status", "--porcelain"], cwd=workdir)
        files: List[str] = []
        for line in (r.stdout or "").splitlines():
            if len(line) >= 4:
                files.append(line[3:].strip())
        return files
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def check_owned_paths_gate(
    workdir: Path,
    owned_paths: Sequence[str],
    base_ref: str = "main",
) -> List[str]:
    """Return list of violating paths (empty = OK)."""
    changed = list_changed_files(workdir, base_ref=base_ref)
    return files_outside_owned(changed, owned_paths)
