# P8-11 Concurrent Fan-out Implementation Plan

> **For agentic workers:** Spec is [`../specs/2026-08-26-p8-11-concurrent-fanout-design.md`](../specs/2026-08-26-p8-11-concurrent-fanout-design.md). This fire implements it (not docs-only).

**Goal:** Ship P8-11 as Agentix **3.10.0**: opt-in `run-parallel --concurrent` (ThreadPoolExecutor + contextvars), stdlib `.agent/` `O_EXCL` lock, serial default unchanged.

**Architecture:** Three new modules (`memory/agent_lock.py`, `memory/stream_context.py`, tests). Serial `run_parallel` keeps fail-fast + `os.environ` patch. Concurrent path does not mutate env; waits for all streams; skips integration merge if any BLOCKED. Provision and git merge stay serial. Never merge `main` from streams.

**Tech Stack:** Python 3.10+, stdlib `concurrent.futures` / `contextvars` / `os.O_EXCL`. No `filelock` extra.

**Out of scope:** ProcessPool/subprocess fan-out, Hub SaaS, messenger worktrees, P8-09/10/12/13/14, wizard default, `memory/store.py`.

## File map

| Path | Action |
|------|--------|
| `memory/agent_lock.py` | Create — O_EXCL + stale PID |
| `memory/test_agent_lock.py` | Create |
| `memory/state.py` | `save_state` takes `name="state"` |
| `memory/handoff_io.py` | `save_handoff` takes `name="handoff"` |
| `memory/stream_context.py` | Create — ContextVar then env |
| `memory/supervisor_parallel.py` | `concurrent=`, `_run_one_stream`, wait-all, hub tmp+replace + lock |
| `memory/supervisor.py` | CLI `--concurrent` |
| `memory/adapters/mock.py` | contextvars-first |
| `memory/test_supervisor_parallel.py` | overlap Barrier, skip-merge, CLI flag |
| `PARALLEL_PROTOCOL.md` | default serial; `--concurrent` |
| `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md` | 3.10.0 last |

## Tasks (this fire)

- [x] Lock module + state/handoff wiring + tests
- [x] Concurrent `run_parallel` + stream_context + mock + tests
- [x] Hub `streams_state` tmp+replace + `agent_lock(name="streams")`
- [x] Spec + this plan
- [x] VERSION 3.10.0 + changelog/roadmap/README

## Pytest

```bash
PYTHONPATH=. python -m pytest memory/test_agent_lock.py memory/test_supervisor_parallel.py memory/test_streams.py memory/test_state_and_handoff.py memory/test_supervisor_fsm.py memory/test_supervisor_mock_cycle.py -q
```

Then full `python -m pytest -q memory/` before push.

## Done when

- Default `run-parallel` still serial; `--concurrent` overlaps disjoint streams.
- Two threads cannot hold the same `.agent/*.lock`; stale PID recovered.
- VERSION 3.10.0 on main after tests green.
