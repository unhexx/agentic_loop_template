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
