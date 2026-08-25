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
