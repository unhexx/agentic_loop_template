# P8-14 Configurable Context Budgets Implementation Plan

> **For agentic workers:** Spec is [`../specs/2026-08-26-p8-14-context-budgets-design.md`](../specs/2026-08-26-p8-14-context-budgets-design.md). This fire records the execute-plan; implementation is a sibling stream (not docs-only on that stream).

**Goal:** Ship P8-14 as Agentix **3.10.1**: configurable supervisor caps from env / `context_budget` / defaults; invalid values fall back; `_maybe_compress_prompt` wires `model=` / `encoding=`. No new CLI, extra, or wizard change.

**Architecture:** New `memory/prompt_caps.py` (`PromptCaps` + `resolve_prompt_caps`). Module constants in `memory/supervisor.py` stay as defaults (8000 / 4000 / 800 / 8000). Helpers keep existing signatures and call `resolve_prompt_caps(load_config(workdir))` internally. Do not edit `memory/context_budget.py` or `memory/store.py`.

**Tech Stack:** Python 3.10+, stdlib `dataclasses` / `os.environ`. No new extra.

**Out of scope:** Hub SaaS, MCP, messenger worktrees, P8-09/10/12/13, `cold_start_tokens` / `next_input_files_tokens` semantics, tiktoken hard-require, compressor empty-string=0, wizard frontend, new CLI flags, ProcessPool, PyPI.

## File map

| Path | Action |
|------|--------|
| `memory/prompt_caps.py` | Create — `PromptCaps`, `resolve_prompt_caps`, env/config/default |
| `memory/test_prompt_caps.py` | Create — G1–G3 unit tests |
| `memory/supervisor.py` | Resolve caps in `build_role_prompt`, `_knowledge_block`, `_maybe_compress_prompt`, `_state_snapshot_for_workdir`; pass `model=` / `encoding=` |
| `memory/test_supervisor_prompt_caps.py` | Create — G4–G5 helper tests |
| `memory/test_observability.py` | `estimate_tokens` monkeypatch accepts `**kwargs` |
| `.agent/project_config.example.json` | Four cap keys with default numbers (not null) |
| `docs/architecture.md` | Context budget row: supervisor caps from `context_budget` |
| spec + this plan | This docs fire |
| `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md` | 3.10.1 last (release commit, not this fire) |

## Tasks (this docs fire)

- [x] Spec + this plan
- [x] `docs/architecture.md` Context budget row (short mention)

## Tasks (implementer, sibling stream)

- [ ] `memory/prompt_caps.py` + `memory/test_prompt_caps.py` (G1–G3)
- [ ] Supervisor helpers + `memory/test_supervisor_prompt_caps.py` + observability `**kwargs` (G4–G6)
- [ ] `.agent/project_config.example.json` four keys with defaults
- [ ] VERSION **3.10.1** + changelog/roadmap/README in the **release** commit only (G7)

## Pytest

```bash
PYTHONPATH=. python -m pytest memory/test_prompt_caps.py memory/test_supervisor_prompt_caps.py memory/test_observability.py memory/test_supervisor_fsm.py memory/test_context_budget.py -q
```

Then full `python -m pytest -q memory/` before push. If the worktree has no `.venv`, use SSOT:

```bash
PYTHONPATH=. /home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python -m pytest memory/test_prompt_caps.py memory/test_supervisor_prompt_caps.py memory/test_observability.py memory/test_supervisor_fsm.py memory/test_context_budget.py -q
```

## Done when

- Spec + plan exist and match the design (env > config > default; invalid → default + WARNING once; helpers keep signatures; estimator kwargs from `context_budget`).
- `docs/architecture.md` Context budget row mentions configurable supervisor caps.
- Defaults unchanged when keys omitted; env overrides config; supervisor uses resolved caps.
- Observability `estimate_tokens` patch accepts `**kwargs`.
- VERSION 3.10.1 only on the release commit after tests green. No merge of messenger / Hub / other P8 leftovers.
