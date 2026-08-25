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
