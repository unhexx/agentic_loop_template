# Configurable Supervisor Context Budgets — Design (Agentix v3.10.1)

**Title:** P8-14 Configurable supervisor caps (`prompt_body_chars`, `snap_json_chars`, `knowledge_budget_tokens`, `prompt_token_cap`)  
**Author:** Agentix SSOT cycle fire  
**Date:** 2026-08-26  
**Status:** Implemented on main (this fire; VERSION 3.10.1)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.10.0**, `main` `5dc0303` (P8-11 concurrent fan-out).  
**Target version:** **3.10.1** (patch: operators already had `compress_when_over`; no new product surface — not 3.11.0)  
**House style:** match [2026-08-26-p8-11-concurrent-fanout-design.md](2026-08-26-p8-11-concurrent-fanout-design.md) structure; API detail as in P8-08 token-estimate spec.  
**Canonical landing path:** `docs/superpowers/specs/2026-08-26-p8-14-context-budgets-design.md`  
**Plan:** [../plans/2026-08-26-p8-14-context-budgets.md](../plans/2026-08-26-p8-14-context-budgets.md)

This document is the execute-plan input for **P8-14**, the leftover named “configurable context budgets / supervisor caps”. It does **not** reopen concurrent fan-out, token-estimate internals, harvest/reflect, Blackbox, Control Plane, packaging layout, P8-09 i18n, P8-10 embeddings, P8-12 splits, P8-13 MultiLLM, messenger worktrees, or Hub SaaS.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Hub SaaS / Linear-Jira-Slack MCP | ROADMAP Future #1–#2 | Rejected this cycle. Optional/huge. |
| B. Messenger leftover worktrees | In-progress other branches | Rejected. Do not merge them. |
| C. P8-09 docs i18n / P8-10 embeddings / P8-12 splits / P8-13 MultiLLM | Other Future leftovers | Rejected this cycle. Different done-criteria. |
| **D. P8-14 configurable supervisor caps** | Env + `context_budget` keys override the four module constants; invalid values fall back; `_maybe_compress_prompt` wires `model=` / `encoding=` | **Accepted.** Patch **3.10.1** |

3.10.0 is shipped (`5dc0303` on `main`). P8-08 (3.9.4) explicitly parked P8-14: “adjacent file, different done-criterion; `compress_when_over` already exists.” That parking still holds as a *reason they were split*; this fire **is** the parked slice. Patch, not 3.11.0: no new CLI flag, no new extra, wizard default unchanged, operators already had `compress_when_over`.

---

## Overview

Four module-level constants in `memory/supervisor.py` slice and compress the assembled role prompt:

| Constant | Value | Unit | Used by |
|----------|------:|------|---------|
| `_PROMPT_BODY_CAP` | 8000 | characters | `build_role_prompt` — role prompt file slice |
| `_SNAP_JSON_CAP` | 4000 | characters | `_state_snapshot_for_workdir` — state snapshot JSON slice |
| `_KNOWLEDGE_BUDGET` | 800 | tokens | `_knowledge_block` — knowledge inject compress budget |
| `_PROMPT_TOKEN_CAP` | 8000 | tokens | `_maybe_compress_prompt` — assembled prompt compress cap |

They are **not** read from `.agent/project_config.json` today. `context_budget` already has `cold_start_tokens`, `next_input_files_tokens`, `compress_when_over`, `encoding`, `model`. Supervisor reads **only** `compress_when_over` (and, after this fire, the four new keys plus `model` / `encoding` for the estimator). `_maybe_compress_prompt` calls `estimate_tokens(text)` with no kwargs, so P8-08 per-model encoding never reaches the supervisor compress path.

The upgrade:

1. Constants **stay** as defaults (same numbers).
2. New `memory/prompt_caps.py` resolves a frozen `PromptCaps` per key: env (non-empty) → `cfg["context_budget"]` → default.
3. Invalid / missing / `null` values fall back to that key’s default. WARNING once per bad key. No exception on the hot path.
4. Supervisor helpers keep **existing signatures**; they call `resolve_prompt_caps(load_config(workdir))` internally.
5. `_maybe_compress_prompt` also passes `model=` / `encoding=` from `context_budget` into `estimate_tokens` (keyword-only; return type stays `int`).
6. Example json gains the four keys with the default numbers (not `null`).

Wizard default unchanged. No new CLI flag. No new extra. `memory/context_budget.py` estimator is **not** edited.

---

## Background & Motivation

### Current state (verified 2026-08-26 on `5dc0303`)

| Layer | What exists | Gap vs P8-14 |
|-------|-------------|--------------|
| Caps | `_PROMPT_BODY_CAP = 8000`, `_SNAP_JSON_CAP = 4000`, `_KNOWLEDGE_BUDGET = 800`, `_PROMPT_TOKEN_CAP = 8000` in `memory/supervisor.py` | Hardcoded. Operators with long role files or tight windows cannot raise/lower without a fork. |
| Config | `.agent/project_config.example.json` `context_budget`: `cold_start_tokens`, `next_input_files_tokens`, `compress_when_over`, `encoding`, `model` | No `prompt_body_chars` / `snap_json_chars` / `knowledge_budget_tokens` / `prompt_token_cap`. |
| Supervisor read | `load_config(workdir)` then `budget_cfg.get("compress_when_over")`. Slice/compress uses the four constants. | Config cannot override caps. |
| Estimator | `estimate_tokens(text, *, model=None, encoding=None) -> int` (P8-08). `describe_estimate` is the richer sibling. | `_maybe_compress_prompt` calls `estimate_tokens(text)` only — `context_budget.model` / `encoding` unused on this path (P8-08 NG7 leftover). |
| Observability | `test_compress_skipped_logs_warning_without_prompt_body` monkeypatches `estimate_tokens` as `lambda text: 99_999` | Keyword-only `model=` / `encoding=` will TypeError unless the patch accepts `**kwargs`. |
| Env | `AGENTIX_TOKEN_ENCODING` / `AGENTIX_TOKEN_MODEL` for the estimator | No `AGENTIX_PROMPT_*` cap env vars. |
| Docs | ROADMAP Future: “Configurable context budgets (`_PROMPT_BODY_CAP`, `_KNOWLEDGE_BUDGET`) (P8-14)”. Architecture Context budget row covers tiktoken only. | Caps not documented as configurable. |
| Version | `VERSION` 3.10.0 | Patch **3.10.1**. VERSION only in the release commit (not this docs commit). |

### Pain

1. **Caps are a fork.** Long `short_*_prompt.md` files get sliced at 8000 chars. Tight windows still assemble up to 8000 estimated tokens before compress. Operators already edit `compress_when_over`; they cannot edit the four numbers.
2. **P8-08 wiring hole.** Per-model encoding is live for CLI / `check_files`. Supervisor compress still uses default `grok` / `cl100k_base` (or chars/4) even when `context_budget.model` is set.
3. **Invalid config must not crash a turn.** A typo `"prompt_token_cap": "eight-k"` cannot raise on `build_role_prompt`. Fall back + one WARNING.

### Why this leftover, why now

P8 parked configurable caps as P8-14. 3.9.4 refused to bundle them with the estimator. 3.10.0 was concurrent fan-out (new product surface). Caps are the remaining closed-loop patch: same `context_budget` object, no wizard, no CLI, no extra. Operators already had `compress_when_over`.

---

## Goals & Non-Goals

### Goals

| ID | Goal |
|----|------|
| G1 | Defaults unchanged when config/env omit the keys. `_PROMPT_BODY_CAP` / `_SNAP_JSON_CAP` / `_KNOWLEDGE_BUDGET` / `_PROMPT_TOKEN_CAP` stay in `memory/supervisor.py` as the documented default numbers (8000 / 4000 / 800 / 8000). |
| G2 | Config keys override defaults; env overrides config. Per-key, independently. |
| G3 | Invalid values fall back to that key’s default (no exception on the hot path). WARNING once per bad key on logger `memory.prompt_caps`. Never log prompt bodies. |
| G4 | Supervisor uses resolved caps for body slice, snap slice, knowledge compress, prompt token cap. Helpers keep existing signatures; they call `resolve_prompt_caps(load_config(workdir))` internally. |
| G5 | `estimate_tokens(..., model=, encoding=)` wired from `context_budget` in `_maybe_compress_prompt`. Do **not** change `estimate_tokens` return type. |
| G6 | Tests in `memory/test_prompt_caps.py` + `memory/test_supervisor_prompt_caps.py`. Existing observability monkeypatch of `estimate_tokens` must accept `**kwargs`. |
| G7 | VERSION **3.10.1** only in the final release commit (not this docs commit). Patch, not 3.11.0. Wizard default unchanged. No new CLI flag. No new extra. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | P8-09 i18n, P8-10 embeddings, P8-12 module splits, P8-13 MultiLLM, messenger, Hub SaaS, MCP | Other leftovers / other worktrees. |
| NG2 | Changing `cold_start_tokens` / `next_input_files_tokens` semantics | Different gates (`python -m memory.context_budget`); not supervisor caps. |
| NG3 | Hard-requiring tiktoken; changing compressor empty-string=0; wizard frontend | P8-08 already decided; patch rule. |
| NG4 | New supervisor CLI flags; ProcessPool; editing `memory/store.py` / `memory/context_budget.py` estimator | Caps resolve in a new module. Estimator API already has `model=` / `encoding=`. |
| NG5 | Publishing PyPI; renaming import package | Out of cycle. |

---

## Proposed Design

### 1. `PromptCaps` + resolver (`memory/prompt_caps.py`)

New module. Stdlib only. Public names English; module docstring / comments in Russian (`DEVELOPMENT_STANDARDS` §1).

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

DEFAULT_PROMPT_BODY_CHARS = 8000
DEFAULT_SNAP_JSON_CHARS = 4000
DEFAULT_KNOWLEDGE_BUDGET_TOKENS = 800
DEFAULT_PROMPT_TOKEN_CAP = 8000

_ENV_KEYS = {
    "prompt_body_chars": "AGENTIX_PROMPT_BODY_CHARS",
    "snap_json_chars": "AGENTIX_SNAP_JSON_CHARS",
    "knowledge_budget_tokens": "AGENTIX_KNOWLEDGE_BUDGET_TOKENS",
    "prompt_token_cap": "AGENTIX_PROMPT_TOKEN_CAP",
}


@dataclass(frozen=True)
class PromptCaps:
    prompt_body_chars: int = DEFAULT_PROMPT_BODY_CHARS
    snap_json_chars: int = DEFAULT_SNAP_JSON_CHARS
    knowledge_budget_tokens: int = DEFAULT_KNOWLEDGE_BUDGET_TOKENS
    prompt_token_cap: int = DEFAULT_PROMPT_TOKEN_CAP


def resolve_prompt_caps(
    cfg: dict | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> PromptCaps:
    ...
```

`environ is None` → read `os.environ` (tests inject a mapping). Do **not** open `project_config.json` inside the resolver; the caller passes `cfg` (supervisor already has `load_config(workdir)`).

Supervisor constants stay as the same default numbers. They may import the `DEFAULT_*` aliases from `prompt_caps` **or** keep the four literals in `supervisor.py` — either is fine if the numbers stay 8000 / 4000 / 800 / 8000 and tests prove omit-keys ⇒ those values (G1). Prefer importing `DEFAULT_*` so the numbers cannot drift.

### 2. Per-key resolution order (G2)

For each of `prompt_body_chars`, `snap_json_chars`, `knowledge_budget_tokens`, `prompt_token_cap`:

1. **Env (non-empty):** corresponding `AGENTIX_*` from `environ`. Empty string / unset → skip to (2).
2. **`cfg["context_budget"]`:** that key. If `cfg` is `None` or not a `dict`, or `context_budget` is missing / not a `dict`, skip to (3).
3. **Default** for that key.

Keys resolve **independently**. A bad env value for `prompt_token_cap` does not discard a valid `prompt_body_chars` env. A valid env value **does not** consult config for that key (env wins even if config is also set).

### 3. Validation (G3)

A value is accepted iff it parses as `int` and the integer is `> 0`.

| Raw | Result |
|-----|--------|
| `int` `> 0` (JSON `8000`) | Use it |
| `str` whose `int(s.strip())` is `> 0` (`"8000"`, env always str) | Use the int |
| `null` / missing / `""` / whitespace-only env | Default (not a WARNING — omitted) |
| `0`, negative, `true`/`false` (`bool` is not an int budget), list, dict, non-numeric str, fractional float | Default + WARNING once per **key name** |

`bool` must be rejected (`True == 1` would otherwise sneak through). Whole floats (`8000.0` from `json.loads`) may be accepted as `int(v)` if `v.is_integer()` and `int(v) > 0`; fractional floats are invalid.

Logger: `memory.logutil.get_logger("memory.prompt_caps")`. WARNING once per bad key name (module-level set, same idea as `context_budget._logged_fallback`). Message includes the key name and that the default was used. **Never** include the prompt body, snapshot JSON, or knowledge blob. Do **not** add this logger to `_CHILD_LOGGERS`.

No exception from `resolve_prompt_caps` for bad operator input. Programming errors (calling with a non-mapping `environ` that is not `None`) may still raise.

### 4. Supervisor wiring (G4)

Helpers **keep existing signatures**. They resolve caps internally — do **not** add a `caps=` argument this cycle.

| Helper | Today | After |
|--------|-------|-------|
| `build_role_prompt(role, handoff_in, workdir)` | `path.read_text(...)[:_PROMPT_BODY_CAP]` | `[:caps.prompt_body_chars]` |
| `_state_snapshot_for_workdir(workdir)` | `json.dumps(...)[:_SNAP_JSON_CAP]` | `[:caps.snap_json_chars]` |
| `_knowledge_block(role, handoff_in, workdir)` | `compress_text(blob, _KNOWLEDGE_BUDGET)` | `compress_text(blob, caps.knowledge_budget_tokens)` |
| `_maybe_compress_prompt(text, workdir)` | `estimate_tokens(text) <= _PROMPT_TOKEN_CAP` then `compress_text(text, _PROMPT_TOKEN_CAP)` | resolved `prompt_token_cap`; see §5 |

Sketch (names, not line-for-line):

```python
def _state_snapshot_for_workdir(workdir: Path) -> str:
    caps = resolve_prompt_caps(load_config(workdir))
    ...
    return json.dumps(snap_obj, ensure_ascii=False)[: caps.snap_json_chars]
```

`load_config` is already called inside `_maybe_compress_prompt`. Calling it again from the other helpers is acceptable (file is small; G4 forbids signature changes). Do not introduce a process-global caps cache — tests and env must be able to change between calls.

Module-level `_PROMPT_BODY_CAP` etc. remain defined (defaults / back-compat for any `from memory.supervisor import _PROMPT_BODY_CAP` in tests). Production slice/compress paths **must not** use the constants once caps are resolved.

### 5. Estimator kwargs (G5)

In `_maybe_compress_prompt`, after loading `budget_cfg`:

```python
caps = resolve_prompt_caps(cfg)
model = budget_cfg.get("model") or None
encoding = budget_cfg.get("encoding") or None
if isinstance(model, str):
    model = model.strip() or None
else:
    model = None
if isinstance(encoding, str):
    encoding = encoding.strip() or None
else:
    encoding = None
if estimate_tokens(text, model=model, encoding=encoding) <= caps.prompt_token_cap:
    return text
return compress_text(text, caps.prompt_token_cap)["text"]
```

`null` / omitted / non-str → `None`, so `estimate_tokens` keeps its own env/default resolution. Do **not** edit `memory/context_budget.py`. Do **not** change the `int` return type. `compress_when_over is False` still short-circuits before estimate/compress.

### 6. Example config

Edit **only** `.agent/project_config.example.json`. Never live `.agent/project_config.json`.

```json
"context_budget": {
  "cold_start_tokens": 16000,
  "next_input_files_tokens": 12000,
  "compress_when_over": true,
  "encoding": null,
  "model": null,
  "prompt_body_chars": 8000,
  "snap_json_chars": 4000,
  "knowledge_budget_tokens": 800,
  "prompt_token_cap": 8000
}
```

The four new keys ship with the **default numbers**, not `null` (G1 documented in the example). Existing keys stay. `cold_start_tokens` / `next_input_files_tokens` semantics unchanged (NG2).

### 7. Docs (this fire + release)

- This spec + plan (this fire).
- `docs/architecture.md` Core Components **Context budget** row: mention supervisor caps now resolved from `context_budget` (`prompt_body_chars`, `snap_json_chars`, `knowledge_budget_tokens`, `prompt_token_cap`). Do **not** bump any version badge.
- ROADMAP: drop the P8-14 Future bullet; add Milestones **v3.10.1** — in the **release** commit with VERSION / CHANGELOG / README badges.
- Wizard / Init frontend: **no change** (G7).
- No new CLI help text (no new flag).

### 8. Observability

Logger `memory.prompt_caps` as G3. Existing supervisor WARNINGs (`compress skipped`, `knowledge inject failed`, `state snapshot failed`, `role prompt read failed`) stay. Do not log sliced bodies. Existing `test_observability.py` prompt-body redaction tests remain valid.

`memory/test_observability.py` `lambda text: 99_999` **must** become a `**kwargs`-accepting callable so G5 does not TypeError (G6).

---

## Alternatives Considered

| Option | Verdict | Why |
|--------|---------|-----|
| **Env → `context_budget` → defaults; new `prompt_caps` module; helpers keep signatures** | **Chosen** | Matches leftover P8-14; no CLI; invalid values cannot crash a turn; estimator kwargs close the P8-08 hole. |
| New supervisor CLI flags (`--prompt-token-cap`) | Rejected | NG4. Operators already have project_config + env. Patch, not a new surface. |
| Make caps required / fail-closed on bad json | Rejected | G3. A typo must not block `build_role_prompt`. |
| Change helper signatures to take `caps=` | Rejected | G4. Call sites and tests keep `(role, handoff, workdir)` / `(text, workdir)` / `(workdir)`. |
| Edit `memory/context_budget.py` to read caps | Rejected | NG4. Estimator is tokenizer SSOT, not supervisor slice policy. Caps are a new small module. |
| Import `store.py` lock / ProcessPool | Rejected | NG4. Irrelevant to prompt assembly. |
| Ship as 3.11.0 | Rejected | No new product surface; `compress_when_over` already existed. **3.10.1**. |
| Bundle P8-09/10/12/13 / messenger / Hub | Rejected | Decision table. Different done-criteria. |
| Env after config | Rejected | G2 is env overrides config (ops overlay without editing json). |

---

## Compatibility

- **Default omit:** no `context_budget` cap keys, no `AGENTIX_PROMPT_*` → identical slices/compress budgets as 3.10.0 (G1).
- **Helper signatures:** unchanged.
- **`estimate_tokens(text) -> int`:** unchanged. New call site passes keyword-only kwargs.
- **`compress_when_over: false`:** still skips compress entirely.
- **`cold_start_tokens` / `next_input_files_tokens`:** untouched (NG2).
- **Live `.agent/`:** never commit. Example json only.
- **Wizard / proxy / concurrent fan-out:** unchanged.
- **`memory/store.py` / `memory/context_budget.py`:** untouched.
- **Consumer:** remains symlink to SSOT; no vendor.

---

## Testing

New files `memory/test_prompt_caps.py` and `memory/test_supervisor_prompt_caps.py`. Extend `memory/test_observability.py` monkeypatch only as required for `**kwargs`. CI stays `pytest memory/`.

| Test | Setup | Assert |
|------|-------|--------|
| `test_defaults_when_cfg_and_env_omitted` | `resolve_prompt_caps(None)` / `{}` / `{"context_budget": {}}` | 8000 / 4000 / 800 / 8000 |
| `test_config_overrides_defaults` | `context_budget.prompt_body_chars=100` etc. | those ints |
| `test_env_overrides_config` | env `AGENTIX_PROMPT_TOKEN_CAP=50`, config `9000` | 50; other keys still from config/default |
| `test_invalid_falls_back_no_raise` | `"prompt_token_cap": "nope"`, `0`, `-1`, `null`, `true` | default 8000; no exception |
| `test_invalid_logs_warning_once` | two resolves with the same bad key; `caplog` | one WARNING on `memory.prompt_caps`; message has key name; no prompt body |
| `test_empty_env_skips_to_config` | `AGENTIX_PROMPT_BODY_CHARS=""` + config `1234` | 1234 |
| `test_per_key_independence` | bad env for one key, valid config for another | only the bad key defaults |
| `test_build_role_prompt_uses_prompt_body_chars` | tmp role file longer than cap; config `prompt_body_chars=32` | returned prompt contains only 32 chars of the file body (or the slice is proven via a spy/read) |
| `test_snap_json_chars_applied` | snapshot JSON longer than cap | `len(result) <= snap_json_chars` |
| `test_knowledge_budget_tokens_passed` | spy `compress_text` | called with resolved `knowledge_budget_tokens` |
| `test_maybe_compress_uses_prompt_token_cap_and_model` | spy `estimate_tokens`; config `prompt_token_cap` + `model` / `encoding` | kwargs `model=` / `encoding=` passed; compress budget is the resolved cap |
| Observability `estimate_tokens` patch | existing test | callable accepts `**kwargs` (or the test is updated to `lambda text, **kwargs: 99_999`) so G5 does not TypeError |

Hermetic: no network, no live `.agent/` of the clone. Inject `environ=` in unit tests; do not leak process env (clear or pass an explicit mapping).

Canonical command:

```bash
PYTHONPATH=. python -m pytest memory/test_prompt_caps.py memory/test_supervisor_prompt_caps.py memory/test_observability.py memory/test_supervisor_fsm.py memory/test_context_budget.py -q
```

Then full `python -m pytest -q memory/` before push. Worktrees may lack `.venv` — use SSOT interpreter `/home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python` if needed.

Do not hit live `.agent/` of the clone. Do not require tiktoken for these tests (NG3).

---

## Security & Privacy

| Topic | Handling |
|-------|----------|
| Prompt / snap / knowledge text | Never logged by `memory.prompt_caps`. Existing supervisor swallows already redact bodies in tests. |
| Env / config values | Integers (or failed parse). WARNING may include the **key name** and that a default was used; do not dump the raw secret-bearing prompt. Cap integers are not secrets. |
| `load_config` | Unchanged path (`.agent/project_config.json` then example). |

---

## Rollout / PRs

| PR | Contents | VERSION |
|----|----------|---------|
| PR0 (this fire) | spec + plan + architecture Context budget row | unchanged **3.10.0** |
| PR1 | `memory/prompt_caps.py` + `memory/test_prompt_caps.py` | unchanged |
| PR2 | supervisor helpers + `test_supervisor_prompt_caps.py` + observability `**kwargs` | unchanged |
| PR3 | `.agent/project_config.example.json` four keys with defaults | unchanged |
| PR4 | VERSION **3.10.1**, CHANGELOG, ROADMAP (drop P8-14 Future bullet; add v3.10.1 milestone), README badges | **3.10.1** last |

Human gate. No auto-merge to `main`. Dual remotes: `github` may use default proxy; `origin` (Bitbucket) `env -u http_proxy -u https_proxy -u ALL_PROXY`. Consumer stays symlink. Do not merge messenger or pxpipe-agy-docs worktrees.

Worktree (docs fire): `/home/unhex/.grok/worktrees/project-agentic-loop-template/subagent-01a03f67-ee6d-7f33-ad0d-178be41b9504` from `main` `5dc0303`.

Implementation is a **sibling stream** — this fire does not edit Python, tests (except the architecture mention), VERSION, CHANGELOG, ROADMAP, or README.

---

## Spec self-review

1. **Placeholders:** none. Env names, config keys, default numbers, function names, test files, pytest command, PR split are explicit.
2. **Consistency:** G1 omit-keys = today’s numbers. G2 env > config > default per key. G3 no raise + WARNING once. G4 signatures unchanged. G5 kwargs only, `int` return stays. G7 VERSION last, patch not 3.11.0.
3. **Scope:** one new module + two test modules + supervisor wiring + example json + docs. No estimator rewrite. No CLI. No wizard. No store.py.
4. **Ambiguity:** `bool` rejected; empty env skips to config; helpers re-`load_config` rather than growing signatures; four example keys are numbers not `null`.

---

## Open questions (none blocking)

None. Invalid-value policy, resolution order, and estimator kwargs are decided above. No product-surface choice remains for the implementer.
