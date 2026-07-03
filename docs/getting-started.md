# Getting Started

Get a full agentic development loop running in under five minutes.

## Prerequisites

- Python 3.10+
- Git
- An agent frontend (Blackbox, Cursor, or Claude Code)

## Windows

```powershell
cd C:\Path\To\Your\Project
.\agentic_loop_template\Agent-Init.ps1
```

Then paste the content of `prompts/short_orchestrator_prompt.md` (or your generated starter prompt) as the first message.

## Linux / macOS

```bash
cd /path/to/your/project
bash agentic_loop_template/Agent-Init.sh
source .venv/bin/activate
```

Paste `prompts/short_orchestrator_prompt.md` as your first message. Python commands use `.venv/bin/python`.

## First Cycle Checklist

1. Agent reads `TASK_SPECIFICATION.md` and `.agent/PLAN.md`
2. Git self-cycle per `DEVELOPMENT_STANDARDS.md` §11
3. Orchestrator plans INVEST tasks → hands off to Coder
4. Cycle continues: Coder → Tester → Debugger → Reviewer
5. Reviewer marks `DONE` or loops back to Orchestrator

## Consumer Projects

Copy the template from [examples/consumer-starter/](../examples/consumer-starter/). Add `agentic_loop_template/` to your `.gitignore`.

## Next Steps

- [Cross-Platform Guide](cross-platform.md)
- [Multi-Frontend Adapters](multi-frontend.md)
- [Architecture Overview](architecture.md)