# PROJECT_CONTEXT.md

> **Source of Truth:** `{{ TASK_SPECIFICATION.md }}`  
> This file is updated by the **Orchestrator** (current status) and the **Reviewer** (self-improvement log).  
> Maximum size: ~3000 tokens. Compress older entries when necessary.  
> All content must be in English.

---

## Project Identification

| Parameter       | Value                                      |
|-----------------|--------------------------------------------|
| **Project**     | `{{ Project Name }}`                       |
| **Goal**        | `{{ Short description of the project goal }}` |
| **Tech Stack**  | `{{ Python 3.11 / Pydantic v2 / SQLAlchemy 2.0 / PostgreSQL 15 / pytest }}` |
| **Current Branch** | `feature-{{ feature-name }}`            |
| **Git User**    | `{{ Real Developer Name }} <{{ email@domain.com }}>` |

---

## Current Status

| Field                  | Value                                      |
|------------------------|--------------------------------------------|
| **Cycle Number**       | `{{ 0 }}`                                  |
| **Current Phase**      | `{{ planning }}`                           |
| **Active Role**        | `{{ Orchestrator }}`                       |
| **Status**             | `IN_PROGRESS`                              |
| **Confidence**         | `{{ 0.0 }}`                                |
| **Last Commit**        | `{{ "" }}`                                 |
| **Last Updated**       | `{{ YYYY-MM-DD HH:MM }}`                   |

---

## Clarification Questions Pool Settings

Пользователь проекта сам задаёт регулярность обработки пула вопросов, требующих уточнения (от владельца продукта / руководителя проекта). Это позволяет петле саморазвития не блокироваться на неясностях, а накапливать их и эскалировать батчами.

**Машинная конфигурация (рекомендуется):**
- Скопируйте `.agent/project_config.example.json` в `.agent/project_config.json`
- Настройте `question_pool.frequency` (every_3_cycles / every_5_cycles / end_of_sprint / end_of_phase / manual) и N/processors.

| Parameter                  | Value / Example                  | Description |
|----------------------------|----------------------------------|-------------|
| **Questions Pool Frequency** | "every_3_cycles"                | Возможные: "every_N_cycles", "end_of_sprint", "end_of_phase", "manual". Задаётся пользователем в project_config.json. |
| **Pool Processors**        | ["product_owner", "project_manager"] | Кто отвечает за обработку пула. |
| **Escalation Method**      | "batch_summary_in_next_handoff" | Как вопросы передаются (в handoff Reviewer'а + .agent/QUESTIONS_POOL.md). |
| **Last Processed At**      | "cycle_12"                      | Когда последний раз пул обрабатывался (questions_pool_config.last_processed_cycle). |

**Процесс (с runnable collector):**
- Orchestrator / другие роли формулируют вопрос и кладут в clarification_questions handoff'а (HANDOFF_SCHEMA.md).
- В конце цикла Reviewer использует collector: `python -m agentic_loop_template.memory.questions_collector sync-handoff --handoff ...` (или append_question).
- Collector пишет .agent/QUESTIONS_POOL.json (машина) + авто-обновляет .agent/QUESTIONS_POOL.md (для владельцев).
- should_escalate / check-escalate по cadence из config.
- После ответов владельцев — mark_reviewed через collector, уроки в LESSONS.md + память.

Полный API и примеры — в agentic_loop_template/memory/questions_collector.py (docstring + CLI).

Это часть механизма для работы в условиях неполной информации без постоянного вмешательства человека.

## Meta Optimizer Settings (v3.x)

Аналогично Clarification Questions Pool, но для самоулучшения *самого процесса разработки* (harness).

**Машинная конфигурация (рекомендуется):**
- Скопируйте `.agent/project_config.example.json` в `.agent/project_config.json`
- Настройте `meta_optimizer.frequency`, `min_quality`, `auto_apply_safe`, `max_proposals_per_cycle`.

| Parameter                  | Value / Example                     | Description |
|----------------------------|-------------------------------------|-------------|
| **Enabled**                | true                                | Включить сбор траекторий и генерацию предложений. |
| **Frequency**              | "after_every_done_cycle"            | Когда запускать harvest: after_every_done_cycle \| every_2_done \| end_of_sprint \| manual. |
| **Min Quality**            | {"confidence": 0.85, "tests_failed": 0} | Пороги для автоматического захвата траектории. |
| **Auto Apply Safe**        | true                                | Автоматически применять только безопасные предложения (few-shot examples, мелкие tips). |
| **Max Proposals per Cycle**| 3                                   | Ограничение на количество генерируемых предложений за цикл. |
| **Last Harvested Cycle**   | 12                                  | Служебное. |

**Процесс:**
- Reviewer на качественном DONE-цикле вызывает `python -m agentic_loop_template.memory.meta_harvester harvest ...`
- Генерируются предложения (см. `.agent/META_PROPOSALS.md`).
- Безопасные могут применяться автоматически или через явный `apply-safe`.
- Ценные паттерны попадают в workspace memory и distillation.
- Полная спецификация формата Trajectory / Proposal — `META_OPTIMIZER_SPEC.md` + `memory/meta_harvester.py`.

Это прямое усиление механизма self-improvement (§3 и §12 DEVELOPMENT_STANDARDS).

## Daily Decomposition Ritual Settings (every 10 cycles, v1.5+)

Пользователь/Reviewer задаёт частоту (по умолчанию every_10_cycles). Ритуал превращает одну приоритетную задачу в 3–5 узких подзадач с binary criteria, риском, mitigation.

| Parameter                  | Value / Example              | Description |
|----------------------------|------------------------------|-------------|
| **Frequency**              | "every_10_cycles"            | every_10_cycles / every_N_cycles / manual. |
| **N**                      | 10                           | Для every_N_cycles. |
| **Last Ritual Cycle**      | 40                           | last_ritual_cycle в .agent/project_config.json. |
| **Enabled**                | true                         | Включить ритуал. |
| **Report Path**            | .agent/daily_rituals/        | Куда писать decomposition_report.json. |

**Процесс**: Reviewer (после meta/questions) проверяет cadence по cycle_number. Выполняет Daily Decomposition Ritual (state discovery из .agent/ + последние 10 циклов lessons + meta trajectories). Выводит таблицу + self-scoring + machine JSON. Записывает отчёт, обновляет handoff + memory.

См. DEVELOPMENT_STANDARDS §13, AGENT_ROLES (Reviewer ritual duty + полный блок ритуала), HANDOFF_SCHEMA (decomposition_ritual field).

## Prompt Refinement Ritual Settings (сразу после decomposition)

| Parameter                  | Value / Example                  | Description |
|----------------------------|----------------------------------|-------------|
| **Trigger**                | Immediately after decomposition  | Lessons → one modular English prompt/tool improvement. |
| **Language of Improvements**| English only                    | Улучшения промптов/инструкций — только английский. |
| **Commits**                | Russian human-style (Working Instructions only) | Естественный язык разработчика, без AI/ритуал упоминаний. |
| **Last Refinement Cycle**  | 40                               | Связан с last_ritual_cycle. |
| **Consumed Marking**       | "consumed_by_prompt2"            | Отмечать исходный decomposition report. |

**Процесс**: После decomposition — выбрать high-priority lesson → Before/After (small context friendly) + rationale + exact diff. Применить, записать в DECISIONS.md, handoff `prompt_refinement`, memory. Self-scoring + machine JSON.

См. DEVELOPMENT_STANDARDS §13 и ритуальные блоки в AGENT_ROLES.md.

---

## Key Files

```
{{ project_root }}/
├── {{ TASK_SPECIFICATION.md }}   ← source of truth
├── PROJECT_CONTEXT.md            ← this file (living memory + self-improvement)
├── SPRINTPLAN.md                 ← current sprint plan
├── AGENT_ROLES.md                ← role instructions
├── HANDOFF_SCHEMA.md             ← role transition contract
├── TOOLS_REGISTRY.md             ← available tools
├── SYSTEM_PROMPT.md              ← main system prompt
├── input/                        ← sample input data
│   ├── example_1.txt
│   └── example_2.txt
├── src/
├── tests/
├── scripts/
│   └── setup_env.ps1
├── pyproject.toml
└── README.md
```

---

## Cycle History (Self-Improvement Log)

| Cycle | Role         | Phase    | Status     | Key Outcomes |
|-------|--------------|----------|------------|--------------|
| 0     | Orchestrator | planning | —          | *(fill after first cycle)* |

---

## Key Decisions & Rationale

> Architectural and process decisions made during development.

- *(to be filled during work)*

---

## Known Limitations and Risks

- *(to be filled in the first cycle)*

---

## Agentic Loop Self-Improvement Log

> Maintained by the **Reviewer** after each completed external cycle.  
> Focus: How effectively the agentic process itself is working.

### Critical Rule — Code Comments and Documentation (Strict)

When **any role** (especially Coder, Debugger, or Reviewer) modifies source code:

- All comments, docstrings, and documentation inside the code **must be written in natural Russian**.
- Write as a real mid/senior human developer who has been actively working on this project for several months.
- Use natural, professional Russian language.
- **Never** mention AI, LLM, agent, MiniMax, Grok, Claude, "neural network", "as an assistant", or any other indication that the changes were generated or assisted by an AI system.
- The same rule applies to git commit messages.

The Reviewer is responsible for enforcing this rule before approving a cycle.

---

## Permanent Rules (Crystallized from Experience)

> These rules must be followed strictly. They are updated by the Reviewer when new lessons are learned.

1. Re-read the full `{{ TASK_SPECIFICATION.md }}` at the beginning of every cycle.
2. Never commit broken code — only working, tested states.
3. Always populate `issues_found` in handoff JSON. Do not skip discovered problems.
4. If confidence is below 0.7 — do not hand off to the next role until uncertainty is resolved.
5. Database roundtrip tests are mandatory starting from the first cycle.
6. На высококачественных DONE-циклах (confidence + тесты чистые) — всегда запускать meta_harvester harvest (см. §12 DEVELOPMENT_STANDARDS).

*(Add new permanent rules as they are discovered — meta proposals часто становятся хорошими кандидатами)*

---

*(The Reviewer appends detailed cycle reviews below after each full loop)*