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

Use this when the work is a normal PR, not a multi-cycle autonomous sprint.

---

## Tier B — full Agentix loop

Autonomous O→C→T→D→R. Keep the template as a **sibling SSOT** (symlink + `pip install -e ../agentic_loop_template[dev]`).

```bash
# layout
#   _PROJECT/agentic_loop_template/   ← SSOT
#   _PROJECT/your-project/

cd /path/to/your-project
cp ../agentic_loop_template/examples/consumer-starter/TASK_SPECIFICATION.example.md TASK_SPECIFICATION.md
cp ../agentic_loop_template/examples/consumer-starter/PROJECT_CONTEXT.example.md PROJECT_CONTEXT.md
cp ../agentic_loop_template/examples/consumer-starter/AGENTS.md.example AGENTS.md
cp ../agentic_loop_template/examples/consumer-starter/Agent-Init.consumer.sh Agent-Init.sh
cat ../agentic_loop_template/examples/consumer-starter/.gitignore.agentic >> .gitignore
chmod +x Agent-Init.sh
bash Agent-Init.sh --wizard
source .venv/bin/activate
# paste ../agentic_loop_template/prompts/short_orchestrator_prompt.md
```

`Agent-Init.consumer.sh` will `ln -s ../agentic_loop_template` and `pip`/`uv` install the SSOT editable so `python -m memory` works without `PYTHONPATH`. PYTHONPATH is only a fallback if the editable install did not take. Live Grok CLI traffic goes through the Agentix gateway (`http://127.0.0.1:8110/v1`) which fronts host pxpipe. Mock stays proxy-free. Opt out: `AGENTIX_PROXY=0`. See [docs/proxy.md](../../docs/proxy.md).

---

## Files in This Directory

| File | Action |
|------|--------|
| `AGENTS.md.example` | Rename → `AGENTS.md` (lite **or** full) |
| `Agent-Init.consumer.sh` | Copy → product `Agent-Init.sh` (full tier) |
| `TASK_SPECIFICATION.example.md` | Rename → `TASK_SPECIFICATION.md` (full) |
| `PROJECT_CONTEXT.example.md` | Rename → `PROJECT_CONTEXT.md` (full) |
| `.gitignore.agentic` | Merge into project `.gitignore` |
| `agentic.env.example` | Copy → `.env.agentic` (never commit) |

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
