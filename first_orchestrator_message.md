# Agentic Development Loop — First Message (Business Efficiency Initiative)

**Project:** Agentix (the template itself)
**Initiative:** Implement all high-impact business efficiency recommendations (see TASK_SPECIFICATION.md)

**Current Model Recommendation:** MiniMax 2.5 (or highest quality) via your agent frontend. Temperature 0.0 for Orchestrator.

## MANDATORY FIRST ACTIONS (follow exactly)
1. Bootstrap env (adapt for platform): run available setup (setup_env_template.ps1 or equivalent python -m pip in .venv).
2. **Git self-cycle + verification per DEVELOPMENT_STANDARDS.md §11** (already partially executed in bootstrap; ensure full on your clones).
3. Read in order:
   - DEVELOPMENT_STANDARDS.md (constitution)
   - TASK_SPECIFICATION.md (the spec for this body of work)
   - .agent/PLAN.md + .agent/TODO.md (current unfinished iteration — advance ONLY these)
   - PROJECT_CONTEXT.md + SPRINTPLAN.md
   - AGENT_ROLES.md + HANDOFF_SCHEMA.md + PROMPT_COMPRESSION_GUIDE.md
4. Query memory (when env ready): python -m agentic_loop_template.memory ...
5. Exercise meta + questions collectors as appropriate.
6. Start as ORCHESTRATOR (PLAN → ACT limited → REFLECT). Output **exactly one JSON handoff** at end.

## Current Project State (compact)

We are dogfooding the Agentix loop to improve *itself* for maximum business impact:
- Deep analysis identified 7 recommendation areas (P1 Metrics/ROI is #1, P4 Meta completion #2).
- Bootstrap artifacts created and committed (fc53aa1).
- First iteration: foundation + start P1 ledger + seed P4 meta harvest + hygiene.
- All future work must use strict process, Russian human commits, sync verification.

Full details in TASK_SPECIFICATION.md (business objectives, constraints, phases, success criteria) and .agent/PLAN.md (INVEST list).

## Recent Context Delta
- New files: TASK_SPECIFICATION.md, ROADMAP.md, PROJECT_CONTEXT.md, SPRINTPLAN.md, .agent/PLAN.md, .agent/TODO.md, .agent/PERFORMANCE_LEDGER.md, .agent/project_config.json (plus updates).
- Memory/meta/rituals already configured and enabled.
- Git: committed bootstrap changes with proper message.

## Next Concrete Work (from .agent/TODO.md / PLAN current iteration)
Pick highest pending:
- Complete any remaining bootstrap validation.
- Implement performance_ledger core (P1-METRICS-01).
- Integrate and run first meta flow on this work.
- Hygiene cleanups.

Aggressively compress. Use deltas. Consult memory. Never skip unfinished iteration items.

**Begin now as ORCHESTRATOR.**
