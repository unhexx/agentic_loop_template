# Harvest / Reflect Skill Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:using-git-worktrees before the first edit.

**Goal:** Ship the harvest vs reflect skill split as Agentix **3.9.3** (patch): two SKILL.md files, `tools/select.py` routing, Reviewer/docs pointers, `pytest memory/` tests; drop `.agent/` runtime dirt.

**Architecture:** `experience-accumulation` writes memory (`experience_harvester cycle` dry-run then `--apply`). `loop-self-improve` queries that memory and apply-safes. `reflective-improvement` is the 6-step write-up **sub-skill** only (not a `select.py` target). Loader is `SKILL_INTENTS` only; no new argparse intents; no harvester code changes.

**Tech Stack:** Markdown skills, `tools/select.py`, `pytest memory/`, importlib load of `tools/select.py`.

**Branch / isolation:** Worktree from `origin/main` (`103976c`, VERSION 3.9.2 — Blackbox `0a864a5` plus pxpipe-agy docs). Branch name: `feature/v3.9.3-harvest-reflect-skills-20260825`. Do **not** commit on dirty SSOT `main`. Do **not** `git add -A`. Do **not** start newportal Go rewrite. Do **not** vendor the consumer symlink. Human gate — no auto-merge to `main`.

**Spec:** [`docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md`](../specs/2026-08-25-harvest-reflect-skill-split-design.md)

**Source leftover (adapt; do not commit from dirty main):** uncommitted files on SSOT `main` after `.agent/` restore. Copy content from this plan (canonical), not `git add` from the dirty tree.

---

## File map

| Path | Action |
|------|--------|
| `memory/test_select.py` | Create (Task 2) |
| `skills/experience-accumulation/SKILL.md` | Create (Task 3) |
| `skills/loop-self-improve/SKILL.md` | Create (Task 4) |
| `tools/select.py` | Modify `SKILL_INTENTS` only (Task 5) |
| `skills/reflective-improvement/SKILL.md` | Trim harvest/apply-safe/export-sft (Task 6) |
| `skills/README.md` | Table + usage comment (Task 7) |
| `skills/local-knowledge-ingestion/SKILL.md` | Integration sentence (Task 7) |
| `tools/blocks/common/experience.md` | Pointer (Task 7) |
| `EXPERIENCE_EXTRACTION_TOOLS.md` | Reviewer sentence (Task 7) |
| `PROMPT_COMPRESSION_GUIDE.md` | DONE sentence (Task 7) |
| `SYSTEM_PROMPT.md` | Self-learning sentence (Task 7) |
| `prompts/short_reviewer_prompt.md` | Mandatory DONE (Task 7) |
| spec + this plan | Add in Task 1 or with docs PR |
| `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md`, `memory/README.md` | Task 8 only |
| `.agent/**` | **Never add** |

**Out of scope:** `memory/experience_harvester.py`, `memory/meta_harvester.py`, `memory/supervisor.py`, `TOOLS_REGISTRY.md` intent table, `DEVELOPMENT_STANDARDS.md`, newportal, `aservice24_22-08-2026`.

---

### Task 1: Isolated worktree from clean origin/main

**Files:** none (git only)

- [ ] **Step 1: Confirm SSOT HEAD**

Run (cwd may be consumer; always `cd` to SSOT):

```bash
cd /home/unhex/_PROJECT/agentic_loop_template
git rev-parse --short HEAD
cat VERSION
git status --short
```

Expected: `103976c` (or whatever `origin/main` is at fire time; must be VERSION `3.9.2`). Dirty `main` may still show leftover skill files + this spec/plan. **Do not commit them here.**

- [ ] **Step 2: Create worktree**

```bash
cd /home/unhex/_PROJECT/agentic_loop_template
mkdir -p .worktrees
git check-ignore -q .worktrees || echo 'WARN: .worktrees not ignored — use ~/.grok/worktrees or existing global path instead'
git worktree add .worktrees/v393-harvest-reflect -b feature/v3.9.3-harvest-reflect-skills-20260825 origin/main
cd .worktrees/v393-harvest-reflect
git status --short
cat VERSION
```

Expected: empty status, VERSION `3.9.2`, HEAD matches `origin/main` (`103976c` at plan time). If `.worktrees` is not ignored, use `/home/unhex/.grok/worktrees/agentic_loop_template/v393-harvest-reflect` instead.

- [ ] **Step 3: Baseline tests**

```bash
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH=. pytest memory/ -q
```

Expected: 180 passed, 6 skipped (or current 3.9.2 count — record exact). If red, STOP and report.

- [ ] **Step 4: Copy spec + plan into the worktree if they are not on origin/main**

From dirty SSOT (read-only copy):

```bash
SSOT=/home/unhex/_PROJECT/agentic_loop_template
WT="$(git rev-parse --show-toplevel)"
mkdir -p "$WT/docs/superpowers/specs" "$WT/docs/superpowers/plans"
cp "$SSOT/docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md" "$WT/docs/superpowers/specs/"
cp "$SSOT/docs/superpowers/plans/2026-08-25-harvest-reflect-skill-split.md" "$WT/docs/superpowers/plans/"
git add docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md docs/superpowers/plans/2026-08-25-harvest-reflect-skill-split.md
git commit -m "Добавил спецификацию и план разделения скиллов harvest и reflect"
```

---

### Task 2: Failing routing tests (`memory/test_select.py`)

**Files:**
- Create: `memory/test_select.py`

- [ ] **Step 1: Write the test file**

Create `memory/test_select.py` with **exactly**:

```python
# -*- coding: utf-8 -*-
"""Routing tests for tools/select.py skill intents (harvest vs reflect)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_select():
    path = REPO / "tools" / "select.py"
    spec = importlib.util.spec_from_file_location("agentix_select", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _relpaths(intent: str, os_name: str = "linux") -> list[str]:
    sel = _load_select()
    return [p.relative_to(REPO).as_posix() for p in sel.resolve_paths(intent, os_name)]


def test_harvest_intent_loads_experience_accumulation_not_reflect() -> None:
    paths = _relpaths("harvest")
    assert "tools/blocks/common/experience.md" in paths
    assert "skills/experience-accumulation/SKILL.md" in paths
    assert "skills/reflective-improvement/SKILL.md" not in paths
    assert "skills/loop-self-improve/SKILL.md" not in paths


def test_reflect_intent_loads_loop_self_improve_not_harvest() -> None:
    paths = _relpaths("reflect")
    assert "skills/loop-self-improve/SKILL.md" in paths
    assert "skills/experience-accumulation/SKILL.md" not in paths
    assert "skills/reflective-improvement/SKILL.md" not in paths


def test_git_intent_does_not_load_jira_or_harvest_skills() -> None:
    joined = " ".join(_relpaths("git"))
    assert "git-commit-to-jira-tasks" not in joined
    assert "experience-accumulation" not in joined
    assert "loop-self-improve" not in joined


def test_skill_files_exist_and_names_match() -> None:
    for rel, name in (
        ("skills/experience-accumulation/SKILL.md", "experience-accumulation"),
        ("skills/loop-self-improve/SKILL.md", "loop-self-improve"),
        ("skills/reflective-improvement/SKILL.md", "reflective-improvement"),
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"name: {name}" in text.split("---", 2)[1]


def test_reflective_improvement_does_not_own_harvest_cycle() -> None:
    text = (REPO / "skills/reflective-improvement/SKILL.md").read_text(encoding="utf-8")
    assert "experience_harvester cycle" not in text
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
PYTHONPATH=. pytest memory/test_select.py -q
```

Expected on clean 3.9.2 (`origin/main`): FAIL. `harvest` still resolves `skills/reflective-improvement/SKILL.md`; new skill files missing; `reflective-improvement` still contains `experience_harvester cycle`. Do **not** write skills before seeing this red.

- [ ] **Step 3: Commit tests only**

```bash
git add memory/test_select.py
git commit -m "Добавляю тесты маршрутизации harvest и reflect в select.py"
```

---

### Task 3: `experience-accumulation` skill

**Files:**
- Create: `skills/experience-accumulation/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `skills/experience-accumulation/SKILL.md` with **exactly**:

```markdown
---
name: experience-accumulation
description: Use when harvesting or accumulating agent experience across sibling repos or a parent `_PROJECT/*` folder, when workspace memory is empty, after Reviewer DONE on a multi-repo session, with `--intent harvest`, or phrases like accumulate experience, harvest lessons, experience_harvester.
---

# experience-accumulation

Write cross-project agent experience into workspace memory. Do not change prompts or playbooks (that is **loop-self-improve**).

Commands, sources, and categories live in `EXPERIENCE_EXTRACTION_TOOLS.md` and `tools/blocks/common/experience.md`. Run `python -m memory.experience_harvester --help` for flags. Do not paste those lists here.

## Steps

1. Resolve `--parent` (sibling layout: `..` when cwd is a consumer under `_PROJECT`).
2. Dry-run: `cycle --parent <parent>` **without** `--apply`. Read the audit/counts.
3. Apply only when the dry-run looks real: same command with `--apply`.
4. Stop. Next: **loop-self-improve** if this cycle should change the harness.

Never read full `.agent/LOOP_STATE` archives. Use `python -m memory state snapshot`.

Not this skill: `local-knowledge-ingestion` (docs → SQLite), web crawl, copying eegent collectors.
```

- [ ] **Step 2: Commit**

```bash
git add skills/experience-accumulation/SKILL.md
git commit -m "Добавил скилл накопления опыта experience-accumulation"
```

---

### Task 4: `loop-self-improve` skill

**Files:**
- Create: `skills/loop-self-improve/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `skills/loop-self-improve/SKILL.md` with **exactly**:

```markdown
---
name: loop-self-improve
description: Use when improving the Agentix loop from accumulated workspace memory — Reviewer DONE, `--intent reflect`, self-improve the loop, apply harvested patterns, meta proposals, or playbook curate after harvest.
---

# loop-self-improve

Change the loop using **already accumulated** memory. Do not harvest here (that is **experience-accumulation**).

**REQUIRED SUB-SKILL:** For the 6-step write-up, use reflective-improvement. This skill owns query-first and apply-safe.

## Steps

1. Query: `python -m memory query --top 5` (failure and strategy categories). If empty, load **experience-accumulation** first.
2. Reflect against **this cycle** only (handoff + ledger snapshot). Do not restate DEVELOPMENT_STANDARDS.
3. Propose playbook bullets, prompt micro-edits, or meta proposals. Label each **proposed** vs **applied**.
4. High-impact (skill rewrite, bulk memory, standards): `python -m memory.meta_harvester apply-safe --dry-run` or ask. Low-risk: playbooks curate / memory append.
5. High-quality DONE (`confidence ≥ 0.85`, `tests_failed=0`): optional `python -m memory.meta_harvester export-sft` (gitignored JSONL, no GPU).

Flags: `python -m memory.meta_harvester --help`. Spec: `META_OPTIMIZER_SPEC.md`.

Not this skill: auto-merge `main`, bulk skill/standards rewrite without confirm, inventing a second mock adapter.
```

- [ ] **Step 2: Commit**

```bash
git add skills/loop-self-improve/SKILL.md
git commit -m "Добавил скилл самоулучшения цикла loop-self-improve"
```

---

### Task 5: Point `SKILL_INTENTS` at the new files

**Files:**
- Modify: `tools/select.py` lines 32 and 35 only

- [ ] **Step 1: Edit SKILL_INTENTS**

In `tools/select.py`, replace:

```python
SKILL_INTENTS = {
    "reflect": ["skills/reflective-improvement/SKILL.md"],
    "knowledge": ["skills/local-knowledge-ingestion/SKILL.md"],
    "compress": ["skills/README.md"],
    "harvest": ["skills/reflective-improvement/SKILL.md"],
}
```

with:

```python
SKILL_INTENTS = {
    "reflect": ["skills/loop-self-improve/SKILL.md"],
    "knowledge": ["skills/local-knowledge-ingestion/SKILL.md"],
    "compress": ["skills/README.md"],
    "harvest": ["skills/experience-accumulation/SKILL.md"],
}
```

Do not change `INTENTS`. `harvest` must still include `common/experience.md`. `reflect` stays `[]` in `INTENTS`.

- [ ] **Step 2: Run harvest/reflect/git tests — still one failure**

```bash
PYTHONPATH=. pytest memory/test_select.py -q
```

Expected: `test_harvest_*`, `test_reflect_*`, `test_git_*`, `test_skill_files_*` PASS. `test_reflective_improvement_does_not_own_harvest_cycle` still FAIL until Task 6.

- [ ] **Step 3: Commit**

```bash
git add tools/select.py
git commit -m "Направил harvest и reflect в select.py на отдельные скиллы"
```

---

### Task 6: Trim harvest/apply-safe out of `reflective-improvement`

**Files:**
- Modify: `skills/reflective-improvement/SKILL.md`

- [ ] **Step 1: Pipeline one-liner (keep if already present from leftover; add if missing)**

Immediately after `# reflective-improvement` insert (once):

```markdown
Pipeline: **experience-accumulation** writes memory; **loop-self-improve** queries it and apply-safes. This file is the 6-step write-up only.
```

- [ ] **Step 2: Replace Trigger & gather**

Replace:

```markdown
1. **Trigger & gather**
   - Scope: single task, cycle, error cluster, full review, or **parent-folder harvest**.
   - Fetch: last handoff JSON, PERFORMANCE_LEDGER recent, LOOP_STATE snapshot, relevant playbooks, test output.
   - Parent-folder session: `python -m memory.experience_harvester cycle --parent <_PROJECT>` (dry-run first). Reads AGENTS.md / playbooks, not only LESSONS.md.
```

with:

```markdown
1. **Trigger & gather**
   - Scope: single task, cycle, error cluster, or full review. Parent-folder harvest is **experience-accumulation**, not this file.
   - Fetch: last handoff JSON, PERFORMANCE_LEDGER recent, LOOP_STATE snapshot (`python -m memory state snapshot`), relevant playbooks, test output.
```

- [ ] **Step 3: Replace Close the loop**

Replace:

```markdown
5. **Close the loop**
   - On DONE: Reviewer MUST run this skill (or equivalent meta_harvester harvest + reflect).
   - If the session spanned `../_PROJECT/*`, apply `experience_harvester cycle --parent ..` and record `memory_updated`. Supervisor also calls `maybe_cycle_on_done` (dry-run) after Reviewer DONE.
   - High-quality DONE: `python -m memory.meta_harvester export-sft` → `.agent/sft/train.jsonl` (gitignored, export only, no GPU).
   - Suggest decomposition into `.agent/TODO.md` / PLAN.
```

with:

```markdown
5. **Close the loop**
   - On DONE: Reviewer follows **loop-self-improve** (query → this 6-step write-up → apply-safe). This file is the write-up only.
   - Parent-folder harvest stays in **experience-accumulation**. Supervisor `maybe_cycle_on_done` (dry-run) is unchanged.
   - High-quality DONE export-sft is owned by **loop-self-improve**.
   - Suggest decomposition into `.agent/TODO.md` / PLAN.
```

- [ ] **Step 4: Prove the grep contract and tests**

```bash
grep -n 'experience_harvester cycle' skills/reflective-improvement/SKILL.md && exit 1 || echo TRIM_OK
PYTHONPATH=. pytest memory/test_select.py -q
```

Expected: `TRIM_OK`; `test_select.py` all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/reflective-improvement/SKILL.md
git commit -m "Убрал harvest из reflective-improvement — скилл только для отчёта"
```

---

### Task 7: Registry and prompt pointers

**Files:**
- Modify: `skills/README.md`
- Modify: `skills/local-knowledge-ingestion/SKILL.md`
- Modify: `tools/blocks/common/experience.md`
- Modify: `EXPERIENCE_EXTRACTION_TOOLS.md`
- Modify: `PROMPT_COMPRESSION_GUIDE.md`
- Modify: `SYSTEM_PROMPT.md`
- Modify: `prompts/short_reviewer_prompt.md`

- [ ] **Step 1: `skills/README.md` table**

Replace the Available Skills table **and** the harvest-is-not-a-skill sentence with:

```markdown
| Skill | Purpose | When to load |
|-------|---------|--------------|
| [experience-accumulation](experience-accumulation/SKILL.md) | Dry-run then apply `experience_harvester cycle` into workspace memory | `--intent harvest`; parent-folder / empty memory; Reviewer DONE on multi-repo session |
| [loop-self-improve](loop-self-improve/SKILL.md) | Query accumulated memory, then propose/apply-safe harness changes | `--intent reflect`; Reviewer DONE after harvest; “self-improve the loop” |
| [reflective-improvement](reflective-improvement/SKILL.md) | 6-step reflection write-up (sub-skill of loop-self-improve) | After memory query, when a structured ritual is needed |
| [local-knowledge-ingestion](local-knowledge-ingestion/SKILL.md) | Templates for crawlers, SQLite local knowledge store, sovereign mirroring of docs/code into structured memory | Orchestrator bootstrap; when external docs or multi-repo knowledge needed |
| [git-commit-to-jira-tasks](git-commit-to-jira-tasks/SKILL.md) | Cluster git commits into INVEST Jira Stories/Tasks with Fibonacci Story Points (hours optional) | **Explicit user request only** or `Follow skills/git-commit-to-jira-tasks/SKILL.md`. Never `--intent git` |
```

Delete this paragraph if present:

```markdown
Cross-project harvest is not a separate skill file: run `python tools/select.py --intent harvest` → `experience_harvester cycle` (see `EXPERIENCE_EXTRACTION_TOOLS.md`).
```

In the Usage comment block, replace:

```markdown
# "Follow skills/reflective-improvement/SKILL.md ritual"
```

with:

```markdown
# "Follow skills/experience-accumulation/SKILL.md then skills/loop-self-improve/SKILL.md"
```

Keep the `python tools/select.py --intent harvest` example.

- [ ] **Step 2: local-knowledge integration sentence**

In `skills/local-knowledge-ingestion/SKILL.md` replace:

```markdown
- Complements reflective-improvement (lessons become ingested knowledge).
```

with:

```markdown
- Complements experience-accumulation / loop-self-improve (lessons become ingested knowledge).
```

- [ ] **Step 3: experience block pointer**

In `tools/blocks/common/experience.md` replace the last line:

```markdown
See `EXPERIENCE_EXTRACTION_TOOLS.md` and `skills/reflective-improvement/SKILL.md`.
```

with:

```markdown
See `EXPERIENCE_EXTRACTION_TOOLS.md` and `skills/experience-accumulation/SKILL.md`.
```

- [ ] **Step 4: EXPERIENCE_EXTRACTION_TOOLS.md**

Replace:

```markdown
- **Reviewer (DONE, parent-folder session):** `python -m memory.experience_harvester cycle --parent ..` then `skills/reflective-improvement`.
```

with:

```markdown
- **Reviewer (DONE, parent-folder session):** `skills/experience-accumulation` (`cycle --parent ..`) then `skills/loop-self-improve`.
```

- [ ] **Step 5: PROMPT_COMPRESSION_GUIDE.md**

Replace:

```markdown
Reviewer on DONE must run the reflective-improvement skill and, if over budget, `--compress`. Sources are never rewritten; the report is the contract.
```

with:

```markdown
Reviewer on DONE must run `skills/loop-self-improve` and, if over budget, `--compress`. Sources are never rewritten; the report is the contract.
```

- [ ] **Step 6: SYSTEM_PROMPT.md**

Replace the Self-learning sentence (keep the **Template Version:** 3.6.0 line unchanged):

```markdown
**Self-learning updates in 3.6:** Orchestrator cold-start: `python -m memory state snapshot --window 3` then `python -m memory query --top 5 --category "Common Failure Patterns"`. On a parent-folder session, Reviewer runs `python -m memory.experience_harvester cycle --parent <_PROJECT>` (see `skills/reflective-improvement`). Distillation and questions pool remain required. Git sync evidence mandatory in every DONE handoff.
```

with:

```markdown
**Self-learning updates in 3.6:** Orchestrator cold-start: `python -m memory state snapshot --window 3` then `python -m memory query --top 5 --category "Common Failure Patterns"`. On a parent-folder session, Reviewer follows `skills/experience-accumulation` then `skills/loop-self-improve`. Distillation and questions pool remain required. Git sync evidence mandatory in every DONE handoff.
```

- [ ] **Step 7: short_reviewer_prompt.md**

Replace the **Mandatory on DONE** bullet with:

```markdown
- **Mandatory on DONE:** follow `skills/loop-self-improve/SKILL.md` (query memory → 6-step write-up in `skills/reflective-improvement/SKILL.md` → apply-safe). If this session touched sibling repos under a parent folder, follow `skills/experience-accumulation/SKILL.md` first (`cycle --parent ..`, dry-run; `--apply` when lessons are real). Default Reviewer path when `../` looks like `_PROJECT`. On high-quality DONE (`confidence ≥ 0.85`, `tests_failed=0`) also `python -m memory.meta_harvester export-sft` (local JSONL only, gitignored, no GPU).
```

- [ ] **Step 8: Smoke + full suite**

```bash
python tools/select.py --intent harvest --list
python tools/select.py --intent reflect --list
python tools/select.py --intent git --list | grep -i jira && exit 1 || echo GIT_INTENT_CLEAN
PYTHONPATH=. pytest memory/ -q
readlink -f /home/unhex/_PROJECT/newportal/agentic_loop_template
git diff --name-only
```

Expected:

- harvest `--list` includes `tools/blocks/common/experience.md` and `skills/experience-accumulation/SKILL.md`
- reflect `--list` is `skills/loop-self-improve/SKILL.md` only
- `GIT_INTENT_CLEAN`
- pytest: previous count **plus 5** (the new tests); 6 skipped unchanged
- symlink target is `/home/unhex/_PROJECT/agentic_loop_template`
- `git diff --name-only` has **no** `.agent/` paths

- [ ] **Step 9: Commit**

```bash
git add skills/README.md skills/local-knowledge-ingestion/SKILL.md tools/blocks/common/experience.md EXPERIENCE_EXTRACTION_TOOLS.md PROMPT_COMPRESSION_GUIDE.md SYSTEM_PROMPT.md prompts/short_reviewer_prompt.md
git commit -m "Подключил скиллы harvest и reflect в реестр и промпты ревьюера"
```

---

### Task 8: VERSION 3.9.3 (last)

**Files:**
- Modify: `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md`, `memory/README.md`

Do this only after Task 7 tests are green.

- [ ] **Step 1: VERSION**

Write `VERSION` as a single line:

```
3.9.3
```

- [ ] **Step 2: CHANGELOG**

Keep the existing `[Unreleased]` pxpipe-agy block (Antigravity/`agy` second pxpipe). Insert a new section **after** that Unreleased block and **before** `## [3.9.2]`:

```markdown
## [3.9.3] - 2026-08-25

### Added
- First-class skills `skills/experience-accumulation` (`--intent harvest`) and `skills/loop-self-improve` (`--intent reflect`)
- `memory/test_select.py` locks harvest/reflect/git loader paths
- Design spec: [`docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md`](docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md)

### Changed
- `tools/select.py` `SKILL_INTENTS`: harvest and reflect no longer share `reflective-improvement`
- `reflective-improvement` is the 6-step write-up sub-skill only (no `experience_harvester cycle`)
- Reviewer DONE / SYSTEM_PROMPT / experience docs point at the split
- `VERSION` → 3.9.3

Patch, not 3.10.0: no wizard default change, no new product surface, skill routing only.
```

- [ ] **Step 3: Badges and footer**

Replace remaining live `3.9.2` product strings:

- `ROADMAP.md` badge `version-3.9.2` → `3.9.3`; add Milestones row `| **v3.9.3** | Harvest/reflect skill split (experience-accumulation vs loop-self-improve) |` **above** v3.9.2. Future list unchanged. Status Date may stay 2026-08-24; Next stays Future.
- `README.md` badge + footer `Agentix 3.9.2` → `3.9.3`
- `docs/README.md` badge + `Aligned with **Agentix 3.9.2**` → `3.9.3` (one sentence: harvest/reflect skill split)
- `memory/README.md` badge

Do **not** bump `AGENTIC_LOOP_README.md` (stale 3.4.0 on purpose) or `docs/proxy.md` (3.7.0).

- [ ] **Step 4: Full suite + no dirt**

```bash
test "$(cat VERSION)" = "3.9.3"
PYTHONPATH=. pytest memory/ -q
git diff --name-only | grep '^\.agent/' && exit 1 || echo NO_AGENT_DIRT
```

Expected: VERSION 3.9.3; suite green; `NO_AGENT_DIRT`.

- [ ] **Step 5: Commit**

```bash
git add VERSION CHANGELOG.md ROADMAP.md README.md docs/README.md memory/README.md
git commit -m "Обновил версию до 3.9.3: скиллы harvest и reflect"
```

---

### Task 9: Dual remotes, PR, stop (no merge)

**Files:** none

- [ ] **Step 1: Remotes**

```bash
git remote -v
git remote add github https://github.com/unhexx/agentic_loop_template.git 2>/dev/null || true
git remote -v
```

Expected: `origin` Bitbucket `expert/agentic_loop_template`; `github` `unhexx/agentic_loop_template`.

- [ ] **Step 2: Push both**

```bash
git push -u origin HEAD
git push -u github HEAD
```

- [ ] **Step 3: Open PR against origin/main (do not merge)**

Title: `3.9.3 harvest/reflect skill split`

Body must cite: spec path, test command + count, “patch not 3.10.0”, “no `.agent/` dirt”, “consumer remains symlink”.

- [ ] **Step 4: Dirty SSOT `main` leftover**

After the worktree branch exists, **do not** commit leftover on SSOT `main`. Next fire after merge may `git restore` those files on `main` so they do not double-ship. Until merge, leave them uncommitted.

- [ ] **Step 5: Stop**

Do not merge. Do not start a Future item. Report sha, version, remotes, test count.

---

## Self-review

| Spec ID | Task |
|---------|------|
| G1 harvest loader | Task 2 + 3 + 5 |
| G2 reflect loader | Task 2 + 4 + 5 |
| G3 git / jira | Task 2 `test_git_intent_*` |
| G4 reflective-improvement trim | Task 6 |
| G5 docs/prompts | Task 7 |
| G6 pytest memory/ + no .agent | Task 2, 7.8, 8.4 |
| G7 VERSION 3.9.3 last | Task 8 |
| Dual remotes | Task 9 |
| Drop .agent dirt | File map + Task 8.4 grep |

No TBD/placeholder steps. Commit messages are natural Russian. `git add` is path-limited.

**Plan complete and saved to `docs/superpowers/plans/2026-08-25-harvest-reflect-skill-split.md`.** Next fire: worktree + Task 2 (tests first).
