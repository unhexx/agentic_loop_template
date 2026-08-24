---
name: git-commit-to-jira-tasks
author: agentix / exception.expert
version: 1.0.0
disable-model-invocation: true
description: >
  Opt-in Agentix skill (disabled by default; do not auto-invoke).
  Clusters git commit history into INVEST-sized Jira Stories/Tasks with
  Fibonacci Story Points as the primary size and optional Original Estimate
  only when the team sets a hours-per-SP heuristic. Load only when the user
  explicitly asks to create Jira issues from commits or says to follow
  skills/git-commit-to-jira-tasks/SKILL.md.
---

# git-commit-to-jira-tasks

**Purpose:** Turn a git range into a small set of well-formed Jira backlog items that match INVEST “Small”, with evidence-based Story Points and a client-side dry-run by default.

**Load contract:** Explicit user request or `Follow skills/git-commit-to-jira-tasks/SKILL.md`. Never via `python tools/select.py --intent git`. Hosts that honor YAML must treat `disable-model-invocation: true`.

## Core principles

- Evidence-based: every SP cites files, churn, type, tests.
- INVEST-friendly: cluster tiny commits; split 13–21+ clusters.
- Idempotent: state file maps hash → issue key; resume is the default.
- SP primary; hours optional and never an identity with points.
- Safety: preview first; no POST `/issue` or `/issueLink` without confirm.

## Required inputs (ask if missing)

- Repo path or current worktree
- `JIRA_PROJECT_KEY` and `JIRA_BASE_URL`
- Auth env (`JIRA_PAT` or `JIRA_EMAIL` + `JIRA_API_TOKEN`)
- Range: `--since` / `--until` / rev-range / branch
- Mode: `dry-run` (default) or `create` after confirm
- Language: RU or EN (match the user)
- Optional: `JIRA_HOURS_PER_SP` / `--hours-per-sp` (omit Original Estimate if unset)

## Workflow (checkpoint after each major step)

### 1. Environment and auth

- `git rev-parse --is-inside-work-tree`
- Load credentials from env; `GET myself` (or project) — no token echo
- Discover createmeta **replacements** and `issueLinkType` (see `references/jira-api-and-git-patterns.md`)
- Load or init `.agent/jira-from-commits-state.json`

### 2. Collect commits

- Run `scripts/parse-git-log.sh` with the requested bounds (`--reverse` is already in the script)
- Parse HASH / AUTHOR / DATE / SUBJECT / BODY / numstat into records
- Optionally attach PR metadata via `gh` when available; continue if `gh` is missing
- Persist the raw list in the run report (not secrets)

### 3. Cluster

- Apply heuristics in `references/estimation-guide.md`
- Aim 1–8 SP per Story; split Epic-scale
- Record sequential dependencies (file write order, “depends on”, chronology)

### 4. Estimate

- Aggregate unique files, churn, dominant conventional type, tests
- Map SP from the table + adjustments in `references/estimation-guide.md`
- Original Estimate **only** if `JIRA_HOURS_PER_SP` is set and `timetracking` is on the create screen
- If numstat is missing, mark `needs-manual-review` — do not invent metrics

### 5. Formulate

- Apply `references/story-templates.md` (summary, description order, AC)
- Russian technical voice when the user/repo is RU
- Build create payloads using only createmeta-allowed fields
- Label `from-git-commits` only if labels are allowed

### 6. Preview and confirm

Report:

- Counts of Stories / Tasks / Sub-tasks
- Each: summary, SP, hours (or omitted), hashes, proposed links
- Full description for the first few
- JSON payloads that **would** be POSTed

Stop in dry-run. Wait for explicit confirm unless `AUTO_CONFIRM=1`.

### 7. Create and link (write step only)

- Create parents, then children
- Link using discovered types only (`Blocks` preferred for sequence)
- Evidence comment on each created issue
- Update state after every Story or every 5 issues
- Cloud bulk POST `/rest/api/3/issue/bulk` is allowed up to 50 **after** the same batch was previewed; DC stays single `POST /rest/api/2/issue`

### 8. Resume

- Skip mapped hashes unless `--force-reprocess`
- `--resume`, `--max-new-issues=N`
- Final Markdown report: keys, summaries, SP totals, hours if any, skipped/failed, source ranges

## Resource references

- `references/estimation-guide.md` — numbers and clustering (SSOT)
- `references/story-templates.md` — formulation and links
- `references/jira-api-and-git-patterns.md` — endpoints, state, safety
- `scripts/parse-git-log.sh` — commit extraction

## Anti-patterns

- One Jira issue per commit (unless the user explicitly demands 1:1, then warn)
- Hours as the primary size, or silent 6 h/SP default in the payload
- Unscoped `/issue/createmeta`
- Assuming Relates or Implements exist
- POST without preview
- Ignoring the state file (duplicates)
- Loading this skill from `--intent git`

## Output

Always a Markdown report of proposed or created keys, summaries, SP, and commit ranges. Never invent metrics.
