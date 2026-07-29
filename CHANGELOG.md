# Changelog

## 3.3.0 — 2026-07-29 — Top-10 harness hardening (context, errors, Linux)

### Added
- Bounded LOOP_STATE: `memory/state.py` (JSON working set + history archive + compact)
- Progressive tools: `tools/select.py` + `tools/blocks/{common,linux,windows}/`
- Memory reunified: `schema.py`, `store.py`, `workspace.py`, `__main__.py` + meta_harvester
- Linux bootstrap: `Agent-Init.sh`
- Handoff schema + validator: `schemas/handoff.schema.json`, `memory/validate_handoff.py`
- Context budget: `memory/context_budget.py`
- Experience harvester: `memory/experience_harvester.py` (+ seed defaults from multi-project analysis)
- Parallel protocol: `PARALLEL_PROTOCOL.md`, `scripts/agentic_loop.sh`
- Git helpers: `scripts/preflight_git.sh`, `scripts/sync-worktree.sh`, `scripts/sync_template_from_ssot.sh`
- Docs: `docs/ANALYSIS_FROM_PROJECTS.md`, `docs/TOP10_IMPROVEMENTS.md`, metrics baseline/after
- `VERSION` file (3.3.0)

### Changed
- `TOOLS_REGISTRY.md` / `TOOLS_INSTRUCTIONS.md` become thin progressive entrypoints
- `EXPERIENCE_EXTRACTION_TOOLS.md` implemented (no longer a stub)
- `project_config.example.json`: git/context_budget/state/profiles

### Why
Evidence from eegent (12MB LOOP_STATE, 115KB TOOLS), classifier stale state, Windows-only bootstrap, and split memory packages. Goal: cut context waste, reduce process errors, enable Linux/Grok autonomous cycles.
