# .agent/TODO.md — Detailed Task Backlog (Current Iteration Focus)

**Rule:** Orchestrator reads this immediately after .agent/PLAN.md. Select only from the *current unfinished iteration* list. Mark progress here too. Use INVEST. Binary completion criteria.

## Current Iteration 1 Tasks (Foundation + P1 Metrics Seed + P4 Meta Seed + Hygiene)

- [x] P0-FOUND-01: Finalize all bootstrap artifacts (TASK_SPECIFICATION.md, .agent/PLAN.md, .agent/TODO.md, PROJECT_CONTEXT.md, SPRINTPLAN.md, .agent/project_config.json, basic .agent/PERFORMANCE_LEDGER.md + .json). Verify consistency. (Owner: Orchestrator bootstrap) — DONE in init cycle 0/1. Ledger producing data.
- [ ] P0-FOUND-02: Create initial workspace memory entries for "Agentix Improvements" category (patterns from analysis: need for metrics, cross-platform gaps).
- [ ] P0-FOUND-02: Create initial workspace memory entries for "Agentix Improvements" category (patterns from analysis: need for metrics, cross-platform gaps).
- [x] P1-METRICS-01: Implement ledger schema + basic collector (memory/performance_ledger.py). CLI. (DONE by Coder) + tests (DONE by Tester via test_performance_ledger.py, all green).
- [ ] P1-METRICS-02: Add first data point collection (simulate or capture from current bootstrap "cycle 0"). Wire call in meta_harvester.update_performance_ledger.
- [ ] P1-METRICS-03: Update HANDOFF_SCHEMA.md with optional "performance" object example. Update PROJECT_CONTEXT_TEMPLATE.md "Current Performance" section.
- [ ] P1-METRICS-04: Modify short_reviewer_prompt.md and AGENT_ROLES.md (Reviewer duties) to mandate ledger update on high-quality DONE. Add to SYSTEM_PROMPT.md if needed.
- [ ] P4-META-01: Execute full meta flow on bootstrap (harvest using a mock or real handoff from init, analyze, propose at least 1 safe improvement e.g. "add metrics compression example", apply-safe). Record trajectory.
- [ ] P4-META-02: Enhance demo_meta.py or add test to demonstrate P1 ledger + meta together.
- [ ] P0-HYGIENE-01: Full grep -r (excluding .git) for eeagent/eegent/legacy strings in *.md *.py *.ps1. Create patch list and clean in small commits.
- [ ] P0-HYGIENE-02: Expand CHANGELOG.md with v3.2+ entries + "Business Efficiency Initiative launched (2026-07)" section.
- [ ] P0-HYGIENE-03: Create ROADMAP.md in root (public version of phases from TASK_SPECIFICATION + current status).
- [ ] P0-DOCS-01: Edit README.md — insert "Current Focus: Business Efficiency Recommendations Implementation" with links to TASK_SPECIFICATION.md and .agent/PLAN.md. Add value metrics section stub.
- [ ] P0-SYNC-01: Perform complete §11 self-cycle after all bootstrap files written (natural Russian commit, push if applicable, verify in worktree + note main clone sim, update LOOP_STATE).

**Completion gate for iteration:** All above checked by Reviewer + at least 1 meta proposal applied or documented win + first performance numbers in reports + git verified. Then mark iteration complete and plan next (P1 continuation or P2).

## Future Iterations (DO NOT START YET)

P2 items, full cross platform scripts, adapters etc. will be added by Reviewer after current iteration closes, using meta harvest + ritual decomposition.

## Notes from Analysis (2026-07-03)
See full recommendations in previous research output / TASK_SPECIFICATION.md. Prioritize P1 and P4 because they accelerate delivery of all others.
