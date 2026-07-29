# Parallel Workstream Protocol (Grok-native)

## When to parallelize

Use parallel streams when **two or more INVEST items touch disjoint paths** and Reviewer can merge independently.

Do **not** parallelize when both streams must edit the same hot files (`DEVELOPMENT_STANDARDS.md`, shared schemas, package `__init__`) without an integration owner.

## Roles

| Role | Duty |
|------|------|
| Orchestrator | Assign streams, `owned_paths`, worktrees; no product code in shared hot files |
| Stream Coder/Tester | Work only under `owned_paths`; handoff with `stream` + `worktree` |
| Integration Reviewer | Merge order, conflict resolution, state CLI updates, DONE gate |

## Handoff extensions

```json
{
  "stream": "meta",
  "worktree": "../agentic-loop-worktrees/20260729-meta",
  "owned_paths": ["memory/", "tools/"],
  "merge_gate": "after-tests-green"
}
```

## Scripts

```bash
./scripts/agentic_loop.sh --workstreams harness,docs
# WT_BASE default: ../agentic-loop-worktrees
```

## State rules

- Update `.agent` **only** via `python -m memory state …` (atomic JSON).
- Never append free-form multi-KB blocks to `LOOP_STATE.md`.
- Each stream may `append-delta`; compact at Reviewer.

## Merge gate checklist

1. Tests green in stream worktree  
2. `validate_handoff` passes  
3. No edits outside `owned_paths` (spot-check `git diff --name-only`)  
4. Integration Reviewer merges; run `state compact` + `metrics-log`  
5. `SYNC_DONE` from `scripts/sync-worktree.sh --verify-only`  
