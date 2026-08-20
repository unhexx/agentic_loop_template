# Agentix Skills Registry

First-class reusable skills for the agentic loop. Skills are progressive knowledge packages that agents load on-demand (via `tools/select.py` or explicit reference) to improve quality, reduce context waste, and compound institutional knowledge.

## Available Skills

| Skill | Purpose | When to load |
|-------|---------|--------------|
| [reflective-improvement](reflective-improvement/SKILL.md) | Structured 6-step reflection after tasks/errors/DONE cycles → persistent lessons, playbook updates, meta proposals | Reviewer on DONE; any role after failure or major milestone |
| [local-knowledge-ingestion](local-knowledge-ingestion/SKILL.md) | Templates for crawlers, SQLite local knowledge store, sovereign mirroring of docs/code into structured memory | Orchestrator bootstrap; when external docs or multi-repo knowledge needed |

## Usage

```bash
# Progressive load example
python tools/select.py --intent reflect
python tools/select.py --intent knowledge
python tools/select.py --intent compress
# or reference in handoff / prompt:
# "Follow skills/reflective-improvement/SKILL.md ritual"
python -m memory.context_budget check --files .agent/PLAN.md --budget 12000 --compress
```

Skills integrate with:
- `memory/playbooks.py` (curate bullets from lessons)
- `memory/meta_harvester.py` (golden trajectories)
- `memory/store.py` / workspace memory
- `PROMPT_COMPRESSION_GUIDE.md` (distillation)

Keep skill bodies short; heavy examples live in playbooks or trajectories.
