# META_PROPOSALS.md — Предложения Meta-Optimizer (Trajectory Harvesting)

**Важно:** этот файл поддерживается автоматически. Reviewer может применять безопасные предложения.
Полный формат и API — см. META_OPTIMIZER_SPEC.md и agentic_loop_template/memory/meta_harvester.py

**Статус:** включен | частота: after_every_done_cycle
**Последний harvested cycle:** 0
**Обновлено:** 2026-07-03T01:50:48.554270+00:00

## Последние собранные траектории (golden / высокое качество)
- **T-058-03f8** (cycle 58) — DONE | conf=0.92
- **T-059-03f8** (cycle 59) — DONE | conf=0.92
- **T-060-03f8** (cycle 60) — DONE | conf=0.92
- **T-061-03f8** (cycle 61) — DONE | conf=0.92
- **T-062-03f8** (cycle 62) — DONE | conf=0.92

## Открытые предложения (ожидают применения или отклонения)
### P-006 → agentic_loop_template/PROMPT_COMPRESSION_GUIDE.md
**Тип:** add_few_shot_example | safe_auto=True | conf=0.85
**Обоснование:** В 3 циклах успех коррелировал с явным включением performance metrics (elapsed, tool_calls, confidence, meta_applied) + success_patterns в сжатые handoff'ы. Позволяет лучше отслеживать ROI и компрессию
**Действие:** Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

---
Команды:
  python -m agentic_loop_template.memory.meta_harvester harvest --handoff ... --cycle N
  python -m agentic_loop_template.memory.meta_harvester propose --limit 3
  python -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run

См. также: DEVELOPMENT_STANDARDS.md §12 (Meta-Optimizer), AGENT_ROLES.md (Reviewer duty).