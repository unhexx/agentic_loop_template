# Документация Agentix

**Language:** [English](../README.md) · [Русский](README.md)

[![Version](https://img.shields.io/badge/version-3.12.0-blue?style=flat-square)](../../CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../../README.ru.md)

Каноническая документация шаблона агентного цикла разработки Agentix.

---

## Учебные маршруты

### Path 1 — Первый запуск (15 мин)

1. [Начало работы](getting-started.md) — bootstrap и первое сообщение
2. [Multi-Frontend](../multi-frontend.md) (English) — Cursor / Claude / Blackbox
3. [Architecture](../architecture.md) (English) — роли и handoff

### Path 2 — Внедрение (30 мин)

1. [../examples/consumer-starter/README.md](../../examples/consumer-starter/README.md) (English)
2. [Onboarding Wizard](../onboarding-wizard.md) (English) — `Agent-Init.sh --wizard`
3. [Cross-Platform](../cross-platform.md) (English) — пути и venv

### Path 3 — Эксплуатация (45 мин)

1. [Metrics & ROI](../metrics-roi.md) (English) — доказательства из ledger
2. [Hub](../hub/README.md) (English) — экспорт и поиск playbook
3. [Enterprise Governance](../enterprise-governance.md) (English) — audit + policy
4. [Integrations](../integrations.md) (English) — GitHub Actions, трекеры, Slack

### Path 4 — Доказательства (20 мин)

1. [Case Study](../case-study.md) (English) — 50+ циклов dogfood
2. [../examples/case-study/sanitized-summary.md](../../examples/case-study/sanitized-summary.md) (English)

Публичные страницы Path 1 (`README.md`, `docs/getting-started.md`, этот индекс) имеют русские соседние файлы (`README.ru.md`, `docs/ru/…`). При правке английского текста меняйте оба.

---

## Справка

| Документ | Описание |
|----------|----------|
| [Начало работы](getting-started.md) | Bootstrap за 5 минут |
| [Cross-Platform](../cross-platform.md) (English) | Windows / Linux / macOS |
| [Multi-Frontend](../multi-frontend.md) (English) | Адаптеры UI агента |
| [Architecture](../architecture.md) (English) | Цикл, память, handoff |
| [Metrics & ROI](../metrics-roi.md) (English) | Доказательства из performance ledger |
| [Request proxy](../proxy.md) (English) | Шлюз pxpipe по умолчанию, SLO, отказ, опциональный второй инстанс agy |
| [Hub](../hub/README.md) (English) | Основа marketplace |
| [Hub Discovery](../hub/discovery.md) (English) | Установка playbook |
| [Hub API Schema](../hub/api-schema.json) (English) | JSON-схема для веба |
| [Pro Tier](../pro-tier.md) (English) | Матрица Free vs Pro |
| [Enterprise Governance](../enterprise-governance.md) (English) | Policy, audit, согласования |
| [Integrations](../integrations.md) (English) | CI, Linear/Jira, Slack |
| [Onboarding Wizard](../onboarding-wizard.md) (English) | Интерактивная настройка |
| [Case Study](../case-study.md) (English) | Результаты dogfood |

---

## Основные спецификации (корень репозитория)

| Файл | Назначение |
|------|------------|
| [../../HANDOFF_SCHEMA.md](../../HANDOFF_SCHEMA.md) (English) | JSON-контракт handoff |
| [../../AGENT_ROLES.md](../../AGENT_ROLES.md) (English) | Инструкции по ролям |
| [../../DEVELOPMENT_STANDARDS.md](../../DEVELOPMENT_STANDARDS.md) (English) | Процессная конституция |
| [../../SYSTEM_PROMPT.md](../../SYSTEM_PROMPT.md) (English) | Мастер-промпт |
| [../../memory/README.md](../../memory/README.md) (English) | API слоя памяти |
| [../../META_OPTIMIZER_SPEC.md](../../META_OPTIMIZER_SPEC.md) (English) | Спецификация meta-optimizer |
| [../../ROADMAP.md](../../ROADMAP.md) (English) | Публичный roadmap |
| [../../CHANGELOG.md](../../CHANGELOG.md) (English) | История релизов |

---

## Версия

Согласовано с **Agentix 3.12.0** (2026-08-27). P8-10 опциональный рейтинг playbooks эмбеддингами (`playbooks.relevance=embed`). Онтология MultiLLM в `memory/llm_ontology.py` (P8-13). Русские соседние файлы Path 1. NG11 `agent_dir=` на harvest/eval/resume. Конфликт-free параллельные сессии: `stream_lease`, `run-parallel --push`, STOP fan-out, страница Streams в Control Plane, живая CLI-идентичность + persist stamp, оставшиеся локи `.agent/`, hub-safe merge интеграций. Лимиты промпта супервизора из `context_budget` / env (P8-14). Опциональный `run-parallel --concurrent` и stdlib-лок `.agent/` (P8-11). Разделение навыков harvest/reflect (`experience-accumulation` vs `loop-self-improve`). Укрепление адаптера Blackbox AI CLI. Опциональный навык `git-commit-to-jira-tasks` (только явная загрузка). P8 Harness Hardening закрыт. Business Efficiency Initiative (P0–P7) закрыта. Control Plane (`memory.dashboard`) на loopback `:8112`. Живой Grok по умолчанию через pxpipe. Параллельные потоки: `python -m memory.supervisor run-parallel` (по умолчанию serial).
