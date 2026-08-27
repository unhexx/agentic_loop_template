# Agentix Public Roadmap

[![Version](https://img.shields.io/badge/version-3.11.2-blue?style=flat-square)](CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](README.md)
[![Docs](https://img.shields.io/badge/docs-available-brightgreen?style=flat-square)](docs/README.md)

**Status Date:** 2026-08-27 · **Initiative:** Business Efficiency — **COMPLETE** · **v3.9 Harness Hardening** — **COMPLETE** · **v3.10 Concurrent fan-out** — **COMPLETE** · **v3.10.1 supervisor caps** — **COMPLETE** · **v3.11 Conflict-free parallel sessions** — **COMPLETE** · **v3.11 NG11 harvester DI** — **COMPLETE** · **v3.11.2 Path 1 docs i18n** — **COMPLETE** · **Next:** Future

---

## Completed (P0–P8)

| Phase | Deliverables |
|-------|--------------|
| P1 Metrics | Performance ledger, [metrics-roi](docs/metrics-roi.md) |
| P2 Cross-Platform | Agent-Init.sh, platform-adaptive prompts |
| P3 Productization | docs/, Hub, [consumer-starter](examples/consumer-starter/) |
| P4 Meta | Playbooks runtime, meta harvester |
| P5 Enterprise | Audit log, policy sample, [GitHub Actions](.github/workflows/agentix-loop.yml) |
| P6 DX | Wizard, [demo-loop.sh](scripts/demo-loop.sh), stack templates |
| P7 Sustain | Resume, eval harness, [case study](docs/case-study.md) |
| P8 Harness Hardening | `pyproject.toml` / `agentix` entry points, logging on critical swallows, schema-backed extract+persist, Init.sh/ps1 cold-start parity, state `agent_dir=` (no bind+chdir), CI pytest + mock cycle on `pull_request`. Spec: [2026-08-24-p8-harness-hardening-design.md](docs/superpowers/specs/2026-08-24-p8-harness-hardening-design.md) |

---

## P8 — done (v3.9.0)

Shipped 2026-08-24. Criteria:

- Consumers install via pip/uv without a PYTHONPATH hack.
- No silent swallow on critical supervisor / adapters / proxy paths.
- Adapter handoffs go through schema + `validate_handoff` + atomic persist.
- Init.ps1 and Init.sh give equivalent cold-start (proxy, knowledge, playbooks).
- CI green on the full mock supervisor cycle.

| ID | Task | Status |
|----|------|--------|
| P8-01 | Packaging (`pyproject.toml`, extras, entry points) | Done |
| P8-02 | Observability (WARNING on critical swallows) | Done |
| P8-03 | JSON extraction + adapter persist | Done |
| P8-04 | Init.sh / Init.ps1 cold-start parity | Done |
| P8-05 | State path DI (`agent_dir=`, no bind+chdir) | Done |
| P8-06 | `validate_handoff` ↔ `schemas/handoff.schema.json` | Done |
| P8-07 | CI pytest + mock O→C→T→R on `pull_request` | Done |

Leftover nice-to-haves (docs i18n, embeddings, …) moved to Future.

---

## Future

- Hosted Agentix Hub SaaS (optional)
- Full MCP skills for Linear/Jira/Slack
- Playbook embeddings ranking (P8-10)
- Modularize large modules (`meta_harvester`, `experience_harvester`, Init.ps1) (P8-12)
- MultiLLM* dataclasses: use or extract (P8-13)
- Operator Messenger (Telegram/MAX)
- Mobile / non-MCP major rewrites (out of scope)

---

## Milestones

| Version | Highlight |
|---------|-----------|
| **v3.11.2** | P8-09: Path 1 Russian siblings (`README.ru.md`, `docs/ru/`) |
| **v3.11.1** | NG11: `agent_dir=` + named locks on meta_harvester / eval_harness / resume |
| **v3.11.0** | Conflict-free parallel sessions: leases, `--push`, STOP fan-out, Streams page, live CLI identity |
| **v3.10.1** | Configurable supervisor context caps from `context_budget` / env (P8-14) |
| **v3.10.0** | Opt-in `run-parallel --concurrent` + stdlib `.agent/` lock (P8-11) |
| **v3.9.4** | Token estimate: tiktoken extra, per-model encoding, chars/4 fallback |
| **v3.9.3** | Harvest/reflect skill split (experience-accumulation vs loop-self-improve) |
| **v3.9.2** | Blackbox AI CLI adapter hardening (WM collision, hermetic tests, probe) |
| **v3.9.1** | Opt-in `git-commit-to-jira-tasks` skill (disabled by default; never `--intent git`) |
| **v3.9.0** | P8 Harness Hardening — packaging, observability, extraction/validation, init parity, state DI, CI mock cycle |
| **v3.8.1** | Parallel streams `run-parallel` on 3.8 line; README badges (CI, pxpipe default) |
| **v3.8.0** | Operator Control Plane (`memory.dashboard` HTMX sidecar on `:8112`), not the runner |
| **v3.7.0** | Default request proxy (Agentix gateway `:8110` fronts host pxpipe), fidelity sidecar, FTS5, honest token SLOs |
| **v3.6.0** | Skills + rule compressor + knowledge store + cross-project experience harvest (`audit`/`cycle`) |
| **v3.5.0** | Supervisor CLI, multi-frontend adapters, mock CI cycle |
| **v3.4.0** | P5–P7 complete, initiative closed |
| **v3.3.0** | docs/, Hub, Pro tier |
| **v3.2** | Meta + MCP/vision/isolation |

---

Contributions via the [loop process](README.md#contributing) or [GitHub issues](https://github.com/unhexx/agentic_loop_template/issues). Maintained by **exception.expert**.
