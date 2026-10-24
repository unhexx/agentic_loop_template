# Consumer Starter Template

[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../../README.md)
[![Getting Started](https://img.shields.io/badge/docs-getting_started-green?style=flat-square)](../../docs/getting-started.md)

Two adoption tiers. **Do not copy the whole `agentic_loop_template/` tree** into a product — that is how `classifier` drifted to a stale Windows `LOOP_STATE` and lost Linux `Agent-Init.sh`.

---

## Tier A — lite (most product repos)

Enough for a coding agent: exact commands, Definition of Done, NEVER list.

```bash
cp examples/consumer-starter/AGENTS.md.example /path/to/your-project/AGENTS.md
# fill {{placeholders}}
```

Pattern sources: `contact-vault/docs/06-ENGINEERING/Agent-Playbook.md`, `telegrok/AGENTS.md`.

Operator search/research containers (SearXNG JSON, optional LDR): [docs/stack.md](../../docs/stack.md). Live Grok still goes through the gateway → host pxpipe; do not copy `deploy/compose.env`.

Use this when the work is a normal PR, not a multi-cycle autonomous sprint.

---

## Tier B — full Agentix loop

Autonomous O→C→T→D→R. One command from the SSOT clone creates the project venv, symlinks the template (it does not copy the tree), and writes the starter files with your name, goal, and frontend filled in:

```bash
bash /path/to/agentic_loop_template/scripts/agentix-init.sh /path/to/your-project \
  --name your-project \
  --frontend grok \
  --goal "One sentence the loop can test"
source /path/to/your-project/.venv/bin/activate
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File C:\path\to\agentic_loop_template\scripts\agentix-init.ps1 C:\path\to\your-project --name your-project --frontend grok
```

`--tier lite` writes only `AGENTS.md`, `.gitignore`, `.env.agentic`, and `.agent/project_config.json`. Repeat the command safely: existing files stay put unless you pass `--force`. `--no-venv` skips `pip install -e`.

`bash Agent-Init.sh` in the new project (copied from `Agent-Init.consumer.sh`) refreshes the venv later. The symlink points at the SSOT, so `python -m memory` comes from `pip install -e "<template>[dev]"`. `PYTHONPATH` is only a fallback if that install did not take.

Live Grok CLI traffic goes through the Agentix gateway (`http://127.0.0.1:8110/v1`) which fronts host pxpipe. Mock stays proxy-free. Opt out: `AGENTIX_PROXY=0`. See [docs/proxy.md](../../docs/proxy.md). The installer writes `GROK_CLI_CHAT_PROXY_BASE_URL` into the project venv via `python -m memory.proxy install-venv`.

---

## Files in This Directory

| File | Written by `agentix-init` as |
|------|--------|
| `AGENTS.md.example` | `AGENTS.md` (lite and full) |
| `Agent-Init.consumer.sh` | `Agent-Init.sh` (full tier) |
| `TASK_SPECIFICATION.example.md` | `TASK_SPECIFICATION.md` (full) |
| `PROJECT_CONTEXT.example.md` | `PROJECT_CONTEXT.md` (full) |
| `.gitignore.agentic` | merged into `.gitignore` |
| `agentic.env.example` | `.env.agentic` (never commit) |

---

## What to Ignore in Git

```
agentic_loop_template/    # symlink to SSOT; do not vendor
.agent/handoff_*.json
PROJECT_CONTEXT.md        # optional: keep local only
.env.agentic
```

---

## Docs

- [Getting Started](../../docs/getting-started.md)
- [Experience harvest](../../EXPERIENCE_EXTRACTION_TOOLS.md)
- [Multi-Frontend](../../docs/multi-frontend.md)
- [Architecture](../../docs/architecture.md)
