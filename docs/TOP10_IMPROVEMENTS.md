# Top 10 harness improvements (ranked)

## 3.6.0 — from `_PROJECT/*` harvest (2026-08-20)

| # | Improvement | Priority | Status in 3.6.0 |
|---|-------------|----------|-----------------|
| 1 | Harvest AGENTS.md / playbooks / living plans, not only LESSONS.md | P0 | **Done** (`experience_harvester.py` scan) |
| 2 | Parent-folder `audit` + `cycle` self-improve CLI | P0 | **Done** |
| 3 | Two-tier adoption (lite AGENTS.md vs full O→C→T→D→R) | P0 | **Done** (consumer-starter) |
| 4 | Linux/Grok-first SYSTEM_PROMPT (PowerShell not mandatory) | P0 | **Done** |
| 5 | Detect stale/foreign LOOP_STATE + Windows-only Init + broken playbook links | P1 | **Done** (audit) |
| 6 | Consumer Agent-Init symlinks SSOT instead of copying the tree | P1 | **Done** (`Agent-Init.consumer.sh`) |
| 7 | Project-specific DoD / MUST NOT playbook example | P1 | **Done** (`AGENTS.md.example`) |
| 8 | Default wizard frontend Grok on Linux hosts | P1 | **Done** (`Agent-Init.sh`) |
| 9 | SSOT clone writable by the agent user (root-owned `_PROJECT` copy blocked upgrades) | P2 | **Documented** (work in `~/work/…`) |
| 10 | Classifier Linux Init + LOOP_STATE refresh | P2 | **Documented** (consumer drift; not patched here) |

## 3.3.0–3.4.1 — original ranking

| # | Improvement | Priority | Context | Errors | Speed | Quality | Status in 3.3.0 |
|---|-------------|----------|---------|--------|-------|---------|-----------------|
| 1 | Bounded `.agent` state + compact CLI | P0 | ★★★★★ | ★★★★ | ★★★ | ★★★ | **Done** (`memory/state.py`) |
| 2 | Progressive TOOLS + OS matrix | P0 | ★★★★★ | ★★★★ | ★★★★ | ★★★ | **Done** (`tools/`) |
| 3 | Reunified memory package | P0 | ★★★ | ★★★★★ | ★★★ | ★★★★ | **Done** (store/schema/workspace + meta) |
| 4 | Linux-first Agent-Init | P0 | ★★★ | ★★★★★ | ★★★★ | ★★★ | **Done** (`Agent-Init.sh`) |
| 5 | Parallel workstream protocol | P1 | ★★ | ★★★ | ★★★★★ | ★★★★ | **Done** (`PARALLEL_PROTOCOL.md`, `scripts/agentic_loop.sh`) |
| 6 | Machine-validated handoffs | P1 | ★★ | ★★★★★ | ★★ | ★★★★★ | **Done** (`schemas/`, `validate_handoff.py`) |
| 7 | Cheap git preflight | P1 | ★★★ | ★★★ | ★★★★★ | ★★ | **Done** (`scripts/preflight_git.sh`, sync bash) |
| 8 | Context budget enforcer | P1 | ★★★★★ | ★★★ | ★★★ | ★★★ | **Done** (`context_budget.py`) |
| 9 | Cross-project experience harvest | P1 | ★★★ | ★★★★★ | ★★ | ★★★★ | **Done** (`experience_harvester.py`) |
| 10 | Packaging / VERSION / consumer sync | P2 | ★★ | ★★★ | ★★ | ★★★ | **Done** (`VERSION`, `sync_template_from_ssot.sh`) |

## How agents should use the stack (after 3.3.0)

```text
Agent-Init.sh
  → memory state snapshot
  → memory query (failures)
  → tools/select.py --intent …
  → work (≤3 tools / ACT)
  → validate_handoff
  → state compact + metrics-log
  → experience_harvester cycle (parent-folder sessions)
  → meta_harvester on DONE
```

## Metrics

See `docs/metrics/baseline.json` and `docs/metrics/after.json`.
