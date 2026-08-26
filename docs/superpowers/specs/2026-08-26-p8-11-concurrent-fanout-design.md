# Concurrent Fan-out + `.agent/` Lock — Design (Agentix v3.10.0)

**Title:** P8-11 True concurrent fan-out (`run-parallel --concurrent`) and stdlib `.agent/` lock  
**Author:** Agentix SSOT cycle fire (detached)  
**Date:** 2026-08-26  
**Status:** Implemented on main (this fire; VERSION 3.10.0)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.9.4**, `main` `cdd6afa` (token estimate P8-08). Both remotes match. CI `32995274427` green. ROADMAP next = Future.  
**Target version:** **3.10.0** (new product behavior: opt-in overlapping streams — not a 3.9.x patch)  
**House style:** match [2026-08-25-token-estimate-per-model-design.md](2026-08-25-token-estimate-per-model-design.md) structure; API detail as in P8 harness spec.  
**Canonical landing path:** `docs/superpowers/specs/2026-08-26-p8-11-concurrent-fanout-design.md`  
**Plan:** [../plans/2026-08-26-p8-11-concurrent-fanout.md](../plans/2026-08-26-p8-11-concurrent-fanout.md)

This document is the execute-plan input for **P8-11**, the leftover named “true concurrent fan-out / shared `.agent/` locking”. It does **not** reopen token estimate, harvest/reflect, Blackbox, Control Plane, packaging layout, P8-09 i18n, P8-10 embeddings, P8-12 splits, P8-13 MultiLLM, P8-14 supervisor caps, messenger, pxpipe-agy-docs worktrees, or Hub SaaS.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. P8-09 docs i18n / P8-10 embeddings / P8-12 splits / P8-13 MultiLLM / P8-14 caps | Other Future leftovers | Rejected this cycle. Not the dogfood pain; caps are a different done-criterion. |
| B. Messenger / pxpipe-agy-docs leftover worktrees | In-progress other branches | Rejected. Do not merge them. |
| C. Hub SaaS / Linear-Jira-Slack MCP | ROADMAP Future #1–#2 | Rejected this cycle. Optional/huge. |
| **D. P8-11 concurrent fan-out + `.agent/` lock** | Opt-in `concurrent=True` / `--concurrent`; contextvars instead of `os.environ` on that path; stdlib `O_EXCL` lock on `.agent/<name>.lock` | **Accepted.** 3.10.0 |

3.9.4 is shipped (`cdd6afa` on origin + github). The 2026-08-25 token-estimate spec **option B** rejected P8-11 as a patch (“3.10-level product; State DI already shipped; do not start locking in a patch”). That rejection still holds as a *patch* rule. This fire **is** the 3.10.0 product: spec, lock, concurrent `run-parallel`, tests, and VERSION land together.

---

## Overview

`python -m memory.supervisor run-parallel` already fans **disjoint** `owned_paths` into git worktrees and one integration PR. The loop over streams is **serial** (`memory/supervisor_parallel.py:run_parallel`): patch `os.environ` (`AGENTIX_STREAM` / `AGENTIX_OWNED_PATHS` / `AGENTIX_WORKTREE`), `run_loop`, owned-paths gate, next stream. Fail-fast on the first non-`PR_READY*` terminal or gate miss. Hub writes `.agent/streams_state.json` with `path.write_text` (not tmp+replace). `state.save_state` is tmp+replace but has **no** inter-process lock. `handoff_io.save_handoff` is tmp+replace, no lock. `memory/store.py` already has a private `O_EXCL` `_file_lock` for `memory.md` in the user home — **do not reopen that module**.

The upgrade is opt-in overlapping time for **disjoint** streams:

1. `run_parallel(..., concurrent: bool = False)` stays serial by default (existing tests remain valid).
2. `concurrent=True` runs stream `run_loop` calls on `concurrent.futures.ThreadPoolExecutor`.
3. That path **must not** mutate process-global `os.environ`. Identity travels on `contextvars` via new `memory/stream_context.py`.
4. Serial path keeps setting `os.environ` **and** the same contextvars (backward compatible).
5. Concurrent waits for **all** streams (no cancel). Any non-`STREAM_READY` / owned-paths miss → terminal `BLOCKED`, skip integration merge. Serial keeps today’s fail-fast.
6. Provision of worktrees and git integration merges stay **serial** (git is the lock).
7. New `memory/agent_lock.py`: stdlib-only exclusive lock (`os.O_CREAT|os.O_EXCL|os.O_WRONLY`) on `.agent/<name>.lock` writing PID. Stale PID recovered. No `filelock` extra.

Wizard default unchanged. Never merge to `main` from streams. Live Grok stays pxpipe-default (`proxy.mode=required`).

---

## Background & Motivation

### Current state (verified 2026-08-26)

| Layer | What exists | Gap vs P8-11 |
|-------|-------------|--------------|
| Orchestration | `run_parallel` `for plan in plans:` sets env, `run_loop(workdir=wt)`, owned-paths gate, fail-fast `return BLOCKED`. Then serial `merge_stream_branch`. | No overlapping time. Env patch is not thread-safe. |
| CLI | `python -m memory.supervisor run-parallel --stream name:paths/` (`memory/supervisor.py`). No `--concurrent`. | Flag + config key missing. |
| Config | `.agent/project_config.example.json` `supervisor.parallel`: `base`, `integration_branch`, `wt_base`, `require_owned_paths`, **`serial: true`**. | No `concurrent` key. Keep `serial: true` as the documented default; do **not** invert `serial` as the API (G2 names `concurrent`). |
| Stream identity | Mock adapter reads `os.environ["AGENTIX_STREAM" / "OWNED_PATHS" / "WORKTREE"]`. Live CLI adapters (`grok`/`blackbox`/`cursor`) `os.environ.copy()` into the child. | Concurrent threads would clobber process env. Contextvars required. Live children that only see env are **best-effort** under `--concurrent` (NG1). |
| Hub state | `_write_hub_streams_state`: `path.write_text(json.dumps(...))`. | Torn JSON under overlap; no tmp+replace; no lock. |
| `save_state` | tmp+replace `LOOP_STATE.json` + MD projection. No inter-process lock. | Two writers in one `.agent/` can interleave JSON vs MD. |
| `save_handoff` | tmp+replace `last_handoff.json`. No lock. | Same, per worktree; still racy if two processes share a dir. |
| `store._file_lock` | `O_EXCL` + PID write for home `memory.md`. No stale-PID recovery (busy-wait until timeout, then yields anyway). | Pattern to **copy the idea from**, not to import. Do not edit `memory/store.py`. |
| Tests | `memory/test_supervisor_parallel.py`: two-stream `PR_READY`, ownership `BLOCKED`, CLI parse. No overlap proof. No lock tests. | Need Barrier overlap test + lock unit tests. Serial default test **unchanged**. |
| Docs | `PARALLEL_PROTOCOL.md`: “Streams run **serially**; concurrent fan-out is future work.” ROADMAP Future bullet P8-11. | Product docs for opt-in concurrent; ROADMAP moves the bullet into **v3.10.0**. |
| Version | `VERSION` 3.9.4. Patch rule 3.9.1–3.9.4 = no new product surface. | This **is** new product surface (opt-in concurrent). **3.10.0**, VERSION only in the release commit. |

### Pain

1. **Wall-clock is N × stream** even when `owned_paths` are disjoint and each stream’s adapter is I/O bound (CLI subprocess / pxpipe). Serial was the honest 3.8.1 ship; operators now want overlap.
2. **`os.environ` is process-global.** Two threads patching `AGENTIX_STREAM` is a data race. Mock (and any in-process reader) would see the last writer’s stream. Cannot “just ThreadPoolExecutor” the current loop.
3. **Hub `streams_state.json` is a torn-write.** `write_text` in place. Concurrent completions (or a crash mid-write) yield partial JSON. State/handoff already learned tmp+replace; hub did not.
4. **No stale-PID recovery** in the only existing `O_EXCL` helper (`store.py`). A killed supervisor leaves `.agent/*.lock` forever if we add locking without `os.kill(pid, 0)`.

### Why this leftover, why now

P8 parked concurrent fan-out as Future. 3.9.0 shipped state `agent_dir=` (the prerequisite: no bind+chdir). 3.9.1–3.9.4 were patches (jira skill, Blackbox, harvest/reflect, token estimate) and **explicitly** refused P8-11. ROADMAP Future still leads with Hub SaaS / MCP — rejected this cycle as optional/huge. P8-11 is the first leftover that is a real product behavior, still closed-loop, and unblocks overlapping disjoint streams without a new dependency.

---

## Goals & Non-Goals

### Goals

| ID | Goal |
|----|------|
| G1 | `run_parallel(..., concurrent: bool = False)` default **serial** (existing tests stay valid). `concurrent=True` runs disjoint streams overlapping in time via `concurrent.futures.ThreadPoolExecutor`. |
| G2 | CLI `python -m memory.supervisor run-parallel --concurrent` (`store_true`). Also read `supervisor.parallel.concurrent` from project_config when the flag is absent. |
| G3 | Concurrent path MUST NOT mutate process-global `os.environ`. Use new `memory/stream_context.py` contextvars (`use_stream(name=, owned_paths=, worktree=)`). Serial path still sets `os.environ` (backward compatible) **and** contextvars. |
| G4 | `memory/adapters/mock.py` reads stream / owned_paths / worktree from contextvars **first**, then env. |
| G5 | Concurrent waits for **all** streams (no cancel). If any stream is not `STREAM_READY` / owned_paths gate fails, terminal `BLOCKED` and skip integration merge. Serial keeps today’s fail-fast. |
| G6 | Provision worktrees and integration git merges stay **serial** (git is the lock). |
| G7 | New `memory/agent_lock.py`: stdlib-only exclusive lock via `os.O_CREAT\|os.O_EXCL\|os.O_WRONLY` on `.agent/<name>.lock` writing PID. Context manager `agent_lock(agent_dir, *, name="agent", timeout=30.0)`. Stale lock: if PID in file is dead (`os.kill(pid, 0)` fails with `ProcessLookupError` / `ESRCH`), unlink and retry. Timeout raises `TimeoutError`. No `filelock` package, no new dependency. |
| G8 | `state.save_state` and `handoff_io.save_handoff` take the lock (`name="state"` / `name="handoff"`). `_write_hub_streams_state` becomes tmp+replace and takes `name="streams"` (or `threading.Lock` if `agent_lock` not yet imported — implementers split: lock module vs supervisor). Shipped 3.10.0 end state uses `agent_lock`, not a process-local lock alone. |
| G9 | Return dict of `run_parallel` includes `"mode": "serial"\|"concurrent"`. |
| G10 | VERSION **3.10.0** in the **release** commit only (not in this docs commit). Patch rule of 3.9.1–3.9.4 does not apply: this is a new product behavior (opt-in concurrent fan-out). Wizard default unchanged. Never merge to `main` from streams. Live Grok stays pxpipe-default. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | `ProcessPoolExecutor` / subprocess per stream | v1 is threads + contextvars. Live CLI adapters that `os.environ.copy()` into the child are **best-effort** under `--concurrent` (child may not see `AGENTIX_STREAM`). |
| NG2 | Shared `.agent/` across worktrees | Each worktree has its own `.agent/`; the lock protects **same-dir** writers. Hub lock is hub `.agent/` only. |
| NG3 | Making concurrent the default | Serial remains default. Existing tests, docs, and wizard stay serial. |
| NG4 | `filelock` extra, portalocker, fcntl-only | Must work on Windows via `O_EXCL`. No new dependency. |
| NG5 | P8-14 prompt caps, P8-09 i18n, messenger, Hub SaaS | Other leftovers / other worktrees. |
| NG6 | Auto-merge to `main` | Human gate (`PARALLEL_PROTOCOL.md`). Streams never merge `main`. |
| NG7 | Changing owned_paths overlap rules | Already hard-fail in `validate_stream_plans`. Concurrent does not relax that. |

---

## Proposed Design

**Shipped 2026-08-26:** `use_stream(*, name, owned_paths: str, worktree: str)` takes a CSV string (same as `AGENTIX_OWNED_PATHS`). Helpers `stream_name()` / `owned_paths_csv()` / `worktree_path()` try ContextVar then env. Mock adapter calls those helpers. Hub `streams_state.json` uses tmp+replace plus `agent_lock(name="streams")`. Sketch below was the contract; names differ slightly, behavior matches G1–G10.

### 1. Stream identity (`memory/stream_context.py`)

New module. Stdlib `contextvars`. Shipped helpers fall back to `os.environ` so serial env patches still work when no ContextVar is set.

```python
# memory/stream_context.py
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple, Union

_stream: ContextVar[Optional[str]] = ContextVar("agentix_stream", default=None)
_owned: ContextVar[Optional[Tuple[str, ...]]] = ContextVar(
    "agentix_owned_paths", default=None
)
_worktree: ContextVar[Optional[str]] = ContextVar("agentix_worktree", default=None)


@contextmanager
def use_stream(
    *,
    name: str,
    owned_paths: Sequence[str],
    worktree: Union[str, Path],
) -> Iterator[None]:
    t1 = _stream.set(name)
    t2 = _owned.set(tuple(owned_paths))
    t3 = _worktree.set(str(worktree))
    try:
        yield
    finally:
        _stream.reset(t1)
        _owned.reset(t2)
        _worktree.reset(t3)


def get_stream() -> Optional[str]:
    return _stream.get()


def get_owned_paths() -> Optional[Tuple[str, ...]]:
    return _owned.get()


def get_worktree() -> Optional[str]:
    return _worktree.get()
```

**Thread rule:** `ThreadPoolExecutor` workers on Python 3.10 do **not** copy the parent `Context`. Call `use_stream` **inside** the worker, not around `executor.submit`.

Keyword-only `name=` / `owned_paths=` / `worktree=` as written in G3. Public names stay English; module docstring / comments in Russian (`DEVELOPMENT_STANDARDS` §1).

### 2. Mock adapter (G4)

`memory/adapters/mock.py` already stamps `stream` / `owned_paths` / `worktree` from env. Change to contextvars first:

```python
from memory.stream_context import get_owned_paths, get_stream, get_worktree

stream = get_stream() or os.environ.get("AGENTIX_STREAM")
if stream:
    data["stream"] = stream
owned = get_owned_paths()
if owned:
    data["owned_paths"] = list(owned)
else:
    env_owned = os.environ.get("AGENTIX_OWNED_PATHS")
    if env_owned:
        data["owned_paths"] = [p.strip() for p in env_owned.split(",") if p.strip()]
wt = get_worktree() or os.environ.get("AGENTIX_WORKTREE")
if wt:
    data["worktree"] = wt
```

Do **not** require grok/cursor/blackbox to do the same this cycle (NG1). Document in `PARALLEL_PROTOCOL.md` that live CLI children inherit the process env snapshot, which the concurrent path will not patch.

`run_loop` already calls `get_adapter(...)` per invocation — each stream gets its **own** adapter instance (no shared `MockAdapter._step` across threads). Do not introduce a process-global adapter cache.

### 3. `run_parallel` (G1, G5, G6, G9)

Signature gains one keyword argument; default preserves today’s loop:

```python
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
    concurrent: bool = False,
) -> dict:
```

**Both modes (unchanged order):** `validate_stream_plans` → load config → **serial** `provision_stream_worktrees` unless `skip_provision` (G6).

**Serial (`concurrent=False`, default):**

1. For each plan, `with use_stream(...)` **and** the existing `os.environ` patch/restore.
2. Fail-fast on missing worktree, non-`PR_READY*` terminal, or owned-paths violations (today’s returns).
3. Then serial integration merges + one PR.
4. Return dict includes `"mode": "serial"`.

**Concurrent (`concurrent=True`):**

1. Do **not** touch `os.environ`.
2. `ThreadPoolExecutor(max_workers=max(1, len(plans)))`. Submit one worker per plan. Worker body:

   ```python
   def _run_one(plan: StreamPlan) -> tuple[StreamPlan, dict]:
       with use_stream(
           name=plan.name,
           owned_paths=plan.owned_paths,
           worktree=plan.worktree,
       ):
           try:
               loop_res = run_loop(
                   workdir=Path(plan.worktree),
                   adapter_name=adapter_name,
                   max_cycles=max_cycles_per_stream,
                   create_pr=False,
               )
           except Exception as exc:
               loop_res = {
                   "terminal": Terminal.BLOCKED,
                   "exit_code": 1,
                   "reason": str(exc),
               }
           return plan, loop_res
   ```

3. Wait for **all** futures (`f.result()` for every future). Do **not** `shutdown(cancel_futures=True)`. Do **not** cancel siblings when one `BLOCKED`.
4. After the join, in **plan order**: classify `STREAM_READY` vs `BLOCKED` (same terminal set as today: `PR_READY` / `PR_READY_LOCAL` plus owned-paths gate). Collect every stream into `stream_results`.
5. If **any** stream is not `STREAM_READY`: write hub state, return `terminal=BLOCKED`, `exit_code=1`, `"mode": "concurrent"`. **Skip** `merge_stream_branch` and integration PR.
6. If all `STREAM_READY`: serial merges (G6), then maybe integration PR, hub state + hub `save_handoff` as today. Return includes `"mode": "concurrent"`.

Hub `streams_state.json` on the concurrent path: **one** write after the join (all results known). Serial may keep today’s per-stream writes for fail-fast observability.

Missing worktree before submit: same as today (`BLOCKED`, that stream named in `reason`). Concurrent still does not start the pool if a plan has no worktree after provision — fail closed before overlap (provision is the serial gate). If implementers prefer to run the others anyway, that would violate “provision first”; do not.

Return shape (both modes) — existing keys plus `mode`:

```python
{
    "terminal": final_term,          # Terminal or str, unchanged
    "exit_code": 0 | 1,
    "streams": stream_results,
    "integration_branch": integration_branch,  # success path; may be absent on early serial fail-fast
    "mode": "serial" | "concurrent",
}
```

Adding `mode` is backward compatible (`test_run_parallel_two_streams_pr_ready` does not assert a closed key set).

### 4. CLI + config (G2)

`memory/supervisor.py` `run-parallel` subparser:

```python
par_p.add_argument(
    "--concurrent",
    action="store_true",
    help="Overlap disjoint streams in time (ThreadPoolExecutor); default serial",
)
```

Resolution when calling `run_parallel`:

1. If `--concurrent` is present → `concurrent=True` (flag wins).
2. Else `load_config(workdir)` → `bool((supervisor.parallel or {}).get("concurrent"))`. Missing / `null` / `false` → `False`.
3. No `--no-concurrent` this cycle. To force serial when the file has `"concurrent": true`, set the file back to `false` (or omit the key).

Python API: `concurrent: bool = False` as G1. Direct callers must pass `True`; `run_parallel` does **not** re-read config to override an explicit `False`. Config is the CLI-when-flag-absent path.

Example json — add the key, do **not** remove `serial`:

```json
"parallel": {
  "base": "main",
  "integration_branch": "feature/integration-parallel",
  "wt_base": null,
  "require_owned_paths": true,
  "serial": true,
  "concurrent": false
}
```

Edit **only** `.agent/project_config.example.json`. Never live `.agent/project_config.json`.

### 5. `memory/agent_lock.py` (G7)

Stdlib: `os`, `time`, `errno`, `contextlib`, `pathlib`. No `fcntl`, no extras.

```python
@contextmanager
def agent_lock(
    agent_dir: Path | str,
    *,
    name: str = "agent",
    timeout: float = 30.0,
) -> Iterator[None]:
    """Эксклюзивный lock `.agent/<name>.lock` через O_EXCL, PID в файле."""
```

Contract:

| Rule | Behavior |
|------|----------|
| Path | `Path(agent_dir) / f"{name}.lock"`. `mkdir(parents=True, exist_ok=True)` on `agent_dir`. |
| Acquire | `os.open(path, os.O_CREAT \| os.O_EXCL \| os.O_WRONLY)` then `os.write(fd, str(os.getpid()).encode("utf-8"))`. |
| Busy | `FileExistsError` → stale-check; else `time.sleep(0.05)` until `timeout`. |
| Stale | Read PID (`int` of stripped text). `os.kill(pid, 0)`: `ProcessLookupError` or `OSError.errno == errno.ESRCH` → unlink and retry `O_EXCL`. `PermissionError` / other `OSError` → treat as **live** (do not steal). Unreadable / empty / non-int PID → stale (otherwise a corrupt file deadlocks). |
| Timeout | `raise TimeoutError(...)` with path and timeout in the message. |
| Release | `os.close(fd)` then `Path.unlink(missing_ok=True)`. Best-effort; do not raise from `finally` on unlink. |
| Reentrancy | **Not** reentrant. Same thread / process must not nest the same `name` in the same dir. |
| Windows | `O_EXCL` is the portable exclusive-create. Do not add an `fcntl` branch. |

Do **not** import this from `memory/store.py`. Do not replace `_file_lock`. Two lock helpers in the tree is acceptable: store’s is private to home `memory.md`; this one is the `.agent/` product lock.

### 6. Wiring the lock (G8)

| Writer | Lock `name=` | Notes |
|--------|--------------|-------|
| `memory/state.py` `save_state` | `"state"` → `.agent/state.lock` | Hold across JSON tmp+replace **and** MD projection so the pair stays consistent. |
| `memory/handoff_io.py` `save_handoff` | `"handoff"` → `.agent/handoff.lock` | Hold across tmp+replace of `last_handoff.json`. |
| `_write_hub_streams_state` | `"streams"` → `.agent/streams.lock` | tmp+replace `streams_state.json` (`path.with_suffix(".json.tmp")` then `replace`). |

PR split allowed: land `agent_lock.py` + tests before supervisor imports it. Interim `threading.Lock` **inside** `_write_hub_streams_state` is acceptable only on that intermediate commit; 3.10.0 shipped code uses `agent_lock`. `threading.Lock` is not inter-process.

Do **not** hold `agent_lock` across `run_loop` or `git merge` — that would serialize the fan-out we just added. Locks wrap **file writes** only.

Timeout propagating out of `save_state` / `save_handoff` is correct (new, rare). Do not swallow into empty state.

### 7. Docs (implementer PRs, not this fire)

- `PARALLEL_PROTOCOL.md`: concurrent is opt-in `--concurrent`; provision + merges stay serial; `.agent/` writers take `agent_lock`; live CLI adapters best-effort under overlap; still never merge `main`.
- README table row for `run-parallel`: mention `--concurrent`.
- `docs/architecture.md` Core Components: one row for stream context + `.agent/` lock.
- ROADMAP: drop the “True concurrent fan-out …” Future bullet; add Milestones **v3.10.0**; leftover list keeps i18n, embeddings, splits, MultiLLM, caps, Hub SaaS.
- CHANGELOG 3.10.0 in the **release** commit with VERSION / badges (`README.md`, `docs/README.md`, `memory/README.md`, `ROADMAP.md`).
- Wizard / Init frontend: **no change** (G10).

### 8. Observability

No new logger required on the lock hot path. `TimeoutError` is the signal. Do not log lock-file contents beyond PID (PID is not secret). Do not log prompt/handoff bodies.

---

## Alternatives Considered

| Option | Verdict | Why |
|--------|---------|-----|
| **Threads + contextvars + stdlib `O_EXCL` lock; default serial** | **Chosen** | Matches ROADMAP P8-11; existing tests stay green; Windows-safe; no extra. |
| `ProcessPoolExecutor` / one subprocess per stream | Rejected this cycle | NG1. Pickle/`run_loop` surface is larger; live CLI already subprocesses. |
| Make concurrent the default | Rejected | NG3. Fail-fast serial is the tested contract. |
| Keep patching `os.environ` under a `threading.Lock` | Rejected | Still wrong for overlapping *time* (the whole point); mock would serialize on env anyway. |
| `filelock` / portalocker extra | Rejected | NG4. P8 required-dep set is jsonschema; do not grow it for a 20-line helper. |
| fcntl / flock only | Rejected | Unix-only. `O_EXCL` is the portable exclusive-create. |
| Re-use `memory.store._file_lock` | Rejected | Private, no stale-PID recovery, home `memory.md` concerns, “do not reopen that module”. |
| Shared `.agent/` across worktrees + one global lock | Rejected | NG2. Isolation is the worktree. |
| Ship P8-11 as 3.9.5 patch | Rejected | Token-estimate option B; new product behavior → **3.10.0**. |
| Wait-all in serial too | Rejected | Serial fail-fast is today’s operator-visible behavior; G5 splits the modes. |
| `--no-concurrent` triad | Rejected this cycle | G2 is `store_true` + config when flag absent. |

---

## Compatibility

- **Default:** `run_parallel(...)` without `concurrent=` is serial, still patches env, still fail-fast. `test_run_parallel_two_streams_pr_ready` unchanged assertions.
- **Return dict:** additive `"mode"` key.
- **CLI:** new optional flag; existing invocations omit it → serial (unless example-config is copied with `concurrent: true`; example ships `false`).
- **Mock:** env fallback remains, so tests that only `monkeypatch.setenv("AGENTIX_STREAM", ...)` keep working (`memory/test_streams.py`).
- **Live adapters:** concurrent is best-effort (NG1). Serial + env patch unchanged.
- **owned_paths overlap:** still hard-fail at `validate_stream_plans` (NG7).
- **Git:** provision + integration merge still serial; never merge `main`.
- **Wizard / proxy:** grok + `proxy.mode=required` unchanged (G10).
- **`memory/store.py`:** untouched.
- **Live `.agent/`:** never commit. Example json only.
- **Consumer:** remains symlink to SSOT; no vendor.

---

## Testing

New file `memory/test_agent_lock.py`. Extend `memory/test_supervisor_parallel.py`. CI stays `pytest memory/`.

| Test | Setup | Assert |
|------|-------|--------|
| `test_acquire_release` | tmp `.agent/` | Inside `agent_lock` the `agent.lock` exists and contains `os.getpid()`; after `with` the file is gone. |
| `test_two_threads_one_winner` | two threads, shared name, `timeout=5` | `max_held == 1` (counter around the hold); both threads complete. |
| `test_stale_pid_recovered` | write `99999999` (dead) into `state.lock` | `with agent_lock(..., name="state")` succeeds; PID replaced with ours. |
| `test_timeout` | holder thread keeps the lock | Second `agent_lock(..., timeout=0.3)` raises `TimeoutError`. |
| `test_run_parallel_two_streams_pr_ready` | **existing** | Unchanged behavior; may see `"mode": "serial"` if asserted later — do **not** break current asserts. |
| `test_run_parallel_concurrent_overlaps` | monkeypatch `run_loop` + `threading.Barrier(2)` | `concurrent=True` both workers hit `barrier.wait` without timeout; `result["mode"] == "concurrent"`; exit 0. |
| `test_run_parallel_concurrent_blocked_skips_merge` | one stream `BLOCKED`, one `PR_READY_LOCAL`; spy `merge_stream_branch` | Wait-all: **both** names in `result["streams"]`; `terminal` `BLOCKED`; merge **not** called. |
| `test_cli_run_parallel_parses_concurrent` | `s.main(["run-parallel", ..., "--concurrent", ...])` | Fake `run_parallel` sees `concurrent is True`. |
| Serial CLI without the flag | existing `test_cli_run_parallel_parses` | Still 0; `concurrent` either absent or `False`. |

Hermetic: no network, no real git merges (keep today’s merge monkeypatch). Barrier timeout ≤ 5s so a serial-by-mistake implementation fails the overlap test instead of hanging CI.

Canonical command (SSOT venv — worktrees may lack `.venv`):

```bash
PYTHONPATH=. /home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python -m pytest memory/test_agent_lock.py memory/test_supervisor_parallel.py -q
```

Do not require `filelock`. Do not hit live `.agent/` of the clone.

---

## Security & Privacy

| Topic | Handling |
|-------|----------|
| Lock file | PID only. No handoff / prompt / token text. |
| `os.kill(pid, 0)` | Existence probe; signal 0 does not kill. |
| Steal live lock | Forbidden (`PermissionError` = live). |
| Worktree isolation | Concurrent does not share `.agent/` across streams (NG2). |
| `main` | Still never merged from streams (NG6). |

---

## Rollout / PRs

| PR | Contents | VERSION |
|----|----------|---------|
| PR0 (this fire) | spec + plan only | unchanged **3.9.4** |
| PR1 | `memory/agent_lock.py` + `memory/test_agent_lock.py` | unchanged |
| PR2 | `memory/stream_context.py` + mock contextvars-first | unchanged |
| PR3 | `run_parallel(concurrent=)`, CLI `--concurrent`, overlap + wait-all tests | unchanged |
| PR4 | `save_state` / `save_handoff` / hub `streams_state` lock + tmp+replace | unchanged |
| PR5 | example json `concurrent: false`, `PARALLEL_PROTOCOL.md`, README, architecture | unchanged |
| PR6 | VERSION **3.10.0**, CHANGELOG, ROADMAP, badges | **3.10.0** last |

Human gate. No auto-merge to `main`. Dual remotes: `github` may use default proxy; `origin` (Bitbucket) `env -u http_proxy -u https_proxy -u ALL_PROXY`. Consumer stays symlink. Do not merge messenger or pxpipe-agy-docs worktrees.

Worktree (docs fire): `/home/unhex/.grok/worktrees/project-agentic-loop-template/subagent-01a03f39-0f47-78c1-96d1-3cd73a4e86fa` on `feature/v3.10.0-p8-11-concurrent-fanout-20260826` from `origin/main` `cdd6afa`.

Implementer worktree (next fire): `/home/unhex/.grok/worktrees/project-agentic-loop-template/v310-concurrent-fanout` on the same branch (or recreate from `origin/main` once this docs commit is on the branch you push).

---

## Spec self-review

1. **Placeholders:** none. Function names, lock `name=` values, CLI flag, config key, test names, PR split, pytest command are explicit.
2. **Consistency:** G1 default serial = existing tests. G3 concurrent does not touch `os.environ`. G5 wait-all vs serial fail-fast is the only behavioral fork after provision. G6 git stays serial. G7 `O_EXCL` + stale PID. G10 VERSION last.
3. **Scope:** three new modules (`agent_lock`, `stream_context`, tests) + wiring in supervisor / mock / state / handoff_io + docs. No store.py. No ProcessPool. No wizard. No Hub SaaS.
4. **Ambiguity:** live CLI env-copy is documented best-effort (NG1), not a silent product claim. Config is CLI-when-flag-absent; Python `concurrent=False` does not re-read the file. Hub concurrent write is once after join.

---

## Open questions (none blocking)

Injecting contextvars into grok/blackbox/cursor child env (`AGENTIX_STREAM=...` on the subprocess dict only) is a follow-up, not G4. Operators who need live-adapter stream stamps under `--concurrent` wait for that slice or run serial. No user decision required to implement G1–G10.
