# Prompt Compression Guide for Agentic Loop (v3+)

**Goal**: Maximize context efficiency (minimize tokens) while preserving or improving the quality and reliability of the self-improving development loop.

## Core Principles

1. **Distill, don't repeat**
   - Move all repeating rules, anti-patterns, values, and detailed explanations into `DEVELOPMENT_STANDARDS.md`.
   - The active prompt should only reference it, not restate it.

2. **Micro-prompts for roles**
   - Role blocks in `AGENT_ROLES.md` should be extremely short ("you are now X, follow the Constitution, do these immediate things").
   - Detailed examples and long explanations belong in previous cycles, docs, or appendices — not in the active context on every handoff.

3. **External memory is primary state**
   - `PROJECT_CONTEXT.md`, `SPRINTPLAN.md`, and `SELF_IMPROVEMENT_LOG.md` are the real working memory.
   - The prompt + recent handoffs should be as thin as possible.

4. **Delta communication**
   - Handoffs should communicate *changes* and compact summaries, not full history.
   - Use the `context_delta` field in the handoff schema.

5. **On-demand reading**
   - Tell the agent *where* to find information instead of including everything upfront.
   - "Read the relevant section of X only when needed."

6. **Self-improvement as a compression engine**
   - Use the loop's own output (lessons_learned) to iteratively shorten and sharpen the instructions themselves.

## Practical Techniques Applied

- Converted full role instructions → micro-prompts (major win on every handoff).
- Moved detailed GIT/COMMIT/CODE COMMENT rules out of SYSTEM_PROMPT → DEVELOPMENT_STANDARDS.md.
- Added explicit `context_delta` support in HANDOFF_SCHEMA.md.
- Strengthened guidance to keep `summary` extremely short.
- Added automatic environment reports and safe helper functions so the agent doesn't need long explanatory text in the prompt.

## Measurement & Maintenance

- Track approximate token counts of:
  - Full SYSTEM_PROMPT + current role block
  - Typical handoff + recent context files
- After every significant compression wave, record the before/after numbers in `SELF_IMPROVEMENT_LOG.md`.
- Reviewer should explicitly comment on context efficiency during handoff review.

## Future Opportunities

- Stronger automation of Context Distillation (e.g., token-count triggers in Agent-Init.ps1).
- More aggressive use of external memory / scratchpad files.
- Dynamic prompt loading based on current phase.
- Tool-assisted context pruning.

## M2.5 + Blackbox Specific (limited resources, no GPU, small practical context window)

When targeting Blackbox + Minimax M2.5:
- The model has excellent nominal long context, but in the loop (Blackbox CLI, no local GPU for extra inference/processing, resource limits on the executor side) the effective window is small in practice.
- Always: ultra-aggressive first compression pass before any planning.
- Use summary + delta + few-shot from real successful compressed handoffs (examples in this guide and SELF_IMPROVEMENT_LOG from previous M2.5 cycles).
- For metrics/ROI tasks (P1): always include "performance" object (elapsed, tool_calls, confidence, meta_applied) + success_patterns in deltas for handoff compression. Example from cycle 1: "performance": {"cycle":1, "elapsed_minutes":2.1, "tool_calls":9, "confidence":0.92}. Reduces verbose repeats.
- Playbooks (full cycle): Перед ACT — select_bullets + инжект bullets. Handoff содержит playbook_refs. См. memory/playbooks.py.
- External memory (.agent/LOOP_STATE, PLAN, TODO, DECISIONS, LESSONS) is primary — prompt should contain only pointers + compact deltas.
- Structured JSON output preferred (easier parsing, less tokens wasted on prose).
- Token awareness: before sending, estimate and cut to budget. Non-interactive Blackbox mode benefits from clear, dense instructions without fluff.
- In Orchestrator/Executor prompts: "treat as small window: summary first, on-demand full read only".
- After each cycle, Reviewer must comment on context efficiency and update few-shot examples if a better compression pattern was used.

## Automatic Context Distillation (Implemented)

As of this version, the template includes an explicit **automatic Context Distillation** mechanism:

- Triggered primarily by the Reviewer at the end of full cycles (or on-demand when context feels heavy).
- Produces structured, high-density summaries using a defined format.
- Stored in `SELF_IMPROVEMENT_LOG.md` (and/or `PROJECT_CONTEXT.md`).
- Supported by an optional `distillation_performed` field in the handoff schema.
- Documented in `DEVELOPMENT_STANDARDS.md` (section 8) and the Reviewer role instructions.

This is the main mechanism for keeping very long-running loops (10+ cycles) practical. See `DEVELOPMENT_STANDARDS.md` and `AGENT_ROLES.md` (Reviewer section) for exact instructions.

**This document itself should stay relatively short.** Its purpose is to guide future compression work, not to become another source of bloat.

## 2026 research → rule compressor (v3.6.0)

Applied as **local rules**, not as extra model calls:

| Paper / idea | What we took | Where |
|--------------|--------------|--------|
| Acon (arXiv:2510.00615), 26–54% peak-context cut | Adaptive drop of low-utility spans first | `memory/compressor.py` file priorities |
| PAACE / SuperCompress / rate-distortion | Spend remaining budget on high-utility head+tail | `truncate_to_budget`, `--compress` |
| Mem0-style tiers | Working set vs history archives | `.agent/LOOP_STATE` vs `.agent/history/` |

```bash
python -m memory.context_budget check --files .agent/PLAN.md .agent/TODO.md --budget 12000 --compress
python -m memory.compressor files --budget 4000 .agent/history/x.md TRAJECTORIES.json
```

Reviewer on DONE must run `skills/loop-self-improve` and, if over budget, `--compress`. Sources are never rewritten; the report is the contract.

## Rituals vs request proxy (v3.7)

File-side compression (`memory.compressor`, `context_budget --compress`) and knowledge ingest run in Init / supervisor / Reviewer **without** an HTTP hop. The request proxy (host pxpipe, later the in-template gateway) compresses **interactive request bodies** only. Do not stack aggressive distill on both paths: gateway keeps `keep_recent_turns=2`; supervisor injects a bounded knowledge block (≤800 tokens). Sources stay the source of truth — the compressor never rewrites them. Harmless if both are imported in the same process.

## Selective Memory (P7)

Do not load full history. Use targeted retrieval:

| Need | Source | Load |
|------|--------|------|
| Current tasks | `.agent/TODO.md` | Delta only (open items) |
| Iteration scope | `.agent/PLAN.md` | Active iteration section |
| Patterns | `memory.playbooks select` | Top k bullets by query |
| Metrics | `.agent/PERFORMANCE_LEDGER.md` | Last 5 cycles |
| Resume after crash | `python -m memory.resume --json` | Full output |
| Trajectory quality | `python -m memory.eval_harness --recent 5` | Scores only |

**Rule:** If resume context + last handoff `context_delta` covers the question, skip full file reads.

---

## Long-context model specifics (M2.5+)

The model handles large context and complex planning well. Use this advantage, but do not abuse it:

- In one message you can provide multiple .agent/ files (PROJECT_CONTEXT.md + SPRINTPLAN.md + TODO.md + recent handoffs) — the model will "swallow" them well and build a coherent plan.
- But **always** start with an ultra-short summary of the current state (1-2 paragraphs + list of open INVEST). This allows the model to enter context immediately without "warm-up".
- Before reading a full file — ask yourself: "Is the delta from the last handoff + summary in memory sufficient?" If yes — do not read.
- For complex refactorings use long context for simultaneous analysis of 4-5 files, but always return the result as a compact patch + updated summary.
- Do self-critique and reflection especially carefully — the model is strong at this. Ask it to find not only errors but also opportunities for further context compression.

Example of a good message start for such a model:
```
Current state (compressed):
- Goal: ...
- Open tasks: [P-01, M-03]
- Recent changes: ...
Full details in files: PROJECT_CONTEXT.md (read only section X if needed), TODO.md.

Now do the following...
```

After each major step — ask the model itself to suggest how context could be compressed even more strongly for the next cycle.

## Few-shot examples of well-compressed handoffs (for M2.5+ and compression)

**Bad handoff (too verbose, repeats rules):**
```
I analyzed TODO. Made changes in file X. According to DEVELOPMENT_STANDARDS.md (which says all comments in Russian, UTF-8, .venv, self-cycle per §11 etc. etc. - long repeat) ...
Full diff: [huge]
Next task: ...
```
Problem: the model wastes tokens on repeating standards that are already in memory/files.

**Good compressed handoff (delta + links + summary):**
```
Compressed state:
- Cycle goal: improve sync-worktree (parameters, verify-only, clean output).
- Done: updated script (git -C support, -VerifyOnly, SYNC_DONE marker without quotes), added test in test_autonomy_logic.py (simulation of git_sync_status enforcement per §11), updated WORKTREE_SYNC.md (link to §11, script description).
- Commits: 4 Russian (ad9558a, 93a75c6 etc.).
- Self-cycle: performed (push, merge in main clone 5872978, sync, LOOP_STATE updated).
- Evidence: grep for "VerifyOnly", "git_sync_status", "SYNC_DONE" in files; test ran OK; script with -MainClonePath -VerifyOnly gives marker.

Full details:
- Changed files: scripts/sync-worktree.ps1, tests/test_autonomy_logic.py, WORKTREE_SYNC.md, .agent/TODO.md, .agent/LOOP_STATE.md.
- Next task: [link to TODO or INVEST].

Read only what is needed: PROMPT_COMPRESSION_GUIDE.md (few-shot section), if you want to improve compression further.
```
Why good: short summary + delta (what was done, commits, evidence), links instead of repetition, the model can plan immediately.

Добавь такие примеры в свои handoff'ы. Reviewer должен хвалить/критиковать сжатие.

## Meta-harvested few-shot examples (v3.x+)

### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-062-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-062-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-062-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-059-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-056-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-053-03f8*


### Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)

```markdown
**Harvested: include 'performance' delta in every handoff (from 20+ loops)**
- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed
- success_patterns for ledger/metrics wins
- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.
Example: "performance": {"cycle": 21, "elapsed_minutes": 3.5, "tool_calls": 12, "confidence": 0.9, "meta_applied": 8}
```

*Добавлено meta_harvester cycle T-020-03f8*


### Demo: harvested marker example from cycle 43

**Harvested (demo cycle 43):** explicit SYNC_DONE + VerifyOnly in handoff delta. Evidence: tests + git log in clones.

*Добавлено meta_harvester cycle T-043-03f8*


Начиная с v3.2, Reviewer + meta_harvester автоматически собирают реальные успешные компрессии с высококачественных DONE-циклов и предлагают их как verified few-shot.

**Как это работает:**
- При успешном цикле (особенно с заметным выигрышем по размеру handoff / скорости планирования) вызывается harvest.
- analyze/propose генерирует кандидатов вида "add_few_shot_example" в этот файл.
- Безопасные предложения (safe_to_auto) могут применяться автоматически или по команде `meta_harvester apply-safe`.
- Reviewer обязан явно упомянуть в distillation / handoff, если meta-пример был использован или улучшен.

**Правила для хорошего meta-примера:**
- Короткий, с реальными цифрами (цикл, размер, что именно дало выигрыш).
- Содержит ссылку на "evidence" (grep, маркер, тест).
- Не повторяет общие правила — только конкретный приём, который сработал.
- Обновляйте этот раздел при каждом принятом meta-предложении.

Пример (harvested):
```
**Meta-harvested (cycle 17, sync-worktree hardening):**
Compressed state + explicit SYNC_DONE marker + link to previous wave.
Evidence: test passed on -VerifyOnly, git log in both clones.
Result: Orchestrator handoff ~40% короче, меньше возвратов.
```

Reviewer / meta должны поддерживать этот раздел актуальным. Это один из главных каналов, через который петля улучшает собственную эффективность сжатия.

---

*Maintained as part of the agentic_loop_template optimization effort. Meta contributions are now first-class.*

## Concrete small-context examples for M2.5+ (Blackbox, limited resources, no GPU) — 2026-06

**Token budget estimation:**

Use `python -m memory.context_budget check --files … --budget N` (optional `--model` / `--encoding`).
Runtime SSOT is `memory.context_budget.estimate_tokens`: tiktoken when the `tokens`/`dev` extra is installed, otherwise `max(1, len(text)//4)`. Default encoding `cl100k_base` (grok); `gpt-4o` / `o1` / `o3` → `o200k_base`.

**Pydantic for structured output (reduces tokens, easier parse):**
Use Pydantic models in executor prompts for handoff/results (see EXECUTOR_PROMPT_TEMPLATE.md). Model enforces schema, less prose from LLM.
Example in SYSTEM_PROMPT: "Return only valid JSON matching the schema. No extra text."

**Open RAG in .agent/memory/ (no external deps, for context):**
Use simple file-based or in-memory retrieval for past lessons/decisions without full dump.
```python
# In memory/ or custom: query top-k relevant from LESSONS.md / DECISIONS.md by keywords.
# Inject only deltas + 2-3 relevant lessons, not whole file.
```
See .agent/memory/ for implementation patterns. Update in COMPRESSION + SYSTEM_PROMPT: "Use external .agent/ as RAG; prompt only pointers + top relevant + compact delta."

Add these to future handoffs. Reviewer must verify compression quality and update examples.

**Marker**: Documentation fully actualized as of 2026-06 (concrete small-context examples added to COMPRESSION).
