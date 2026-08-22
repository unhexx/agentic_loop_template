# Agentix Public Roadmap

[![Version](https://img.shields.io/badge/version-3.7.0-blue?style=flat-square)](CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](README.md)
[![Docs](https://img.shields.io/badge/docs-available-brightgreen?style=flat-square)](docs/README.md)

**Status Date:** 2026-08-22 · **Initiative:** Business Efficiency — **COMPLETE** · **Next:** P8 Harness Hardening

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

## P8 — Harness Hardening / Технический долг (активный план)

Главные точки роста (приоритет по обзору 2026-08-22):

1. **Packaging** — нормальная упаковка для consumers
2. **Observability** — убрать silent exception swallowing
3. **Устойчивость extraction / валидации** — надёжный разбор JSON handoff и строгая schema-проверка
4. **Снижение platform drift** — синхронизация Windows (ps1) и Unix (sh) bootstrap

### Высокий приоритет

| ID | Задача | Детали |
|----|--------|--------|
| P8-01 | Packaging | Добавить `pyproject.toml`, pinned зависимости, entry points, понятный package layout (`python -m memory` / `agentix` без хака PYTHONPATH). Init-скрипты сейчас ставят pyyaml/pytest/jsonschema ad-hoc. |
| P8-02 | Observability | Заменить широкие `except Exception: pass` / silent fallback (knowledge inject, compress, path rebinding, config load, proxy) на logging + конкретные исключения. Повысить наблюдаемость циклов. |
| P8-03 | JSON extraction | Усилить `extract_json_object` в адаптерах (Grok и др.): balanced braces / несколько кандидатов + обязательная `validate_handoff` после извлечения. Убрать хрупкий greedy regex как единственный fallback. |
| P8-04 | Platform drift | Синхронизировать `Agent-Init.ps1` (~34 KB) и `Agent-Init.sh`. Либо единый SSOT + генерация, либо явный checklist паритета фич (wizard, proxy export, knowledge ingest-if-empty). |

### Средний приоритет

| ID | Задача | Детали |
|----|--------|--------|
| P8-05 | State path DI | Убрать `_bind_state_paths` + `chdir` + мутацию module-level путей в supervisor. Dependency injection / явный context — безопаснее для тестов и параллельных worktree. |
| P8-06 | Validator ↔ schema | Держать `validate_handoff` в синхроне с `schemas/handoff.schema.json` либо перейти на `jsonschema` + schema file. |
| P8-07 | CI integration | Полный supervisor mock-cycle (O→C→T→R → PR_READY) в GitHub Actions, не только точечные `test_*` модули. |
| P8-08 | Token estimate | Улучшить эвристику chars/4 (или опциональный tiktoken/path) — сейчас может плыть между моделями. |
| P8-09 | Docs i18n | Чётче разделить RU/EN (или dual-language) в доках и примерах. Commit style «натуральный русский» остаётся процессом, публичные guides — понятнее для международной аудитории. |

### Низкий приоритет / nice-to-have

| ID | Задача | Детали |
|----|--------|--------|
| P8-10 | Playbook ranking | Keyword scoring → опциональные embeddings при росте базы playbooks. |
| P8-11 | Concurrency | Явная story для shared `.agent/` state (PARALLEL_PROTOCOL уже есть; supervisor пока sequential). |
| P8-12 | Modularize | Разбить крупные модули (`meta_harvester`, `experience_harvester`, `Agent-Init.ps1`) без ломки CLI. |
| P8-13 | MultiLLM schema | MultiLLM* dataclasses в `schema.py` — либо использовать, либо вынести в отдельный experimental слой. |
| P8-14 | Config budgets | Больше конфигурируемых budget’ов вместо module-level констант (`_PROMPT_BODY_CAP`, `_KNOWLEDGE_BUDGET` и т.п.). |

### Критерии готовности P8

- Consumers могут установить шаблон через pip/uv без ручного PYTHONPATH.
- Нет silent swallow критичных путей supervisor / adapters / proxy.
- Любой handoff из адаптера проходит schema + validate_handoff.
- Init.ps1 и Init.sh дают эквивалентный cold-start (proxy, knowledge, playbooks).
- CI зелёный на полном mock-цикле supervisor.

Целевая версия после закрытия блока: **v3.8.0**.

---

## Future (после P8)

- Hosted Agentix Hub SaaS (optional)
- Full MCP skills for Linear/Jira/Slack
- Mobile / non-MCP major rewrites (out of scope)

---

## Milestones

| Version | Highlight |
|---------|-----------|
| **v3.8.0** (plan) | P8 Harness Hardening — packaging, observability, extraction/validation, platform parity |
| **v3.7.0** | Default request proxy (Agentix gateway `:8110` fronts host pxpipe), fidelity sidecar, FTS5, honest token SLOs |
| **v3.6.0** | Skills + rule compressor + knowledge store + cross-project experience harvest (`audit`/`cycle`) |
| **v3.5.0** | Supervisor CLI, multi-frontend adapters, mock CI cycle |
| **v3.4.0** | P5–P7 complete, initiative closed |
| **v3.3.0** | docs/, Hub, Pro tier |
| **v3.2** | Meta + MCP/vision/isolation |

---

Contributions via the [loop process](README.md#contributing) or [GitHub issues](https://github.com/unhexx/agentic_loop_template/issues). Maintained by **exception.expert**.
