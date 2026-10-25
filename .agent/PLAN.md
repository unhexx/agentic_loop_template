# .agent/PLAN.md — Living Project Plan

**Initiative:** Unreleased closeout (docs and verify)  
**Template Version:** 3.13.0 (VERSION unchanged)  
**Last Update:** 2026-10-25  
**Loop:** 8 — finish the in-tree line. No tag. No push.

## Delivered (already on main at 929ff64)

| Idea | Artifact |
|------|----------|
| 1 Hygiene | L6-02 gitignore, SYSTEM_PROMPT 3.13.0 |
| 2 P11 MCP | `memory/local_mcp/`, integrations, tests |
| 3 Doctor | `memory/doctor.py`, `agentix-doctor` |
| 4 Routing | `adapters_by_role` (no Blackbox) |
| 5 Stack/meta | `stack_check_on_start`, `auto_harvest_on_done` default off |
| 6 Live verify | `docs/live-verify.md` |
| 7 Parallel scaffold | `scripts/parallel_scaffold.sh` |

P9 commits `c198787` and `c047efe` are ancestors of `929ff64`. Do not evaluate or merge `feature/p9-operator-stack`. `origin/main` is behind; do not push.

## This sprint

**Status:** COMPLETE.

Docs-and-verify closeout. Design: `/tmp/grok-root/grok-design-doc-06651148.md` (review consensus, 0 open issues, 2026-10-25).

1. [x] PR 1 — one hermetic test `test_meta_harvest_on_done_is_opt_in` in `memory/test_adapters_by_role.py`. Stage only that file. This commit ends its wake. The scheduler stays off until that diff is reviewed.
2. [x] First scheduler wake runs the named proof slice once. Red: debug only. No doctor. No PR 2.
3. [x] Green proof, then doctor (TCP and files only). Pass or environment-blocked, then PR 2 docs. Product-bug blocks PR 2.

## Exit

- Proof slice exit 0 recorded in `docs/review/2026-10-25-unreleased-closeout.md`
- TOP10 has a 3.13.0 / Unreleased section; P10 is Planned, not Done
- SPRINTPLAN, this file, TODO, and PROJECT_CONTEXT do not tell the operator to merge or evaluate `feature/p9-operator-stack`
- LOOP_STATE via `python -m memory state set` then one `append-delta`; `template_version` 3.13.0; status READY; `last_commit` lags the docs commit by one
- VERSION stays 3.13.0. `git tag --points-at HEAD` empty. No fetch. No push.

## Standards override (Key Decision 7)

This sprint runs none of `DEVELOPMENT_STANDARDS.md` §11. Do not set `git_sync.verified`. Do not create `SELF_IMPROVEMENT_LOG.md`. Do not run `experience_harvester` on sibling repos. A later wake must not “correct” that skip.

## Parked

- P10 `agy` until a region-eligible `GEMINI_API_KEY`. No pxpipe bypass.
- VERSION bump / tag after a human review. Not this sprint.
- Hub SaaS / messenger / mobile
