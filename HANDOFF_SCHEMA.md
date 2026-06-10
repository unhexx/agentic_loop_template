# HANDOFF SCHEMA — Control Transfer Between Roles

> Every agent message **must** end with **exactly one JSON object**.  
> Nothing after the JSON.  
> All fields are required unless marked `(optional)`.  
> Empty arrays `[]` and empty strings `""` are valid — never omit fields.

---

## Full Schema (aligned with SYSTEM_PROMPT 2.1)

```json
{
  "handoff_to": "Coder",
  // Allowed: "Orchestrator" | "Coder" | "Tester" | "Debugger" | "Reviewer" | "None"
  // Use "None" only when status = "DONE"

  "role": "Orchestrator",
  // Current role: "Orchestrator" | "Coder" | "Tester" | "Debugger" | "Reviewer"

  "current_phase": "planning",
  // "planning" | "implementation" | "testing" | "debugging" | "review" | "finalization"

  "cycle_number": 0,
  // Incremented by Reviewer at the start of each new cycle.

  "summary": "Very compact summary of what was done this step (1-2 sentences max). Focus on outcome and key decisions.",

  "context_delta": "Key new facts, decisions, or changes since last handoff (keep very short). Use this for incremental updates instead of full history.",
  // Optional but strongly recommended for context efficiency.

  "distillation_performed": false,
  // Set to true if the Reviewer performed a Context Distillation step in this cycle and appended it to memory.
  // This signals to future roles that compact long-term memory is available.

  "memory_updated": false,
  // Set to true if the Reviewer extracted and merged patterns into the Workspace-Scoped Structured Memory (see DEVELOPMENT_STANDARDS.md §9).
  "patterns_merged": 0,
  // Number of new/updated patterns written to memory in this cycle (0 is valid).

  "context_updates": ["PROJECT_CONTEXT.md", "SPRINTPLAN.md"],
  // Files that were created or significantly updated in this step.

  "artifacts": ["src/parser.py", "src/models.py"],
  // Important new or modified files/directories for the next role.

  "next_input_files": [
    "{{ SPEC_FILE }}",
    "PROJECT_CONTEXT.md",
    "SPRINTPLAN.md",
    "DEVELOPMENT_STANDARDS.md"
  ],
  // Files the next role MUST read before starting work. Prefer minimal relevant set.

  "git_branch": "feature-{{ FEATURE_NAME }}",

  "last_commit": "Реализовал базовый парсер и добавил тесты на нормализацию",
  // Last commit message (in Russian). Empty string if no commits were made.

  "confidence": 0.9,
  // 0.0–1.0. Below 0.7 usually means the handoff should be reconsidered.

  "status": "IN_PROGRESS",
  // "IN_PROGRESS" | "BLOCKED" | "DONE"

  "git_final": "",
  // Заполняется только Reviewer при status = "DONE".
  // Короткая заметка о финальном merge в main.

  "git_sync_status": {
    "feature_pushed": true,
    "main_merged_commit": "a3bd062..",
    "clones_synced": [
      "C:/_PROJECT/eegent:main@7679335",
      "current_worktree:feature@c159692"
    ],
    "verified": true,
    "timestamp": "2026-06-04T..",
    "commands_run": ["git push origin feature-...", "git -C 'C:\\_PROJECT\\eegent' merge ... --no-ff", "powershell ...sync-worktree.ps1", "git log checks in both"]
  },
  // Обязательно заполняется Orchestrator в начале каждого цикла (перед планированием следующего).
  // Доказательство, что коммиты сделаны, self-cycle выполнен, изменения видны во всех активных репозиториях (основной клон + worktree).
  // См. DEVELOPMENT_STANDARDS.md §11. Если verified=false или отсутствует — следующий цикл может быть заблокирован Reviewer'ом.

  "metrics": {
    "tests_total": 12,
    "tests_failed": 3,
    "coverage": 67.4,
    "tool_calls": 5,
    "elapsed_minutes": 14.5
  },

  "issues_found": [
    {
      "type": "env_setup",
      "location": "scripts/setup_env.ps1",
      "pattern": "Venv not activated before running tests",
      "frequency": 2
    }
  ],

  "process_tags": ["env_setup_missing_checks"],
  // Recurring process problems. Examples: "too_many_small_commits", "spec_not_reread", "architecture_skipped", "english_comments_violation"

  "feedback_from_previous": {
    "what_worked_well": ["Good test coverage on normalization"],
    "what_needs_improvement": ["Missing error handling in parser"],
    "suggestions": ["Add retry logic for flaky network calls"]
  },

  "lessons_learned": [
    "Always run setup_env.ps1 at the beginning of a new cycle"
  ],

  "clarification_questions": [
    {
      "id": "Q-001",
      "question": "Нужен точный список целевых платформ и версий ОС для поддержки изоляции (Windows 10/11, Ubuntu 24.04, Arch).",
      "context": "Требуется для обновления VM_VERIFICATION_*.md и TEST_PLAN.md. Без этого не можем завершить верификацию установщиков.",
      "priority": "high",
      "source_role": "orchestrator",
      "created_cycle": 7,
      "created_sprint": "sprint-3",
      "suggested_recipient": "product_owner",
      "suggested_phase": "end_of_phase"
    }
  ],
  // Пул вопросов, требующих уточнения от владельца продукта / руководителя проекта.
  // Заполняется Orchestrator / Reviewer / другие роли, когда для продолжения нужна внешняя информация.
  // Не блокирует цикл — вопросы накапливаются collector'ом (questions_collector.py) и обрабатываются батчами по расписанию пользователя (project_config.json).
  // После handoff Reviewer делает sync-handoff. См. DEVELOPMENT_STANDARDS.md §10, questions_collector.py, PROJECT_CONTEXT_TEMPLATE.md.

  "meta_harvest": {
    "performed": true,
    "trajectories_captured": 1,
    "proposals_generated": 1,
    "proposals_auto_applied": 0,
    "notes": "Harvested successful sync-worktree trajectory; one compression pattern proposed"
  },
  // Meta-Optimizer trajectory harvesting (v3.x). Заполняется Reviewer на качественных DONE циклах.
  // Используйте memory.meta_harvester. См. DEVELOPMENT_STANDARDS §12 и META_OPTIMIZER_SPEC.md.

  "questions_pool_config": {
    "frequency": "every_3_cycles",
    // Возможные значения: "every_N_cycles", "end_of_sprint", "end_of_phase", "manual"
    // Пользователь задаёт в .agent/project_config.json (секция question_pool). См. project_config.example.json
    "last_processed_cycle": 5,
    "processors": ["product_owner", "project_manager"]
  }
  // Конфиг частоты обработки пула (каденс). Обновляется Reviewer при эскалации / sync. Используйте questions_collector для актуализации.

  "meta_harvest": {
    "performed": true,
    "trajectories_captured": 1,
    "proposals_generated": 2,
    "proposals_auto_applied": 1,
    "notes": "Harvested golden trajectory for sync-worktree; added compression few-shot example"
  }
  // Заполняется Reviewer при высококачественном DONE (см. DEVELOPMENT_STANDARDS.md §12 и META_OPTIMIZER_SPEC.md).
  // Используйте python -m agentic_loop_template.memory.meta_harvester harvest ...
  // Опционально, но обязательно на циклах с высоким качеством. Backward-compatible.

  "decomposition_ritual": {
    "performed": true,
    "report_path": ".agent/daily_rituals/prompt1_decomposition_report.json",
    "subtasks": 4,
    "chosen_task_id": "...",
    "chosen_task_title": "...",
    "checklist_passed": true,
    "self_scoring_total": 43
  },
  // Daily Decomposition Ritual (every 10 cycles). Заполняется Reviewer при срабатывании cadence (см. DEVELOPMENT_STANDARDS §13).
  // Используйте ritual role block (Meta-Orchestrator eeagent v1.5 M2.7). Отчёт + machine JSON обязателен.

  "prompt_refinement": {
    "performed": true,
    "refinements_applied": 1,
    "source_report": ".agent/daily_rituals/prompt1_decomposition_report.json",
    "target_files": ["AGENT_ROLES.md", "PROMPT_COMPRESSION_GUIDE.md"],
    "consumed": true
  }
  // Lessons → Prompt Refinement (сразу после decomposition). Заполняется Reviewer.
  // Улучшения только на английском, small-context friendly. Refinements применяются через search_replace + Russian human commit.
  // См. DEVELOPMENT_STANDARDS §13 и ритуальные блоки в AGENT_ROLES.md.
}
```
      "Быстрая реализация моделей",
      "Чёткое разделение нормализаторов"
    ],
    "what_failed_or_was_inefficient": [
      "Скелет тестов был слишком поверхностным"
    ],
    "suggestions_for_next_agent": [
      "Уделить особое внимание edge-кейсу: пустые поля в секции documents",
      "Проверить поведение при UTF-8 BOM в начале файла"
    ]
  },

  // ─── УРОКИ ЭТОГО ШАГА ──────────────────────────────────────────────────
  "lessons_learned": [
    "Всегда запускать PowerShell-скрипт подготовки окружения перед оценкой статуса.",
    "Читать TASK_SPECIFICATION.md заново перед каждым циклом, не полагаться на кэш."
  ],
  // Кандидаты в постоянные правила следующего цикла.

  "inner_loop_summary": "Запланировал 5 шагов, выполнил 3 tool calls, отрефлексировал после каждой тройки — обнаружил проблему с venv, исправил в плане.",
  // Краткое описание поведения PLAN → ACT → REFLECT в этом шаге.

  // ─── ФЛАГИ ─────────────────────────────────────────────────────────────
  "requires_architecture_review": false,
  // true = обнаружены архитектурные проблемы, требующие переработки.
  // При true: Reviewer должен запустить новый цикл с architecture review фазой.

  // ─── ФИНАЛЬНОЕ СОСТОЯНИЕ ───────────────────────────────────────────────
  "status": "IN_PROGRESS",
  // "IN_PROGRESS" = цикл продолжается
  // "DONE" = Reviewer подтвердил 100% соответствие спецификации

  "git_final": ""
  // Заполняется только Reviewer при status = "DONE".
  // Короткая заметка о финальном merge в main.
}
```

---

## Правила валидации JSON

### Критические требования
1. Ровно **один** JSON-объект в конце сообщения.
2. **Никакого текста** после закрывающей `}`.
3. Все обязательные поля присутствуют — пустые значения (`""`, `[]`, `0`, `false`) допустимы.
4. `handoff_to` содержит только допустимые значения.
5. `cycle_number` ≥ 0, не убывает.
6. `confidence` в диапазоне `[0.0, 1.0]`.
7. `status` = `"DONE"` только при `handoff_to` = `"None"`.

### Типичные ошибки
| Ошибка | Правильно |
|--------|-----------|
| Отсутствует поле `metrics` | Добавить с нулевыми значениями |
| `issues_found: null` | `issues_found: []` |
| `last_commit` содержит слово "агент" | Переформулировать по-человечески |
| `confidence: 1.0` при незакрытых issues | Снизить до ≤ 0.8 |
| Текст после JSON | Удалить всё после `}` |

---

## Матрица передачи управления

| От / До | Orchestrator | Coder | Tester | Debugger | Reviewer | None |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|
| **Orchestrator** | ◇ (если нужен ещё шаг) | ✓ (обычный) | — | — | — | — |
| **Coder** | — | ◇ (если модуль не завершён) | ✓ (обычный) | — | — | — |
| **Tester** | — | — | ◇ (если тесты не написаны) | ✓ (обычный) | — | — |
| **Debugger** | — | — | ◇ (если нужно переписать тесты) | — | ✓ (обычный) | — |
| **Reviewer** | ✓ (если NOT DONE) | — | — | — | — | ✓ (если DONE) |

✓ = основной маршрут  ◇ = условный (с обоснованием в summary)  — = недопустимо

---

## Примеры быстрых JSON-блоков

### Orchestrator → Coder (старт)
```json
{
  "handoff_to": "Coder", "role": "Orchestrator", "current_phase": "planning",
  "cycle_number": 0, "summary": "Подготовил окружение, создал SPRINTPLAN.md с 5 фазами.",
  "context_updates": ["PROJECT_CONTEXT.md", "SPRINTPLAN.md"],
  "artifacts": ["SPRINTPLAN.md", "PROJECT_CONTEXT.md"],
  "next_input_files": ["TASK_SPECIFICATION.md", "PROJECT_CONTEXT.md", "SPRINTPLAN.md"],
  "git_branch": "feature-parser-impl", "last_commit": "Добавил план спринта и обновил контекст",
  "confidence": 0.92, "metrics": {"tests_total":0,"tests_failed":0,"coverage":0.0,"tool_calls":5,"elapsed_minutes":8},
  "issues_found": [], "process_tags": [], "feedback_from_previous": {"what_worked_well":[],"what_failed_or_was_inefficient":[],"suggestions_for_next_agent":[]},
  "lessons_learned": ["Читать спецификацию заново перед каждым циклом."],
  "inner_loop_summary": "Запланировал 4 шага, выполнил, нашёл пропущенный venv — исправил в скрипте.",
  "requires_architecture_review": false, "status": "IN_PROGRESS", "git_final": ""
}
```

### Reviewer → None (DONE)
```json
{
  "handoff_to": "None", "role": "Reviewer", "current_phase": "finalization",
  "cycle_number": 2, "summary": "Все 48 тестов прошли, покрытие 94%, спецификация выполнена полностью.",
  "context_updates": ["PROJECT_CONTEXT.md", "README.md", "USAGE.md"],
  "artifacts": ["src/", "tests/", "migrations/", "README.md", "USAGE.md", "pyproject.toml"],
  "next_input_files": [], "git_branch": "main", "last_commit": "Финализировал документацию и смержил в main",
  "confidence": 0.98, "metrics": {"tests_total":48,"tests_failed":0,"coverage":94.2,"tool_calls":6,"elapsed_minutes":12},
  "issues_found": [], "process_tags": [],
  "feedback_from_previous": {"what_worked_well":["Debugger быстро закрыл все edge-кейсы"],"what_failed_or_was_inefficient":[],"suggestions_for_next_agent":[]},
  "lessons_learned": ["DB roundtrip тест должен быть в чеклисте с первого цикла."],
  "inner_loop_summary": "Прошёл по всему чеклисту, запустил smoke tests, смержил ветку.",
  "requires_architecture_review": false, "status": "DONE",
  "git_final": "Ветка feature-parser-impl смержена в main, тег v1.0.0 проставлен."
}
```
