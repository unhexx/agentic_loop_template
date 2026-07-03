# Stack Template: Python API

Minimal FastAPI adoption pattern for Agentix consumer projects.

## Setup

1. Copy `agentic_loop_template/` and this template into your repo.
2. Create `TASK_SPECIFICATION.md` from `examples/consumer-starter/`.
3. Run `bash Agent-Init.sh --wizard` or `Agent-Init.ps1`.
4. Add to `.gitignore`: `.venv/`, `agentic_loop_template/` (if syncing separately).

## Suggested Layout

```
your-project/
├── agentic_loop_template/
├── src/
│   └── api/
│       └── main.py
├── tests/
├── TASK_SPECIFICATION.md
├── PROJECT_CONTEXT.md
└── .agent/
```

## Loop Focus

- Orchestrator: INVEST tasks for API endpoints
- Tester: pytest with coverage
- Reviewer: OpenAPI schema + ledger update on DONE

See [docs/getting-started.md](../../docs/getting-started.md).