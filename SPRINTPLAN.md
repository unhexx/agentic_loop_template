# SPRINTPLAN.md

**Sprint:** Unreleased closeout (docs and verify)
**Status:** COMPLETE
**Goal:** Operator docs match the tree. No version bump.

## Not next

Do not evaluate or merge the old P9 feature branch. P9 commits `c198787` and `c047efe` are ancestors of `929ff64`. The remote feature branch has 0 commits that `main` does not. `origin/main` is behind; do not push.

P9 is already on main. The Compose stack (SearXNG JSON, LDR via the host gateway, optional Ollama), `memory.stack` / `memory.search`, and the hermetic tests shipped with `c198787`. This sprint does not reopen that work.

## Exit

1. Loop 7 / P11 claims in `CHANGELOG.md` `[Unreleased]` are named against existing hermetic tests, including `memory/test_adapters_by_role.py`.
2. `docs/TOP10_IMPROVEMENTS.md` has a 3.13.0 / Unreleased section. P10 is Planned, not Done.
3. `.agent/PLAN.md`, `.agent/TODO.md`, and this file do not tell the operator to merge or evaluate the old P9 feature branch. `PROJECT_CONTEXT.md` identification, Current Status, and Reviewer Notes match the tree.
4. `.agent/LOOP_STATE.md` is the state CLI projection: `template_version` 3.13.0, status READY. `last_commit` lags the docs commit by one.
5. `docs/review/2026-10-25-unreleased-closeout.md` records pass or environment-blocked from the TCP and file checks.
6. `VERSION` stays 3.13.0. No git tag. No push.

## Standards skip (Key Decision 7)

This sprint runs none of `DEVELOPMENT_STANDARDS.md` §11, does not set `git_sync.verified`, does not create `SELF_IMPROVEMENT_LOG.md`, and does not run `experience_harvester` on sibling repos. See Key Decision 7. A later wake must not “correct” that skip.
