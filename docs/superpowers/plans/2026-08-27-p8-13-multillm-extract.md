# P8-13 MultiLLM extract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship P8-13 as Agentix **3.11.3**: extract MultiLLM dataclasses and ontology CRUD into `memory/llm_ontology.py`; re-export from `schema`/`store`; `agent_lock` + `base_dir=`; tests in `memory/test_llm_ontology.py`. No new CLI, no supervisor/dashboard wiring, no P8-12 split.

**Architecture:** Types and JSON ontology leave markdown memory. On-disk `{wid}.llm_ontology.json` stays under `memory_paths()["dir"]` unless tests pass `base_dir=`. Writers take `agent_lock(dir, name="llm_ontology")`. `store.snapshot()` still embeds `llm_ontology`.

**Tech Stack:** Python 3.10+, stdlib only. Existing `memory.agent_lock.agent_lock`. No new extra.

**Spec:** [`../specs/2026-08-27-p8-13-multillm-extract-design.md`](../specs/2026-08-27-p8-13-multillm-extract-design.md)

**Out of scope:** supervisor/adapters/dashboard wiring, P8-12, Hub/MCP/messenger/embeddings, moving the json into `.agent/`, locking markdown `store.py` RMW.

**House rules:** comments and commit messages in natural Russian (`DEVELOPMENT_STANDARDS.md` §1). Public names English. Do not mention AI/agents in commits. Do not commit live `.agent/`. Do not edit `memory/supervisor.py`, `memory/dashboard/**`, `Agent-Init.*`.

---

## File map

| Path | Action |
|------|--------|
| `memory/test_llm_ontology.py` | Create — G6 |
| `memory/llm_ontology.py` | Create — dataclasses + CRUD |
| `memory/schema.py` | Drop inline MultiLLM block; re-export |
| `memory/store.py` | Drop CRUD block; re-export; snapshot keeps getter |
| `VERSION` | `3.11.3` (last commit of this plan) |
| `CHANGELOG.md` | `[3.11.3]` section |
| `ROADMAP.md` | Drop P8-13 Future bullet; milestone v3.11.3; badge |
| `README.md`, `README.ru.md`, `docs/README.md`, `docs/ru/README.md` | Version badges only |

**Interpreter:** prefer `.venv/bin/python`. Worktrees may use SSOT `/home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python`. Prefix tests with `PYTHONPATH=.`.

---

### Task 1: Failing tests

**Files:**
- Create: `memory/test_llm_ontology.py`

- [ ] **Step 1: Write G6 tests** (must fail until the module exists)

See `memory/test_llm_ontology.py` in the repo after this task.

- [ ] **Step 2: Run `PYTHONPATH=. pytest memory/test_llm_ontology.py -q` — expect import/collection fail**

---

### Task 2: Extract module + re-exports

**Files:**
- Create: `memory/llm_ontology.py`
- Modify: `memory/schema.py`
- Modify: `memory/store.py`

- [ ] **Step 3: Move dataclasses and CRUD; `agent_lock`; `base_dir=`**
- [ ] **Step 4: Re-export from schema and store**
- [ ] **Step 5: `PYTHONPATH=. pytest memory/test_llm_ontology.py -q` green**

---

### Task 3: Release 3.11.3

- [ ] **Step 6:** `VERSION` → `3.11.3`; CHANGELOG `[3.11.3]`; ROADMAP drop P8-13; badges
- [ ] **Step 7:** `PYTHONPATH=. pytest memory/ -q` green
- [ ] **Step 8:** Commit (Russian message). Do not add `.agent/`. Push origin (env without proxy) and github.

---

## Self-review

- No supervisor/dashboard edits.
- Tests use `base_dir=` only.
- Re-export identity holds.
- VERSION 3.11.3, P8-13 Future bullet gone.
