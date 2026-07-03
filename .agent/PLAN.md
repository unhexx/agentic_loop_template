# .agent/PLAN.md — Living Project Plan (Agentix Business Efficiency Initiative)

**Source of truth for iteration continuity.**
Orchestrator MUST read this + .agent/TODO.md first thing after git sync.
Advance ONLY tasks from the *last unfinished iteration*.

**Current Initiative:** Implement prioritized recommendations for business efficiency (see TASK_SPECIFICATION.md).
**Template Version:** 3.3.0 (target)
**Last Plan Update:** 2026-07-03 (Iteration 1 complete; P2/P3 in progress)

## Phases / Streams (High Level)

- **Phase 0: Foundation & Bootstrap** — COMPLETE
- **P1: Metrics / Observability / ROI Layer** — COMPLETE
- **P2: Cross-Platform + Multi-Frontend** — Iteration 2 (IN PROGRESS)
- **P3: Productization, Docs, GTM & Ecosystem** — Iteration 3 (pending P2 gate)
- **P4: Meta-Optimizer Completion & Eval Harness** — COMPLETE
- **P5–P7:** Future

## Iteration 1 — Foundation + P1 + P4 Seed

**Status:** COMPLETE (cycle 53). Gate met.

## Iteration 2 — P2 Cross-Platform Close

**Goal:** Platform-adaptive prompts/roles and cross-platform + multi-frontend quickstart docs.

**Status:** COMPLETE

### INVEST Tasks

1. **[P2-CROSS-02]** Platform-adaptive bootstrap in all `prompts/short_*.md` + `AGENT_ROLES.md`; reference `cross-platform` playbook scope.
   - Criteria: no prompt mandates PowerShell-only without *nix alternative.

2. **[P2-DOCS]** Cross-platform + multi-frontend quickstart in `README.md` and `AGENTIC_LOOP_README.md`.
   - Criteria: Linux worktree can follow README without PowerShell; Cursor/Claude paths documented.

**Iteration 2 gate:** Both tasks DONE; Reviewer approves; ledger + §11 sync.

## Iteration 3 — P3 Productization

**Goal:** Professional docs site, consumer starter, proof-driven README, full Agentix Hub, Pro tier hooks, v3.3.0 release.

**Status:** COMPLETE (P3 gate passed)

### INVEST Tasks

1. **[P3-DOCS-01]** Create `docs/` structure (getting-started, cross-platform, multi-frontend, architecture, metrics-roi, hub/).
2. **[P3-README-01]** README: Current Focus, measured results from ledger, docs nav.
3. **[P3-EXAMPLE-01]** `examples/consumer-starter/` adoption skeleton.
4. **[P3-HUB-01]** `playbooks list/export/discover` CLI, `HUB_INDEX.json`, `docs/hub/api-schema.json`, tests.
5. **[P3-PRO-01]** `docs/pro-tier.md` + `tier`/`feature_flags` in `project_config.json`.
6. **[P3-RELEASE-01]** CHANGELOG v3.3.0, ROADMAP update, version bump.
7. **[P3-VERIFY-01]** Tests green, doc links valid, meta harvest on DONE.

**P3 gate:** README proof points; docs/ + examples/ exist; Hub schema + CLI; Pro hooks; CHANGELOG v3.3.0.

**Orchestrator Instruction:** Advance Iteration 2 tasks first. Do not start P3 until Iteration 2 gate passes.