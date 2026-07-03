# PROJECT_CONTEXT.md

> **Source of Truth:** `TASK_SPECIFICATION.md`  
> This file is updated by the **Orchestrator** (current status) and the **Reviewer** (self-improvement log).  
> Maximum size: ~3000 tokens. Compress older entries when necessary.  
> All content must be in English.

## Project Identification

| Parameter       | Value                                      |
|-----------------|--------------------------------------------|
| **Project**     | Agentix (agentic_loop_template)            |
| **Goal**        | Production-grade self-improving agentic dev loop template. Current focus: implement business efficiency recommendations to maximize ROI, adoption, and self-improvement velocity for users and maintainers (exception.expert). |
| **Tech Stack**  | Python (memory layer), PowerShell (primary scripts), Markdown docs, MCP skills, extensible via skills. Cross-platform path to Linux/Mac + multi-frontend. |
| **Current Branch** | main (worktree: upgrade-03-07)           |
| **Git User**    | Unhandled Exception (template maintainer)  |

## Current Status

| Field                  | Value                                      |
|------------------------|--------------------------------------------|
| **Cycle Number**       | 0 (bootstrap for business efficiency initiative) |
| **Current Phase**      | planning / foundation                      |
| **Active Role**        | Orchestrator                               |
| **Status**             | IN_PROGRESS                                |
| **Confidence**         | 0.75                                       |
| **Last Commit**        | "Подготовил артефакты инициации цикла улучшений бизнес-эффективности Agentix" |
| **Last Updated**       | 2026-07-03                                 |

## Key Decisions & Context

- Deep analysis completed (2026-07-03): identified 7 high-impact recommendation areas. See TASK_SPECIFICATION.md for full business objectives, phases, success criteria and risks.
- Prioritization: P1 Metrics/ROI first (proof + data for everything), P4 Meta completion second (accelerates all future cycles).
- This repo is the template + dogfooding target. All changes must propagate via §11 discipline to consumer projects.
- Platform reality: Primary target remains Windows/PowerShell/Blackbox/MiniMax, but cross-platform adapters required for broader adoption.
- Self-improvement active: meta_optimizer enabled, daily_decomposition_ritual enabled, question_pool ready.

## Performance Snapshot (P1 + Playbooks P4 — after 40+ cycles)

Recent (post playbooks integration):
- Cycles tracked: 40+
- Avg elapsed_min (recent 10): ~2.6 (down from ~4+ pre-P4)
- Avg confidence: ~0.93
- Meta/playbook applied: multiple
- Key wins: 
  - Playbooks module live with select/curate (ACE-style). Seeded + used in cycles. Proposals for metrics/ledger in handoffs.
  - Full cycle support: playbooks for tools/roles/phases + other objects foundation.
  - P2 cross-platform start (Agent-Init.sh, portable prompts).
  - Hygiene: legacy refs generalized.
- Tests: playbooks functional (direct load + select returns relevant bullets).
- Spec alignment: Matches "playbooks and other objects for full work with all tools of the continuous development cycle".

See .agent/PLAYBOOKS/, .agent/PERFORMANCE_LEDGER.md, .agent/handoff_orchestrator_playbooks.json.

## Reviewer Notes (Cycle 52)
- Work reviewed against TASK_SPECIFICATION and current PLAN/TODO: Strong progress.
- Process: Git self-cycles compliant (Russian commits, pushes, sync verified). No violations found in this slice.
- Quality: Playbooks follow best practices (ACE/Reflexion). Integrated across docs and meta. Good.
- Gaps noted for next: Deeper runtime injection of playbooks into actual tool/role execution (beyond docs). Accelerate P2 cross-platform code. Complete any remaining hygiene.
- Recommendation: Continue to Orchestrator/Coder for P2 runtime + full playbook usage in cycle. High confidence in direction.

## Open High-Value Questions (non-blocking)

(Will be managed via .agent/QUESTIONS_POOL.md + collector. None critical for bootstrap iteration.)

## Recent Distillations & Lessons (to be updated by Reviewer)

- Bootstrap created living plan artifacts to allow the loop itself to execute the efficiency program.
- Memory/meta must be exercised immediately to demonstrate value.

## Permanent Rules (distilled, high-signal only)

- Always start cycles by advancing unfinished items from .agent/PLAN.md + .agent/TODO.md (current iteration only).
- Metrics and meta data are first-class sources of truth alongside SPEC.
- All public-facing docs and examples must support the "business efficiency" narrative with evidence.

See also .agent/PLAN.md for iteration details and TASK_SPECIFICATION.md.
