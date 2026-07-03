# Consumer Starter Template

[![Main README](https://img.shields.io/badge/Main-README-blue)](../../README.md)
[![Getting Started](https://img.shields.io/badge/docs-getting%20started-green)](../../docs/getting-started.md)

Minimal adoption skeleton for new projects using Agentix.

---

## Quick Start

```bash
# 1. Copy template into your project
cp -r agentic_loop_template/ /path/to/your-project/
cd /path/to/your-project

# 2. Copy starter files from this directory
cp examples/consumer-starter/TASK_SPECIFICATION.example.md TASK_SPECIFICATION.md
cp examples/consumer-starter/PROJECT_CONTEXT.example.md PROJECT_CONTEXT.md
cat examples/consumer-starter/.gitignore.agentic >> .gitignore

# 3. Bootstrap
bash agentic_loop_template/Agent-Init.sh --wizard
source .venv/bin/activate

# 4. Launch loop — paste prompts/short_orchestrator_prompt.md to your agent
```

---

## Files in This Directory

| File | Action |
|------|--------|
| `TASK_SPECIFICATION.example.md` | Rename → `TASK_SPECIFICATION.md`, fill placeholders |
| `PROJECT_CONTEXT.example.md` | Rename → `PROJECT_CONTEXT.md` |
| `.gitignore.agentic` | Merge into project `.gitignore` |
| `agentic.env.example` | Copy → `.env.agentic` (never commit) |

---

## What to Ignore in Git

```
agentic_loop_template/    # if synced separately
.agent/handoff_*.json
PROJECT_CONTEXT.md        # optional: keep local only
.env.agentic
```

---

## Example: First Task in SPEC

```markdown
## Success Criteria
- Implement user authentication API endpoint
- Tests pass with >80% coverage
- Reviewer approves with status=DONE
```

The Orchestrator decomposes this into INVEST tasks in `.agent/TODO.md`.

---

## Docs

- [Getting Started](../../docs/getting-started.md)
- [Multi-Frontend](../../docs/multi-frontend.md)
- [Architecture](../../docs/architecture.md)