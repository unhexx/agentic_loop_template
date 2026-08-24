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
