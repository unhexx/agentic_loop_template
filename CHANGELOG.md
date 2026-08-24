# Changelog

## [Unreleased]

### Added
- Opt-in skill `skills/git-commit-to-jira-tasks`: cluster git commits into INVEST Jira Stories (Fibonacci Story Points; Original Estimate only if `JIRA_HOURS_PER_SP` / `--hours-per-sp` and timetracking is on the create screen). Disabled by default (`disable-model-invocation: true`; never `--intent git`)

## [3.9.0] - 2026-08-24

### Added (P8 Harness Hardening)
- Packaging: `pyproject.toml` (dist name `agentix`, import package `memory`), extras `dev` / `dashboard`, console scripts `agentix` / `agentix-supervisor` / `agentix-dashboard` / `agentix-proxy`. `pip install -e ".[dev]"` — `python -m memory` without PYTHONPATH
- Observability: `logging.getLogger("memory.*")` + `AGENTIX_LOG_LEVEL`; WARNING on critical supervisor / proxy / playbooks swallows (no `except Exception: pass` on those paths)
- Handoff extract/persist: `extract_handoff` picks the last persistable candidate; adapters `validate_handoff` + atomic `save_handoff`; structural checks from `schemas/handoff.schema.json` via `jsonschema`
- Init parity: `Agent-Init.sh` and `Agent-Init.ps1` share the cold-start ritual (editable install, `state init`, knowledge ingest, playbooks seed, proxy); wizard on both; default frontend **grok**
- State DI: state helpers take `agent_dir=`; supervisor no longer mutates module globals or `os.chdir` for correctness
- CI: GitHub Actions `pull_request` + `pytest memory/` including the full mock O→C→T→R cycle; G1 import from `/tmp` with PYTHONPATH unset
- Design spec: [`docs/superpowers/specs/2026-08-24-p8-harness-hardening-design.md`](docs/superpowers/specs/2026-08-24-p8-harness-hardening-design.md)

### Changed
- `VERSION` → 3.9.0
- ROADMAP P8 complete; next = Future
- Living docs: install without PYTHONPATH; consumer-starter editable-installs the sibling SSOT

## [3.8.1] - 2026-08-23

### Added
- Supervisor `run-parallel`: disjoint `owned_paths` streams (serial), git worktree provision, integration branch, one PR — never merges `main`
- Mock adapter fills `stream` / `owned_paths` / `worktree` from `AGENTIX_*` env
- Tests: `memory/test_streams.py`, `memory/test_supervisor_parallel.py`

### Changed
- Live Grok remains **pxpipe-default** (`proxy.mode=required`); README badges (version, CI, pxpipe) and Quick Start call this out
- Handoff `stream` is a free-form name (named parallel streams), not a closed `product|meta|cross` enum
- `VERSION` → 3.8.1

## [3.8.0] - 2026-08-22

### Added (Agents Dashboard / Control Plane)
- Operator HTMX Control Plane sidecar: `python -m memory.dashboard serve --workdir PATH` / `scripts/agentix-dashboard`
- Loopback **`:8112`** only (gateway owns `:8110`, pxpipe `:8100`). Does **not** call `run_loop` or spawn adapters — observes `.agent/*` SSOT, is not the runner
- Screens: Loop, Handoff, Ledger, Playbooks, Audit, Questions, Plan, Memory. Server-rendered HTMX partials, Tailwind/HTMX CDN, no-Jinja string substitution, `/ws/ui` + polling fallback
- Security: loopback peer + Host (`ipaddress` 127/8; `Host: 127.0.0.1.nip.io` is 403), same-origin POST, CSRF, optional `DASHBOARD_TOKEN`. Empty token = local trust. **Set a token before SSH `-L` / funnel**
- Gated writes: cooperative `.agent/STOP`, clear-stop, resolve questions; PR link is read-only `gh pr view`. Writes are confirmed and audited (`role=operator`)
- Supervisor liveness file `.agent/supervisor.heartbeat` (20 s daemon tick; dashboard freshness 45 s). `LOOP_STATE` remains SSOT
- Design spec: [`docs/superpowers/specs/2026-08-21-agents-dashboard-design.md`](docs/superpowers/specs/2026-08-21-agents-dashboard-design.md)
- Tests: `memory/test_dashboard_*.py` (`pytest.importorskip("fastapi")` so the stdlib `memory/` suite stays green)

### Changed
- `VERSION` → 3.8.0
- README CLI table + Dashboard security (`:8112`, token before tunnel)
- `docs/architecture.md` Control Plane row; `memory/README.md` pointer

## [3.7.0] - 2026-08-21

### Added (request proxy policy — wrap host pxpipe)
- `memory/proxy`: config + policy + health (stdlib). `python -m memory.proxy health|install-venv|install-host`
- Fail-closed `proxy.mode=required` for live adapters (`grok` / HTTP). Mock and CI stay proxy-free.
- Explicit opt-out: `AGENTIX_PROXY=0` or `proxy.mode=off`. `preferred` is an escape hatch, not the example-config default.
- `GrokAdapter` probes pxpipe (`127.0.0.1:8100`) before `grok -p`; unhealthy + required → BLOCKED, no silent public upstream.
- Init writes `GROK_CLI_CHAT_PROXY_BASE_URL` into `.venv/bin/activate` (marker `# agentix-proxy`). Does **not** rewrite `~/.grok/config.toml` (opt-in `install-host`).
- Example config `proxy` section; systemd unit template `scripts/systemd/pxpipe.service.example`.
- Tests: `python -m memory.test_proxy` (mode matrix, mock skip, fake TCP). No live pxpipe required.

### Changed
- Existing clones without a `proxy` key are treated as `mode=required` once this code ships; mock adapter still skips the probe. Set `AGENTIX_PROXY=0` if a Grok clone has no pxpipe yet.

### Added (default distillation / knowledge rituals)
- Supervisor `build_role_prompt` injects a bounded knowledge block (top 3, ≤800 tokens) when the SQLite store is seeded; over-budget prompts run the rule compressor (`compress_when_over`).
- Init: `knowledge ingest-if-empty` + `context_budget cold-start --compress`. Reviewer DONE harvest when parent looks like `_PROJECT` is the documented default path.
- Tests: knowledge block when DB seeded; `ingest_if_empty` helper.

### Added (Agentix gateway fronts pxpipe)
- stdlib reverse proxy `python -m memory.proxy serve` on `127.0.0.1:8110` → pxpipe `:8100`. Streaming copy, JSONL audit, exact-hash cache when `AGENTIX_PROJECT_ROOT` / `X-Agentix-Root` is set.
- Fail-closed if pxpipe is down and `mode=required` — no silent public upstream.
- Init venv export now `GROK_CLI_CHAT_PROXY_BASE_URL=http://127.0.0.1:8110/v1`.
- `scripts/agentix-proxy.sh`, `scripts/systemd/agentix-gateway.service.example`.
- Tests: chunked SSE fake upstream, `/v1/responses` round-trip, `/healthz`, header redaction.

### Added (identifier fidelity + knowledge FTS5)
- Gateway extracts SHA/UUID/workspace ids into a native-text `FIDELITY` sidecar before pxpipe imaging. Compressor still does not rewrite source files.
- `memory.knowledge query` uses FTS5 MATCH with LIKE fallback. `sqlite-vec` remains disabled.
- Tests: golden SHA/UUID survive distill.

### Added (token stats, SLOs, consumer path)
- `python -m memory.proxy stats` merges pxpipe `stats --json`, project JSONL, last compressor report.
- CI runs `python -m memory.test_proxy`. Docs: `docs/proxy.md`. VERSION **3.7.0**.
- Optional handoff `proxy_stats`. Raw-token % remains **unslod** until pxpipe `count_tokens` probes > 0 (`measured_saved_pct` is null on this host).

### Changed
- `VERSION` → 3.7.0
- README / ROADMAP / consumer-starter: default live path is gateway `:8110` → pxpipe `:8100`.
- CI: `test_grok_adapter_calls_assert_ready` stubs `shutil.which` / `subprocess.run` so GitHub runners without `grok` on PATH stay green.

### Added (continual-learning export)
- `python -m memory.meta_harvester export-sft` writes `.agent/sft/train.jsonl` (gitignored, no GPU).
- `experience_harvester.maybe_cycle_on_done` runs a dry-run parent harvest after Reviewer DONE when `../` looks like `_PROJECT`.

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
