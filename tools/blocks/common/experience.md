# Experience harvest (cross-project)

Scan sibling repos under a parent folder. Do **not** load multi-MB `.agent` archives.

```bash
# Dry-run self-improve cycle (seeds + playbooks + adoption audit)
python -m memory.experience_harvester cycle --parent /path/to/_PROJECT

# Apply into workspace memory
python -m memory.experience_harvester cycle --parent /path/to/_PROJECT --apply

# Audit only
python -m memory.experience_harvester audit --parent /path/to/_PROJECT
```

Sources (v3.6): `AGENTS.md`, Agent-Playbook, CONTRIBUTING, living plans, LESSONS, LOOP_STATE drift, broken README agent-doc links.

When: Reviewer on DONE (parent-folder session), or Orchestrator after adopting a new consumer.

See `EXPERIENCE_EXTRACTION_TOOLS.md` and `skills/reflective-improvement/SKILL.md`.
