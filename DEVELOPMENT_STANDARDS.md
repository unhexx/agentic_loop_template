# DEVELOPMENT STANDARDS

This document defines the non-negotiable standards for all work performed in this project, especially when using the agentic development loop.

All roles (Orchestrator, Coder, Tester, Debugger, Reviewer) must follow these rules without exception.

---

## 1. Language of Code, Comments, and Commits (Strict Rule)

**This is the most important rule in the entire agentic loop.**

When writing or modifying any source code:

- **All comments, docstrings, module-level documentation, and inline explanations must be written in natural Russian.**
- The style must be that of a real mid/senior human developer who has been actively working on this project for several months.
- Use professional, natural Russian that a real developer would actually write.
- **Absolutely forbidden**:
  - Writing comments in English
  - Using AI-typical phrasing ("As an AI language model...", "Here is the implementation...", "This function does the following...")
  - Any mention of AI, LLM, agent, MiniMax, Grok, Claude, neural network, "assisted by", or similar

**The same rule applies to git commit messages:**
- Every commit message must be written in natural Russian.
- It must sound like it was written by a real developer who understands the codebase.
- Never mention the agentic loop, AI, or any model in commit messages.

**Enforcement:**
- The **Reviewer** is responsible for catching violations.
- If non-compliant comments or commit messages are found, the Reviewer must reject the handoff and send the work back for correction.
- Repeated violations must be recorded in `SELF_IMPROVEMENT_LOG.md`.

---

## 2. Code Quality Standards

- All code must be production-grade: proper typing, error handling, logging, and meaningful tests.
- No stubs or TODO comments that block the next role.
- Prefer small, well-tested, incremental changes.
- Every meaningful change must be committed before handing off to the next role.

---

## 3. Self-Improvement Discipline

- The agentic loop exists to improve both the product **and** the development process itself.
- After every full cycle, the Reviewer must update:
  - `PROJECT_CONTEXT.md` (project state + decisions)
  - `SPRINTPLAN.md` (progress and next tasks)
  - `SELF_IMPROVEMENT_LOG.md` (what the loop learned about itself)
- Lessons about prompt effectiveness, role performance, and common failure patterns must be recorded.

---

## 4. Environment and Tooling Rules

- All Python work must happen inside the local `.venv`.
- Use the provided scripts (`scripts/setup.ps1`, `agentic_loop_template/setup_env.ps1`, etc.).
- Never run Python commands using the system `python` when a virtual environment is available.

---

## 5. Handoff and Process Discipline

- Always follow the exact JSON handoff schema defined in `HANDOFF_SCHEMA.md`.
- Never skip the PLAN → ACT → REFLECT pattern inside a role.
- The Reviewer has final authority on both code quality and process adherence.

---

## 6. File Encoding (UTF-8 by Default) — Critical for Stability

**This rule exists to prevent mojibake and broken handoff files on Windows (especially Russian systems).**

All text files (including handoff JSONs, logs, reports, etc.) **must** be written and read using UTF-8.

### Mandatory Rules When Writing Files

1. **Preferred method — Python (most reliable):**
   ```python
   import json
   with open("handoff_orchestrator_to_coder.json", "w", encoding="utf-8") as f:
       json.dump(data, f, ensure_ascii=False, indent=2)
   ```

2. **When using PowerShell:**
   - Always specify encoding explicitly:
     ```powershell
     Set-Content -Path "file.json" -Value $json -Encoding utf8
     "text" | Out-File -FilePath "file.txt" -Encoding utf8
     ```
   - Never rely on bare `>` or `>>` redirection without setting defaults first.

3. **Never** use:
   - Bare `echo "text" > file.json`
   - Python `open("file", "w")` without `encoding="utf-8"`
   - PowerShell redirection without explicit UTF-8

### When Reading Files

- **Recommended (and now the default after running Agent-Init.ps1)**:
  - Bare `cat file.json` or `Get-Content file.json` will now work correctly for UTF-8 files.
- Explicit form (always safe):
  - PowerShell: `Get-Content "file.json" -Encoding utf8`
- Python: `open("file.json", encoding="utf-8")`

**Note**: `Agent-Init.ps1` now sets `$PSDefaultParameterValues['Get-Content:Encoding'] = 'utf8'` so that simple `cat` commands behave as expected on UTF-8 content.

### Enforcement

- The **Reviewer** must check that all handoff JSON files and important text outputs are valid UTF-8.
- If mojibake appears in handoff files or logs, the Reviewer should treat it as a process violation and request correction.
- Record any encoding-related problems in `SELF_IMPROVEMENT_LOG.md`.

**Recommendation for the Orchestrator:**
At the beginning of every cycle, after running `Agent-Init.ps1`, ensure the current PowerShell session has UTF-8 defaults enabled.

---

## 9. Workspace-Scoped Structured Memory (v3+)

**This is one of the most important tools for the long-term self-improvement of the loop.**

The system stores structured patterns by categories with counters, automatically deduplicates and compacts records. Memory is bound to the workspace ID based on the git remote — it is automatically shared between all clones and worktrees of a single repository and lives in `~/.grok/agentic-loop-memory/`.

**Goal**: the agent starts every cycle with real "institutional memory" of the project and stops repeating the same mistakes over and over.

### Mandatory usage rules

**Orchestrator (start of every cycle, immediately after Agent-Init.ps1):**
- Be sure to request a memory snapshot (or targeted query).
- Review the top patterns for the relevant categories before writing SPRINTPLAN.md and doing design work.
- Account for these patterns during planning (explicitly mention them in the summary or context where applicable).

**Reviewer (end of every cycle, after lessons_learned + Context Distillation):**
- Extract 1–3 specific, actionable patterns from the cycle results (lessons_learned, issues_found, distillation).
- Call update and record them in the appropriate category.
- In the handoff JSON, be sure to set `memory_updated: true` and `patterns_merged: N` (even if N=0 this is still a signal).

Memory is **not a replacement** for SELF_IMPROVEMENT_LOG.md. The log tracks process and meta-lessons; memory tracks specific recurring failure patterns and effective techniques for the project.

### Recommended categories (starter set)
- Common Failure Patterns
- Windows & PowerShell Gotchas
- Testing & Quality Strategies
- Context & Prompt Hygiene
- Project-Specific Architectural Decisions
- Effective Loop Strategies (meta-harvested)
- High-Value Compression Patterns (meta)
- Meta Improvement Patterns

You can add your own — the main thing is that the description is specific and useful for future cycles. Meta categories are populated primarily by meta_harvester (see §12).

### How to use (working examples)

**Correct way (recommended):**
```powershell
# Information about the current workspace and memory file
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' info

# Get snapshot (the most frequent call at the beginning of the cycle)
$mem = & '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' snapshot | ConvertFrom-Json

# Targeted query for top patterns (reliable way)
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory query --top 5 --category 'Common Failure Patterns'

# Record a pattern (Reviewer)
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' update `
    -Category 'Windows & PowerShell Gotchas' `
    -Description 'Never use %LOCALAPPDATA% or manual site-packages guessing — always call Get-PythonEnvironmentReport from Agent-Init.ps1'

# With distillation
& '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' update `
    -Category 'Context & Prompt Hygiene' `
    -Description 'Distillation after every full cycle + memory update dramatically reduces repeated mistakes' `
    -Date '2026-05-28' `
    -Summary 'Introduced structured memory system'
```

Direct Python call (when the venv is already activated or the exact path is known):
```powershell
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory snapshot
& ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory query --top 3
```

**Important**: old examples with `& { . 'path.ps1'; info }` should no longer be used — they are unreliable. Always call the script directly.

### Automatic compaction
On every `update` the system:
- Deduplicates patterns (via normalize).
- When a category exceeds 30 records, keeps only the most frequent ones (by count).
- Retains the last 20 distillations.

Compaction statistics are returned in the update response — the Reviewer can see them.

Full documentation, workspace ID mechanics, file format, and seeding examples from old logs are in `agentic_loop_template/memory/README.md`.

**The Reviewer bears personal responsibility** for ensuring that memory is actually used and kept up to date. Violating this rule is a serious process violation.

---

## 10. Clarification Questions Pool

To allow the self-improvement loop to work effectively under incomplete information (a common situation in early phases or when integrating with external systems), a mechanism for collecting questions into a single pool was introduced.

**Goal**: do not block cycles on every clarification, but accumulate questions and process them in batches at the regularity that the project user themselves specifies in the settings (PROJECT_CONTEXT.md → Clarification Questions Pool Settings).

**How it works:**
- During work, the Orchestrator, Coder, Tester, Debugger or Reviewer can formulate a question that requires external input (from the product owner / project lead / stakeholder) if necessary.
- The question is added to the handoff (clarification_questions field in HANDOFF_SCHEMA.md) with context, priority and suggested recipient.
- At the end of the cycle the Reviewer accumulates the pool (updates it in PROJECT_CONTEXT.md or a separate file like CLARIFICATION_QUESTIONS.md).
- When the configured frequency is reached (every_N_cycles / end_of_sprint / end_of_phase / manual) the Reviewer prepares a compact batch-summary of the pool and escalates it (in handoff, report, email/Slack — as agreed with users).
- After the user processes them, questions are marked resolved, related lessons/decisions are recorded in LESSONS.md and memory (so that the loop itself accounts for them in the future).
- If the question is critical, it can be marked priority: "blocking" and escalated immediately, but by default — in batches.

**Frequency settings (set by the project user):**
- every_3_cycles (default for fast iteration)
- end_of_sprint
- end_of_phase
- manual (only on explicit request)

**Roles:**
- Orchestrator / active roles: collect questions when they see ambiguity that hinders quality work.
- Reviewer: the main "collector" and escalator of the pool. Responsible for ensuring the pool does not grow infinitely and that questions are actionable.
- Product owner / project lead: process the pool at the specified regularity. Their answers become part of the context for subsequent cycles.

**Example question in the pool:**
- "Need an exact list of target platforms for Linux isolation support (Ubuntu 24.04 / Arch / RHEL?). Without this we cannot complete VM_VERIFICATION_LINUX.md and a full TEST_PLAN."

**Integration with other mechanisms:**
- Questions from the pool can influence SPRINTPLAN (add INVEST tasks after escalation).
- Lessons from processing go into SELF_IMPROVEMENT_LOG.md and memory (category "Requirements Clarification Patterns").
- In prompts (SYSTEM_PROMPT, AGENT_ROLES) it is explicitly stated: "If external stakeholder information is needed to continue — formulate a question in the pool, do not block work."

**Enforcement:**
- The Reviewer checks that when ambiguities exist, questions actually made it into the pool (and were not left only in handoff comments).
- If the pool is ignored and work proceeds "by guess" — this is a process violation.

Full examples and question templates are in PROJECT_CONTEXT_TEMPLATE.md (Clarification Questions Pool Settings section) and HANDOFF_SCHEMA.md.

---

## 11. Git self-cycle and cross-repository synchronization (Orchestrator responsibility before the next cycle)

**Critical for working with multiple checkouts and parallel flows.**

The project simultaneously has:
- The user's main clone (usually `C:\_PROJECT\eegent` or `X:\LocalRepo\eeagent`) — the "primary" repository where normal work, file viewing, and manual test runs happen.
- The agent's current worktree (where the loop executes, e.g. `C:\Users\ROOT\.grok\worktrees\...`).
- Additional worktrees (in `.agent/worktrees/` — mainly historical P0-simulation snapshots, do not touch them).

Changes (code, .agent/PLAN.md, TODO, commits) made in one checkout do not physically appear in another until an explicit git push + pull/merge + (if necessary) running the sync script in the target clone is performed.

**Strict rule:**
The Orchestrator **must**, before starting planning for the next cycle (i.e. at the very beginning of its PLAN step, immediately after bootstrap and before reading memory/compression), guarantee that:
- All changes from the previous cycle are committed on the current feature branch (with a natural Russian human developer message).
- A self-cycle has been performed (push feature → merge --no-ff into main with a meaningful Russian message → push).
- Changes have been synchronized to all active repositories/clones (minimum: current worktree + main user clone at the known path).
- Verification has been performed (git log / git status in each clone match on the required branches, files are physically visible on disk).
- Synchronization status is recorded in the handoff (git_branch, last_commit, git_final fields + new git_sync_status) and .agent/LOOP_STATE.md is updated.

Only after successful synchronization and recording — can you proceed to memory query, context compression, reading SPEC and planning INVEST for the next cycle.

**Exact process (execute precisely):**
1. In the current directory:
   - `git status --short` (if there are changes — `git add` the required paths).
   - `git commit -m "Natural Russian message from a developer..."` (no words about AI/loop).
   - `git push origin <current-feature-branch>`.
2. Self-cycle merge into main:
   - Use `git -C 'C:\_PROJECT\eegent'` (or the actual path of the main clone) for commands in the other checkout to bypass the git worktree limitation (one branch — one active checkout).
   - In the main clone: fetch, if necessary commit/stash local dirty files, `git merge origin/<feature> --no-ff -m "Merge ... (Russian description)"`.
   - `git push origin main`.
3. Synchronization to the main clone:
   - Execute the equivalent of `.\scripts\sync-worktree.ps1` in the main clone (via `git -C 'C:\_PROJECT\eegent' powershell -ExecutionPolicy Bypass -File .\scripts\sync-worktree.ps1` or direct git fetch/checkout main/pull).
   - For active worktrees (from `git worktree list`, excluding historical .agent/worktrees/P0-*) — if necessary `git -C <path> fetch && git -C <path> pull --ff-only` or switch.
4. Verification (minimum 2 clones):
   - `git -C 'C:\_PROJECT\eegent' log --oneline -3`
   - `git log --oneline -3`
   - Make sure the last commit is visible in both, files on disk are updated (cat or ls).
5. Record the result:
   - In the handoff: extend `git_final` / add `git_sync_status` (see HANDOFF_SCHEMA).
   - Update .agent/LOOP_STATE.md (last_git_sync field with paths, commits, time).
   - On synchronization error — handoff with "status": "BLOCKED", describe the problem, do not continue the cycle.

**Historical snapshots** (`.agent/worktrees/P0-*`): do not synchronize, do not change. These are time-capsules of previous iterations.

**Enforcement:**
- The Orchestrator at the beginning of each of its cycles performs a check "has synchronization been done after the previous one?" (if not — does it).
- The Reviewer at the end of the cycle checks for evidence of synchronization in the handoff from the Orchestrator (git_sync_status + command logs). If not — reject the handoff, return for rework.
- Violation (working "in only one checkout") — process violation, recorded in SELF_IMPROVEMENT_LOG.md.

**Tools:**
- `git` (via tool or powershell).
- `scripts/sync-worktree.ps1` (refine if necessary for reliable call from the loop).
- `git worktree list` to discover all checkouts.
- Explicit `git -C <full-path>` to work with another clone.

See also:
- WORKTREE_SYNC.md (explanation of the problem).
- scripts/sync-worktree.ps1 (current implementation).
- HANDOFF_SCHEMA.md (git_* fields).
- .agent/LOOP_STATE.md (where to write last_sync).

This requirement was introduced for reliable operation with parallel flows (Product + Meta), multiple worktrees, and so that "continue" does not lead to state divergence between repositories.

---

**This document is the single source of truth for development standards in this project.**

When in doubt, re-read this file. The Reviewer will hold all roles accountable to these standards.

## 12. Meta-Optimizer and Trajectory Harvesting (v3.x+)

**Цель**: систематически превращать собственные успешные циклы петли в улучшения *самого harness'а* (промпты, few-shot примеры, правила, стратегии сжатия, использование памяти).

Это естественное развитие §3 (Self-Improvement Discipline) и §9 (Workspace Memory). Если обычная память и distillation фиксируют "что пошло не так в проекте", то Meta-Optimizer фиксирует "что сработало в процессе разработки и как это сделать ещё эффективнее в следующий раз".

### Обязанности
- **Reviewer** (конец успешного цикла, status=DONE + высокие quality signals):
  - Вызвать `python -m agentic_loop_template.memory.meta_harvester harvest --handoff ... --cycle N --outcome DONE` (если цикл прошёл все гейты).
  - При необходимости запустить `analyze` / `propose`.
  - Рассмотреть сгенерированные предложения (особенно safe_to_auto).
  - Перенести 1–2 самых ценных паттерна в обычную workspace memory (категории "Effective Loop Strategies", "High-Value Compression Patterns", "Meta Improvement Patterns").
  - Зафиксировать `meta_harvest` в handoff (performed, trajectories_captured, proposals_generated).
- **Orchestrator** (начало цикла):
  - Запрашивать свежие meta-паттерны вместе с обычным memory snapshot.
  - Учитывать их при планировании (особенно на повторяющихся классах задач: sync, infra, context-heavy).
- **Enforcement**: Reviewer отклоняет handoff, если при наличии высокого качества цикла harvest не был вызван (аналогично sync и memory).

### Конфигурация
См. `.agent/project_config.json` → `meta_optimizer` (полностью параллельно `question_pool`):
- `frequency`: "after_every_done_cycle" | "every_2_done" | "end_of_sprint" | "manual"
- `min_quality`: пороги (confidence, tests_failed, process_violations)
- `auto_apply_safe`: true/false (только для безопасных типов изменений — добавление few-shot, мелкие tips по компрессии)
- `max_proposals_per_cycle`

Полный формат траекторий (Trajectory), предложений (Proposal), API и примеры — в `META_OPTIMIZER_SPEC.md` (корень шаблона) и `memory/meta_harvester.py`.

### Интеграция с существующими механизмами
- Предложения, принятые meta, могут становиться кандидатами в **Permanent Rules** (§3) и обновления **PROMPT_COMPRESSION_GUIDE.md**.
- Успешные паттерны из траекторий улетают в workspace memory (та же compaction/dedup механика).
- Все действия meta отражаются в `SELF_IMPROVEMENT_LOG.md` / distillation и в `.agent/META_PROPOSALS.md`.
- Git self-cycle (§11) обязателен и для изменений, инициированных meta (если они затрагивают файлы шаблона).

**Reviewer несёт личную ответственность** за то, чтобы meta-слой реально работал и не превращался в "ещё один неиспользуемый лог".

**Current eeagent architecture context (2026-06, for all loops)**: MCP Layer (MCPBaseSkill + registry + AgentExecutionSkill with real TaskService/Executor + isolation_hint), Strong Isolation (Persistent Firecracker guest + Windows JobObject + stubs + MCP-aware sandbox routing via sandbox_requirements), Live HTMX Control Plane, Vision Grounding (LocateAnything). See .agent/PLAN.md (single source) and docs/ARCHITECTURE.md. All new skills and tools must integrate with PolicyEngine + routing.