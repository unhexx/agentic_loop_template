# Onboarding Wizard (P6)

Interactive cross-platform setup for new consumer projects.

## One-Command Demo

```bash
bash scripts/demo-loop.sh
```

Runs: env bootstrap → playbooks seed → plan check → resume context → eval harness sample.

## Wizard Mode (Agent-Init.sh)

```bash
bash Agent-Init.sh --wizard
```

Prompts for:
1. Project name
2. Platform (win/linux/mac)
3. Frontend (blackbox/cursor/claude)
4. Spec file path

Outputs tailored next steps and copies consumer-starter templates if missing.

## Stack Templates

| Template | Path | Use case |
|----------|------|----------|
| Python API | `examples/stack-templates/python-api/` | FastAPI/backend services |
| Static docs | `examples/stack-templates/static-docs/` | Docs-only adoption |

## IDE Launch Stubs

- **VS Code / Cursor:** [.vscode/extensions.json](../.vscode/extensions.json) — recommended extensions
- **Cursor rules:** Point to `SYSTEM_PROMPT.md` + `DEVELOPMENT_STANDARDS.md`
- **Multi-frontend:** [multi-frontend.md](multi-frontend.md)

## Video / Quickstart Script

Outline for a 3-minute demo (record-ready):

1. (0:00) Clone template, run `bash scripts/demo-loop.sh`
2. (0:45) Show `docs/getting-started.md` and first orchestrator prompt
3. (1:30) Walk through one mini cycle (plan → handoff JSON)
4. (2:15) Show ledger metrics and Hub export
5. (2:45) Point to `examples/consumer-starter/`

Save recording assets to `docs/assets/` (optional, not committed by default).