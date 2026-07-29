# Changelog

## [Unreleased]

### Changed
- Documentation overhaul: README with badges, TOC, mermaid diagrams, cycle examples
- docs/ index with learning paths; getting-started with session examples
- AGENTIC_LOOP_README streamlined as operations guide

## [3.4.1] - 2026-07-29

### Added (top-10 harness hardening, multi-project analysis)
- Bounded LOOP_STATE: `memory/state.py` (JSON working set + history archive + compact)
- Progressive tools: `tools/select.py` + `tools/blocks/{common,linux,windows}/`
- Memory core reunified on Linux path: `schema.py`, `store.py`, `workspace.py` (with existing playbooks/ledger/meta)
- Handoff schema + validator: `schemas/handoff.schema.json`, `memory/validate_handoff.py`
- Context budget: `memory/context_budget.py`
- Experience harvester: `memory/experience_harvester.py` (+ seed defaults)
- Parallel protocol: `PARALLEL_PROTOCOL.md`, `scripts/agentic_loop.sh`
- Git helpers: `scripts/preflight_git.sh`, `scripts/sync-worktree.sh`, `scripts/sync_template_from_ssot.sh`
- Docs: `docs/ANALYSIS_FROM_PROJECTS.md`, `docs/TOP10_IMPROVEMENTS.md`, metrics baseline/after
- `VERSION` file

### Changed
- `Agent-Init.sh` merges wizard (P6) + cold-start state/tools/experience seed
- `TOOLS_REGISTRY.md` / `TOOLS_INSTRUCTIONS.md` progressive entrypoints
- `EXPERIENCE_EXTRACTION_TOOLS.md` implemented
- Orchestrator short prompt: bounded state + progressive tools + playbooks
- `project_config.example.json`: git/context_budget/state/profiles + playbooks
- DEVELOPMENT_STANDARDS §5.1 bounded `.agent` state

### Why
Evidence from eegent (12MB LOOP_STATE, 115KB TOOLS), classifier stale state, Windows-only bootstrap friction, split memory packages. Goal: cut context waste, reduce process errors, enable Linux/Grok autonomous cycles on top of 3.4.0.

## [3.4.0] - 2026-07-03

### Added
- **P5 Enterprise:** `memory/audit_log.py`, `examples/policy/sample-policy.toml`, `docs/enterprise-governance.md`, `docs/integrations.md`, `.github/workflows/agentix-loop.yml`
- **P6 DX:** `Agent-Init.sh --wizard`, `scripts/demo-loop.sh`, `docs/onboarding-wizard.md`, stack templates, `.vscode/extensions.json`
- **P7 Sustain:** `memory/resume.py`, `memory/eval_harness.py`, selective memory in compression guide, `docs/case-study.md`, `examples/case-study/`
- Tests: `memory/test_p5_p7.py`

### Changed
- Generalized legacy project paths in `AGENT_ROLES.md` and `DEVELOPMENT_STANDARDS.md`
- Business Efficiency Initiative marked **COMPLETE** (P0–P7)

## [3.3.0] - 2026-07-03

### Added
- `docs/` site, `examples/consumer-starter/`, Agentix Hub CLI, Pro tier hooks
- Platform-adaptive prompts, cross-platform quickstart, proof-driven README

## 2026-07-03 — Business Efficiency Initiative

- 50+ dogfood cycles; measurable gains (ledger ~1.6 min avg, 0.94 confidence)
- P1–P7 delivered across iterations 1–6
