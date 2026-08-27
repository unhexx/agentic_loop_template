# P8-13 MultiLLM extract — Design (Agentix v3.11.3)

**Title:** Extract `MultiLLM*` dataclasses and ontology CRUD out of `schema.py` / `store.py`  
**Author:** Agentix SSOT cycle fire  
**Date:** 2026-08-27  
**Status:** Accepted for implementation (this fire)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.11.2**, `main` `eb94e9b` (P8-09 Path 1 docs i18n).  
**Target version:** **3.11.3** (patch: no new CLI, no wizard/dashboard/supervisor wiring. Not 3.12.0.)  
**House style:** match [2026-08-27-ng11-agent-dir-harvester-di-design.md](2026-08-27-ng11-agent-dir-harvester-di-design.md) structure; leftover named in [2026-08-24-p8-harness-hardening-design.md](2026-08-24-p8-harness-hardening-design.md) NG9.  
**Canonical landing path:** `docs/superpowers/specs/2026-08-27-p8-13-multillm-extract-design.md`

This document is the execute-plan input for **P8-13**, parked as “MultiLLM* dataclasses: use or extract.” It does **not** reopen P8-09 i18n, NG11 harvester DI, leases, Hub SaaS, MCP, embeddings, P8-12 splits, or messenger.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Hub SaaS / MCP / messenger / embeddings / P8-12 | Other Future leftovers | Rejected. Different done-criteria. P8-12 is a large-module split. |
| B. **Use:** wire MultiLLM sessions into the supervisor loop | Product: parallel models per role | Rejected. New CLI/product surface; not a patch. |
| C. Delete the dataclasses and `llm_ontology.json` | “Unused, drop it” | Rejected. `snapshot()` already embeds `llm_ontology`; CRUD is live API. |
| D. Tests only, leave types in `schema.py` and CRUD in `store.py` | Prove roundtrip, skip extract | Rejected. The leftover is extract, not coverage of a mixed module. |
| **E. Extract to `memory/llm_ontology.py`, re-export, `agent_lock`, `base_dir=`** | Dedicated module; `schema`/`store` keep import compatibility; hermetic tests; patch **3.11.3** | **Accepted.** |

“Use or extract” forks. Use is a MultiLLM product. Extract is the closed-loop leftover: types and CRUD leave the institutional-memory markdown schema, stay callable, gain tests and the same lock as other writers.

---

## Overview

`memory/schema.py` holds `MemoryState` / `Pattern` (markdown memory) **and** six MultiLLM dataclasses. `memory/store.py` holds markdown RMW **and** a second file (`{wid}.llm_ontology.json`) with a homemade `_file_lock`. There are **no** tests. Supervisor, dashboard, and adapters do not import these types.

This fire moves types + CRUD into `memory/llm_ontology.py`. On-disk layout stays workspace-scoped under `memory_paths()["dir"]`. Writers take `agent_lock(..., name="llm_ontology")`. Tests pass `base_dir=` so they never touch `~/.grok/agentic-loop-memory`. `from memory.schema import MultiLLMSession` and `from memory.store import create_llm_session` keep working via re-exports.

---

## Background and motivation

### Current state (verified 2026-08-27 on `eb94e9b`)

| Layer | What exists | Gap vs P8-13 |
|-------|-------------|--------------|
| Types | `LLMProvider`, `PromptVariant`, `MultiLLMSession`, `ModelComparisonResult`, `Decision`, `CrossModelToolCall` in `schema.py` (`to_dict` / `from_dict`, extra keys ignored). | Mixed with markdown memory schema. |
| CRUD | `create_llm_provider`, `create_llm_session` (+ alias `create_multi_llm_session`), `record_model_comparison`, `record_decision`, `record_cross_tool_call` (+ alias), `query_llm_sessions`, `get_llm_ontology_snapshot` in `store.py`. File `{wid}.llm_ontology.json`, lock `{wid}.llm.lock`, tmp+replace. | Homemade `_file_lock`, not `agent_lock`. No `base_dir=`. |
| Snapshot | `store.snapshot()` sets `llm_ontology` from `get_llm_ontology_snapshot`; swallow → `{}`. | Coupling store markdown to ontology. |
| Call sites | Nothing in supervisor / dashboard / adapters. `__init__.py` does not re-export MultiLLM. | Extract, do not invent call sites. |
| Tests | Zero matches for `MultiLLM` / `llm_ontology` in `memory/test_*.py`. | G6. |
| ROADMAP | Future: “MultiLLM* dataclasses: use or extract (P8-13)” | This fire. |
| VERSION | 3.11.2 | Patch **3.11.3**. |

### Pain

1. **Two ontologies in one file.** Reviewers of markdown memory must skip CROSS-MEMORY-002 dataclasses. Reviewers of MultiLLM must scroll past `render_markdown`.
2. **Untested persistence.** Corrupt JSON, extra keys, query filters, and lock behaviour have no pytest.
3. **Lock drift.** Playbooks / ledger / harvest use `agent_lock`. Ontology uses a 10s homemade lock without stale-PID recovery or in-process thread guard.

### Why this leftover, why now

P8 NG9 parked “use or extract.” 3.11.2 closed P8-09. Remaining Future items that need Hub, MCP, embeddings, mobile, or a 800-line split are out. Extract is stdlib-only and one cycle.

---

## Goals and non-goals

### Goals

| ID | Goal |
|----|------|
| G1 | Dataclasses live in `memory/llm_ontology.py`. Behaviour of `to_dict` / `from_dict` (defaults, ignore extra keys) unchanged. |
| G2 | CRUD + snapshot reader live in that module. On-disk name remains `{wid}.llm_ontology.json` under `memory_paths()["dir"]` when `base_dir` is omitted. |
| G3 | `memory.schema` re-exports the six types. `memory.store` re-exports CRUD + aliases + `get_llm_ontology_snapshot`. `store.snapshot()` still embeds `llm_ontology`. |
| G4 | Writers use `agent_lock(dir, name="llm_ontology")`. Reads stay unlocked. tmp+replace stays. |
| G5 | Public functions take optional `cwd=` (workspace id) and keyword-only `base_dir=` (directory for the json + lock). Tests pass `base_dir=` and never write the operator home memory dir. |
| G6 | `memory/test_llm_ontology.py`: roundtrip, extra keys, create+query, snapshot key, corrupt JSON → empty collections, lock file present during write / absent after, schema and store re-exports. |
| G7 | VERSION **3.11.3** only in the release commit. No new CLI, extra, or supervisor/dashboard edits. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | Wire MultiLLM into supervisor / adapters / dashboard | Decision B. |
| NG2 | P8-12 split of harvesters / Init.ps1 | Different leftover. |
| NG3 | Move the json into `.agent/` or change the `{wid}.` prefix | On-disk compatibility. |
| NG4 | Switch markdown `store.py` RMW to `agent_lock` | Out of MultiLLM extract. |
| NG5 | New pip extra, HTTP to providers, cost accounting | No new deps / network. |
| NG6 | Hub SaaS, MCP, embeddings, messenger, i18n follow-up | Other leftovers. |
| NG7 | Re-export MultiLLM from `memory/__init__.py` | `__init__` already guards schema/store; do not grow it. |

---

## Proposed design

### 1. Module `memory/llm_ontology.py`

Dataclasses (moved verbatim in behaviour, Russian comments kept):

- `LLMProvider`, `PromptVariant`, `MultiLLMSession`, `ModelComparisonResult`, `Decision`, `CrossModelToolCall`

Helpers and public functions:

```python
_LLM_STATE_FILE = "llm_ontology.json"
_LOCK_NAME = "llm_ontology"

def _get_llm_paths(cwd: Path | None = None, *, base_dir: Path | None = None) -> dict[str, Any]:
    mp = memory_paths(cwd=cwd)
    wid = mp["workspace_id"]
    root = Path(base_dir) if base_dir is not None else Path(mp["dir"])
    return {"file": root / f"{wid}.{_LLM_STATE_FILE}", "dir": root, "workspace_id": wid}

def create_llm_provider(provider, cwd=None, *, base_dir=None) -> dict[str, Any]: ...
# same cwd / base_dir for session, comparison, decision, tool_call, query, snapshot
```

Empty / missing / non-dict / unreadable JSON → `{"providers": [], "sessions": [], "comparisons": [], "tool_calls": [], "decisions": []}`.

Write: `agent_lock(root, name="llm_ontology")`, then read-modify, `*.tmp` + `Path.replace`.

Aliases stay: `create_multi_llm_session = create_llm_session`, `record_cross_model_tool_call = record_cross_tool_call`.

### 2. `schema.py` / `store.py`

`schema.py` drops the CROSS-MEMORY-002 block and does:

```python
from .llm_ontology import (
    LLMProvider, MultiLLMSession, PromptVariant,
    ModelComparisonResult, Decision, CrossModelToolCall,
)
```

`__all__` still lists those names.

`store.py` drops the CRUD block, imports `get_llm_ontology_snapshot` (and re-exports the rest of the public names so existing `memory.store` imports keep working). `snapshot()` body unchanged except the callee now lives in `llm_ontology`.

### 3. Tests

New `memory/test_llm_ontology.py`. All disk tests use `tmp_path` as `base_dir`. Assert the produced filename is under `tmp_path`, not `Path.home()`.

### 4. Version

Release commit only: `VERSION` `3.11.3`, CHANGELOG `[3.11.3]`, ROADMAP drop the P8-13 Future bullet, milestone v3.11.3, badges.

---

## Test plan (G6)

| Test | Assert |
|------|--------|
| `test_provider_roundtrip_ignores_extra` | `from_dict` drops unknown keys; defaults fill missing optionals. Same for session + nested `PromptVariant`. |
| `test_create_session_and_query` | `create_llm_session` + `query_llm_sessions(task_id=, model=, base_dir=)` filters. |
| `test_snapshot_collections` | `get_llm_ontology_snapshot(base_dir=)` has the five keys after a provider create. |
| `test_corrupt_json_returns_empty` | Garbage file → empty collections, no raise. |
| `test_store_snapshot_includes_ontology` | `store.snapshot` has `llm_ontology` key (monkeypatch getter). |
| `test_schema_and_store_reexports` | `memory.schema.MultiLLMSession is memory.llm_ontology.MultiLLMSession`; `store.create_llm_session is llm_ontology.create_llm_session`. |
| `test_lock_file_during_write` | Patch replace; while held, `{base_dir}/llm_ontology.lock` exists; after context, absent. |
| `test_writes_stay_in_base_dir` | No file created under `Path.home() / ".grok" / "agentic-loop-memory"` for this test’s wid. |

Interpreter: `.venv/bin/python` or SSOT venv. `PYTHONPATH=. pytest memory/test_llm_ontology.py memory/ -q` for the touched tests; full `pytest memory/` before merge.

---

## File map

| Path | Action |
|------|--------|
| `memory/llm_ontology.py` | **Add** — types + CRUD + lock |
| `memory/test_llm_ontology.py` | **Add** — G6 |
| `memory/schema.py` | Remove inline MultiLLM types; re-export |
| `memory/store.py` | Remove CRUD block; re-export; snapshot still calls getter |
| `docs/superpowers/specs/2026-08-27-p8-13-multillm-extract-design.md` | This spec |
| `docs/superpowers/plans/2026-08-27-p8-13-multillm-extract.md` | Plan |
| `VERSION` / `CHANGELOG.md` / `ROADMAP.md` | 3.11.3 release |
| README / docs badges | Version shields only |

Do not edit `memory/supervisor.py`, `memory/dashboard/**`, `Agent-Init.*`, `architecture.md` body, messenger worktrees.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Import cycle schema → llm_ontology → schema | `llm_ontology` imports only `workspace` + `agent_lock` (stdlib + those). |
| Tests pollute `~/.grok/agentic-loop-memory` | G5 `base_dir=`; G6 path assertion. |
| Silent break of `from memory.schema import MultiLLMSession` | Re-export + identity test. |
| `agent_lock` not reentrant | CRUD never nests the same name. |

---

## ROADMAP / VERSION (release commit)

- `VERSION` → `3.11.3`
- CHANGELOG `[3.11.3]`: extract MultiLLM ontology to `memory/llm_ontology.py`; tests; `agent_lock`; P8-13 closed
- ROADMAP: drop the P8-13 Future bullet; milestone **v3.11.3**; badge; status date
- Patch, not 3.12.0: no new CLI, wizard/dashboard/supervisor unchanged.
