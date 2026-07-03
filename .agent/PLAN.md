# .agent/PLAN.md — Living Project Plan (Agentix Business Efficiency Initiative)

**Source of truth for iteration continuity.** 
Orchestrator MUST read this + .agent/TODO.md first thing after git sync. 
Advance ONLY tasks from the *last unfinished iteration*. Do not start new phases until current iteration items are DONE or explicitly closed with justification.

**Current Initiative:** Implement prioritized recommendations for business efficiency (see TASK_SPECIFICATION.md).
**Template Version:** 3.2+
**Last Plan Update:** 2026-07-03 (bootstrap)

## Phases / Streams (High Level)

- **Phase 0: Foundation & Bootstrap** (current iteration)
- **P1: Metrics / Observability / ROI Layer** (top priority — enables proof & faster iteration)
- **P2: Cross-Platform + Multi-Frontend**
- **P3: Productization, Docs, GTM & Ecosystem**
- **P4: Meta-Optimizer Completion & Eval Harness**
- **P5: Enterprise Governance + Key Integrations**
- **P6: Onboarding & DX Overhaul**
- **P7: Efficiency, Cleanup, Sustain & Dogfooding**

## Current Iteration (Iteration 1 — Foundation + P1 Start + P4 Seed)

**Goal of this iteration:** Establish the loop artifacts for the initiative, implement core scaffolding for performance ledger/metrics (P1), initialize full meta_harvester usage + first eval seeds (P4), perform hygiene cleanup, update docs. Produce measurable first data points.

**Status:** IN_PROGRESS (P1 ledger slice complete: code, tests, debug fixes, doc integration, push done. See cycle 1 handoffs)

### INVEST Tasks for Current Iteration (priority order)

1. **[P0-FOUND] Create and validate full set of planning artifacts for the initiative**  
   - TASK_SPECIFICATION.md (done in bootstrap), .agent/PLAN.md, .agent/TODO.md, PROJECT_CONTEXT.md, SPRINTPLAN.md, update .agent/LOOP_STATE.md.  
   - Add initial performance ledger skeleton (.agent/PERFORMANCE_LEDGER.md or json).  
   - Criteria: All files exist, consistent with TASK_SPECIFICATION, Orchestrator can read them cleanly. Test: cat + grep for key sections.  
   - Value: Enables the entire loop to operate on this body of work without context loss.

2. **[P1-METRICS-01] Design & implement Performance Ledger data model + collection hooks**  
   - Define schema (cycle stats: tokens, elapsed_min, tool_calls, confidence, violations, tests, meta_proposals, success_patterns).  
   - Add Python helper (extend memory/ or new ledger.py) + CLI entry similar to meta_harvester.  
   - Wire basic collection in meta_harvester (update_performance_ledger already partially stubbed) and handoff examples.  
   - Write to .agent/PERFORMANCE_LEDGER.json + human .md view.  
   - INVEST: Independent, small slice (core model + 1 write path). Testable via demo script.

3. **[P1-METRICS-02] Integrate ledger into Reviewer + Orchestrator duties**  
   - Update AGENT_ROLES.md (Reviewer section) and short_reviewer_prompt.md to call ledger update on DONE.  
   - Orchestrator reads top metrics + trends at PLAN start (like memory snapshot).  
   - Add to HANDOFF_SCHEMA.md optional "performance" block.  
   - Update PROJECT_CONTEXT_TEMPLATE.md with metrics section.  
   - Small, valuable for visibility.

4. **[P4-META-01] Complete & verify meta_harvester end-to-end on this initiative**  
   - Ensure harvest works on a high-quality cycle (this bootstrap as seed).  
   - Run analyze + propose + at least one safe apply (e.g. add a compression example or rule).  
   - Wire update_performance_ledger call.  
   - Create demo trajectory for P1 work.  
   - Criteria: At least 1 proposal generated/applied, recorded in .agent/META_PROPOSALS.md and memory. Run `python -m ... meta_harvester` commands successfully.  
   (Progress: harvest fixed and succeeded T-001-03f8; patterns seeded for ledger/metrics; proposal generation needs heuristic update)

5. **[P0-HYGIENE] Legacy cleanup + initial docs/roadmap hygiene**  
   - Grep for remaining "eeagent|eegent" references across non-history files and remove/generalize.  
   - Flesh out CHANGELOG.md with recent + this initiative entry.  
   - Add initial public ROADMAP.md (derived from phases).  
   - Commit discipline enforced.

6. **[P0-DOCS-01] Update core docs to reflect initiative and value props**  
   - README.md: add section on "Business Efficiency Improvements in progress" + link to TASK_SPECIFICATION.  
   - AGENTIC_LOOP_README.md and META_OPTIMIZER_SPEC.md minor sync if needed.  
   - Ensure all point to new TASK_SPECIFICATION as example of using the loop for meta-work.

7. **[P0-SYNC-01] Full git self-cycle + multi-clone verification for bootstrap changes**  
   - All changes committed with natural Russian human dev messages.  
   - Sync verified across worktree + simulated main clone paths per §11.  
   - Update LOOP_STATE.md.  
   - Record in handoff.

**Unfinished from previous (none — this is the initial iteration for the business upgrade).** Do not create new P2/P3 tasks until these are closed.

## Next Iteration Planning Signals (for future)

After this iteration Reviewer will run Daily Decomposition Ritual (if cadence) + meta harvest, then propose refined tasks for P1 completion, P2 start etc.

**Orchestrator Instruction:** Pick the next pending item from the list above (start with 1 if not marked, then highest value). Decompose if needed but stay in current iteration. Update this file + .agent/TODO.md + SPRINTPLAN.md at end of your planning.

**Evidence Markers:** Use [DONE] when Reviewer approves. Link commits.
