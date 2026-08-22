# Architecture

[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../README.md)
[![Handoff Schema](https://img.shields.io/badge/spec-handoff%20schema-lightgrey?style=flat-square)](../HANDOFF_SCHEMA.md)

How the Agentix loop is structured: roles, state transfer, and self-improvement layers.

---

## Loop Overview

```mermaid
flowchart LR
    O[Orchestrator] --> C[Coder]
    C --> T[Tester]
    T --> D[Debugger]
    D --> R[Reviewer]
    R -->|NOT DONE| O
    R -->|DONE| Done[Lessons saved]
```

Each role: **PLAN → ACT (≤3 tool calls) → REFLECT → handoff JSON**.

---

## Core Components

| Layer | Location | Purpose |
|-------|----------|---------|
| Roles & prompts | `AGENT_ROLES.md`, `prompts/` | Per-role discipline |
| Handoffs | `HANDOFF_SCHEMA.md` | State transfer contract |
| Memory | `memory/` | questions_collector, meta_harvester, playbooks, ledger |
| Planning | `.agent/PLAN.md`, `.agent/TODO.md` | Iteration continuity |
| Playbooks | `.agent/PLAYBOOKS.json` | Knowledge bullets (ACE scoring) |
| Hub | `.agent/HUB_INDEX.json` | Exportable discovery index |
| Audit | `memory/audit_log.py` | Enterprise trail (P5) |
| Resume | `memory/resume.py` | Crash recovery (P7) |
| Request proxy | `memory/proxy/` | Loopback `:8110` gateway → host pxpipe `:8100` ([docs/proxy.md](proxy.md)) |
| Control Plane | `memory.dashboard` | operator HTMX UI, not the runner |

---

## Handoff Example

Every role ends with exactly one JSON object:

```json
{
  "handoff_to": "Coder",
  "role": "Orchestrator",
  "summary": "Planned next INVEST task. Git sync verified.",
  "next_input_files": [".agent/TODO.md"],
  "git_sync_status": { "verified": true },
  "confidence": 0.9,
  "status": "IN_PROGRESS"
}
```

Full schema: [HANDOFF_SCHEMA.md](../HANDOFF_SCHEMA.md).

---

## Self-Improvement Stack

| Module | CLI | When |
|--------|-----|------|
| Performance Ledger | `python -m memory.performance_ledger` | Reviewer on DONE |
| Meta Harvester | `python -m memory.meta_harvester harvest` | High-quality cycles |
| Playbooks | `python -m memory.playbooks select/curate` | PLAN / REFLECT |
| Questions Pool | `python -m memory.questions_collector` | Non-blocking approvals |
| Eval Harness | `python -m memory.eval_harness` | Trajectory scoring |

---

## Data Flow

```mermaid
flowchart TD
    Handoff[Handoff JSON] --> Reviewer
    Reviewer --> Ledger[performance_ledger]
    Reviewer --> Meta[meta_harvester]
    Reviewer --> Playbooks[playbooks curate]
    Orchestrator --> Select[playbooks select]
    Select --> Plan[SPRINTPLAN]
    Playbooks --> Hub[HUB_INDEX export]
```

---

## Related

- [Metrics & ROI](metrics-roi.md) — measured cycle gains
- [Hub](hub/README.md) — playbook marketplace
- [memory/README.md](../memory/README.md) — memory layer API
- [Agents Dashboard design](superpowers/specs/2026-08-21-agents-dashboard-design.md) — Control Plane spec (shipped `:8112` / 3.8.0)