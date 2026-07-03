# Agentic Loop — Operations Guide

[![Version](https://img.shields.io/badge/version-3.4.0-blue.svg)](CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue)](README.md)

> **Start here:** [README.md](README.md) — quick start, examples, badges, full documentation map.

This guide covers **loop operations**: roles, temperatures, launch commands, and runtime rules.

---

## Roles

```
Orchestrator → Coder → Tester → Debugger → Reviewer
     ↑ (if NOT DONE) ─────────────────────────┘
     DONE → lessons crystallized
```

Inner loop per role: **PLAN → ACT (≤3 tool calls) → REFLECT → JSON handoff**.

---

## Launch

### Windows

```powershell
.\Agent-Init.ps1 -OutputFile "blackbox_start_prompt.txt"
# Paste blackbox_start_prompt.txt or prompts/short_orchestrator_prompt.md
```

### Linux / macOS

```bash
bash Agent-Init.sh
source .venv/bin/activate
# Paste prompts/short_orchestrator_prompt.md
```

### Demo (any platform)

```bash
bash scripts/demo-loop.sh
```

---

## Model Settings (recommended)

| Role | Temperature | Top-P | Max Tokens |
|------|-------------|-------|------------|
| Orchestrator | 0.0 | 0.9 | 4096 |
| Coder | 0.2 | 0.95 | 8192 |
| Tester | 0.0 | 0.9 | 4096 |
| Debugger | 0.2 | 0.95 | 4096 |
| Reviewer | 0.0 | 0.9 | 4096 |

---

## Runtime Rules

1. **Handoffs** — exactly one JSON object per turn ([HANDOFF_SCHEMA.md](HANDOFF_SCHEMA.md)).
2. **Git** — self-cycle §11 before planning ([DEVELOPMENT_STANDARDS.md](DEVELOPMENT_STANDARDS.md)).
3. **Commits** — natural Russian, human senior-dev voice; never mention AI/LLM.
4. **Playbooks** — `python -m memory.playbooks select` before PLAN.
5. **Reviewer** — ledger + meta harvest on high-quality DONE.

---

## Prompts (entry points)

| File | Use |
|------|-----|
| [prompts/short_orchestrator_prompt.md](prompts/short_orchestrator_prompt.md) | **Default first message** |
| [prompts/short_coder_prompt.md](prompts/short_coder_prompt.md) | Coder role block |
| [prompts/short_tester_prompt.md](prompts/short_tester_prompt.md) | Tester role block |
| [prompts/short_debugger_prompt.md](prompts/short_debugger_prompt.md) | Debugger role block |
| [prompts/short_reviewer_prompt.md](prompts/short_reviewer_prompt.md) | Reviewer role block |
| [first_orchestrator_message.md](first_orchestrator_message.md) | Initiative bootstrap variant |

Full role blocks: [AGENT_ROLES.md](AGENT_ROLES.md).

---

## Directory Map

```
├── prompts/           # Short role prompts (start loop here)
├── memory/            # Ledger, playbooks, meta, audit, resume
├── .agent/            # PLAN, TODO, ledger, playbooks, hub index
├── docs/              # Full documentation site
├── examples/          # consumer-starter, stack templates, case study
└── scripts/           # demo-loop.sh
```

---

## Further Reading

- [docs/getting-started.md](docs/getting-started.md) — step-by-step with examples
- [docs/architecture.md](docs/architecture.md) — memory and handoff flow
- [docs/multi-frontend.md](docs/multi-frontend.md) — Cursor, Claude, Blackbox
- [memory/README.md](memory/README.md) — memory layer API

**Maintained by exception.expert** · Agentix 3.4.0