# Design: Agentix Supervisor 3.5 — End-to-end autonomy (multi-frontend)

**Status:** Draft for implementation (brainstorm approved 2026-07-29)  
**Repo:** `agentic_loop_template` (Agentix harness)  
**Baseline:** v3.4.1 (bounded state, progressive tools, memory/playbooks, handoff schema)  
**Target version:** 3.5.0  

---

## 1. Problem

The harness today is a **strong constitution** (roles, handoffs, memory, tools) but still depends on a human saying **«продолжай»** between role turns and cycles. That blocks unattended multi-cycle work and wastes human attention on process glue rather than product decisions.

Multi-frontend reality (Grok CLI, Cursor, Blackbox, others) means any autonomy solution must not hard-wire a single agent host.

## 2. Goals

| Priority | Goal |
|----------|------|
| P0 | **End-to-end autonomy** through role turns until **PR ready** without human nudges |
| P0 | **Multi-frontend**: same supervisor, pluggable adapters |
| P0 | **Human only for merge to `main`** |
| P1 | Reuse 3.4.1 SSOT: `LOOP_STATE`, validate_handoff, memory, progressive tools, playbooks |
| P1 | Measurable success: unattended cycles + CI mock path |

### Non-goals (3.5.0)

- Auto-merge to `main`
- Control Plane UI / eeagent product integration as primary runner
- Firecracker / heavy isolation
- Default multi-repo `gh` ritual every turn
- Parallel multi-stream orchestration (see `PARALLEL_PROTOCOL.md`; deferred to 3.5.1+)

## 3. Success criteria

| Level | Criterion |
|-------|-----------|
| **MVP (CI)** | ≥ **3** full mock-adapter cycles reach `PR_READY` without network or human input |
| **Dogfood** | ≥ **1** real adapter run (default: Grok when available) on this repo opens a PR; **0** human «продолжай» |
| **Human** | Only action required: **approve/merge PR to main** |
| **Safety** | Invalid handoff / adapter crash / policy risk → **BLOCKED** with reason, not infinite retry |

## 4. Architecture

Hybrid **supervisor CLI** + **frontend adapters** on top of existing harness files.

```
[INVEST queue / .agent/TODO]
        │
        ▼
┌───────────────────┐     ┌──────────────────────────┐
│  supervisor CLI   │────▶│ LOOP_STATE.json (bounded) │
│  role FSM + gates │     │ last_handoff.json         │
└─────────┬─────────┘     └──────────────────────────┘
          │ one role-turn
          ▼
   ┌──────────────┐
   │ Frontend     │  mock | grok | cursor | blackbox | (ext)
   │ Adapter      │  contract: prompt in → handoff out
   └──────┬───────┘
          ▼
   validate_handoff → metrics → next role | PR_READY
          │
          ▼
   open PR (gh) ── human merge to main
```

**Boundaries**

- Supervisor does **not** implement product code; it only orchestrates roles, gates, and PR creation.
- Adapters do **not** own FSM logic.
- Memory / playbooks / state / tools remain the single source of truth from 3.4.1.

## 5. Components

### 5.1 Supervisor entrypoint

- Preferred: `python -m memory.supervisor` (package-local; works when `PYTHONPATH` is repo root).
- Optional shim: `scripts/agentix-supervisor` → same module.

**Commands**

| Command | Behavior |
|---------|----------|
| `run` | Start or continue until terminal state |
| `status` | Print compact LOOP_STATE + last handoff summary |
| `resume` | Alias of `run` when state is mid-cycle |
| `stop` | Cooperative stop flag (finish current turn if possible) |

**Flags**

- `--adapter mock|grok|cursor|blackbox`
- `--max-cycles N` (hard stop)
- `--until pr_ready` (default terminal success)
- `--workdir PATH` (default: repo root)

### 5.2 Role FSM

```
Orchestrator → Coder → Tester
                 → Debugger   if metrics.tests_failed > 0
                 → Reviewer   otherwise
Reviewer → Orchestrator (status IN_PROGRESS, next INVEST)
        → PR_READY      (status DONE on assigned slice)
```

**Rules**

- Transition only if `validate_handoff` passes.
- `status=BLOCKED` → stop; persist reason; exit non-zero.
- Reviewer `DONE` → **do not** merge `main`; enter `PR_READY` and create PR.
- Debugger is **skipped** when tests are green (approved in design).

### 5.3 Adapter interface

Stable contract (Python protocol / callable):

```text
run_role_turn(
  role: str,
  prompt: str,
  handoff_in_path: Path | None,
  workdir: Path,
  timeout_s: int,
) -> Path  # path to handoff JSON written by adapter
```

| Adapter | 3.5.0 requirement |
|---------|-------------------|
| **mock** | Required. Deterministic handoffs for full cycle in CI |
| **grok** | Required if `grok` on PATH; else skip dogfood with clear message |
| **cursor** | Config-driven command template; may stub with “not configured” |
| **blackbox** | Config-driven; may stub with “not configured” |

### 5.4 Gates

| Gate | Action |
|------|--------|
| Handoff schema | `memory.validate_handoff` — hard fail path |
| Tests | If `tests_failed > 0` after Tester → Debugger |
| PR | `gh pr create` on feature branch; **never** merge main |
| Context budget | `context_budget` **warn** only in 3.5.0 (not hard fail) |
| Policy | Configurable risk tags / process_tags → BLOCKED |

### 5.5 Config (`.agent/project_config.json`)

```json
{
  "supervisor": {
    "adapter": "mock",
    "max_cycles": 5,
    "max_role_retries": 2,
    "role_timeout_s": 900,
    "pr": {
      "base": "main",
      "draft": false,
      "title_prefix": "agentix:"
    },
    "adapters": {
      "grok": { "command": "grok" },
      "cursor": { "command": null },
      "blackbox": { "command": null }
    },
    "block_process_tags": ["secrets_exposure", "destructive_prod"]
  }
}
```

Defaults live in `project_config.example.json`.

## 6. Data flow (one role-turn)

1. Load `LOOP_STATE.json` + `.agent/last_handoff.json`.
2. Build cold prompt:
   - role micro-prompt (`prompts/short_*.md`)
   - `memory state snapshot --window 3` (structured, not raw file dump)
   - `memory query` top-5 Common Failure Patterns
   - previous handoff `summary` + `context_delta` only
   - pointer to `tools/select.py` (do not inline full tools)
3. Adapter runs frontend → writes handoff JSON.
4. `validate_handoff`:
   - fail → retry ≤ `max_role_retries` → else BLOCKED
5. Persist: last_handoff, `state append-delta`, `metrics-log`.
6. FSM computes next role or terminal state.
7. On `PR_READY`: ensure feature branch, `gh pr create` (or `PR_READY_LOCAL` if gh fails).

## 7. Error handling

| Case | Behavior |
|------|----------|
| Invalid handoff JSON/schema | Retry → BLOCKED + reason |
| Adapter timeout/crash | Retry → BLOCKED + reason |
| `tests_failed > 0` | Next role = Debugger |
| `max_cycles` reached | `STOPPED_LIMIT`, exit 0 or 2 (documented) |
| `gh pr create` fails | `PR_READY_LOCAL` (branch + push if possible) |
| Policy / risk tags | Immediate BLOCKED |
| Missing adapter binary | Fail fast at start of `run` with install hint |

No silent infinite loops. Every stop writes machine-readable status into LOOP_STATE.

## 8. Prompt assembly (context discipline)

Supervisor **must** keep cold-start small:

- Prefer existing progressive tools + bounded state (3.4.1).
- Do not read `.agent/history/*` or multi-MB archives.
- Role prompts stay short; constitution stays in files referenced on demand.

## 9. Testing strategy

| Layer | What |
|-------|------|
| Unit | FSM transition table; retry/BLOCKED paths |
| Integration | mock adapter full path → `PR_READY` without network |
| Smoke | `python -m memory.supervisor run --adapter mock --max-cycles 1` |
| Optional dogfood | `--adapter grok` when binary present |

Tests must not require Blackbox/Cursor licenses.

## 10. File / package layout (proposed)

```text
memory/
  supervisor.py          # CLI + FSM + gates
  adapters/
    __init__.py
    base.py              # protocol
    mock.py
    grok.py
    cursor.py
    blackbox.py
  test_supervisor_fsm.py
  test_supervisor_mock_cycle.py
scripts/agentix-supervisor   # thin wrapper (optional)
docs/superpowers/specs/2026-07-29-agentix-supervisor-3.5-design.md  # this file
```

Update: `VERSION` → 3.5.0, `CHANGELOG.md`, `project_config.example.json`, short orchestrator prompt note that supervisor may drive turns.

## 11. Rollout plan (implementation slices)

1. **FSM + mock adapter + tests** — CI green, 3 mock cycles  
2. **Prompt assembly + state/metrics wiring** — real files only  
3. **PR_READY + gh create** — human merge remaining  
4. **Grok adapter** — dogfood one unattended cycle  
5. **Cursor/Blackbox stubs + docs** — multi-frontend story complete  

## 12. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Frontend output not pure JSON | Extract last JSON object; validate; retry with stricter instruction |
| Grok CLI flags change | Adapter isolates command; mock remains CI SSOT |
| Runaway cost/time | `max_cycles`, timeouts, BLOCKED on policy |
| Merge conflict with future parallel streams | Keep adapter/FSM free of path ownership; 3.5.1 adds stream field |

## 13. Key decisions (locked)

1. Hybrid supervisor + adapters (not protocol-only, not full control plane).  
2. Primary goal: unattended autonomy to **PR ready**.  
3. Multi-frontend via adapters.  
4. Human gate: **merge to main only**.  
5. Debugger only when tests failed.  
6. Context budget: warn in 3.5.0, not hard fail.  
7. Success: ≥3 mock cycles in CI; ≥1 real dogfood unattended PR.  

## 14. Open items for implementation plan (not design blockers)

- Exact Grok CLI invocation flags (discover from installed `grok --help` during implement).  
- Whether PR is draft by default (`supervisor.pr.draft`) — default **false** unless config says otherwise.  
- Exit codes table for CI (`0` success PR_READY, `1` BLOCKED, `2` STOPPED_LIMIT) — set in plan.

---

## Approval

Brainstorm sections 1–3 approved by user 2026-07-29.  
Next: user reviews this written spec → then `writing-plans` for implementation plan.
