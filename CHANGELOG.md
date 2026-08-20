# Changelog

## [Unreleased]

## [3.6.0] - 2026-08-20

### Added (cross-project experience harvest — 2026-08-20 self-improve)
- Harvester v3.6: scan `AGENTS.md`, Agent-Playbook, CONTRIBUTING, living plans, LOOP_STATE drift, broken README agent-doc links (old scan of LESSONS-only returned **0** on current `_PROJECT/*`)
- CLI: `python -m memory.experience_harvester audit|cycle --parent …`
- Seeds from live tree: docs_gap (signet/nesttunnel), classifier Windows-only Init + stale LOOP_STATE, telegrok incomplete Init, two-tier adoption
- Lite consumer: `examples/consumer-starter/AGENTS.md.example`, `Agent-Init.consumer.sh` (sibling SSOT symlink + PYTHONPATH)
- `tools/select.py --intent harvest`; `tools/blocks/common/experience.md`
- Tests: `memory/test_experience_harvester.py` (`python -m` + CI verify step)
- Docs: `docs/ANALYSIS_FROM_PROJECTS.md` 2026-08-20 section; Linux/Grok-first `SYSTEM_PROMPT.md`

### Added (skills + rule-based context compressor)
- Skills registry: `skills/README.md`
  - `skills/reflective-improvement/SKILL.md` — 6-step reflection ritual (Reviewer MUST on DONE)
  - `skills/local-knowledge-ingestion/SKILL.md` — SQLite knowledge template, crawlers, sovereign mirroring
- Rule-based compressor: `memory/compressor.py`
  - CLI: `python -m memory.compressor files --budget 12000 …` / `distill --text-file`
  - Priority drop (history/trajectories first), markdown distill, head+tail truncate
  - Inspired by Acon (arXiv:2510.00615, 26–54% peak reduction), PAACE / rate-distortion — rules only, no network
- `context_budget` `--compress`: when over budget, run compressor (sources not rewritten)
- Tests: `memory/test_compressor.py`, `memory/test_knowledge.py`
- Config: `context_budget.compress_when_over` in `.agent/project_config.example.json`
- Local knowledge store: `memory/knowledge.py`
  - CLI: `python -m memory.knowledge query|upsert|ingest-docs|stats`
  - SQLite schema from `skills/local-knowledge-ingestion` (unique source+title, category cap)
  - `ingest-docs` distills markdown via the rule compressor before upsert

### Changed
- `VERSION` → 3.6.0
- README features/CLI + ROADMAP milestone
- Reviewer short prompt: mandatory reflective-improvement + compress-when-over
- `PROMPT_COMPRESSION_GUIDE.md`: 2026 research mapped to the rule compressor
- `python -m memory compressor` / `python -m memory context-budget` dispatch

## [3.5.0] - 2026-07-29

### Added (Agentix Supervisor — multi-frontend autonomy)
- Supervisor CLI: `python -m memory.supervisor` / `python -m memory supervisor` / `scripts/agentix-supervisor`
  - subcommands: `run`, `resume`, `status`, `stop`
- FSM role transitions: Orchestrator → Coder → Tester → (Debugger) → Reviewer → `PR_READY`
- Mock adapter full cycle path for CI (`--adapter mock`, ≥3 cycles without network)
- Multi-frontend adapters: `mock`, `grok`, `cursor`, `blackbox` under `memory/adapters/`
- PR gate: `gh pr create` only (never merge to main); fallback `PR_READY_LOCAL`
- Config: `supervisor` section in `.agent/project_config.example.json`

### Changed
- `VERSION` → 3.5.0
- README CLI table: supervisor entry

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
