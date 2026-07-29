# Top 10 harness improvements (ranked)

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
  → meta_harvester on DONE
```

## Metrics

See `docs/metrics/baseline.json` and `docs/metrics/after.json`.
