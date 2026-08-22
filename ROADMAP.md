# Agentix Public Roadmap

[![Version](https://img.shields.io/badge/version-3.7.0-blue?style=flat-square)](CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](README.md)
[![Docs](https://img.shields.io/badge/docs-available-brightgreen?style=flat-square)](docs/README.md)

**Status Date:** 2026-08-22 · **Initiative:** Business Efficiency — **COMPLETE** · **v3.7 request proxy** · **Next: P8 Hardening**

---

## Completed (P0–P7)

| Phase | Deliverables |
|-------|--------------|
| P1 Metrics | Performance ledger, [metrics-roi](docs/metrics-roi.md) |
| P2 Cross-Platform | Agent-Init.sh, platform-adaptive prompts |
| P3 Productization | docs/, Hub, [consumer-starter](examples/consumer-starter/) |
| P4 Meta | Playbooks runtime, meta harvester |
| P5 Enterprise | Audit log, policy sample, [GitHub Actions](.github/workflows/agentix-loop.yml) |
| P6 DX | Wizard, [demo-loop.sh](scripts/demo-loop.sh), stack templates |
| P7 Sustain | Resume, eval harness, [case study](docs/case-study.md) |

---

## P8 — Harness Hardening & Tech Debt (Active)

Главные точки роста (code review 2026-08-22) + полный технический долг.
Цель: packaging, observability, устойчивость extraction/валидации, снижение platform drift.

### High priority

- **Packaging**: добавить `pyproject.toml`, pinned dependencies, entry points, нормальный package layout (src/ или agentix), reproducible install для consumers
- **Observability**: заменить bare `except Exception: pass` / silent fallbacks на logging + конкретные исключения (supervisor, knowledge, compress, proxy config, path rebinding)
- **Extraction / validation resilience**: усилить JSON extraction в адаптерах (Grok и др.); всегда валидировать извлечённый handoff через schema; держать `validate_handoff` в синхронизации со `schemas/handoff.schema.json` (или перейти на jsonschema)
- **Platform drift reduction**: выровнять `Agent-Init.ps1` ↔ `Agent-Init.sh` (parity / генерация из одного источника), уменьшить размер PS1

### Medium

- Dependency injection для state paths вместо module-level mutation + `chdir` в supervisor
- Полный integration-тест mock supervisor cycle в CI (сейчас точечные unit)
- Улучшение token estimator (model-aware / точнее chars/4)
- Dual-language docs clarity или EN-first + явные RU conventions
- Обновить living `.agent/PLAN.md` / `TODO.md` / `SPRINTPLAN.md` под новую инициативу

### Lower / nice-to-have

- Concurrency story для shared `.agent/` state (если parallel roles расширятся)
- Modularize крупные модули (`meta_harvester`, `experience_harvester`, `Agent-Init.ps1`)
- Optional embeddings для playbook ranking при росте базы
- Cleanup leftover feature branches после подтверждения, что уникальный контент отсутствует

### Success criteria

- Consumers могут `pip install -e .` / `uv sync` без ad-hoc pip в Init
- Логи показывают реальные ошибки вместо silent fail
- Adapter extraction + validation robust на malformed LLM output
- Init scripts не расходятся по фичам
- CI гоняет full mock O→C→T→R cycle

Target: **v3.8.0** после закрытия High items.

---

## Future (Post-Hardening)

- Hosted Agentix Hub SaaS (optional)
- Full MCP skills for Linear/Jira/Slack
- Mobile / non-MCP major rewrites (out of scope)

---

## Milestones

| Version | Highlight |
|---------|-----------|
| **v3.8.0** (planned) | P8 Hardening: packaging + observability + extraction robustness + platform parity |
| **v3.7.0** | Default request proxy (Agentix gateway `:8110` fronts host pxpipe), fidelity sidecar, FTS5, honest token SLOs |
| **v3.6.0** | Skills + rule compressor + knowledge store + cross-project experience harvest (`audit`/`cycle`) |
| **v3.5.0** | Supervisor CLI, multi-frontend adapters, mock CI cycle |
| **v3.4.0** | P5–P7 complete, initiative closed |
| **v3.3.0** | docs/, Hub, Pro tier |
| **v3.2** | Meta + MCP/vision/isolation |

---

Contributions via the [loop process](README.md#contributing) or [GitHub issues](https://github.com/unhexx/agentic_loop_template/issues). Maintained by **exception.expert**.
