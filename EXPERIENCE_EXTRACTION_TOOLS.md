# Experience Extraction Toolkit (v3.6)

## Purpose

Pull recurring failures and strategies from real project runs **and** product agent docs into workspace memory so Orchestrator snapshots prevent repeats.

v3.5 only read `LESSONS.md` / `SELF_IMPROVEMENT_LOG.md`. On the 2026-08-20 `_PROJECT/*` tree that scan returned **zero** hits. v3.6 reads the docs agents actually maintain.

## Commands

```bash
# Seed high-value defaults (eegent 2026-07 + _PROJECT 2026-08)
python -m memory.experience_harvester seed-defaults --apply

# Scan parent folder (playbooks + AGENTS.md + living plans)
python -m memory.experience_harvester scan --parent /path/to/_PROJECT --apply

# Adoption health (tiers: full|partial|lite|stale|docs_gap|none|empty)
python -m memory.experience_harvester audit --parent /path/to/_PROJECT

# Self-improve cycle: seeds + scan + audit
python -m memory.experience_harvester cycle --parent /path/to/_PROJECT --apply

# Dry-run any of the above by omitting --apply
python -m memory.experience_harvester cycle --parent /path/to/_PROJECT
```

Aliases: `python -m memory experience …` / `python -m memory harvest-experience …`.

## Categories written

- `Common Failure Patterns`
- `Effective Loop Strategies`
- `High-Value Compression Patterns`
- `Meta Improvement Patterns`
- `Project Playbook Patterns` (contracts-first, provenance, MUST NOT)

## Sources scanned

- `.agent/LESSONS.md`, `SELF_IMPROVEMENT_LOG.md`
- `AGENTS.md`, `CONTRIBUTING.md`, `TIPS_AND_TRICKS.md`
- `PROJECT_CONTEXT.md`, `SPRINTPLAN.md`, `TASK_SPECIFICATION.md`, `SYSTEM_PROMPT.md`
- `.agent/PLAN.md`, `.agent/TODO.md`
- `**/Agent-Playbook.md`, `**/AGENT_PLAYBOOK.md`, `**/AGENTIC_LOOP.md`
- LOOP_STATE drift (Windows paths, foreign worktree names)
- README links to missing agent docs (`docs_gap`)

## Integration

- **Orchestrator:** `python -m memory query --top 5 --category "Common Failure Patterns"` at cycle start.
- **Reviewer (DONE, parent-folder session):** `python -m memory.experience_harvester cycle --parent ..` then `skills/reflective-improvement`.
- Progressive tools: `python tools/select.py --intent harvest`.
- Skill-compatible with eegent `agentic-loop-error-collector` (same memory update path).

## Evidence (2026-08-20)

See `docs/ANALYSIS_FROM_PROJECTS.md`. Highest-signal product playbooks: `contact-vault/docs/06-ENGINEERING/Agent-Playbook.md`, `telegrok/AGENTS.md`.
