# Agentix Parallel Streams 3.5.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run multiple disjoint INVEST streams under the Agentix supervisor with worktree isolation, `owned_paths` enforcement, and a single Integration Reviewer gate that stops at PR ready (human still merges to `main`).

**Architecture:** Keep the single-stream `run_loop` from 3.5.0 unchanged. Add a thin **parallel orchestrator** that (1) materializes N stream worktrees, (2) runs one full role cycle **per stream workdir**, (3) checks path ownership + green tests, (4) merges stream branches into an integration branch in a fixed order, then (5) opens one PR. Streams run **serially** in 3.5.1 (deterministic CI); true concurrent process fan-out is out of scope.

**Tech Stack:** Python 3.10+ stdlib, existing `memory.supervisor` / `memory.state` / `validate_handoff`, `git worktree`, pytest, optional `gh` for PR (never merge `main`).

**Spec sources:**
- `PARALLEL_PROTOCOL.md` (roles, handoff extensions, merge checklist)
- `docs/superpowers/specs/2026-07-29-agentix-supervisor-3.5-design.md` § non-goals (deferred parallel → 3.5.1)
- Existing scaffold: `scripts/agentic_loop.sh`, handoff fields `stream` / `worktree` / `owned_paths` / `merge_gate`

---

## File map

| Path | Responsibility |
|------|----------------|
| `memory/streams.py` | Stream plan model, path ownership, worktree provision, integration merge order |
| `memory/supervisor_parallel.py` | `run_parallel`: drive N streams + integration gate + one PR |
| `memory/test_streams.py` | Unit tests for ownership + plan validation |
| `memory/test_supervisor_parallel.py` | Integration: 2 mock streams → integration → PR_READY without network |
| `memory/adapters/mock.py` | Optional stream-aware fields on mock handoffs |
| `memory/supervisor.py` | CLI subcommand `run-parallel` only (no FSM rewrite) |
| `schemas/handoff.schema.json` | Allow free-form stream names; document `owned_paths` / `merge_gate` |
| `.agent/project_config.example.json` | `supervisor.parallel` defaults |
| `scripts/agentic_loop.sh` | Call into Python provision when available (or stay scaffold + docs) |
| `PARALLEL_PROTOCOL.md` | Point to supervisor CLI for unattended parallel |
| `VERSION` / `CHANGELOG.md` / `README.md` | 3.5.1 |

**Exit codes (reuse 3.5.0):**

| Code | Meaning |
|------|---------|
| 0 | All streams integrated → `PR_READY` / `PR_READY_LOCAL` |
| 1 | Any stream `BLOCKED`, ownership violation, or merge fail |
| 2 | `STOPPED_LIMIT` / cooperative `STOP` |

**Non-goals (3.5.1):**
- Concurrent multi-process stream execution
- Auto-merge to `main`
- Cross-repo multi-clone ritual every turn
- Control Plane UI

---

### Task 1: Path ownership + stream plan model (TDD)

**Files:**
- Create: `memory/streams.py`
- Create: `memory/test_streams.py`

- [ ] **Step 1: Write failing tests**

Create `memory/test_streams.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from memory.streams import (
    StreamPlan,
    files_outside_owned,
    parse_stream_specs,
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
```

- [ ] **Step 2: Run tests — expect fail**

```bash
cd /home/unhex/_PROJECT/agentic_loop_template
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py -v
```

Expected: `ModuleNotFoundError: No module named 'memory.streams'` (or import errors).

- [ ] **Step 3: Implement `memory/streams.py` (ownership + plan only)**

```python
# -*- coding: utf-8 -*-
"""Parallel stream plans, path ownership, worktree helpers (3.5.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
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


def path_is_owned(rel_path: str, owned_paths: Sequence[str]) -> bool:
    """True if rel_path is exactly an owned token or under an owned directory prefix."""
    rel = rel_path.replace("\\", "/").lstrip("./")
    for raw in owned_paths:
        own = _norm_owned(raw)
        if own.endswith("/"):
            if rel == own.rstrip("/") or rel.startswith(own) or rel.startswith(own.rstrip("/") + "/"):
                return True
        else:
            if rel == own or rel.startswith(own.rstrip("/") + "/"):
                # file token: exact match only unless own looks like a dir without slash
                if own.endswith("/") or "/" in own.rstrip("/").split("/")[-1] and not Path(own).suffix:
                    pass
                if rel == own:
                    return True
                # directory-like without trailing slash: treat as prefix dir
                if not Path(own).suffix and (rel == own or rel.startswith(own + "/")):
                    return True
    return False


def files_outside_owned(changed: Sequence[str], owned_paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    for f in changed:
        rel = f.replace("\\", "/").lstrip("./")
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
    a_dir = a_n if a_n.endswith("/") else (a_n + "/" if not Path(a_n).suffix else None)
    if a_dir and (b_n == a_dir.rstrip("/") or b_n.startswith(a_dir)):
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
```

Note: keep `path_is_owned` logic consistent with tests; if a test fails due to edge cases, fix the helper — do not weaken tests.

- [ ] **Step 4: Run tests — expect pass**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/streams.py memory/test_streams.py
git commit -m "Добавил модель параллельных stream и проверку owned_paths"
```

---

### Task 2: Worktree provision helpers (TDD)

**Files:**
- Modify: `memory/streams.py`
- Modify: `memory/test_streams.py`

- [ ] **Step 1: Write failing tests for provision (tmpdir git repo)**

Append to `memory/test_streams.py`:

```python
import json
import subprocess
from pathlib import Path

from memory.streams import provision_stream_worktrees, StreamPlan


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
    # branch checked out in worktree
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=out[0].worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "feature/c1-harness"
```

- [ ] **Step 2: Run test — expect fail**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py::test_provision_stream_worktrees -v
```

Expected: `ImportError` / `AttributeError: provision_stream_worktrees`.

- [ ] **Step 3: Implement provision**

Append to `memory/streams.py`:

```python
import os
import subprocess
from datetime import datetime, timezone


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def provision_stream_worktrees(
    repo_root: Path,
    plans: List[StreamPlan],
    cycle_id: Optional[str] = None,
    wt_base: Optional[Path] = None,
    main_branch: str = "main",
) -> List[StreamPlan]:
    """
    Create git worktrees for each plan under wt_base/<cycle>-<name>.
    Idempotent if worktree path already exists.
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
        if wt.is_dir() and (wt / ".git").exists() or (wt.is_dir() and (wt / ".git").is_file()):
            # already a worktree checkout
            plan.worktree = str(wt.resolve())
            plan.branch = branch
            plan.status = "PENDING"
            result.append(plan)
            continue
        if wt.exists() and not any(wt.iterdir()) if wt.is_dir() else False:
            pass
        # remove empty dir if present without git
        if wt.is_dir() and not (wt / ".git").exists() and not list(wt.iterdir()):
            wt.rmdir()
        if wt.exists():
            # reuse existing path that is not a worktree — error clearly
            if not ((wt / ".git").exists() or (wt / ".git").is_file()):
                raise RuntimeError(f"worktree path exists but is not a git worktree: {wt}")
            plan.worktree = str(wt.resolve())
            plan.branch = branch
            result.append(plan)
            continue

        # Prefer create branch from main
        r = _run_git(
            ["worktree", "add", "-b", branch, str(wt), main_branch],
            cwd=repo_root,
        )
        if r.returncode != 0:
            # branch may already exist — try without -b
            r2 = _run_git(
                ["worktree", "add", str(wt), branch],
                cwd=repo_root,
            )
            if r2.returncode != 0:
                raise RuntimeError(
                    f"git worktree add failed for {plan.name}: {r.stderr or r.stdout} "
                    f"| fallback: {r2.stderr or r2.stdout}"
                )
        plan.worktree = str(Path(wt).resolve())
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
        files = []
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
```

Simplify the “exists” branch logic if needed so the test is green — prefer clear, short code over handling every git edge case.

- [ ] **Step 4: Run tests — expect pass**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/streams.py memory/test_streams.py
git commit -m "Добавил provision git worktree для параллельных stream"
```

---

### Task 3: Stream-aware mock handoffs + schema tweak

**Files:**
- Modify: `memory/adapters/mock.py`
- Modify: `schemas/handoff.schema.json`
- Modify: `memory/test_streams.py` (or new assertions in adapter tests)

- [ ] **Step 1: Write failing test**

In `memory/test_streams.py` (or `memory/test_adapters.py`):

```python
from memory.adapters.mock import MockAdapter


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
```

- [ ] **Step 2: Run — expect fail** (missing keys)

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py::test_mock_includes_stream_fields_when_env_set -v
```

- [ ] **Step 3: Implement mock env enrichment**

In `memory/adapters/mock.py`, after `data = _base(...)`:

```python
        import os
        stream = os.environ.get("AGENTIX_STREAM")
        if stream:
            data["stream"] = stream
        owned = os.environ.get("AGENTIX_OWNED_PATHS")
        if owned:
            data["owned_paths"] = [p.strip() for p in owned.split(",") if p.strip()]
        wt = os.environ.get("AGENTIX_WORKTREE")
        if wt:
            data["worktree"] = wt
        data.setdefault("merge_gate", "after-tests-green")
```

Also update `schemas/handoff.schema.json` stream property to free string (keep examples product/meta/cross):

```json
    "stream": { "type": "string", "minLength": 1 },
    "worktree": { "type": "string" },
    "owned_paths": { "type": "array", "items": { "type": "string" } },
    "merge_gate": { "type": "string" },
```

(Only change `stream` from enum to string if enum blocks custom names.)

- [ ] **Step 4: Run existing supervisor tests + new**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py memory/test_supervisor_fsm.py memory/test_supervisor_mock_cycle.py memory/test_adapters.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/adapters/mock.py schemas/handoff.schema.json memory/test_streams.py
git commit -m "Расширил mock handoff полями stream/owned_paths для 3.5.1"
```

---

### Task 4: `run_parallel` orchestrator (TDD)

**Files:**
- Create: `memory/supervisor_parallel.py`
- Create: `memory/test_supervisor_parallel.py`

- [ ] **Step 1: Write failing integration test (two fake streams, no real multi-git required for core FSM)**

Strategy: for CI determinism, `run_parallel` accepts pre-built plans with `worktree` pointing at prepared dirs that already have prompts + config (same as `test_supervisor_mock_cycle`). Path gate can use a monkeypatched `list_changed_files` when not in a git repo; a second test uses real worktrees when git is available.

Create `memory/test_supervisor_parallel.py`:

```python
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

    # No real git diffs in tmp dirs — treat as clean ownership
    import memory.streams as streams_mod
    monkeypatch.setattr(streams_mod, "list_changed_files", lambda workdir, base_ref="main": [])
    # Skip git merge in unit test
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
    assert result["streams"]["harness"]["status"] == "STREAM_READY" or result["streams"]["harness"]["status"] == "MERGED"
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
```

- [ ] **Step 2: Run — expect fail (module missing)**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_supervisor_parallel.py -v
```

Expected: `ModuleNotFoundError: memory.supervisor_parallel`.

- [ ] **Step 3: Implement `memory/supervisor_parallel.py`**

```python
# -*- coding: utf-8 -*-
"""Parallel multi-stream supervisor (3.5.1) — serial stream runs + integration gate."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.streams import (
    StreamPlan,
    check_owned_paths_gate,
    provision_stream_worktrees,
    validate_stream_plans,
)
from memory.supervisor import Terminal, load_config, maybe_create_pr, run_loop, save_handoff


def merge_stream_branch(
    hub_workdir: Path,
    stream_branch: str,
    integration_branch: str,
    main_branch: str = "main",
) -> Dict[str, Any]:
    """
    Ensure integration_branch exists from main, merge stream_branch into it.
    Runs in hub_workdir (primary clone). Never merges to main.
    """
    hub = Path(hub_workdir)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(hub), capture_output=True, text=True
        )

    # create integration branch if needed
    r = git("rev-parse", "--verify", integration_branch)
    if r.returncode != 0:
        c = git("checkout", "-B", integration_branch, main_branch)
        if c.returncode != 0:
            return {"ok": False, "error": c.stderr or c.stdout}
    else:
        c = git("checkout", integration_branch)
        if c.returncode != 0:
            return {"ok": False, "error": c.stderr or c.stdout}

    m = git("merge", "--no-ff", stream_branch, "-m", f"Integrate stream branch {stream_branch}")
    if m.returncode != 0:
        git("merge", "--abort")
        return {"ok": False, "error": m.stderr or m.stdout}
    return {"ok": True, "branch": integration_branch}


def maybe_create_integration_pr(
    hub_workdir: Path,
    sup: dict,
    integration_branch: str,
) -> Terminal:
    """Create PR from integration branch; never merge main."""
    # ensure we are on integration branch is caller's job; reuse maybe_create_pr
    return maybe_create_pr(Path(hub_workdir), sup)


def _write_hub_streams_state(hub: Path, payload: Dict[str, Any]) -> None:
    agent = Path(hub) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "streams_state.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_parallel(
    hub_workdir: Path,
    plans: List[StreamPlan],
    adapter_name: Optional[str] = None,
    max_cycles_per_stream: int = 1,
    create_pr: bool = True,
    base_ref: str = "main",
    cycle_id: Optional[str] = None,
    wt_base: Optional[Path] = None,
    skip_provision: bool = False,
    integration_branch: Optional[str] = None,
) -> dict:
    """
    Serial parallel orchestration:
      provision → for each stream: run_loop → owned_paths gate
      → merge into integration branch → one PR.
    """
    hub_workdir = Path(hub_workdir).resolve()
    validate_stream_plans(plans)
    cfg = load_config(hub_workdir)
    sup = cfg.get("supervisor") or {}
    if not isinstance(sup, dict):
        sup = {}
    par = sup.get("parallel") or {}
    if not isinstance(par, dict):
        par = {}
    adapter_name = adapter_name or sup.get("adapter") or "mock"
    base_ref = par.get("base") or base_ref or "main"
    if integration_branch is None:
        integration_branch = par.get("integration_branch") or "feature/integration-parallel"

    if not skip_provision:
        plans = provision_stream_worktrees(
            repo_root=hub_workdir,
            plans=plans,
            cycle_id=cycle_id,
            wt_base=wt_base,
            main_branch=base_ref,
        )

    stream_results: Dict[str, Any] = {}
    for plan in plans:
        if not plan.worktree:
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"stream {plan.name} has no worktree",
                "streams": stream_results,
            }
        wt = Path(plan.worktree)
        plan.status = "RUNNING"
        env_patch = {
            "AGENTIX_STREAM": plan.name,
            "AGENTIX_OWNED_PATHS": ",".join(plan.owned_paths),
            "AGENTIX_WORKTREE": str(wt),
        }
        old_env = {k: os.environ.get(k) for k in env_patch}
        try:
            os.environ.update(env_patch)
            loop_res = run_loop(
                workdir=wt,
                adapter_name=adapter_name,
                max_cycles=max_cycles_per_stream,
                create_pr=False,  # one PR only at integration
            )
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        term = loop_res.get("terminal")
        term_s = term.value if isinstance(term, Terminal) else str(term)
        if term_s not in (Terminal.PR_READY.value, Terminal.PR_READY_LOCAL.value, "PR_READY", "PR_READY_LOCAL"):
            plan.status = "BLOCKED"
            stream_results[plan.name] = {
                "status": "BLOCKED",
                "loop": loop_res,
                "worktree": str(wt),
            }
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"stream {plan.name} terminal {term_s}",
                "streams": stream_results,
            }

        violations = check_owned_paths_gate(wt, plan.owned_paths, base_ref=base_ref)
        if violations:
            plan.status = "BLOCKED"
            stream_results[plan.name] = {
                "status": "BLOCKED",
                "reason": "owned_paths",
                "violations": violations,
                "worktree": str(wt),
            }
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"owned_paths violations in {plan.name}: {violations}",
                "streams": stream_results,
            }

        plan.status = "STREAM_READY"
        stream_results[plan.name] = {
            "status": "STREAM_READY",
            "loop": loop_res,
            "worktree": str(wt),
            "branch": plan.branch,
        }

    # Integration merges (order = plan order)
    for plan in plans:
        if not plan.branch:
            continue
        m = merge_stream_branch(
            hub_workdir=hub_workdir,
            stream_branch=plan.branch,
            integration_branch=integration_branch,
            main_branch=base_ref,
        )
        if not m.get("ok"):
            stream_results[plan.name]["status"] = "BLOCKED"
            stream_results[plan.name]["merge_error"] = m.get("error")
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"merge failed for {plan.name}: {m.get('error')}",
                "streams": stream_results,
            }
        stream_results[plan.name]["status"] = "MERGED"

    final_term: Terminal = Terminal.PR_READY_LOCAL
    if create_pr:
        final_term = maybe_create_integration_pr(hub_workdir, sup, integration_branch)

    payload = {
        "streams": stream_results,
        "terminal": final_term.value if isinstance(final_term, Terminal) else str(final_term),
        "integration_branch": integration_branch,
    }
    _write_hub_streams_state(hub_workdir, payload)
    # Hub last_handoff summary for humans
    save_handoff(
        hub_workdir,
        {
            "handoff_to": "None",
            "role": "Reviewer",
            "current_phase": "finalization",
            "cycle_number": 0,
            "summary": f"parallel integration {integration_branch}: {list(stream_results)}",
            "status": "DONE",
            "confidence": 0.9,
            "lessons_learned": ["parallel streams 3.5.1"],
            "sync_waived": "integration PR path",
            "process_tags": ["parallel_integration"],
            "stream": "cross",
            "merge_gate": "after-tests-green",
        },
    )

    exit_code = 0 if final_term in (Terminal.PR_READY, Terminal.PR_READY_LOCAL) else 1
    return {
        "terminal": final_term,
        "exit_code": exit_code,
        "streams": stream_results,
        "integration_branch": integration_branch,
    }
```


- [ ] **Step 4: Run tests**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_supervisor_parallel.py memory/test_streams.py -v
```

Expected: PASS. If ownership test runs mock loop before gate — ensure gate still runs after STREAM cycle and fails on violations.

- [ ] **Step 5: Commit**

```bash
git add memory/supervisor_parallel.py memory/test_supervisor_parallel.py
git commit -m "Реализовал run_parallel: serial streams, owned_paths, integration"
```

---

### Task 5: CLI `run-parallel` + config

**Files:**
- Modify: `memory/supervisor.py` (`main` only)
- Modify: `.agent/project_config.example.json`
- Modify: `memory/test_supervisor_parallel.py` (CLI smoke)

- [ ] **Step 1: Write failing CLI test**

```python
def test_cli_run_parallel_help():
    from memory.supervisor import main
    try:
        main(["run-parallel", "--help"])
    except SystemExit as e:
        assert e.code == 0
```

Or capture argparse:

```python
def test_cli_run_parallel_parses(tmp_path, monkeypatch):
    from memory import supervisor as s
    calls = {}

    def fake_run_parallel(**kwargs):
        calls.update(kwargs)
        return {"terminal": Terminal.PR_READY_LOCAL, "exit_code": 0, "streams": {}}

    monkeypatch.setattr(
        "memory.supervisor_parallel.run_parallel", fake_run_parallel
    )
    # import after patch path used inside main
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
```

Note: implement CLI so it imports `run_parallel` inside the branch (patch the same import path).

- [ ] **Step 2: Run — expect fail** (unknown command)

```bash
export PYTHONPATH=.
python3 -m memory.supervisor run-parallel --help
```

Expected: error unknown / invalid choice until implemented.

- [ ] **Step 3: Extend `main` in `memory/supervisor.py`**

After existing subparsers, add:

```python
    par_p = sub.add_parser(
        "run-parallel",
        help="Run N disjoint streams then integration PR (3.5.1)",
    )
    par_p.add_argument(
        "--stream",
        action="append",
        dest="streams",
        required=True,
        help="name:owned/path1,path2 (repeatable)",
    )
    par_p.add_argument("--adapter", default=None)
    par_p.add_argument("--max-cycles-per-stream", type=int, default=1)
    par_p.add_argument("--workdir", type=Path, default=None)
    par_p.add_argument("--wt-base", type=Path, default=None)
    par_p.add_argument("--cycle-id", default=None)
    par_p.add_argument("--base", default="main")
    par_p.add_argument("--integration-branch", default=None)
    par_p.add_argument("--no-pr", action="store_true")
    par_p.add_argument(
        "--skip-provision",
        action="store_true",
        help="Use plans only for worktrees already present (testing)",
    )
```

In dispatch:

```python
    if args.cmd == "run-parallel":
        from memory.streams import parse_stream_specs
        from memory.supervisor_parallel import run_parallel

        plans = parse_stream_specs(args.streams)
        res = run_parallel(
            hub_workdir=workdir,
            plans=plans,
            adapter_name=args.adapter,
            max_cycles_per_stream=args.max_cycles_per_stream,
            create_pr=not args.no_pr,
            base_ref=args.base,
            cycle_id=args.cycle_id,
            wt_base=args.wt_base,
            skip_provision=args.skip_provision,
            integration_branch=args.integration_branch,
        )
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
        return int(res.get("exit_code", 1))
```

Add to `.agent/project_config.example.json` under `supervisor`:

```json
    "parallel": {
      "base": "main",
      "integration_branch": "feature/integration-parallel",
      "wt_base": null,
      "require_owned_paths": true,
      "serial": true
    }
```

- [ ] **Step 4: Run tests**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_supervisor_parallel.py memory/test_streams.py memory/test_supervisor_mock_cycle.py -q
python3 -m memory.supervisor run-parallel --help
```

Expected: tests PASS; help prints.

- [ ] **Step 5: Commit**

```bash
git add memory/supervisor.py memory/test_supervisor_parallel.py .agent/project_config.example.json
git commit -m "Добавил CLI run-parallel и config supervisor.parallel"
```

---

### Task 6: Docs + VERSION 3.5.1 + script pointer

**Files:**
- Modify: `PARALLEL_PROTOCOL.md`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `README.md` (short section)
- Modify: `scripts/agentic_loop.sh` (echo Python entrypoint)

- [ ] **Step 1: Update PARALLEL_PROTOCOL.md** — add section:

```markdown
## Supervisor unattended (3.5.1+)

After worktrees exist (or let supervisor provision them):

```bash
export PYTHONPATH=.
python -m memory.supervisor run-parallel \
  --stream harness:memory/,tools/ \
  --stream docs:docs/ \
  --adapter mock \
  --no-pr

# Real adapter (when configured):
# python -m memory.supervisor run-parallel \
#   --stream harness:memory/ --stream docs:docs/ --adapter grok
```

- Human gate remains: **merge PR to `main` only**.
- Streams run **serially** in 3.5.1; concurrent fan-out is future work.
- Hub writes `.agent/streams_state.json` with per-stream status.
```

- [ ] **Step 2: VERSION → `3.5.1`**

- [ ] **Step 3: CHANGELOG entry**

```markdown
## 3.5.1

### Added
- Parallel streams: `memory/streams.py`, `memory/supervisor_parallel.py`
- CLI: `python -m memory.supervisor run-parallel --stream name:paths`
- owned_paths gate + serial integration merge + single PR
- Tests: `memory/test_streams.py`, `memory/test_supervisor_parallel.py`
```

- [ ] **Step 4: README** — under supervisor section, 5–10 lines for `run-parallel`.

- [ ] **Step 5: `scripts/agentic_loop.sh`** — after creating worktrees, print:

```bash
log "Next: PYTHONPATH=. python -m memory.supervisor run-parallel --stream <name:paths> ..."
```

- [ ] **Step 6: Full test suite subset**

```bash
export PYTHONPATH=.
python3 -m pytest memory/test_streams.py memory/test_supervisor_parallel.py memory/test_supervisor_fsm.py memory/test_supervisor_mock_cycle.py memory/test_adapters.py -q
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add PARALLEL_PROTOCOL.md VERSION CHANGELOG.md README.md scripts/agentic_loop.sh
git commit -m "Релиз 3.5.1: parallel streams supervisor + docs"
```

---

### Task 7: Optional real-git end-to-end smoke (manual / CI if git ok)

**Files:**
- Modify: `memory/test_supervisor_parallel.py` only if time; otherwise document manual smoke.

- [ ] **Step 1: Manual smoke (implementer runs once)**

```bash
cd /tmp && rm -rf agx-par && mkdir agx-par && cd agx-par
# clone or copy template; or use the real repo on a throwaway branch
cd /home/unhex/_PROJECT/agentic_loop_template
export PYTHONPATH=.
# dry provision via unit test is enough for CI; for real:
python -m memory.supervisor run-parallel \
  --stream harness:memory/ \
  --stream docs:docs/ \
  --adapter mock \
  --no-pr \
  --cycle-id smoke351 \
  --wt-base /tmp/agentic-wts-smoke
```

Expected: exit 0 **or** BLOCKED with clear merge error if hub is dirty — document outcome in commit message / CHANGELOG notes. Do **not** fail release if smoke is environment-specific; unit/integration tests above are the gate.

- [ ] **Step 2: Commit only if code fixes needed from smoke**

---

## Self-review (author checklist)

### 1. Spec coverage (`PARALLEL_PROTOCOL` + 3.5 deferred parallel)

| Requirement | Task |
|-------------|------|
| Disjoint paths / owned_paths | Task 1 (`files_outside_owned`, overlap reject) |
| Worktrees per stream | Task 2 (`provision_stream_worktrees`) |
| Handoff `stream` / `worktree` / `owned_paths` / `merge_gate` | Task 3 |
| Stream Coder/Tester isolated workdir | Task 4 (`run_loop` per worktree) |
| Integration Reviewer / merge order / no main merge | Task 4 (`merge_stream_branch`, `maybe_create_pr`) |
| Tests green before merge gate | Task 4 (stream must reach PR_READY/LOCAL; mock has green tests) |
| SYNC_DONE optional | Deferred as soft note in protocol; not hard-required in 3.5.1 code (YAGNI) |
| CLI unattended | Task 5 |
| Docs | Task 6 |
| Human merge main only | Tasks 4–5 (`create_pr` only; never `gh pr merge`) |

### 2. Placeholder scan

No TBD / “add tests later” / “similar to Task N” without full code. SYNC_DONE left explicitly out of hard gate (documented).

### 3. Type consistency

- `StreamPlan` fields: `name`, `owned_paths`, `worktree`, `branch`, `status`, `merge_gate`
- Status strings: `PENDING` → `RUNNING` → `STREAM_READY` → `MERGED` | `BLOCKED`
- Terminal reuse: `memory.supervisor.Terminal`
- CLI flag: `--stream` (repeatable) → `parse_stream_specs`

---

## Execution notes for agents

1. Work on branch `feature/agentix-parallel-3.5.1` from current `main`.
2. Prefer **Subagent-Driven**: one task per subagent; re-run listed pytest after each task.
3. Do not rewrite `run_loop` FSM; only call it.
4. Russian commit messages; no AI self-mentions.
5. After all tasks: open PR; human merges to `main`.
