# TASK_SPECIFICATION.md — Agentix Business Efficiency Initiative

**Project:** Agentix (agentic_loop_template)
**Version Target:** 3.3+ (business hardened)
**Primary Goal:** Transform the Agentix template into the highest-ROI, most adoptable, self-improving agentic development harness on the market by implementing the prioritized business efficiency recommendations.

**Source of Truth for this initiative:** This file + .agent/PLAN.md + SPRINTPLAN.md + PROJECT_CONTEXT.md (updated each cycle).

## Business Objectives (Measurable)

- **Velocity & Cost:** Reduce average cycles-to-DONE by 30-50%, token usage per complex task by 25%+, human oversight time by 60%+ through better observability, meta-optimization, compression, and DX.
- **Adoption & Revenue:** Enable productization. Achieve measurable "10x" proof. Support 3+ platforms/frontends. Create sellable assets (Pro tier, skills marketplace, case studies).
- **Quality & Trust:** Enterprise-grade governance, audit, integrations. Self-improving harness that compounds value faster than competitors.
- **Maintainability:** Full cross-platform parity, lower support burden via better onboarding and metrics. Clean technical debt (legacy refs, incomplete meta).

## Scope & Constraints

- **In scope:** All high-impact recommendations from the deep analysis (2026-07-03):
  1. Metrics / Observability / ROI Dashboard & Ledger
  2. Cross-platform (Linux/Mac) + Multi-LLM/Frontend adapters
  3. Productization, GTM, docs site readiness, examples, marketplace foundation
  4. Complete & harden Meta-Optimizer + eval harness + performance trends
  5. Enterprise governance (policies, approvals, audit, key MCP integrations)
  6. Onboarding & DX overhaul (init wizard, templates, extension stubs)
  7. Supporting: Cost efficiency (compression, tiering, resume), cleanup of legacy, public roadmap, dogfooding on this repo, case studies.
- **Out of scope (for initial sprints):** Full hosted SaaS execution, mobile, non-MCP major rewrites, unrelated features.
- **Tech/Process Constraints (strict):**
  - Follow DEVELOPMENT_STANDARDS.md exactly (natural Russian human dev voice for commits and code comments; no AI mentions; UTF-8; .venv where applicable; INVEST tasks; §11 multi-repo sync rituals; PLAN→ACT→REFLECT).
  - All changes backward-compatible or explicitly versioned.
  - Use existing memory/meta/questions_collector for self-improvement.
  - Primary focus on template itself; consumer projects benefit automatically.
  - Evidence-based: tests, demos, metrics where possible.
  - On this Linux worktree: implement cross-platform paths; PowerShell remains primary but scripts must be adaptable.
- **Quality bar:** Production-grade. Every feature must have tests or verifiable demo. Reviewer enforces process + spec.

## High-Level Phases (Streams)

Use phases in .agent/PLAN.md. Current iteration focuses on foundation + P1 (Metrics) + critical parts of P4 (Meta) because they unlock everything else.

**Phase 0 / Foundation (this cycle):** Create living plans, bootstrap loop state for this initiative, initial metrics scaffolding, cleanup hygiene.

**P1 — Metrics, Observability & ROI Proof Layer** (Highest business impact)
- Performance ledger (cycles, tokens, time, success %, violations, meta impact).
- Auto reports + trends in .agent/ and PROJECT_CONTEXT.
- Dashboard (md + simple visual or MCP skill).
- Integration with handoff, Reviewer, meta_harvester, rituals.

**P2 — Cross-Platform & Multi-Frontend**
- Linux/Mac bootstrap scripts (bash shims + .sh equivalents or unified).
- Adapters for Claude Code, Cursor, generic LLM frontends.
- Path handling, shell hygiene portable.
- Documentation and examples.

**P3 — Productization, Docs, GTM, Ecosystem**
- Professional README updates, dedicated docs/, sample consumer repo template.
- Public roadmap.md, CHANGELOG as real releases.
- Foundation for "Agentix Hub" / skills marketplace (index + discovery).
- Pro tier hooks (feature flags or separate docs).

**P4 — Complete Meta Self-Improvement Engine**
- Finish meta_harvester (full harvest/analyze/propose/apply + ledger update).
- Eval harness for trajectories (replay, scoring).
- A/B or before/after demonstration of improvements.
- Exportable playbooks + injection into memory.

**P5 — Enterprise Governance & Integrations**
- Enhanced PolicyEngine examples + human approval flows (non-blocking via pool).
- Audit trail improvements, signed artifacts where sensible.
- Key MCP integrations: GitHub Actions loop trigger, Linear/Jira, Slack notifications.
- Stronger isolation stories and compliance notes.

**P6 — Onboarding & DX**
- Interactive / improved Agent-Init (cross-platform).
- Stack templates, one-command demos.
- VS Code / Cursor extension stubs or launch instructions.
- Video/quickstart ready artifacts.

**P7 — Efficiency, Reliability, Cleanup, Sustain**
- Advanced compression + selective memory.
- Partial resume / better error recovery.
- Full legacy cleanup (eeagent refs), make CHANGELOG complete.
- Dogfood: run full loop cycles on this repo's own improvements; publish sanitized trajectory summaries.
- Public case study skeleton.

## Success Criteria (per phase, for Reviewer)

- P1: Concrete numbers visible in reports after 2+ cycles. "We reduced X by Y%".
- All: INVEST tasks marked DONE only when tests pass + docs updated + sync verified.
- Meta: At least 1-2 safe auto-applied or proposed improvements per qualifying cycle.
- Overall: Measurable improvement in self-reported cycle efficiency within 5-7 cycles.
- Adoption proxies: Updated README with proof points; new example assets; clear value prop.

## Known Risks & Mitigations (to be expanded in PLAN)

- Platform differences (Linux here vs primary Win target) → Use adapters + dual scripts.
- LLM variability → Strong schemas, compression, tests independent of model.
- Over-automation of meta → Strict safe_to_auto + human review gate + revert hints.
- Scope creep → Strict adherence to current unfinished iteration in .agent/PLAN.md / .agent/TODO.md.

**Start by advancing the last unfinished iteration** (none yet — this is bootstrap iteration 1 of the initiative).

**Next:** Orchestrator must read this file + DEVELOPMENT_STANDARDS.md + AGENT_ROLES + create/update .agent/PLAN.md, .agent/TODO.md, PROJECT_CONTEXT.md, SPRINTPLAN.md with concrete INVEST tasks from the phases above. Prioritize P1 + P4 foundation first.

All output in the loop must use the strict handoff JSON.
