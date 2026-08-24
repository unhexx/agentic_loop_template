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
