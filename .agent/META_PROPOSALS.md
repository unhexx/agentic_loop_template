# META_PROPOSALS.md — Предложения Meta-Optimizer (Trajectory Harvesting)

**Важно:** этот файл поддерживается автоматически. Reviewer может применять безопасные предложения.
Полный формат и API — см. META_OPTIMIZER_SPEC.md и agentic_loop_template/memory/meta_harvester.py

**Статус:** включен | частота: after_every_done_cycle
**Последний harvested cycle:** 0
**Обновлено:** 2026-07-03T01:19:00.061706+00:00

## Последние собранные траектории (golden / высокое качество)
- **T-001-03f8** (cycle 1) — DONE | conf=0.92
- **T-005-03f8** (cycle 5) — DONE | conf=0.92
- **T-010-03f8** (cycle 10) — DONE | conf=0.92
- **T-015-03f8** (cycle 15) — DONE | conf=0.92
- **T-020-03f8** (cycle 20) — DONE | conf=0.92

## Открытые предложения (ожидают применения или отклонения)
(нет открытых — отлично! или все применены)
---
Команды:
  python -m agentic_loop_template.memory.meta_harvester harvest --handoff ... --cycle N
  python -m agentic_loop_template.memory.meta_harvester propose --limit 3
  python -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run

См. также: DEVELOPMENT_STANDARDS.md §12 (Meta-Optimizer), AGENT_ROLES.md (Reviewer duty).