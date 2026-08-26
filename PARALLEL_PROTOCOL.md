# Parallel Workstream Protocol (Grok-native)

## When to parallelize

Use parallel streams when **two or more INVEST items touch disjoint paths** and Reviewer can merge independently.

Do **not** parallelize when both streams must edit the same hot files (`DEVELOPMENT_STANDARDS.md`, shared schemas, package `__init__`) without an integration owner.

## Roles

| Role | Duty |
|------|------|
| Orchestrator | Assign streams, `owned_paths`, worktrees; no product code in shared hot files |
| Stream Coder/Tester | Work only under `owned_paths`; handoff with `stream` + `worktree` |
| Integration Reviewer | Merge order, conflict resolution, state CLI updates, DONE gate |

## Handoff extensions

```json
{
  "stream": "meta",
  "worktree": "../agentic-loop-worktrees/20260729-meta",
  "owned_paths": ["memory/", "tools/"],
  "merge_gate": "after-tests-green"
}
```

`persist_role_handoff` stamps `stream` / `owned_paths` / `worktree` from the live ContextVar when a stream is active (ContextVar is authority on mismatch). Single-stream `run` leaves the keys absent.

## Scripts

```bash
./scripts/agentic_loop.sh --workstreams harness,docs
# WT_BASE default: ../agentic-loop-worktrees

python -m memory.stream_lease claim --stream harness:memory/,tools/ --workdir . --worktree PATH
python -m memory.stream_lease status --workdir .
python -m memory.stream_lease release --stream harness --workdir .
```

## Operator session recipe

Conflict-free parallel **operator** sessions (two live worktrees, not only `run-parallel`):

1. **Claim** exclusive `owned_paths`: `python -m memory.stream_lease claim --stream name:paths/ --workdir HUB --worktree PATH`.
2. **Worktree** — work only in the claimed git worktree. Hub `HEAD` stays on `main`. Do not `git checkout` the integration branch in the hub clone.
3. **Fence** — supervisor role prompts under `use_stream` include an English stream fence (name, worktree, owned_paths, hot-file ban, language/authorship rules). Compress keeps the fence (appended after `_maybe_compress_prompt`).
4. **Persist stamp** — handoff JSON carries `stream` / `owned_paths` / `worktree` from ContextVar; live CLI children see the same keys in the **child env dict** (`apply_stream_env`, **once** per spawn). Concurrent path does not patch process-global `os.environ`.
5. **Gate** — after `PR_READY*`, `check_owned_paths_gate` (honors `require_owned_paths`; default / missing / `true` enforce; explicit `false` skips with a WARNING).
6. **Push** — opt-in `run-parallel --push` (or `supervisor.parallel.push`) pushes stream branches after `STREAM_READY`, then the integration branch, remote default `origin`. Refuses `main` / `master`. Failure → `BLOCKED`, skip `gh pr create`.
7. **`gh pr create`** runs from the **integration worktree** (`cwd` is that worktree, `--base main`). Supervisor **never** merges `main`. Never `git push main`.
8. **Human** merges the PR to `main` and pushes `main` (and `github/main` if dual-remote).

## Leases

- Registry: hub `.agent/stream_leases.json` under `agent_lock(name="leases")`.
- CLI: `claim` / `renew` / `release` / `status`.
- Overlap with a **live PID** raises (same message shape as `validate_stream_plans`), **even if TTL expired**. **Live PID is never stolen.**
- Dead or unreadable PID may be recovered. `PermissionError` on `os.kill(pid, 0)` = live, do not steal.
- TTL (`supervisor.parallel.lease_ttl_s`, default **7200**) is display-only. `run_parallel` claims after validate, renews on hub `streams_state` ticks, releases in `finally`.

## STOP fan-out

`python -m memory.supervisor stop --workdir HUB` and Control Plane `POST /actions/stop` write hub `.agent/STOP` **and** each worktree listed in `streams_state.json` / leases. A stream still finishes the current adapter turn (up to `role_timeout_s`). No ThreadPool cancel.

Control Plane `:8112` **Streams** page (`GET /streams`) shows per-stream status, worktree, heartbeat age, STOP.

## Language

- Agent instructions and prompts stay **English**.
- Commits, code comments, and human-facing Russian docs stay **Russian** (`DEVELOPMENT_STANDARDS.md` §1).
- PR **titles** in this protocol: English. **Commit messages**: Russian.
- Never reveal AI or agent authorship.

## State rules

- Update `.agent` **only** via `python -m memory state …` (atomic JSON).
- Never append free-form multi-KB blocks to `LOOP_STATE.md`.
- Each stream may `append-delta`; compact at Reviewer.
- Remaining writers (`audit`, `playbooks`, `questions`, `ledger`) take `agent_lock` on the parent of the file being written. Each worktree keeps its own `.agent/`. Hub-only files: `streams_state.json`, `stream_leases.json`.

## Merge gate checklist

1. Tests green in stream worktree
2. `validate_handoff` passes
3. No edits outside `owned_paths` (spot-check `git diff --name-only`)
4. Integration Reviewer merges in the **dedicated integration worktree** (steady-state: never `git checkout` of hub `HEAD`); run `state compact` + `metrics-log`
5. `SYNC_DONE` from `scripts/sync-worktree.sh --verify-only`
6. Human merges the integration PR to `main` and pushes `main`. Supervisor never does this.

## Supervisor unattended

After worktrees exist (or let supervisor provision them). Requires `pip install -e .` (Init does this); PYTHONPATH is only a fallback for an uninstalled clone:

    python -m memory.supervisor run-parallel \
      --stream harness:memory/,tools/ \
      --stream docs:docs/ \
      --adapter mock \
      --no-pr

    python -m memory.supervisor run-parallel \
      --stream harness:memory/,tools/ \
      --stream docs:docs/ \
      --adapter mock \
      --no-pr \
      --concurrent

    python -m memory.supervisor run-parallel \
      --stream harness:memory/,tools/ \
      --stream docs:docs/ \
      --adapter mock \
      --concurrent \
      --push

- Human gate remains: **merge PR to `main` only**. Supervisor never merges `main`. Never `git push main`.
- Default: streams run **serially**. `run-parallel --concurrent` (or `supervisor.parallel.concurrent`) overlaps disjoint streams in time; provision and integration merge stay serial.
- Opt-in `--push` (or `supervisor.parallel.push`) pushes stream + integration branches to `origin` (never `main` / `master`). When `create_pr` and `push` are both on, push is a hard precondition of `gh pr create`. `gh` runs with `cwd` = integration worktree.
- Child env (`AGENTIX_STREAM` / `AGENTIX_OWNED_PATHS` / `AGENTIX_WORKTREE`) is applied **once** per spawn on the child dict only.
- Hub writes `.agent/streams_state.json` with per-stream status (mid-flight on concurrent completions so the Streams page is not blind until join).
- Live Grok still uses pxpipe by default (`proxy.mode=required`).
- Example `supervisor.parallel` keys: `serial: true` (documentation of the default), `concurrent: false`, `push: false`, `lease_ttl_s: 7200`. Runtime flag is `concurrent`; do not invert the API.
