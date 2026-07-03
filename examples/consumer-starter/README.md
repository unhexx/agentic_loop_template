# Consumer Starter Template

Minimal adoption skeleton for new projects using Agentix.

## Setup

1. Copy `agentic_loop_template/` into your repo root (or sync from this repo).
2. Copy files from this directory into your project root:
   - Rename `.gitignore.agentic` → merge into your `.gitignore`
   - Rename `TASK_SPECIFICATION.example.md` → `TASK_SPECIFICATION.md`
   - Rename `PROJECT_CONTEXT.example.md` → `PROJECT_CONTEXT.md`
   - Copy `agentic.env.example` → `.env.agentic` (add to `.gitignore`)
3. Fill `{{placeholders}}` in `SYSTEM_PROMPT.md`.
4. Bootstrap:
   - Windows: `.\agentic_loop_template\Agent-Init.ps1`
   - Linux/Mac: `bash agentic_loop_template/Agent-Init.sh`
5. Start loop with `prompts/short_orchestrator_prompt.md`.

## What to Ignore in Git

See `.gitignore.agentic` — template folder, handoffs, cycle artifacts stay out of product history.

## Docs

- [docs/getting-started.md](../../docs/getting-started.md)
- [docs/multi-frontend.md](../../docs/multi-frontend.md)