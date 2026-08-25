# Harvest / Reflect Skill Split — Design (Agentix v3.9.3)

**Title:** Split harvest vs reflect into first-class skills  
**Author:** Agentix SSOT cycle fire (detached)  
**Date:** 2026-08-25  
**Status:** Accepted for implementation (next fire)  
**Repo / home:** `agentic_loop_template` (Agentix harness)  
**Baseline:** VERSION **3.9.2**, `main` `103976c` (Blackbox 3.9.2 at `0a864a5`, then pxpipe-agy docs merge; VERSION unchanged). ROADMAP next = Future.  
**Target version:** **3.9.3** (patch: skill routing + docs — not a product-facing 3.10.0)  
**House style:** match [2026-08-25-blackbox-cli-adapter-design.md](2026-08-25-blackbox-cli-adapter-design.md) structure/quality, shorter because this is docs/routing only.  
**Canonical landing path:** `docs/superpowers/specs/2026-08-25-harvest-reflect-skill-split-design.md`

This document is the execute-plan input for shipping the **uncommitted leftover** harvest/reflect skill split as **3.9.3**, and for **dropping** `.agent/` runtime dirt. It does **not** reopen Blackbox, P8 packaging, Control Plane, git-commit-to-jira-tasks, or newportal Go rewrite.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Drop leftover; pick a Future item | `git restore` skill files; delete untracked skills; start P8-08 token estimate / P8-11 locking / … | Rejected. HEAD maps **both** `--intent harvest` and `--intent reflect` to `skills/reflective-improvement/SKILL.md`. That is a real routing bug, and the leftover already fixes it. |
| B. Ship leftover as-is | Include `.agent/` P-DEMO-001 ledger/playbook timestamp churn | Rejected. Demo dirt, not product. |
| **C. Ship skill split only as 3.9.3** | Keep two new skills + `select.py` + prompt/doc pointers; restore `.agent/`; add `pytest memory/` tests; trim harvest steps out of `reflective-improvement`; VERSION last | **Accepted.** |

`.agent/` dirt was restored on SSOT working tree this fire (`git restore -- .agent/…`). Leftover product files remain uncommitted on `main` (do **not** commit them on dirty `main`; next fire uses a worktree from `origin/main`).

---

## Overview

HEAD 3.9.2 has one ritual skill (`reflective-improvement`) that mixes three jobs:

1. **Write** cross-project experience into workspace memory (`experience_harvester cycle`).
2. **Change** the harness from that memory (query → playbook/prompt/meta apply-safe).
3. **Write up** a 6-step reflection.

`tools/select.py` `SKILL_INTENTS` currently points **both** `harvest` and `reflect` at that one file. Reviewer DONE prompts tell agents to run harvest commands inline. Agents either skip harvest, or rewrite prompts while harvesting.

The leftover (working tree, not committed) already introduces:

| Skill | Job | Loader |
|-------|-----|--------|
| `skills/experience-accumulation/SKILL.md` | Dry-run then `--apply` `experience_harvester cycle`. Do **not** change prompts/playbooks. | `--intent harvest` |
| `skills/loop-self-improve/SKILL.md` | Query memory, then propose/apply-safe. Do **not** harvest. REQUIRED SUB-SKILL: `reflective-improvement` for the 6-step write-up. | `--intent reflect` |
| `skills/reflective-improvement/SKILL.md` | 6-step write-up only (sub-skill). | Loaded by loop-self-improve, not by `select.py` |

That split is the product. Implementation still needs: tests under `memory/` (CI is `pytest memory/`), a contradiction trim in `reflective-improvement` (it still tells agents to run `experience_harvester cycle`), VERSION/CHANGELOG/badges, worktree isolation, dual-remote push.

---

## Current state (verified 2026-08-25)

### SSOT git

- Path: `/home/unhex/_PROJECT/agentic_loop_template`
- Branch: `main` @ `103976c` = origin/main, VERSION `3.9.2` (parent `0a864a5` Blackbox; `103976c` pxpipe-agy docs, still unreleased in CHANGELOG)
- Remote: **only** `origin` → `https://git.aservice24.ru/scm/expert/agentic_loop_template.git`
- GitHub repo exists (`gh repo view unhexx/agentic_loop_template`) but **no local `github` remote**. Dual-push at ship must `git remote add github …` if still missing.
- Consumer `/home/unhex/_PROJECT/newportal/agentic_loop_template` remains a **symlink**. Do not vendor. Do not start Go rewrite.

### HEAD routing (bug)

`tools/select.py` on `origin/main` (unchanged since `0a864a5`):

```
SKILL_INTENTS = {
    "reflect": ["skills/reflective-improvement/SKILL.md"],
    "knowledge": ["skills/local-knowledge-ingestion/SKILL.md"],
    "compress": ["skills/README.md"],
    "harvest": ["skills/reflective-improvement/SKILL.md"],  # same file as reflect
}
INTENTS["harvest"] = ["common/experience.md"]  # commands; keep
INTENTS["reflect"] = []  # no tool block; skill only
```

`skills/README.md` on HEAD: “Cross-project harvest is not a separate skill file”.

### Leftover (keep; do not commit on dirty main)

Modified: `EXPERIENCE_EXTRACTION_TOOLS.md`, `PROMPT_COMPRESSION_GUIDE.md`, `SYSTEM_PROMPT.md`, `prompts/short_reviewer_prompt.md`, `skills/README.md`, `skills/local-knowledge-ingestion/SKILL.md`, `skills/reflective-improvement/SKILL.md` (pipeline note only), `tools/blocks/common/experience.md`, `tools/select.py`.

Untracked: `skills/experience-accumulation/SKILL.md`, `skills/loop-self-improve/SKILL.md`.

### Leftover dropped this fire

`.agent/HUB_INDEX.json`, `LOOP_PERFORMANCE.md`, `PERFORMANCE_LEDGER.json`, `PERFORMANCE_LEDGER.md`, `PLAYBOOKS.json`, `PLAYBOOKS/overview.md` — demo `P-DEMO-001` timestamps. Restored to HEAD.

### Tests

- Baseline claim: `PYTHONPATH=. pytest memory/` → 180 passed, 6 skipped (3.9.2).
- No `memory/` test currently imports `tools/select.py` or names the new skills.
- CI / exact command stays `pytest memory/` (and `go test` is newportal — out of scope).

---

## Goals / Non-goals

### Goals (G)

| ID | Goal |
|----|------|
| G1 | `--intent harvest` loads `tools/blocks/common/experience.md` **and** `skills/experience-accumulation/SKILL.md`. It does **not** load `loop-self-improve` or `reflective-improvement`. |
| G2 | `--intent reflect` loads `skills/loop-self-improve/SKILL.md` only. Progressive disclosure: 6-step write-up is a REQUIRED SUB-SKILL, not auto-inlined. Does **not** load `experience-accumulation`. |
| G3 | `--intent git` still does **not** load `git-commit-to-jira-tasks` (3.9.1 contract). |
| G4 | `reflective-improvement` body no longer instructs parent-folder `experience_harvester cycle` or apply-safe/export-sft. Those stay in the two new skills. Pipeline one-liner at top stays. |
| G5 | Reviewer DONE / SYSTEM_PROMPT / EXPERIENCE_EXTRACTION_TOOLS / compression guide / local-knowledge integration sentences point at the new names. |
| G6 | `pytest memory/` includes routing tests and stays green. No `.agent/` runtime files in the diff. |
| G7 | VERSION **3.9.3** in the final docs PR only (same pattern as 3.9.2 PR4). Patch, not 3.10.0. |

### Non-goals (NG)

| ID | Out of scope |
|----|----------------|
| NG1 | New harvester/meta code, new `--intent` keys, TOOLS_REGISTRY intent table expansion |
| NG2 | Changing `experience_harvester` / `meta_harvester` / supervisor `maybe_cycle_on_done` |
| NG3 | Skill TDD-via-subagent pressure tests (writing-skills iron law). This fire ships **routing**; bodies are short command maps over existing CLIs already tested in `memory/test_experience_harvester.py`. |
| NG4 | Future items: token estimate, docs i18n, concurrent `.agent/` locking, Hub SaaS |
| NG5 | newportal Go rewrite; vendoring the consumer symlink; copying secrets; modifying `aservice24_22-08-2026` |
| NG6 | Auto-merge to `main`. Human gate (`PARALLEL_PROTOCOL.md`). |
| NG7 | Committing `.agent/` ledger/playbook demo churn |

---

## Architecture

Two thin SKILL.md files + loader map. No new Python product module.

```
Reviewer DONE (parent-folder session)
  1. experience-accumulation   → dry-run cycle --parent ..  [then --apply if real]
  2. loop-self-improve         → memory query
       └─ reflective-improvement (sub-skill)  → 6-step write-up
       └─ apply-safe / playbooks curate / optional export-sft
```

**Isolation:** harvest never edits prompts/playbooks/standards. Reflect never runs `experience_harvester`. Write-up never claims to own harvest or apply-safe.

**Loader:** `tools/select.py` `SKILL_INTENTS` only. Do **not** add new argparse intents. `INTENTS['harvest']` keeps `common/experience.md` (flags/commands). `INTENTS['reflect']` stays empty (skill only).

**Consumer:** symlink `newportal/agentic_loop_template -> ../agentic_loop_template`. After merge, consumer sees 3.9.3 without a copy.

---

## Approaches considered

1. **Keep one skill, document two modes inside it.** Cheapest docs. Rejected: `select.py` cannot load different files per intent; Reviewer still mixes harvest+rewrite.
2. **Three equal skills all in SKILL_INTENTS['reflect'].** Rejected: blows the reflect context budget; harvest would still be missing as its own intent.
3. **Two new skills + write-up remains sub-skill; harvest/reflect intents point at different files.** Accepted. Matches leftover. Token-cheap. Tests can lock the map.

---

## File map

| Path | Responsibility |
|------|----------------|
| `skills/experience-accumulation/SKILL.md` | Harvest-only steps; CSO description starts with `Use when…` |
| `skills/loop-self-improve/SKILL.md` | Query + apply-safe; points at reflective-improvement |
| `skills/reflective-improvement/SKILL.md` | 6-step write-up; **strip harvest/apply-safe/export-sft commands** |
| `skills/README.md` | Table rows + usage comment |
| `skills/local-knowledge-ingestion/SKILL.md` | Integration sentence |
| `tools/select.py` | `SKILL_INTENTS` harvest/reflect paths |
| `tools/blocks/common/experience.md` | Pointer to experience-accumulation |
| `EXPERIENCE_EXTRACTION_TOOLS.md` | Reviewer integration sentence |
| `PROMPT_COMPRESSION_GUIDE.md` | DONE → loop-self-improve |
| `SYSTEM_PROMPT.md` | 3.6 self-learning sentence (names only; leave “Template Version: 3.6.0”) |
| `prompts/short_reviewer_prompt.md` | Mandatory DONE path |
| `memory/test_select.py` | **New.** Routing tests collected by `pytest memory/` |
| `VERSION` + badges + CHANGELOG + ROADMAP | **Final PR only** |
| `.agent/*` | **Do not touch** |

---

## Testing

Add `memory/test_select.py` (importlib-load `tools/select.py` so CI `pytest memory/` sees it).

Must assert:

- `resolve_paths("harvest", "linux")` contains `tools/blocks/common/experience.md` and `skills/experience-accumulation/SKILL.md`; excludes the other two skills.
- `resolve_paths("reflect", "linux")` contains `skills/loop-self-improve/SKILL.md`; excludes harvest skill and does **not** auto-include `reflective-improvement`.
- `resolve_paths("git", "linux")` excludes `git-commit-to-jira-tasks` and harvest/reflect skills.
- Skill files exist and YAML `name:` matches folder.

TDD: on clean `origin/main` the harvest/reflect tests **fail** (paths still point at `reflective-improvement`). Implement skills + `SKILL_INTENTS`, then green.

Do **not** run live `experience_harvester cycle --apply` against `_PROJECT`. Harvester behavior is already covered.

---

## Versioning

Patch **3.9.3**, not 3.10.0: no wizard default change, no new HTTP/CLI product surface, no adapter. Two skill files + loader map + docs.

VERSION bump files (copy 3.9.2 list): `VERSION`, `CHANGELOG.md`, `ROADMAP.md` (badge + Milestones row **v3.9.3**; Future list unchanged), `README.md` (badge **and** footer), `docs/README.md` (badge + “Aligned with **Agentix 3.9.2**” → 3.9.3), `memory/README.md` (badge).

---

## Dual remotes

At ship (last PR):

```bash
git remote add github https://github.com/unhexx/agentic_loop_template.git   # skip if present
git push -u origin HEAD
git push -u github HEAD
```

Do not force-push `main`. Open PR on Bitbucket origin; GitHub tracks the same branch.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Dirty `main` leftover committed with `.agent/` dirt | Worktree from `origin/main`; never `git add -A`; path-limited `git add` |
| `reflective-improvement` still harvests | Explicit trim task + grep test that the file does not contain `experience_harvester cycle` |
| Consumer accidentally vendored | Assert `newportal/agentic_loop_template` is a symlink after tests |
| Dual-remote forgotten | Ship task includes `git remote -v` evidence |

---

## Open questions — closed here

1. **Ship or drop leftover?** Ship skill split; drop `.agent/` dirt. Closed.
2. **Load reflective-improvement on `--intent reflect`?** No. Sub-skill, progressive. Closed.
3. **3.9.3 vs 3.10.0?** Patch. Closed.
4. **Subagent pressure-test the new SKILL.md bodies?** No for 3.9.3 (NG3). Closed.

---

## Implementation handoff

Next fire: follow [../plans/2026-08-25-harvest-reflect-skill-split.md](../plans/2026-08-25-harvest-reflect-skill-split.md). One slice per fire. Worktree + tests before VERSION.
