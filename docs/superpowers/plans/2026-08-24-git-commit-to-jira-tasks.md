# git-commit-to-jira-tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the research-backed `git-commit-to-jira-tasks` skill into the Agentix template as a first-class, **disabled-by-default** skill that clusters git commits into INVEST Jira Stories (Fibonacci Story Points primary; Original Estimate optional and team-adjustable) and never writes to Jira without an explicit client-side confirm.

**Architecture:** New sibling tree under `skills/git-commit-to-jira-tasks/` (SKILL.md + `scripts/` + `references/` + empty `assets/`). Load only via explicit user request or a recursive `Follow skills/git-commit-to-jira-tasks/SKILL.md` line. Do **not** attach the skill to `tools/select.py` `SKILL_INTENTS['git']`, role prompts, or `TOOLS_REGISTRY.md` — that would fire on every `--intent git`. Host frontends that honor YAML get `disable-model-invocation: true`. Writes stay client-side dry-run until confirmation; createmeta and issueLinkType are discovered per project (no unscoped `/issue/createmeta`, no assumed Relates/Implements, no server dry-run flag).

**Tech Stack:** Markdown skill package, bash `git log --numstat` helper, Jira Cloud REST v3 / Data Center REST v2, local JSON state under `.agent/`.

**Branch / isolation:** `feature/git-commit-to-jira-tasks-20260824` from `origin/main` (3.9.0, `ae229dd` at plan time). Owned paths only: `skills/git-commit-to-jira-tasks/**`, `skills/README.md`, `.gitignore`. Do not touch `tools/select.py`, `TOOLS_REGISTRY.md`, `DEVELOPMENT_STANDARDS.md`, `docs/integrations.md`, `VERSION`, packaging, supervisor, or P8 files. Do not merge to `main` in this stream — human gate (`PARALLEL_PROTOCOL.md`).

**Source export (adapt, do not copy blindly):** `/home/unhex/Загрузки/git-commit-to-jira-tasks-SKILL-EXPORT.md`

---

## Research corrections vs the export package

These are **required** deltas. The export is the starting package; the numbers and Jira API claims below supersede it.

1. **Cluster, do not 1:1.** OSS commits are heavy-tailed and mostly tiny (median ~1–3 files / ~14–16 lines; ~75–83% extra-small or small). One Jira issue per hash is noise. Cluster to INVEST-Small Stories (days to at most a few person-weeks); split anything at Epic/21+ SP.
2. **Story Points are primary.** Fibonacci relative size, not hours. Mountain Goat and Atlassian treat a fixed “1 SP = N hours” identity as a mistake. Original Estimate is a **separate**, optional field. Default of 6 h/SP in the export is a local heuristic only — never write hours unless the user sets `JIRA_HOURS_PER_SP` **and** timetracking is on the create screen.
3. **Createmeta is paginated.** Do not call unscoped `GET /rest/api/{2|3}/issue/createmeta` (deprecated on Cloud, removed in Jira DC 9.0). Use `GET /rest/api/{2|3}/issue/createmeta/{projectIdOrKey}/issuetypes` then `GET .../issuetypes/{issueTypeId}`. Only fields on that create screen are settable.
4. **No server dry-run.** Create and issueLink POST persist immediately (201 / empty success). Rehearse with GET createmeta, field/search, issueLinkType. Do not POST `/issue` or `/issueLink` until an explicit write step.
5. **Link types are discovered.** Official samples document Blocks and Duplicate, not Relates or Implements. `Implements` is typically custom. `GET /rest/api/{2|3}/issueLinkType` first; skip a link type if it is absent.
6. **Disabled until asked.** Register only a README When-to-load row. Omit `SKILL_INTENTS`. Do not add a CLI `--intent` key.

Empirical anchors (plan-time): Hattori/Lanza / ICPC 2008 (median 2 files / 14 lines); Ohloh 8.7M commits (median 16 LoC); HICSS companion (83.54% are 1–100 SLoC); 2022 GitHub Java (most commits 1–10 files). INVEST: XP123. Estimation: Mountain Goat “Don’t Equate Story Points to Hours”; Atlassian Fibonacci / Jira estimate docs. API: Atlassian Cloud REST v3 Issues, createmeta replacements, issue links; DC REST examples.

---

## File map

| Path | Responsibility |
|------|----------------|
| `skills/git-commit-to-jira-tasks/SKILL.md` | Imperative workflow, opt-in contract, safety |
| `skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh` | Structured `git log --numstat` extractor |
| `skills/git-commit-to-jira-tasks/references/estimation-guide.md` | Commit-size evidence, SP table, clustering, hours heuristic |
| `skills/git-commit-to-jira-tasks/references/story-templates.md` | Summary/description/AC templates, link policy |
| `skills/git-commit-to-jira-tasks/references/jira-api-and-git-patterns.md` | Auth, createmeta, payloads, state file, safety |
| `skills/git-commit-to-jira-tasks/assets/.gitkeep` | Reserved empty assets dir |
| `skills/README.md` | One When-to-load row (explicit request only) |
| `.gitignore` | Ignore `.agent/jira-from-commits-state.json` |

**Out of scope (do not edit in this stream):** `tools/select.py`, `TOOLS_REGISTRY.md`, `docs/integrations.md` (Linear/Jira MCP stub stays a *future* MCP skill — this package is REST + git, not MCP), `VERSION` / product bump, role prompts, `CHANGELOG.md` (optional follow-up after merge).

---

### Task 1: Baseline — skill is absent and git intent stays clean

**Files:**
- Verify only: `skills/README.md`, `tools/select.py`, `skills/`

- [ ] **Step 1: Confirm the skill tree does not exist**

Run:

```bash
test ! -e skills/git-commit-to-jira-tasks && echo ABSENT_OK
python tools/select.py --intent git --list
```

Expected: `ABSENT_OK`. `--list` prints only git preflight/sync blocks (paths under `tools/blocks/`). It must **not** contain `git-commit-to-jira-tasks`.

- [ ] **Step 2: Confirm registry has exactly the two current skills**

```bash
grep -c 'git-commit-to-jira-tasks' skills/README.md || true
grep -E 'reflective-improvement|local-knowledge-ingestion' skills/README.md
```

Expected: grep count `0`. Both existing skill names present in the table.

---

### Task 2: Parser script

**Files:**
- Create: `skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh`

- [ ] **Step 1: Write the helper**

Create `skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh` with mode `0755`:

```bash
#!/usr/bin/env bash
# Helper: structured commit extraction for git-commit-to-jira-tasks.
# Usage: ./parse-git-log.sh [--since=DATE] [--until=DATE] [--max-count=N] [rev-range]
# Outputs machine-friendly records separated by ---COMMIT---
set -euo pipefail

SINCE=""
UNTIL=""
MAX_COUNT=""
RANGE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --since=*) SINCE="${1#*=}"; shift ;;
    --until=*) UNTIL="${1#*=}"; shift ;;
    --max-count=*) MAX_COUNT="${1#*=}"; shift ;;
    --since|--until|--max-count)
      echo "Use $1=VALUE form" >&2
      exit 2
      ;;
    *) RANGE="$1"; shift ;;
  esac
done

ARGS=()
[[ -n "$SINCE" ]] && ARGS+=(--since="$SINCE")
[[ -n "$UNTIL" ]] && ARGS+=(--until="$UNTIL")
[[ -n "$MAX_COUNT" ]] && ARGS+=(--max-count="$MAX_COUNT")
[[ -n "$RANGE" ]] && ARGS+=("$RANGE")

git log \
  --pretty=format:'---COMMIT---%nHASH:%H%nAUTHOR:%an%nEMAIL:%ae%nDATE:%aI%nSUBJECT:%s%nBODY:%b%n---NUMSTAT---' \
  --numstat \
  --reverse \
  "${ARGS[@]}"
```

`--reverse` is required so clusters and `blocks` links are built oldest-first.

- [ ] **Step 2: Smoke-run against this repo**

```bash
chmod +x skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh
skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh --max-count=3
```

Expected: at least one `---COMMIT---` block, a `HASH:` line of 40 hex chars, a `---NUMSTAT---` section. Exit 0.

- [ ] **Step 3: Commit**

```bash
git add skills/git-commit-to-jira-tasks/scripts/parse-git-log.sh
git commit -m "Добавил разбор git log для кластеризации коммитов в Jira"
```

---

### Task 3: Estimation guide (research-corrected)

**Files:**
- Create: `skills/git-commit-to-jira-tasks/references/estimation-guide.md`

- [ ] **Step 1: Write the guide**

Create `skills/git-commit-to-jira-tasks/references/estimation-guide.md`:

```markdown
# Estimation Guide — Commit Size, Clustering, Story Points

Authoritative numbers for this skill. Do not invent a different SP table in SKILL.md.

## Typical commit size (empirical)

Open-source history is heavy-tailed: most commits are tiny; a thin tail of merges, refactors, and bulk updates dominates the mean. That is why 1:1 commit→Jira Task mapping produces noise.

| Population | Median files | Median lines | Small-commit share |
|---|---|---|---|
| Nine OSS systems; gcc 54 536 commits | 2 | 14 | ~75% extra-small or small (files, lines, hunks) |
| Ohloh, Mar 2008, 8 705 118 commits | — | 16 | 50% ≤ 16 LoC (mean 465.72; p90 261; p95 604.5) |
| HICSS companion, >8 million commits | — | — | 83.54% are 1–100 SLoC; one-line commits are the mode (>12%) |
| Hattori & Lanza file-count bins | — | — | tiny 1–5 files ~80%; small 6–25 ~15%; medium 26–125 <5%; large ≥126 <1% |
| 1M commits, 24 popular GitHub Java projects (2022) | 1–3 (2 in 58% of systems) | — | most commits 1–10 files and 1–4 source files |

Hönel et al. (arXiv:2005.13904) report later medians around 67 gross / 51 net LOC — still not Story-sized. There is **no** peer-reviewed commits-per-story ratio; clustering is a heuristic aimed at INVEST “Small” (a few person-days, at most a few person-weeks).

## Clustering heuristics

Group consecutive or related commits into **one Story** when most of these hold:

- Same conventional scope (`feat(auth):` / `fix(auth):`) or overlapping message keywords
- File-path overlap > 40% of unique paths
- Same PR (`gh`) or same topic branch
- Wall-clock proximity (same day / same session) **and** a logical progression (model → service → API → test)

Keep a commit as its own Task or Sub-task when it is a distinct deliverable (docs-only, independent bugfix, infra).

**Split** a cluster when:

- Mapped SP would be 13 without a clear single user outcome
- Mapped SP would be 21+ (Epic-scale — never one issue)
- Distinct user-facing outcomes appear in the messages (auth vs billing)
- The cluster mixes a feature with an unrelated refactor/chore spanning other trees

Target 1–8 SP per Story.

## Story Points (Fibonacci, primary size)

SP is relative effort/complexity, **not** hours. Do not replace SP with Original Estimate.

| SP | Typical cluster | Unique files | Sum \|added\|+\|deleted\| (approx) | Risk | Example |
|----|-----------------|--------------|-------------------------------------|------|---------|
| 1  | Trivial         | 1            | ≤ 15                                | Low  | Typo, config value, comment |
| 2  | Simple          | 1–3          | 15–60                               | Low–medium | Small bugfix, one validation rule |
| 3  | Standard small  | 2–5          | 60–150                              | Medium | One endpoint + tests |
| 5  | Standard        | 4–10         | 150–400                             | Medium–high | Multi-file feature, moderate refactor |
| 8  | Complex         | 8–20         | 400–800                             | High | New subsystem slice, non-trivial state |
| 13 | Large           | 15+          | 800–2000                            | High + uncertainty | Prefer split; one issue only if still INVEST-Small |
| 21+| Epic            | many         | very large                          | Very high | **Always split** — do not create a single issue |

Use the **maximum** of (files band, lines band), then apply adjustments. Lines are a weak proxy (generated files, lockfiles, vendor): down-weight `package-lock.json`, `*.min.js`, generated protobufs, and pure formatting.

**Adjustments** (bump or drop one Fibonacci step, not a fraction):

- High uncertainty / new tech / first integration with an external API: +1 step
- Security, concurrency, or data migration: +1–2 steps
- Pure refactor with existing tests and no behaviour change: −1 step possible
- Tests missing for a behaviour change: +1 step (include writing tests in the Story)

If metrics cannot be collected (binary-only, empty numstat, submodule pointer), mark SP as `needs-manual-review` and do not invent a number.

## Original Estimate (optional, never primary)

Do **not** treat SP as hours. Mountain Goat Software and Atlassian treat a fixed points-to-hours identity as a mistake; the points–hours relationship is a distribution, not an equation.

Third-party Jira recipes commonly use 4 h or 8 h per SP as a **local capacity heuristic**. This skill’s documented range is 4–8 focused hours per SP. There is no Scrum/INVEST/Atlassian-standard conversion.

Write `timetracking.originalEstimate` **only when all** of these are true:

1. The user set `JIRA_HOURS_PER_SP` (positive number) **or** passed `--hours-per-sp=N`
2. Time tracking is enabled and `timetracking` appears on the create screen for that issue type
3. The project is not in Legacy mode that rejects the field

If those fail, omit the field. Never default to 6 h/SP in the payload.

If the user **does** set a rate, compute `originalEstimate` as `{SP * JIRA_HOURS_PER_SP}h` and record both the rate and “heuristic, not identity” in the evidence comment.

Do not use Jira Align Member-Week tables (portfolio config, not a universal mapping).

## Metrics to collect per commit / cluster

From `scripts/parse-git-log.sh` / `git log --numstat --reverse`:

- `files_changed` (unique paths; ignore numstat `-` binary rows for line sums)
- `lines_added`, `lines_deleted`, `net_churn = added + deleted`
- conventional type prefix (`feat` `fix` `refactor` `docs` `test` `chore` `perf` `ci`) when present
- tests present (`test`, `tests`, `spec` in paths, or type `test`)
- cluster time span (`DATE` min/max)
- file overlap with previous cluster (for `blocks`)

Optional enrichment: `gh pr list` / `gh pr view` when the remote is GitHub and `gh` is authenticated — attach PR number in Implementation Notes, do not fail the run if `gh` is missing.
```

- [ ] **Step 2: Commit**

```bash
git add skills/git-commit-to-jira-tasks/references/estimation-guide.md
git commit -m "Описал оценку Story Points по размеру и кластерам коммитов"
```

---

### Task 4: Story templates

**Files:**
- Create: `skills/git-commit-to-jira-tasks/references/story-templates.md`

- [ ] **Step 1: Write templates**

Create `skills/git-commit-to-jira-tasks/references/story-templates.md`:

```markdown
# Jira Issue Templates & Formulation Rules

## Preferred issue types (map via createmeta names, not guesses)

Discover actual names/ids from createmeta. Typical mapping when those types exist:

- **Story** (or “User Story”) — feature-oriented cluster with user/system value
- **Task** — tooling, infra, chore, or no clear end-user role
- **Bug** — `fix` commits where the defect and outcome are clear
- **Sub-task** — only when the project has a subtask type **and** a parent key; payload must include `parent` + subtask issuetype
- **Epic** — do not create from this skill; split instead

If the project has no Story type, use Task and say so in the preview.

## Summary (title)

- ≤ 100 characters (aim 80)
- Imperative or clear noun phrase; include component when useful
- Never start with a commit hash or “Commit …”
- Avoid “update”, “fix stuff”, “changes”, “misc”

Good: `Auth: implement JWT refresh flow`
Bad: `a3f2c1e update`

## Description structure (this order)

Match project markup: ADF for Cloud API when the project uses the documentation format; otherwise Jira wiki markup or Markdown as createmeta implies. Preview in the same format you will POST.

```
h2. User Story / Goal
As a [role or system],
I want [capability],
So that [business or technical benefit].

h2. Context
[1-3 sentences from messages + diff summary — why these commits exist]

h2. Technical Changes
* Key modules/files and behaviour changes (no full diffs)

h2. Acceptance Criteria
* [ ] AC1 — testable statement
* [ ] AC2

h2. Implementation Notes
- Related commits: [short hash + one-line, oldest first]
- Branch / PR: [if known]
- Dependencies: [hashes or proposed issue summaries that must land first]
- Risks / follow-ups

h2. Estimates
- Story Points: X (or needs-manual-review)
- Original Estimate: omitted | Yh at N h/SP heuristic (not an identity)
```

Acceptance criteria must be testable (“API returns 401 for expired refresh token”), not “code is committed”.

## Link policy

1. `GET /rest/api/{2|3}/issueLinkType` and keep the name/id map.
2. Use **Blocks** when that type exists and commit/file order implies a sequence (write-before-read, “depends on” in messages).
3. Use **Duplicate** only for true duplicates (rare in this skill).
4. Use **Relates** only if the project actually has it.
5. Use **Implements** / “is implemented by” only if the project has that custom type — do not assume it.
6. One link per `POST /issueLink`. Alternatively a single `update.issuelinks` add on create if Linked Issues is on the create screen (still one link per create/edit call).
7. Create parents first, then children, then links. Checkpoint after each Story or every 5 issues.

## Language

If the user or repo history is Russian-speaking, write Summary and Description in clear technical Russian; keep identifiers, API names, and Agile nouns (Story Points, INVEST) in English. Otherwise English. Do not mix randomly inside one issue.

## Labels / components

Add label `from-git-commits` only if labels are on the create screen. Set `components` only when the name matches an existing component from createmeta. Do not invent components.
```

- [ ] **Step 2: Commit**

```bash
git add skills/git-commit-to-jira-tasks/references/story-templates.md
git commit -m "Добавил шаблоны формулировок Story и правила связей Jira"
```

---

### Task 5: Jira API and git patterns

**Files:**
- Create: `skills/git-commit-to-jira-tasks/references/jira-api-and-git-patterns.md`

- [ ] **Step 1: Write API patterns**

Create `skills/git-commit-to-jira-tasks/references/jira-api-and-git-patterns.md`:

```markdown
# Jira API Patterns & Git Analysis Patterns

## Auth and environment

Required:

- `JIRA_BASE_URL` — `https://example.atlassian.net` or DC base (no trailing slash)
- `JIRA_PROJECT_KEY`
- Cloud: `JIRA_EMAIL` + `JIRA_API_TOKEN` (Basic) **or** `JIRA_PAT` if the site accepts Bearer
- Data Center: `JIRA_PAT` as Bearer

Optional:

- `JIRA_HOURS_PER_SP` — only if the team wants Original Estimate written
- `AUTO_CONFIRM=1` — allow writes after preview **only** when the user set this for the run
- State path default: `.agent/jira-from-commits-state.json`

Never print tokens. Prefer env over asking the user to paste secrets into chat.

API version: Cloud REST **v3**; Data Center REST **v2**. Detect from `JIRA_BASE_URL` (atlassian.net → v3) or user flag `--jira-api=2|3`.

## Discovery (always before any POST)

Do **not** call unscoped `GET /rest/api/{2|3}/issue/createmeta` (deprecated on Cloud; removed in Jira Data Center 9.0).

```
GET /rest/api/{2|3}/issue/createmeta/{projectIdOrKey}/issuetypes
GET /rest/api/{2|3}/issue/createmeta/{projectIdOrKey}/issuetypes/{issueTypeId}
GET /rest/api/{2|3}/issueLinkType
GET /rest/api/{2|3}/myself
GET /rest/api/{2|3}/field          # or field/search — resolve Story Points customfield id
```

Connectivity check is `GET myself` or a cheap project GET. Then cache:

- issue types (id, name, subtask flag)
- required fields and allowed values for the chosen type
- Story Points field id (`customfield_<id>` — often 10016 on Cloud **but never hard-code**)
- whether `duedate`, `labels`, `timetracking`, Linked Issues appear on the create screen
- link type names (`Blocks`, `Duplicate`, maybe `Relates`, maybe custom `Implements`)

Only fields returned for that issue type are settable. Hidden fields are not writable via createmeta.

`duedate` format is `YYYY-MM-DD` and only if present. This skill does **not** set due dates unless the user passes `--due=YYYY-MM-DD`.

## Create payloads

Cloud single: `POST /rest/api/3/issue` → 201  
Cloud bulk: `POST /rest/api/3/issue/bulk` (max 50) — use only after preview of the same batch  
DC: `POST /rest/api/2/issue` (do not assume a DC bulk twin of Cloud bulk)

Minimum body:

```json
{
  "fields": {
    "project": { "key": "ABC" },
    "issuetype": { "id": "10001" },
    "summary": "Auth: implement JWT refresh flow",
    "description": "<format required by the project>"
  }
}
```

Sub-task additionally needs a subtask type and `parent: { "key": "ABC-123" }`.

Story Points: `fields["customfield_<id>"] = <number>` only if that field is on the create screen.

Original Estimate, only when allowed (see estimation-guide.md):

```json
"timetracking": { "originalEstimate": "12h" }
```

Omit `timetracking` in Legacy mode or when the field is absent.

Links after create:

```
POST /rest/api/{2|3}/issueLink
{
  "type": { "name": "Blocks" },
  "inwardIssue": { "key": "ABC-2" },
  "outwardIssue": { "key": "ABC-1" }
}
```

Direction: outward `Blocks` inward means the outward issue blocks the inward issue. Confirm the site’s inward/outward labels from issueLinkType. Checkpoint after each link.

Evidence comment (Cloud ADF or DC wiki) on every created issue: commit hashes + subjects, files/lines, SP rationale, hours heuristic if used, analysis run id. Do not POST comments until the issue exists.

## Dry-run is client-side

There is no server dry-run query flag (documented create query param is `updateHistory` only). Preview = print payloads + human report, write nothing. `AUTO_CONFIRM` skips the wait but still requires that preview was produced in the same run.

## Git commands

Prefer `scripts/parse-git-log.sh`. Equivalent core:

```bash
git rev-parse --is-inside-work-tree
git log --reverse --numstat --pretty=format:'---COMMIT---%nHASH:%H%nAUTHOR:%an%nEMAIL:%ae%nDATE:%aI%nSUBJECT:%s%nBODY:%b%n---NUMSTAT---' --since=... --until=... RANGE
```

Numstat data rows: `added<TAB>deleted<TAB>filename`. Binary: `-<TAB>-<TAB>filename`.

## State file (idempotency)

Path: `.agent/jira-from-commits-state.json` (gitignored). Shape:

```json
{
  "version": 1,
  "project": "ABC",
  "jira_base_url": "https://example.atlassian.net",
  "hours_per_sp": null,
  "runs": [
    {
      "id": "2026-08-24T12:00:00Z",
      "range": "v3.8.0..HEAD",
      "mode": "dry-run"
    }
  ],
  "commits": {
    "abc123deadbeef": {
      "issue": "ABC-42",
      "role": "story|task|subtask|skipped",
      "cluster_id": "c1"
    }
  },
  "clusters": {
    "c1": {
      "issue": "ABC-42",
      "sp": 3,
      "hashes": ["..."]
    }
  }
}
```

Skip mapped hashes unless `--force-reprocess`. Support `--resume`, `--max-new-issues=N`, `--dry-run` (default), `--since`, `--until`, `--branch` / rev-range.

## Safety

- Default mode is dry-run.
- Do not POST `/issue` or `/issueLink` until explicit confirm or `AUTO_CONFIRM=1`.
- Do not log tokens.
- Do not create an issue per commit unless the user explicitly asks for 1:1 (and even then warn).
- Stop a run that cannot resolve project/issuetype/required fields; ask rather than guess.
```

- [ ] **Step 2: Commit**

```bash
git add skills/git-commit-to-jira-tasks/references/jira-api-and-git-patterns.md
git commit -m "Зафиксировал безопасные вызовы Jira API и формат состояния"
```

---

### Task 6: SKILL.md (opt-in, imperative)

**Files:**
- Create: `skills/git-commit-to-jira-tasks/SKILL.md`

- [ ] **Step 1: Write the skill body**

Create `skills/git-commit-to-jira-tasks/SKILL.md`:

```markdown
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
```

- [ ] **Step 2: Frontmatter sanity**

```bash
python - <<'PY'
from pathlib import Path
text = Path("skills/git-commit-to-jira-tasks/SKILL.md").read_text(encoding="utf-8")
assert text.startswith("---\n")
fm = text.split("---", 2)[1]
for key in ("name: git-commit-to-jira-tasks", "disable-model-invocation: true", "version: 1.0.0"):
    assert key in fm, key
assert "SKILL_INTENTS" not in text
assert "--intent git" in text
print("FRONTMATTER_OK")
PY
```

Expected: `FRONTMATTER_OK`

- [ ] **Step 3: Commit**

```bash
git add skills/git-commit-to-jira-tasks/SKILL.md
git commit -m "Добавил opt-in скилл разбора коммитов в задачи Jira"
```

---

### Task 7: Reserved assets directory

**Files:**
- Create: `skills/git-commit-to-jira-tasks/assets/.gitkeep`

- [ ] **Step 1: Keep the empty assets dir**

```bash
mkdir -p skills/git-commit-to-jira-tasks/assets
: > skills/git-commit-to-jira-tasks/assets/.gitkeep
```

- [ ] **Step 2: Commit**

```bash
git add skills/git-commit-to-jira-tasks/assets/.gitkeep
git commit -m "Зарезервировал каталог assets для скилла Jira"
```

If the previous skill commits can be squashed by the implementer, combining Tasks 6–7 is acceptable; do not squash the plan commit with implementation.

---

### Task 8: Registry row (explicit load only) + gitignore

**Files:**
- Modify: `skills/README.md` (table after `local-knowledge-ingestion`)
- Modify: `.gitignore` (append after `.agent/sft/`)

- [ ] **Step 1: Add the When-to-load row**

In `skills/README.md`, after the `local-knowledge-ingestion` table row, insert exactly:

```markdown
| [git-commit-to-jira-tasks](git-commit-to-jira-tasks/SKILL.md) | Cluster git commits into INVEST Jira Stories/Tasks with Fibonacci Story Points (hours optional) | **Explicit user request only** or `Follow skills/git-commit-to-jira-tasks/SKILL.md`. Never `--intent git` |
```

Do not add a `select.py --intent` example for this skill in the Usage block.

- [ ] **Step 2: Ignore the state file**

In `.gitignore`, immediately after the `.agent/sft/` line, append:

```
.agent/jira-from-commits-state.json
```

- [ ] **Step 3: Prove the loader still does not pull the skill**

```bash
python tools/select.py --intent git --list
python tools/select.py --intent git --list | grep -i jira && exit 1 || echo GIT_INTENT_CLEAN
grep -F 'git-commit-to-jira-tasks' skills/README.md
grep -F '.agent/jira-from-commits-state.json' .gitignore
test -f skills/git-commit-to-jira-tasks/SKILL.md
```

Expected: `GIT_INTENT_CLEAN`; README and gitignore matches; skill file present. `tools/select.py` is **unchanged** (`git diff -- tools/select.py` empty).

- [ ] **Step 4: Commit**

```bash
git add skills/README.md .gitignore
git commit -m "Подключил скилл Jira в реестр только по явному запросу"
```

---

### Task 9: Preflight and feature-branch push (no merge to main)

**Files:** none besides what is already committed.

- [ ] **Step 1: Cheap git preflight**

```bash
./scripts/preflight_git.sh
```

Expected: exit 0. Record exit code + the `branch=` / `head=` lines only (not full `gh` JSON).

- [ ] **Step 2: Tree and loader check**

```bash
find skills/git-commit-to-jira-tasks -type f | sort
git diff --name-only origin/main...HEAD
python tools/select.py --intent git --list
```

Expected files only under:

- `skills/git-commit-to-jira-tasks/`
- `skills/README.md`
- `.gitignore`
- (plus this plan file if it landed on the same branch)

No `tools/select.py`, `VERSION`, `DEVELOPMENT_STANDARDS.md`, `docs/integrations.md`.

- [ ] **Step 3: Push the feature branch**

```bash
git push -u origin feature/git-commit-to-jira-tasks-20260824
```

- [ ] **Step 4: Stop before main**

Do **not** `git merge --no-ff` into `main` and do **not** `git push origin main` from this stream. Human gate: open a PR if asked (`gh pr create`), otherwise leave the branch for review.

`DEVELOPMENT_STANDARDS.md` §11 describes a self-cycle merge into main; `PARALLEL_PROTOCOL.md` says merge PR to main is a human gate. For this opt-in skill, **the human gate wins**.

Russian commit messages already used above; keep that voice (no AI/agent/loop/model words). If a PR is opened, title/body in the same voice.

---

## Self-review

**Spec coverage**

| Requirement | Task |
|-------------|------|
| Cluster to INVEST Stories, not 1:1 hashes | Task 3 + SKILL steps 3–4 |
| Fibonacci SP primary | Task 3 + SKILL step 4 |
| No default hours identity; 6 h/SP not written unless user rate set | Task 3 + Task 5 + SKILL inputs |
| Paginated createmeta replacements | Task 5 |
| Client-side dry-run; no POST until confirm | Task 5 + SKILL steps 6–7 |
| Discover link types; Blocks/Duplicate official | Task 4 + Task 5 |
| Disabled-by-default: README only, no SKILL_INTENTS | Task 6 + Task 8 |
| Disjoint paths; no P8/select.py/VERSION | File map + Task 9 |
| Feature branch; no merge to main | Task 9 |
| State file gitignored | Task 8 |
| Parser oldest-first | Task 2 `--reverse` |
| Export scripts/references/assets layout | Tasks 2–7 |

**Placeholder scan:** none — every create/modify step has full file text.

**Type consistency:** state shape `commits` / `clusters` / `hours_per_sp` is the same in Task 5 and SKILL.md. Env names `JIRA_*`, `AUTO_CONFIRM` match across files. Skill directory name = frontmatter `name` = README link.

**Loader consistency:** `INTENTS['git']` and `SKILL_INTENTS` stay as in `tools/select.py` at 3.9.0. No new `--intent` key.
