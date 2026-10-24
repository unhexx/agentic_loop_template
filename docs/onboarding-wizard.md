# Onboarding Wizard (P6)

Interactive cross-platform setup for new consumer projects.

## One-command install into a new project

From a clone of this template:

```bash
bash scripts/agentix-init.sh /path/to/your-project \
  --name your-project \
  --frontend grok \
  --goal "One sentence the loop can test"
source /path/to/your-project/.venv/bin/activate
```

That creates `.venv`, runs `pip install -e "<this-repo>[dev]"`, symlinks `agentic_loop_template` at the SSOT, and writes `AGENTS.md`, `TASK_SPECIFICATION.md`, `PROJECT_CONTEXT.md`, `.agent/project_config.json`, `.agent/PLAN.md`, and the Agentix `.gitignore` block. Placeholders are filled from `--name`, `--goal`, `--stack`, and `--frontend`. A second run does not overwrite those files (`--force` does, except `.env.agentic`).

| Flag | Effect |
|------|--------|
| `--tier lite` | `AGENTS.md` + config only |
| `--tier full` | Loop spec, plan, and `Agent-Init.sh` (default) |
| `--frontend` | `grok` (default), `cursor`, `claude`, `blackbox` |
| `--no-venv` | Files only, no pip |
| `--no-ritual` | Skip state / playbooks / proxy cold start |

Windows: `powershell -ExecutionPolicy Bypass -File scripts\agentix-init.ps1 C:\path\to\your-project --name your-project`. Same flags. Help: `python -m memory init --help`.

## One-Command Demo

```bash
bash scripts/demo-loop.sh
```

Runs: env bootstrap → playbooks seed → plan check → resume context → eval harness sample.

## Wizard Mode

Unix:

```bash
bash Agent-Init.sh --wizard
```

Windows:

```powershell
.\Agent-Init.ps1 -Wizard
```

Prompts for:
1. Project name
2. Platform (win/linux/mac)
3. Frontend (**grok** / cursor / claude / blackbox) — default **grok**
4. Spec file path

Outputs tailored next steps and copies consumer-starter templates if missing.

Live/wizard default frontend is **grok** (fail-closed proxy health). Pass `-Frontend blackbox` (or set `supervisor.adapter` in `.agent/project_config.json`) to keep Blackbox. The subprocess CLI adapter is opt-in: `python -m memory.supervisor run --adapter blackbox`. Non-wizard Init stays best-effort so CI does not require pxpipe. `AGENTIX_PROXY=0` still opts out.

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