# .agent/TODO.md — Task Backlog

**P9:** COMPLETE on `main` (v3.13.0). Do not merge `feature/p9-operator-stack`.  
**P11:** IN TREE (Unreleased). VERSION stays 3.13.0.  
**Sprint:** Unreleased closeout — tasks written 2026-10-25 before execution.

## Standards override (Key Decision 7)

This sprint runs none of `DEVELOPMENT_STANDARDS.md` §11, does not set `git_sync.verified`, does not create `SELF_IMPROVEMENT_LOG.md`, and does not run `experience_harvester` on sibling repos. A later wake must not “correct” that skip.

## PR 1 — before the scheduler

- [x] Add `test_meta_harvest_on_done_is_opt_in` in `memory/test_adapters_by_role.py` (`json` and `_maybe_meta_harvest_on_done` imported). Hermetic: harvest stays off unless the JSON boolean is `true`; `enabled: false` skips the call.
- [x] `.venv/bin/python -m pytest -q memory/test_adapters_by_role.py` is green. If red, fix the test. Do not flip the default in `memory/meta/store.py`. Touch `memory/supervisor.py` only if the gate disagrees with the snippet.
- [x] Commit stages only `memory/test_adapters_by_role.py` (plus `memory/supervisor.py` only in that gate case). This file and PLAN stay unstaged until PR 2. Review `git show --stat HEAD` before the scheduler starts.

## PR 2 — after a green proof slice

- [x] TOP10 3.13.0 / Unreleased section for in-tree P8–P11. P10 row says Planned, not Done.
- [x] SPRINTPLAN, PLAN, TODO, and PROJECT_CONTEXT (identification, Current Status, Reviewer Notes) no longer say to evaluate or merge `feature/p9-operator-stack`, and PROJECT_CONTEXT no longer says `evaluation before merge` or `DONE on feature branch`.
- [x] ROADMAP date 2026-10-25. Header Next is the local-closeout sentence (`local closeout only`). No `release hygiene`. No 3.14.0 milestone.
- [x] Brainstorm status banner contains `Phase E.3 deferred`.
- [x] CHANGELOG Unreleased Changed bullet contains `VERSION unchanged`. No 3.14.0 section.
- [x] LOOP_STATE refreshed only by `python -m memory state set` and one `append-delta`. Projection shows `template_version` 3.13.0 and `**status**: READY`. `last_commit` is the parent hash. No third commit to refresh it.
- [x] `docs/review/2026-10-25-unreleased-closeout.md` records pass or environment-blocked from the TCP and file commands and contains `proof_slice_exit: 0` only after the named proof slice exits 0. A missing Gemini key is a boolean, not a failure. No HTTP 401 row.
- [x] Exit: VERSION is 3.13.0, `git tag --points-at HEAD` is empty, no fetch and no push. `git status -sb` shows `main...origin/main` with `впереди` 21 or more. That is not a failure.

## Parked

- P10 `agy` — geo / Gemini key; no pxpipe bypass; no Blackbox
- VERSION bump / tag Unreleased after human review
- Hub SaaS / messenger / mobile (ROADMAP Future)
