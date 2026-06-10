# Agentic Loop Memory System

**Workspace-scoped structured memory with counters, automatic deduplication and compaction.**

Inspired by:
- `memory.py` from the `/implement` skill (workspace ID from git remote, locking, compaction)
- Generative Agents (memory stream + reflection)
- Reflexion (verbal self-reflection stored for future use)

The goal: the agentic loop accumulates real "институциональная память" of the project. The Orchestrator starts every cycle knowing the most common failure patterns seen on *this exact repository* (across all clones and worktrees). The Reviewer systematically feeds high-value lessons back into the memory.

---

## Workspace ID (the key property)

The memory file is chosen by a stable identifier derived **only** from the git remote (or fallback to `.git` common dir / cwd).

Example for this repo:
- Remote `https://github.com/.../eeagent.git` or `git@github.com:.../eeagent.git`
- Canonicalized to `github.com/.../eeagent`
- SHA-256 prefix (12 hex) → `eegent-b5ba3fe3655e`
- File: `~/.grok/agentic-loop-memory/eegent-b5ba3fe3655e.md`

**This means**:
- All worktrees of the same clone share the memory.
- The user's `X:\LocalRepo\eeagent` and Grok's `C:\Users\ROOT\.grok\worktrees\...` automatically see the same memory (once the `.md` file exists in the home directory).
- Different remotes (forks, different orgs) get separate memory files — correct isolation.

Run `python -m agentic_loop_template.memory info` (or the PS1) to see the ID and paths for the current workspace.

## Clarification Questions Pool (questions_collector)

New helper for non-blocking operation when external clarifications are needed:

- `python -m agentic_loop_template.memory.questions_collector append --question "..." --context "..." --cycle 12 --source_role orchestrator`
- `python -m agentic_loop_template.memory.questions_collector list`
- `python -m agentic_loop_template.memory.questions_collector check-escalate --cycle 15`
- `python -m agentic_loop_template.memory.questions_collector resolve --ids Q-007 --notes "Answer from PO: use Win10 + Arch only for demo." --by product_owner`
- `python -m agentic_loop_template.memory.questions_collector sync-handoff --handoff .agent/last_handoff.json --cycle 15`

See: DEVELOPMENT_STANDARDS.md §10, .agent/QUESTIONS_POOL.md (human view for PO/PM), project_config.example.json, HANDOFF_SCHEMA.md.

The collector maintains .agent/QUESTIONS_POOL.json (state) + auto .md report. Cadence from project_config.json. Questions survive across cycles and are processed in batches at user-defined frequency (cycles / sprints / phases).

## Meta-Optimizer & Trajectory Harvesting (meta_harvester, v3.x)

Новый слой самоулучшения **самого цикла** (harness / prompts / стандарты / стратегии).

В отличие от обычной памяти (паттерны ошибок проекта) и clarification pool (внешние вопросы), meta_harvester собирает **успешные траектории** (golden trajectories) на высококачественных DONE-циклах и превращает их в конкретные предложения по улучшению:

- Few-shot примеры в PROMPT_COMPRESSION_GUIDE.md
- Постоянные правила и гигиена в DEVELOPMENT_STANDARDS.md
- Использование памяти и эффективные стратегии планирования
- Кандидаты в micro-prompt'ы ролей

**Основные команды (Reviewer / Orchestrator):**

```powershell
# После успешного Reviewer-цикла (если качество позволяет по конфигу)
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory.meta_harvester harvest `
    --handoff .agent/last_handoff.json --cycle 17 --outcome DONE

# Анализ и генерация предложений
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory.meta_harvester analyze --recent 5
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory.meta_harvester propose --limit 3

# Применение безопасных (dry-run по умолчанию)
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run
```

**Хранение:**
- `.agent/TRAJECTORIES.json` (машинный индекс)
- `.agent/META_PROPOSALS.md` (человекочитаемый + список предложений)
- Высококачественные паттерны автоматически попадают в workspace memory (категории "Effective Loop Strategies", "High-Value Compression Patterns", "Meta Improvement Patterns").

**Конфигурация** — в `.agent/project_config.json` (секция `meta_optimizer`), полностью аналогично `question_pool`. См. META_OPTIMIZER_SPEC.md (полный формат траекторий, proposal schema, правила safe_to_auto).

**Обязанности Reviewer (см. AGENT_ROLES.md):**
- При высоком качестве цикла (DONE + confidence + 0 violations) — вызывать harvest.
- Рассматривать свежие meta-предложения перед финальным handoff.
- Переносить полезные паттерны в обычную память и distillation.

Полная спецификация формата, эвристик анализа и roadmap — в `META_OPTIMIZER_SPEC.md` (в корне шаблона).

---

## File Format (human + machine readable)

The on-disk format is Markdown so both the agent and humans can read it directly in the editor or with `cat`.

```markdown
# Agentic Loop Memory

## Common Failure Patterns
- Using bare python instead of venv python.exe (seen 2 times)
- Guessing long site-packages paths instead of calling Get-PythonEnvironmentReport (seen 4 times)

## Testing Strategies
- Always test empty input and None cases first (seen 1 time)

## Recent Distillations

### 2026-05-28 — First integration test
...
```

- Categories are free-form but a recommended starter set lives in `DEVELOPMENT_STANDARDS.md` §9.
- Counts are maintained automatically on deduplicated descriptions.
- Only the last N distillations are kept (compaction).

---

## Usage from the agent (PowerShell tool)

**Always prefer the PS1 wrapper** — it guarantees the correct project `.venv` Python.

```powershell
# 1. At the very start of a cycle (Orchestrator)
$mem = & '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' snapshot | ConvertFrom-Json
$mem.patterns.'Common Failure Patterns'   # review before writing SPRINTPLAN

# Query form (often more convenient)
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' query --top 5 --category 'Common Failure Patterns'

# 2. At the end of a cycle (Reviewer) — after lessons + distillation
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' update `
    -Category 'Windows & PowerShell Gotchas' `
    -Description 'Never use %LOCALAPPDATA% or findstr in agent commands — use Get-PythonEnvironmentReport instead'

# With an attached distillation record
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' update `
    -Category 'Context Hygiene' `
    -Description 'Distillation after cycle 3 reduced active handoff size by 40%' `
    -Date '2026-05-28' `
    -Summary 'Added memory system; first real compounding of experience'
```

Direct Python (when you have already activated the venv or are inside a tool that gives you the full path):

```powershell
& ".\\.venv\\Scripts\\python.exe" -m agentic_loop_template.memory snapshot
& ".\\.venv\\Scripts\\python.exe" -m agentic_loop_template.memory query --top 3
```

---

## Python API (for advanced use or future tools)

```python
from agentic_loop_template.memory import (
    get_workspace_id,
    memory_paths,
    read_memory,
    update_memory,
    snapshot,
    query_memory,
)

wid = get_workspace_id()
print(memory_paths()["file"])

update_memory(
    new_patterns=[{"category": "Testing Strategies", "description": "..."}],
    distillation={"date": "2026-05-28", "summary": "..."}
)

top = query_memory(category="Common Failure Patterns", top_n=5, contains="venv")
```

---

## Compaction Policy (automatic, deterministic)

Inside every `update_memory` (under the lock):

- After merging new patterns, each category is sorted by count (desc) then description.
- Any category exceeding **30** entries has the lowest-count ones dropped.
- `recent_distillations` is trimmed to the last **20** entries.
- The return value from update includes `"compaction": { "categories_capped": N, "patterns_dropped": M, ... }` so the Reviewer can see what was retained.

This prevents unbounded growth while keeping the strongest signals.

---

## Integration Contract with the Agentic Loop

**Mandatory** (enforced by Reviewer):

- **Orchestrator** (beginning of every cycle, right after `Agent-Init.ps1` and reading the spec):
  - Call `snapshot` (or a targeted `query`).
  - Explicitly consider the top patterns when writing `SPRINTPLAN.md` and designing the next steps.
  - Record in the handoff that memory was consulted (`relevant_memory_queried` or just mention in `summary`).

- **Reviewer** (end of cycle, after writing lessons_learned + performing Context Distillation):
  - Extract 1–3 high-value, actionable, specific patterns from the cycle output.
  - Call `update` with them under the appropriate category.
  - Set `memory_updated: true` and `patterns_merged: <count>` in the handoff JSON.
  - If nothing worth persisting was learned, it is valid to merge zero patterns.

See `DEVELOPMENT_STANDARDS.md` §9 and the role definitions in `AGENT_ROLES.md` for the exact wording that must be followed.

---

## Windows Notes & Robustness

- The system is deliberately designed for the primary environment of this template (Windows + PowerShell agent).
- Locking works without any extra packages (pure stdlib fallback using exclusive file creation). Installing `filelock` ( `pip install filelock` ) gives even better diagnostics and is recommended for long-running or multi-process scenarios.
- The PS1 wrapper **never** trusts bare `python` or manual path construction. It mirrors the safety work done in `Agent-Init.ps1`.
- Memory lives in the user's home (`%USERPROFILE%\.grok\agentic-loop-memory\`) — outside any repo or worktree. This is intentional and matches the `/implement` skill.

---

## Seeding from Old Flat Logs

When first enabling the memory system on an existing project that has a rich `SELF_IMPROVEMENT_LOG.md`:

1. Reviewer (or a one-time manual pass) reads the old lessons.
2. Extracts recurring concrete patterns ("Always run Agent-Init before any Python work", "UTF-8 must be explicit on every write", etc.).
3. Calls `update` multiple times (or a small script) to populate the initial memory file.
4. From that point the loop compounds automatically.

---

## Files

- `workspace.py` — ID derivation + path resolution (stable across clones/worktrees)
- `schema.py` — `Pattern` / `MemoryState` dataclasses + markdown parse/render + `normalize`
- `store.py` — `update_memory` (dedup + counters + compaction + atomic write + cross-platform lock), `snapshot`, `query_memory`, `read_memory`
- `Invoke-AgenticMemory.ps1` — the agent-friendly PowerShell surface (safe Python, correct args)
- `__main__.py` / `__init__.py` — CLI and public Python API
- `questions_collector.py` — Clarification Questions Pool (неблокирующий сбор внешних вопросов)
- `meta_harvester.py` — Meta-Optimizer & Trajectory Harvesting (v3.x: сбор успешных траекторий и генерация предложений по улучшению самого harness'а)

All changes to the memory format or behaviour must be reflected here and in `DEVELOPMENT_STANDARDS.md` §9 (and the new §12 for meta).

---

**This is now the single source of truth for recurring project-specific knowledge that the agentic loop must not forget.**

**v3.x addition**: meta_harvester добавляет слой улучшения *самой петли* (траектории успешных циклов → предложения по промптам/правилам/компрессии). 

Дополнительно:
- `seed_example_trajectory()` — для сидинга примера "золотой" траектории.
- `update_performance_ledger()` — простая заглушка сбора метрик (пишет в LOOP_PERFORMANCE.md).
- Ритуалы (v1.5+): каждые 10 циклов Reviewer запускает Daily Decomposition Ritual + Lessons→Prompt Refinement (см. DEVELOPMENT_STANDARDS §13, AGENT_ROLES ritual duty). Используют тот же паттерн (config cadence, .agent/ reports, handoff flags, memory feed). Лёгкая поддержка через существующие collectors + meta_harvester (или отдельный ritual_collector по аналогии).

Полная документация и примеры — META_OPTIMIZER_SPEC.md, .agent/TRAJECTORIES/example-*.json и `python memory/demo_meta.py` (интерактивная демонстрация всего цикла). Ритуалы документированы в DEVELOPMENT_STANDARDS §13 и AGENT_ROLES.md.