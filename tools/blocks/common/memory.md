# Memory CLI (cross-platform)

Run from project root. Prefer project venv python if present.

```bash
# Workspace id + paths
python -m memory info

# Top failure patterns before PLAN
python -m memory query --top 5 --category "Common Failure Patterns"

# Snapshot (JSON)
python -m memory snapshot

# Record pattern
python -m memory update --category "Common Failure Patterns" --description "exact lesson text"

# Questions pool
python -m memory.questions_collector list

# Meta harvest after high-quality DONE
python -m memory.meta_harvester harvest --handoff .agent/last_handoff.json --cycle N --outcome DONE
```

When nested as `agentic_loop_template/` in a product repo:

```bash
python -m agentic_loop_template.memory info
```
