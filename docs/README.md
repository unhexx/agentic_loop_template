# Agentix Documentation

**Language:** [English](README.md) · [Русский](ru/README.md)

[![Version](https://img.shields.io/badge/version-3.11.4-blue?style=flat-square)](../CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../README.md)

Canonical documentation for the Agentix agentic development loop template.

---

## Learning Paths

### Path 1 — First run (15 min)

1. [Getting Started](getting-started.md) — bootstrap + first message
2. [Multi-Frontend](multi-frontend.md) — Cursor / Claude / Blackbox
3. [Architecture](architecture.md) — how roles and handoffs work

### Path 2 — Adoption (30 min)

1. [../examples/consumer-starter/README.md](../examples/consumer-starter/README.md)
2. [Onboarding Wizard](onboarding-wizard.md) — `Agent-Init.sh --wizard`
3. [Cross-Platform](cross-platform.md) — paths and venv

### Path 3 — Operations (45 min)

1. [Metrics & ROI](metrics-roi.md) — ledger proof
2. [Hub](hub/README.md) — playbook export and discovery
3. [Enterprise Governance](enterprise-governance.md) — audit + policy
4. [Integrations](integrations.md) — GitHub Actions, trackers, Slack

### Path 4 — Evidence (20 min)

1. [Case Study](case-study.md) — 50+ dogfood cycles
2. [../examples/case-study/sanitized-summary.md](../examples/case-study/sanitized-summary.md)

Path 1 public pages (`README.md`, `docs/getting-started.md`, this index) have Russian siblings (`README.ru.md`, `docs/ru/…`). Edit both when the English text changes.

---

## Reference

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | 5-minute bootstrap |
| [Cross-Platform](cross-platform.md) | Windows / Linux / macOS |
| [Multi-Frontend](multi-frontend.md) | Agent UI adapters |
| [Architecture](architecture.md) | Loop, memory, handoffs |
| [Metrics & ROI](metrics-roi.md) | Performance ledger proof |
| [Request proxy](proxy.md) | Default pxpipe gateway, SLOs, opt-out, optional agy second instance |
| [Hub](hub/README.md) | Marketplace foundation |
| [Hub Discovery](hub/discovery.md) | Install playbooks |
| [Hub API Schema](hub/api-schema.json) | Web-ready JSON schema |
| [Pro Tier](pro-tier.md) | Free vs Pro matrix |
| [Enterprise Governance](enterprise-governance.md) | Policy, audit, approvals |
| [Integrations](integrations.md) | CI, Linear/Jira, Slack |
| [Onboarding Wizard](onboarding-wizard.md) | Interactive setup |
| [Case Study](case-study.md) | Dogfood results |

---

## Core Specs (repository root)

| File | Purpose |
|------|---------|
| [../HANDOFF_SCHEMA.md](../HANDOFF_SCHEMA.md) | JSON handoff contract |
| [../AGENT_ROLES.md](../AGENT_ROLES.md) | Per-role instructions |
| [../DEVELOPMENT_STANDARDS.md](../DEVELOPMENT_STANDARDS.md) | Process constitution |
| [../SYSTEM_PROMPT.md](../SYSTEM_PROMPT.md) | Master system prompt |
| [../memory/README.md](../memory/README.md) | Memory layer API |
| [../META_OPTIMIZER_SPEC.md](../META_OPTIMIZER_SPEC.md) | Meta-optimizer spec |
| [../ROADMAP.md](../ROADMAP.md) | Public roadmap |
| [../CHANGELOG.md](../CHANGELOG.md) | Release history |

---

## Version

Aligned with **Agentix 3.11.4** (2026-08-27). P8-12 module split: `memory/meta/`, `memory/experience/`, Init.ps1 dotsource. MultiLLM ontology in `memory/llm_ontology.py` (P8-13). Path 1 Russian siblings (`README.ru.md`, `docs/ru/`). NG11 `agent_dir=` landed on harvest/eval/resume. Conflict-free parallel sessions: `stream_lease`, `run-parallel --push`, STOP fan-out, Control Plane Streams page, live CLI identity + persist stamp, remaining `.agent/` locks, hub-safe integration merge. Supervisor prompt caps from `context_budget` / env (P8-14). Opt-in `run-parallel --concurrent` plus stdlib `.agent/` lock (P8-11). Harvest/reflect skill split (`experience-accumulation` vs `loop-self-improve`). Blackbox AI CLI adapter hardening. Opt-in skill `git-commit-to-jira-tasks` (explicit load only). P8 Harness Hardening complete. Business Efficiency Initiative (P0–P7) complete. Control Plane (`memory.dashboard`) on loopback `:8112`. Live Grok uses pxpipe by default. Parallel streams: `python -m memory.supervisor run-parallel` (serial default).
