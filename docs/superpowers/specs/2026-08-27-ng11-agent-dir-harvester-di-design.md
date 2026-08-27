# NG11 agent_dir DI for harvester / eval / resume — Design (Agentix v3.11.1)

**Title:** Additive `agent_dir=` and named `agent_lock` on `meta_harvester`, `eval_harness`, `resume`  
**Author:** Agentix SSOT cycle fire  
**Date:** 2026-08-27  
**Status:** Accepted for implementation (this fire)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.11.0**, `main` `f019fd8` (thread-safe `agent_lock`: in-process guard, not PID-only).  
**Target version:** **3.11.1** (patch: no new CLI, no wizard/proxy/`--concurrent` default change. Not 3.12.0.)  
**House style:** match [2026-08-26-p8-14-context-budgets-design.md](2026-08-26-p8-14-context-budgets-design.md) structure; path/lock pattern from [2026-08-26-conflict-free-parallel-sessions-design.md](2026-08-26-conflict-free-parallel-sessions-design.md) A8 (playbooks / ledger).  
**Canonical landing path:** `docs/superpowers/specs/2026-08-27-ng11-agent-dir-harvester-di-design.md`

This document is the execute-plan input for **NG11**, the leftover named in ROADMAP Future and in the 3.11.0 spec NG table. It does **not** reopen leases, `--push`, STOP fan-out, Streams, P8-12 module splits, `experience_harvester`, Control Plane wiring, Hub SaaS, MCP, messenger, or P8-13 MultiLLM.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Hub SaaS / MCP / messenger / i18n / embeddings | ROADMAP Future #1–#6 | Rejected this cycle. Different done-criteria. |
| B. P8-12 split `meta_harvester` / `experience_harvester` / Init.ps1 | Large-module modularize | Rejected. NG11 is path DI, not a file split. |
| C. API + dashboard/supervisor wiring + `--agent-dir` CLI | Full isolation | Rejected. 3.11 playbooks/ledger did not add CLI flags or invent call sites. Harvest from a worktree CLI already has the right cwd. |
| D. `bind_agent_dir` context manager rebinding module Path globals | Tiny public API | Rejected. Races under `--concurrent` threads. 3.11 deleted bind+chdir on purpose. |
| **E. Patch copy of the 3.11 playbooks/ledger pattern** | Additive `agent_dir=` on the three modules, named locks on writers, tmp+replace for JSON/MD indexes, cwd module globals stay as defaults, tests pass `agent_dir=` | **Accepted.** Patch **3.11.1** |

3.11.0 shipped exclusive leases, `--push`, STOP fan-out, Streams, live CLI identity, and `agent_lock` on audit / playbooks / questions / ledger. It parked `meta_harvester` / `eval_harness` / `resume` so those files would not join A8 `owned_paths`. That parking is the reason this fire exists.

---

## Overview

Three modules still resolve `.agent/` from process cwd via module-level `Path(".agent/…")`:

| Module | Kind | Paths |
|--------|------|-------|
| `memory/resume.py` | reader | `last_handoff.json`, `LOOP_STATE.md` (fallback glob `handoff_*.json`) |
| `memory/eval_harness.py` | reader | `TRAJECTORIES.json` |
| `memory/meta_harvester.py` | writer | `TRAJECTORIES.json`, `TRAJECTORIES/` (mkdir only), `META_PROPOSALS.md`, `project_config.json`, `sft/train.jsonl`, `LOOP_PERFORMANCE.md` |

Playbooks and ledger already take `agent_dir: Optional[Path] = None` and a named `agent_lock` on the parent of the file being written. `agent_dir` is the `.agent` directory itself (`Path(agent_dir) / "PLAYBOOKS.json"`). `agent_dir=None` keeps the module globals (CLI / old tests).

The upgrade copies that shape into the three modules. No new production modules (tests add `memory/test_meta_lock.py`). No new CLI. Dashboard and supervisor are **not** edited unless they already import these functions (they do not). `apply_safe_proposals` still writes `PROMPT_COMPRESSION_GUIDE.md` relative to cwd; that is a repo file, not `.agent` state. `basic_replay_harness` is a pure mock and stays without `agent_dir`.

`update_performance_ledger` is the one in-module caller of `performance_ledger.append_cycle`. Once it takes `agent_dir=`, it **must** pass that through. That is not a new call site. It is the DI actually working for the structured ledger.

---

## Background and motivation

### Current state (verified 2026-08-27 on `f019fd8`)

| Layer | What exists | Gap vs NG11 |
|-------|-------------|-------------|
| State / handoff / audit / questions / playbooks / ledger | `agent_dir=` + named `agent_lock` + tmp+replace | Done in 3.9.0 (P8-05) and 3.11.0 A8. |
| `resume.py` | `LAST_HANDOFF = Path(".agent/last_handoff.json")`, `LOOP_STATE = Path(".agent/LOOP_STATE.md")`. `build_resume_context()` has no kwargs. | Dashboard 3.8 spec told the store **not** to call this module (hard-coded paths). Tests assign module globals. |
| `eval_harness.py` | `TRAJECTORIES = Path(".agent/TRAJECTORIES.json")`. `replay_recent` has no `agent_dir`. | Same cwd leak. Reader of the file `meta_harvester` writes. |
| `meta_harvester.py` | Module Paths listed above. `_save_index` uses `write_text`. `export_sft` opens append. `update_performance_ledger` writes `LOOP_PERFORMANCE.md` with `write_text`, then `append_cycle()` with no kwargs. | Concurrent / in-process callers from hub cwd write hub `.agent/`. Crash mid-`write_text` can truncate JSON. `append_cycle` already has `agent_dir=` and `name="ledger"`; harvester does not pass it. |
| `agent_lock` | `O_EXCL` + stale PID + in-process `threading.Lock` per path (`f019fd8`). **Not reentrant.** | Nested `agent_lock` on the same `(dir, name)` deadlocks the same thread. Playbooks already uses `_load_index_unlocked` / `_write_index_unlocked`. |
| Tests | `test_p5_p7.py` assigns `resume.LAST_HANDOFF` / `eval_harness.TRAJECTORIES`. `test_meta_harvester.py` assigns `mh.TRAJECTORIES_INDEX` / `META_PROPOSALS_MD`. `test_playbooks_lock.py` is the pattern to copy. | No test that harvest with `agent_dir=` plus `chdir` elsewhere leaves cwd `.agent` untouched. |
| Call sites | Reviewer harvest is CLI from the worktree (`python -m memory.meta_harvester harvest --handoff …`). `memory/__init__.py` re-exports harvest functions. Supervisor `resume` subcommand is `run_loop`, not `memory.resume`. Dashboard does not import these three. | Wiring dashboard/supervisor is out (Decision C). CLI cwd default is enough for worktree harvest. |
| ROADMAP | Future: "`agent_dir=` / `agent_lock` on `meta_harvester`, `eval_harness`, `resume` cwd writers (NG11)" | This fire. |

### Pain

1. **In-process hub cwd.** `append_cycle` without `agent_dir` from `update_performance_ledger` writes the hub ledger when the supervisor process cwd is the hub, even if the cycle ran in a worktree. 3.11 locked ledger and gave it `agent_dir=`, then left this caller on purpose.
2. **Truncated JSON.** `TRAJECTORIES.json` is `write_text` of the whole index. Two harvests in one process (concurrent streams, or tests) also race. Named lock plus tmp+replace is what A8 already did for playbooks.
3. **Tests lie.** Assigning `TRAJECTORIES_INDEX = tmp / "TRAJECTORIES.json"` does not prove the public API can aim at a directory. Playbooks tests `chdir` elsewhere and pass `agent_dir=`.

### Why this leftover, why now

3.11 A8 closed audit / playbooks / questions / ledger. The spec said the remaining three are NG11, follow-up after 3.11.0. They are the last `.agent/` Path globals in the harvest/resume/eval path. Patch, not 3.12: no new product surface.

---

## Goals and non-goals

### Goals

| ID | Goal |
|----|------|
| G1 | Public functions on the three modules take `agent_dir: Optional[Path] = None`. `None` keeps module-level cwd Paths (CLI and any remaining no-kwarg caller). Explicit value is the `.agent` directory, same as playbooks. |
| G2 | Writers use named `agent_lock` on the parent of the file: `"trajectories"` for `TRAJECTORIES.json` + `META_PROPOSALS.md`, `"sft"` for default `sft/train.jsonl`, `"ledger"` for `LOOP_PERFORMANCE.md`. Readers (`resume`, `eval_harness`) take **no** lock. |
| G3 | `TRAJECTORIES.json` and `META_PROPOSALS.md` use tmp+replace (`*.json.tmp` / `*.md.tmp` then `Path.replace`). `LOOP_PERFORMANCE.md` same (trimmed last 50 lines). `sft/train.jsonl` stays append (`open(..., "a")`) under `"sft"` when dest is the default under `agent_dir`. |
| G4 | `agent_lock` is not reentrant. Index RMW uses unlocked helpers inside one lock section (`_load_index_unlocked` / `_write_index_unlocked`), same as playbooks. |
| G5 | `update_performance_ledger(..., agent_dir=)` writes the legacy md under `"ledger"`, **releases**, then calls `append_cycle(..., agent_dir=agent_dir)`. Same PID must not nest `"ledger"`. |
| G6 | Tests prove: files land in `agent_dir`, not cwd `.agent`; lock name and parent are the tmp dir; lock file present during index `replace`, absent after; two harvest threads `max_held == 1`; `test_p5_p7` / `test_meta_harvester` pass `agent_dir=` instead of assigning Path globals. |
| G7 | VERSION **3.11.1** only in the release commit (not this docs commit). Wizard default unchanged. No `--agent-dir`. No new extra. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | P8-12 split of `meta_harvester` / `experience_harvester` / Init.ps1 | Different leftover. This fire only threads `agent_dir`. |
| NG2 | Edit `experience_harvester.py` | 3.11 Q1 closed: skip. `maybe_cycle_on_done(..., apply=False)` does not write playbooks. |
| NG3 | Dashboard `resume_projection` calling `build_resume_context`; supervisor passing `agent_dir` into harvest | No current import. Decision C. Dashboard already reads explicit Paths. |
| NG4 | CLI `--agent-dir`; infer `agent_dir` from `--handoff`.parent | Playbooks CLI has no such flag. Worktree harvest cwd is already the worktree. |
| NG5 | `apply_safe` GUIDE edit under a lock or via `agent_dir` | `PROMPT_COMPRESSION_GUIDE.md` is a repo file. cwd is the clone. Out of NG11 path list. |
| NG6 | Lock `store.py` / `knowledge.sqlite`; `filelock` extra; `chdir` / bind | 3.11 NG6 / NG9. SQLite timeout is the DB lock. |
| NG7 | Shared `.agent/` across worktrees; making `--concurrent` the default | 3.11 NG8 / NG3. |
| NG8 | Lock sft when `export_sft(out=)` points at a caller path | Caller owns that file. Lock `"sft"` only for the default dest under `agent_dir`. |
| NG9 | Keep monkeypatch-of-globals as the test contract | G6. Globals remain as CLI defaults; tests pass `agent_dir=`. |
| NG10 | Hub SaaS, MCP, i18n, embeddings, P8-13, messenger | ROADMAP Future. |

---

## Proposed design

### 1. Path helpers (all three modules)

Copy playbooks. Module globals stay. Helpers:

```python
def _trajectories_index(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "TRAJECTORIES.json" if agent_dir is not None else TRAJECTORIES_INDEX
```

Same for `_trajectories_dir`, `_meta_proposals_md`, `_project_config_path`, `_sft_path`, `_loop_performance_md` in `meta_harvester.py`.

`resume.py`: `_last_handoff(agent_dir)`, `_loop_state(agent_dir)`. Fallback `handoff_*.json` glob stays, scoped to `_last_handoff(agent_dir).parent`.

`eval_harness.py`: `_trajectories(agent_dir)` → `TRAJECTORIES.json` (same filename as the harvester index).

`_ensure_agent_dir(agent_dir)` mkdir parents of the index (and `TRAJECTORIES/` in the harvester). Readers do not mkdir.

Keyword-only `*, agent_dir: Optional[Path] = None` on public functions that already have optional positional args (`replay_recent`, `get_recent_trajectories`, `export_sft`, `analyze_for_proposals`, `generate_proposals`, `apply_safe_proposals`, `update_performance_ledger`). Functions whose last args are already easy to extend (`load_last_handoff`, `build_resume_context`, `harvest_from_handoff`, `load_config`, `seed_example_trajectory`) add `agent_dir: Optional[Path] = None` at the end, matching playbooks `seed_initial_playbooks(agent_dir=None)`.

Do **not** add `agent_dir` to `score_trajectory`, `_sft_record`, or `basic_replay_harness` (pure).

`handoff_path` in `harvest_from_handoff` stays an explicit Path. It is **not** resolved through `agent_dir`.

### 2. Locks and unlocked RMW (`meta_harvester.py`)

```python
def _trajectories_lock(agent_dir: Optional[Path] = None):
    return agent_lock(_trajectories_index(agent_dir).parent, name="trajectories")
```

| Name | Parent | Held around |
|------|--------|-------------|
| `"trajectories"` | parent of `TRAJECTORIES.json` | load/save of the index **and** rewrite of `META_PROPOSALS.md` (always together in `_write_index_unlocked`) |
| `"sft"` | parent of default `sft/train.jsonl` | append of the default dest |
| `"ledger"` | parent of `LOOP_PERFORMANCE.md` | tmp+replace of that md only |

`_load_index` / `_save_index` take `_trajectories_lock`. `_load_index_unlocked` / `_write_index_unlocked` do not. `_write_index_unlocked` writes JSON tmp+replace, then `_write_human_summary` (MD tmp+replace). Callers that RMW (`harvest_from_handoff`, `analyze_for_proposals`, `apply_safe_proposals`, `seed_example_trajectory`) take the lock **once** and use unlocked helpers. Do not call `_save_index` from inside a section that already holds `"trajectories"`.

`get_recent_trajectories` may call `_load_index` (lock on read, like playbooks). `eval_harness.replay_recent` does **not** take that lock (G2: readers in resume/eval have no lock). POSIX `replace` keeps the JSON complete for an unlocked reader.

### 3. tmp+replace

JSON (playbooks):

```python
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(path)
```

MD: `path.with_suffix(".md.tmp")` then `replace`. After success the `*.tmp` file is gone.

Corrupt `TRAJECTORIES.json`: under the trajectories lock, rename to `.json.bak`, return empty index, `WARNING` on logger `memory.meta_harvester` via `memory.logutil`. Same bak behavior as today, now locked so it cannot race a writer.

### 4. `export_sft`

```python
def export_sft(
    out: Optional[Path] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    recent: int = 100,
    *,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
```

`dest = Path(out) if out is not None else _sft_path(agent_dir)`. `mkdir` parent. Read index via `_load_index(agent_dir)` (trajectories lock, then release). Append loop:

- default dest (`out is None`): `with agent_lock(dest.parent, name="sft"):` then `open(dest, "a")`.
- explicit `out`: no `"sft"` lock (NG8).

### 5. `update_performance_ledger` and `append_cycle`

```python
def update_performance_ledger(
    proposal_id: str,
    impact: str = "",
    cycle_stats: dict | None = None,
    *,
    agent_dir: Optional[Path] = None,
) -> None:
```

1. `_ensure_agent_dir(agent_dir)`.
2. `with agent_lock(_loop_performance_md(agent_dir).parent, name="ledger"):` read existing md (or empty), append one line, keep last 50, tmp+replace.
3. Exit the `with`.
4. Import `performance_ledger` the way the module already does (file-location spec, avoid `__init__` cycle). `append_cycle(..., agent_dir=agent_dir)` with the same kwargs as today (`cycle_stats` or the `META_APPLIED` fallback). Still non-fatal: existing `except` + stderr.

Do not hold `"ledger"` across the `append_cycle` call. `append_cycle` takes `"ledger"` itself. Nested same name deadlocks (G5).

Do not switch the file-location import to `from memory import performance_ledger` unless tests prove there is no cycle. Leave that import style alone.

### 6. `apply_safe_proposals`

`.agent` index RMW under `"trajectories"` (unlocked load/mutate/write). GUIDE edit stays `Path("PROMPT_COMPRESSION_GUIDE.md")` relative to cwd (NG5). After releasing `"trajectories"`, call `update_performance_ledger(..., agent_dir=agent_dir)` for each applied id (today it calls inside the loop, before `_save_index`). New order: save index, release, then ledger. Avoids trajectories → ledger nesting if a future caller ever takes ledger first.

Dry-run: no index write, no ledger, no GUIDE write. Same as today.

### 7. CLI

No new flags (NG4, G7). `_cli` calls the public functions with no `agent_dir` (cwd defaults). `--handoff` remains an explicit Path.

### 8. `__init__.py` re-exports

Signatures grow additively. Re-exports in `memory/__init__.py` stay. No new names.

---

## Data flow

```
agent_dir=None  → module Path(".agent/…")  → process cwd
agent_dir=Path  → Path(agent_dir) / filename

harvest_from_handoff(handoff_path, cycle, …, agent_dir=)
  read handoff_path as given
  load_config(agent_dir)          # project_config.json, no lock
  lock trajectories
    load unlocked → maybe append traj → write index + META_PROPOSALS.md
  release

export_sft(out=None, …, agent_dir=)
  dest = out or _sft_path(agent_dir)
  _load_index(agent_dir)          # trajectories lock, released
  if out is None: lock sft; append jsonl; release
  else: append without sft lock

update_performance_ledger(..., agent_dir=)
  lock ledger → tmp+replace LOOP_PERFORMANCE.md → release
  append_cycle(..., agent_dir=agent_dir)

resume / eval: read only; missing file → not resumable / empty list
```

---

## Error handling

| Case | Behavior |
|------|----------|
| Missing `TRAJECTORIES.json` | Empty index `{"trajectories": [], "proposals": [], "updated_at": …}`. |
| Corrupt JSON | Rename `.json.bak` under trajectories lock, empty index, WARNING on `memory.meta_harvester`. Do not raise. |
| Missing handoff file | `harvest_from_handoff` returns `None` (today). |
| Missing resume files | `resumable=False`, `recommended_next_role="Orchestrator"`. |
| `append_cycle` raises | Caught, stderr, md write already done. Cycle continues. |
| GUIDE I/O in `apply_safe` | Swallowed (today). Proposal still marked applied. |
| Lock timeout | `TimeoutError` from `agent_lock` (default 30s). These modules do not catch it. |
| Invalid `agent_dir` type | `Path(agent_dir)` like playbooks. No extra validation. |

Do not log handoff bodies or SFT message text.

---

## Testing

CI stays `pytest memory/`. Hermetic: `tmp_path` + `monkeypatch.chdir` elsewhere. Do not write the clone's live `.agent/`.

### New `memory/test_meta_lock.py` (copy `test_playbooks_lock.py` / `test_audit_lock.py`)

| Test | Assert |
|------|--------|
| `test_harvest_agent_dir_not_cwd` | `chdir` to a second tmp; `harvest_from_handoff(..., agent_dir=agent)`; `agent/TRAJECTORIES.json` and `META_PROPOSALS.md` exist; cwd `.agent` does not. |
| `test_harvest_lock_name_and_parent` | spy `agent_lock`; name `"trajectories"`; root is `agent.resolve()`, not hub `.agent`. |
| `test_harvest_lock_held_during_replace` | wrap `Path.replace` for `TRAJECTORIES.json`; lock file present at replace; absent after. |
| `test_harvest_releases_lock` | after harvest, `lock_path(agent, "trajectories")` does not exist; no leftover `.json.tmp` / `.md.tmp`. |
| `test_two_harvest_threads_max_held` | counting lock; two threads harvest different cycles (or seed + harvest); `max_held == 1`; no exceptions. |
| `test_export_sft_default_uses_sft_lock` | `export_sft(agent_dir=agent)` (no `out`); spy name `"sft"`; file at `agent/sft/train.jsonl`; cwd not written. |
| `test_export_sft_out_skips_sft_lock` | `export_sft(out=tmp/other.jsonl, agent_dir=agent)`; no `"sft"` lock; dest is `out`. |
| `test_update_performance_ledger_passes_agent_dir` | after harvest/seed; `update_performance_ledger("P-1", agent_dir=agent)`; `LOOP_PERFORMANCE.md` under `agent`; `PERFORMANCE_LEDGER.json` under `agent` (via `append_cycle`); cwd clean. |
| `test_ledger_lock_not_nested` | spy `agent_lock` around `update_performance_ledger`; `"ledger"` sections do not overlap on the same thread (enter count returns to 0 before the next enter). |

### Resume / eval

Rewrite `test_p5_p7.py` `TestResume` / `TestEvalHarness`: pass `agent_dir=` pointing at a tmp directory that **contains** `last_handoff.json` / `LOOP_STATE.md` / `TRAJECTORIES.json`. Stop assigning `resume.LAST_HANDOFF` and `eval_harness.TRAJECTORIES`.

New or folded tests:

| Test | Assert |
|------|--------|
| `test_build_resume_context_agent_dir` | handoff in `tmp/.agent`; `chdir` elsewhere; `build_resume_context(agent_dir=…)` resumable; cwd has no `.agent`. |
| `test_build_resume_context_missing` | no files; `resumable=False`. |
| `test_replay_recent_agent_dir` | write a small index under `agent`; `replay_recent(agent_dir=agent)` scores it; missing file → `[]`. |

### `test_meta_harvester.py`

Script-style test stays runnable as `python memory/test_meta_harvester.py`. Stop assigning `TRAJECTORIES_INDEX` / `META_PROPOSALS_MD`. Pass `agent_dir=tmp_path / ".agent"` (or `tmp_path` if the script keeps files at the tmp root: **prefer `tmp/.agent`** so it matches playbooks). `export_sft(out=sft)` remains an explicit dest.

Existing no-kwarg tests that run from repo root and only `load_config()` may keep using cwd defaults. Do not harvest into the live clone `.agent/` from pytest.

Canonical command:

```bash
PYTHONPATH=. python -m pytest memory/test_meta_lock.py memory/test_p5_p7.py memory/test_playbooks_lock.py memory/test_performance_ledger.py memory/test_agent_lock.py -q
```

Then full `python -m pytest -q memory/` before push. Worktrees may lack `.venv`. Use SSOT `/home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python` if needed.

---

## Docs and version

This fire (spec only): this file. `VERSION` stays **3.11.0**.

Release commit (implementation PR):

- `VERSION` → `3.11.1`
- CHANGELOG `[3.11.1]`: additive `agent_dir=` + named locks on `meta_harvester` / `eval_harness` / `resume`; tmp+replace for trajectory index and `META_PROPOSALS.md`; `update_performance_ledger` passes `agent_dir` into `append_cycle`. NG11 closed.
- ROADMAP: drop the NG11 Future bullet. Milestones row **v3.11.1**. Status date.
- README / `docs/README.md` version badges only. No `architecture.md` rewrite (P8-05 already describes `agent_dir=`).

Do not commit live `.agent/`.

---

## Alternatives considered

| Option | Verdict | Why |
|--------|---------|-----|
| **In-place playbooks clone (path helpers + unlocked RMW + named locks)** | **Chosen** | Same package already has the pattern. One PR. No new module. |
| Shared `memory/agent_paths.py` | Rejected | Playbooks/audit/ledger would not migrate this cycle. Two path styles. |
| `bind_agent_dir` rebinding globals | Rejected | Thread-unsafe. 3.11 removed bind+chdir. |
| One lock `name="meta"` for every harvester write | Rejected | 3.11 used per-artifact names so audit/questions/playbooks can proceed in parallel. `"ledger"` on `LOOP_PERFORMANCE.md` serializes with `append_cycle`. |
| Global `name="agent"` | Rejected | Serializes harvest with state/handoff. Diverges from 3.11. |
| tmp+replace for sft jsonl | Rejected | Gitignored append log. Copy-append-replace is more than the job needs. |
| Wire dashboard + `--agent-dir` | Rejected | Decision C. No current import. Patch rule. |
| Ship as 3.12.0 | Rejected | No new product surface. **3.11.1**. |
| Split `meta_harvester` (P8-12) in the same PR | Rejected | NG1. |

---

## Compatibility

- **Omit `agent_dir`:** CLI and `append_cycle()` from harvester with no kwargs keep writing cwd `.agent/`, same as 3.11.0. `update_performance_ledger` without kwargs still calls `append_cycle()` with no `agent_dir` (module ledger globals). Tests that only `chdir` into a tmp and use no kwargs keep working if they did today.
- **Module Path globals:** still assigned by CLI-default helpers. Assigning them in leftover scripts still redirects `agent_dir=None`. Tests stop relying on that (NG9).
- **`score_trajectory` / SFT record shape:** unchanged.
- **Wizard / proxy / `--concurrent` default / serial default:** unchanged.
- **Consumer:** symlink to SSOT. No vendor.
- **`memory/store.py` / `experience_harvester.py` / dashboard / supervisor:** untouched.

---

## Security and privacy

| Topic | Handling |
|-------|----------|
| Handoff / SFT message text | Not logged by the new helpers. |
| Lock files | PID only, already the 3.11 contract. Unlinked after the section. |
| `sft/train.jsonl` | Stays gitignored. This fire does not change gitignore. |

---

## Rollout / PRs

| PR | Contents | VERSION |
|----|----------|---------|
| PR0 (this fire) | this spec | unchanged **3.11.0** |
| PR1 | `resume.py`, `eval_harness.py`, `meta_harvester.py`, tests (`test_meta_lock.py`, rewrite `test_p5_p7.py` resume/eval, `test_meta_harvester.py`), VERSION **3.11.1**, CHANGELOG, ROADMAP, README badges | **3.11.1** |

One implementation PR. `owned_paths` can be those files. Not a parallel DAG.

Human gate. No auto-merge to `main`. Dual remotes: `github` may use default proxy; `origin` (Bitbucket) `env -u http_proxy -u https_proxy -u ALL_PROXY`. Do not merge messenger or other leftover worktrees.

---

## Open questions

None. Closed during brainstorming 2026-08-27:

| # | Question | Decision |
|---|----------|----------|
| Q1 | Done criterion | Patch copy of 3.11 playbooks/ledger. No new call sites, no CLI flag, no P8-12 split. |
| Q2 | Lock names | Per-artifact: `"trajectories"`, `"sft"`, `"ledger"` on `LOOP_PERFORMANCE.md`. Readers unscoped. |
| Q3 | Atomic writes | tmp+replace for JSON/MD indexes. sft stays append. |
| Q4 | How to thread `agent_dir` | In-place helpers. Not a shared `agent_paths` module, not a bind CM. |
| Q5 | PR shape | One implementation PR after this spec. |
