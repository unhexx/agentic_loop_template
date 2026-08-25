# Token Estimate (tiktoken / per-model) — Design (Agentix v3.9.4)

**Title:** P8-08 Token estimate upgrade (tiktoken default, per-model encoding)  
**Author:** Agentix SSOT cycle fire (detached)  
**Date:** 2026-08-25  
**Status:** Accepted for implementation (next fire)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.9.3**, `main` `d729b1a` (harvest/reflect skill split). Both remotes match. ROADMAP next = Future.  
**Target version:** **3.9.4** (patch: estimator accuracy — not a product-facing 3.10.0)  
**House style:** match [2026-08-25-harvest-reflect-skill-split-design.md](2026-08-25-harvest-reflect-skill-split-design.md) structure; API detail as in P8 harness spec.  
**Canonical landing path:** `docs/superpowers/specs/2026-08-25-token-estimate-per-model-design.md`  
**Plan:** [../plans/2026-08-25-token-estimate-per-model.md](../plans/2026-08-25-token-estimate-per-model.md)

This document is the execute-plan input for **P8-08**, the first leftover named after P8 (“token estimate, docs i18n, concurrency”). It does **not** reopen harvest/reflect, Blackbox, Control Plane, packaging layout, P8-11 locking, P8-14 supervisor caps, messenger, pxpipe-agy, or newportal Go rewrite.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Hub SaaS / Linear-Jira-Slack MCP | ROADMAP Future #1–#2 | Rejected this cycle. Optional/huge; P8 research parked them after hardening. |
| B. P8-11 true concurrent fan-out | Shared `.agent/` locking; `run-parallel` currently serial | Rejected this cycle. 3.10-level product; State DI already shipped; do not start locking in a patch. |
| C. P8-09 docs i18n / P8-10 embeddings / P8-12 splits / P8-13 MultiLLM | Other Future leftovers | Rejected this cycle. Not the first leftover; no dogfood pain. |
| D. P8-14 configurable `_PROMPT_BODY_CAP` / `_KNOWLEDGE_BUDGET` | Supervisor constants | Rejected this cycle. Adjacent file, different done-criterion. Follow-up after P8-08. |
| E. Messenger / pxpipe-agy-docs worktrees | In-progress other branches | Rejected. Explicitly not the next version. |
| **F. P8-08 token estimate (tiktoken / per-model)** | Upgrade `estimate_tokens`; optional extra; per-model map; chars/4 fallback | **Accepted.** First P8 leftover; harvest-reflect spec named it as the example Future pick; `context_budget` already tries tiktoken but it is not a declared extra, not per-model, and the host venv has no tiktoken so every budget gate is `len//4`. |

3.9.3 is shipped (`d729b1a` on origin + github, PR #17 merged). No approved plan existed for any Future item. This fire writes spec + plan only.

---

## Overview

`memory/context_budget.py:estimate_tokens` is the SSOT for “how many tokens is this string?”. Callers: compressor, knowledge ingest, supervisor prompt cap, gateway fidelity + middleware distillation. Today it does:

```python
def estimate_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)
```

Verified 2026-08-25 on SSOT venv: `ModuleNotFoundError: No module named 'tiktoken'`. `tiktoken` is **not** in `pyproject.toml` (required or extras). CI `.[dev]` is pytest only. So production, CI, and this host all take the `chars/4` path. The `try` is dead code unless an operator happened to `pip install tiktoken` by hand.

The upgrade is **not** a new product surface. It is: declare tiktoken as the **dev/tokens extra**, pick encoding **per model** (gpt-4o → `o200k_base`, grok/default → `cl100k_base`), keep a **stdlib fallback**, and **report** which estimator ran. Callers keep `estimate_tokens(text) -> int`.

Patch **3.9.4**, not 3.10.0: no wizard default change, no new HTTP/CLI product, no adapter. Estimator + extra + config keys + docs.

---

## Background & Motivation

### Current state (verified 2026-08-25)

| Layer | What exists | Gap vs P8-08 |
|-------|-------------|--------------|
| Estimator | `estimate_tokens(text)` tries `cl100k_base`, bare `except Exception` → `max(1, len//4)` | Hardcoded encoding; swallows encode errors; no model argument; no report of which path ran. |
| Packaging | `pyproject.toml` required = `jsonschema` only. extras `dev` (pytest), `dashboard` (FastAPI). | tiktoken undeclared. Host `.venv` does not have it. |
| Tests | `test_estimate_tokens`: `assert estimate_tokens("abcd"*100) >= 50` (true for both 100 tiktoken-ish and 100 chars/4). `test_budget_check` only checks keys. | No fallback-vs-tiktoken split; no per-model; no import-failure hermetic test. |
| Config | `.agent/project_config.example.json` `context_budget`: `cold_start_tokens`, `next_input_files_tokens`, `compress_when_over`. Supervisor reads **only** `compress_when_over`. | No `encoding` / `model`. Caps `_PROMPT_TOKEN_CAP=8000` stay hardcoded (P8-14). |
| CLI | `python -m memory.context_budget check\|cold-start --budget N [--compress] [--strict]` | No `--model` / `--encoding`. JSON has no `estimator` field. |
| Callers | `compressor.py` (empty string → 0, else `estimate_tokens`); `knowledge.py`; `supervisor._maybe_compress_prompt`; `proxy/fidelity.py`; `proxy/middleware.py` (own `except` → chars/4). | Must keep positional `estimate_tokens(text) -> int`. New kwargs keyword-only. |
| Docs | `PROMPT_COMPRESSION_GUIDE.md` still shows `len(text)//3` “conservative mixed”. Module docstring says chars/4 override if tiktoken installed. | Drift. Guide should point at `memory.context_budget`. |
| CHANGELOG | `[Unreleased]` holds pxpipe-agy host recipe already merged at `103976c` (ancestor of 3.9.3). | 3.9.4 notes absorb that Unreleased bullet as changelog catch-up **plus** P8-08. Do not re-implement pxpipe-agy. |

### Pain

1. **Budgets lie by a factor of ~1–2.** `chars/4` is a planning heuristic. Russian prompts (this repo’s comments/commits) are denser; tiktoken `cl100k` on mixed RU/EN is closer to what Grok/OpenAI-shaped models actually consume. Supervisor compresses at `_PROMPT_TOKEN_CAP = 8000` **tokens** estimated this way — so the gate is not the model’s tokenizer.
2. **Dead optional import.** The try/except looks like tiktoken is the default. It is not installed anywhere in the supported install paths.
3. **gpt-4o / o-series would be wrong even with tiktoken.** Those models use `o200k_base`. One `cl100k_base` call is not “per-model”.
4. **Silent swallow.** A broken tiktoken wheel, a bad encoding name, or a non-str sneak-in all look like chars/4. P8 taught us not to do that on critical paths; this is a budget path — DEBUG once per process on fallback is enough, not WARNING every call.

### Why this leftover, why now

P8 explicitly parked P8-08 as NG5: “already tries tiktoken and falls back to chars/4 — leave it.” P8 is done. 3.9.1–3.9.3 were patches (jira skill, Blackbox, harvest/reflect). Harvest spec option A named **P8-08 token estimate** as the example Future pick. ROADMAP leftover list leads with token estimate. Smallest closed-loop item that is still a real accuracy fix.

---

## Goals & Non-Goals

### Goals

| ID | Goal |
|----|------|
| G1 | `estimate_tokens(text, *, model=None, encoding=None) -> int` stays the caller API. New kwargs are keyword-only. Return type stays `int`. |
| G2 | When tiktoken is importable, the default encoding is **`cl100k_base`** (current try-path; grok/wizard default). `gpt-4o` / `gpt-4o-*` / `o1` / `o3` prefixes resolve to **`o200k_base`**. |
| G3 | When tiktoken is missing or `get_encoding` / `encode` fails, fallback is **`max(1, len(text)//4)`** (unchanged). Empty string: tiktoken → 0; fallback → 1. Compressor still forces empty → 0. |
| G4 | Encoding resolution order: explicit `encoding=` → `AGENTIX_TOKEN_ENCODING` → `context_budget.encoding` → model map (`model=` → `AGENTIX_TOKEN_MODEL` → `context_budget.model` → `"grok"`) → `cl100k_base`. |
| G5 | `check_files` / CLI JSON include `estimator` (`tiktoken` \| `chars_div_4`), `encoding` (or null), `model` (resolved or null). CLI gains `--model` and `--encoding`. |
| G6 | `tiktoken` is **not** a required dep. extras: `tokens = ["tiktoken>=0.7,<1"]` and the same pin **added to `dev`** so `pip install -e ".[dev]"` (Init + CI) gets the real encoder. Core `pip install -e .` stays jsonschema-only. |
| G7 | Tests in `memory/test_context_budget.py` prove fallback **without** a real tiktoken (fake/`sys.modules` or import patch) **and** real tiktoken when installed (`pytest.importorskip` or skipif). Existing `>= 50` test still passes. |
| G8 | VERSION **3.9.4** in the final docs PR only. Patch, not 3.10.0. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | Hard-require tiktoken in `[project].dependencies` | Offline / slim consumer install; P8 required-dep set is jsonschema only. |
| NG2 | HuggingFace `transformers` tokenizers, Anthropic count API, Gemini count API | Network and heavy deps. Operator who needs them sets `encoding=` or lives with cl100k approximation. |
| NG3 | Claim cl100k **is** the Grok/Claude/Gemini official tokenizer | It is the **current approximation**. Document that. Official Grok tokenizer is unpublished here. |
| NG4 | P8-14: make `_PROMPT_BODY_CAP` / `_KNOWLEDGE_BUDGET` / `_PROMPT_TOKEN_CAP` configurable | Separate leftover. `compress_when_over` already exists. |
| NG5 | P8-11 concurrent fan-out / `.agent/` file lock | 3.10. |
| NG6 | Change compressor empty-string = 0 | Callers depend on it (`test_compressor`). |
| NG7 | Change caller sites to pass model on every call | Default resolution is enough. Optional later. |
| NG8 | Wizard / Init frontend change | Patch rule from 3.9.1–3.9.3. |
| NG9 | Messenger, pxpipe-agy re-work, newportal Go, aservice24 snapshot | Out of SSOT cycle. |
| NG10 | Log prompt bodies or token text | Budget path; DEBUG encoding name only, never content. |

---

## Proposed Design

### 1. Resolution and cache (`memory/context_budget.py`)

Keep the module as the only tokenizer SSOT. Add a frozen result type for CLI/report; do **not** change `estimate_tokens` return type.

```python
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Optional

Estimator = Literal["tiktoken", "chars_div_4"]

@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimator: Estimator
    encoding: Optional[str]
    model: Optional[str]

# Longest-prefix first. Approximation for non-OpenAI chat frontends = cl100k_base
# (today’s try-path), not a claim of official vocab.
_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gpt-4o", "o200k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
    ("o1", "o200k_base"),
    ("o3", "o200k_base"),
    ("grok", "cl100k_base"),
    ("blackbox", "cl100k_base"),
    ("cursor", "cl100k_base"),
    ("claude", "cl100k_base"),
    ("gemini", "cl100k_base"),
)
_DEFAULT_MODEL = "grok"
_DEFAULT_ENCODING = "cl100k_base"
```

`encoding_for_model(model: str) -> str`: lower-case, longest prefix match, else `_DEFAULT_ENCODING`.

`resolve_encoding(*, model=None, encoding=None, cfg=None) -> tuple[str, str | None]`  
returns `(encoding_name, resolved_model)`.

Env and config are read **inside** `describe_estimate` / CLI / `check_files`, not on every `estimate_tokens(text)` default call unless kwargs/env are set. **Hot-path rule:** `estimate_tokens("x")` with no kwargs and no env must stay one import + cached encoder (or one chars/4). Read `os.environ` is cheap; do **not** open `project_config.json` from `estimate_tokens` (supervisor already has the cfg). Config file is applied by `check_files(..., model=, encoding=)` and by CLI, which load config once.

`estimate_tokens` implementation:

1. Resolve encoding name from kwargs / env / default (no file IO).
2. Cached `_encoder(name)` → tiktoken Encoding or `None` if import/get_encoding failed (DEBUG log once per name).
3. If encoder: `len(enc.encode(text))`. Catch encode errors → fallback, DEBUG.
4. Else: `max(1, len(text) // 4)`.

`describe_estimate` returns `TokenEstimate` using the same path (so CLI does not guess).

`_encoder` uses a module dict cache, not unbounded `lru_cache` on the public function (tests must be able to clear it).

```python
_encoders: dict[str, Any] = {}
_tiktoken_missing = False
_logged_fallback: set[str] = set()
```

Test helper (not public API, underscore is enough): `memory.context_budget._reset_encoder_cache()` used by tests.

### 2. `check_files` / CLI

`check_files` grows optional `model=` / `encoding=` (keyword-only). Report dict gains:

```json
{
  "budget_tokens": 12000,
  "total_tokens": 123,
  "within_budget": true,
  "estimator": "chars_div_4",
  "encoding": null,
  "model": "grok",
  "files": [],
  "over_by": 0
}
```

When tiktoken works: `"estimator": "tiktoken", "encoding": "cl100k_base"`. Per-file rows stay `{path, exists, bytes, tokens, truncated_read}` — do not duplicate estimator on every file.

CLI:

```
python -m memory.context_budget check --files a.md --budget 12000 --model grok --encoding cl100k_base
python -m memory.context_budget cold-start --model gpt-4o
```

Unknown `--encoding` with tiktoken installed: `get_encoding` fails → fallback + DEBUG, process still exits 0 unless `--strict` and over budget. Do **not** fail-closed the CLI on a bad encoding name (budget checker is advisory except `--strict` on overage).

### 3. project_config

Example + documented keys (optional; missing = defaults):

```json
"context_budget": {
  "cold_start_tokens": 16000,
  "next_input_files_tokens": 12000,
  "compress_when_over": true,
  "encoding": null,
  "model": null
}
```

`null` / omitted → G4 defaults. CLI `--model` / `--encoding` override file. Supervisor **does not** start passing model into `estimate_tokens` in 3.9.4 (NG7). A later P8-14 slice can wire `context_budget.model` into `_maybe_compress_prompt`.

Do **not** edit live `.agent/project_config.json` (runtime). Only `project_config.example.json`.

### 4. Packaging

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0,<9", "tiktoken>=0.7,<1"]
tokens = ["tiktoken>=0.7,<1"]
dashboard = [ ... unchanged ... ]
```

Pin: `>=0.7,<1` covers encodings `cl100k_base` and `o200k_base` (o200k shipped in tiktoken 0.7). Do not pin a single micro.

Init already `pip install -e ".[dev]"` — after this extra, cold-start **gets tiktoken**. That is “tiktoken default” for supported installs without making the wheel required for `pip install agentix` (when/if published).

### 5. Observability

`get_logger("memory.context_budget")`. DEBUG once per encoding name when falling back (`tiktoken missing` / `unknown encoding {name}: {exc}`). Do **not** add the logger to `_CHILD_LOGGERS` (no secret-bearing records). Do **not** log `text`.

### 6. Docs

- Module docstring: resolution order + fallback formula.
- `PROMPT_COMPRESSION_GUIDE.md`: replace the `len//3` snippet with a pointer to `python -m memory.context_budget` and `estimate_tokens`.
- `docs/architecture.md`: one row or footnote under Core Components — Context budget / tiktoken extra.
- CHANGELOG 3.9.4: P8-08 bullets; **also** move the current `[Unreleased]` pxpipe-agy recipe into 3.9.4 Added (already on `main` since `103976c`; changelog catch-up, not a re-ship).
- ROADMAP: drop the “Token estimate …” Future bullet; add Milestones **v3.9.4** row; leftover list still has i18n, concurrency, …; Next stays Future.
- README / docs/README / memory/README / ROADMAP badges `3.9.3` → `3.9.4` in the **last** PR only.

---

## Alternatives Considered

| Option | Verdict | Why |
|--------|---------|-----|
| **Optional extra `[tokens]` + same pin on `[dev]`; per-model prefix map; chars/4 fallback** | **Chosen** | Matches ROADMAP “tiktoken / per-model”; CI/Init get the encoder; slim `pip install -e .` still works; callers unchanged. |
| Required `[project].dependencies` tiktoken | Rejected | Native wheel; offline/slim consumers; P8 required-dep set is jsonschema. |
| Leave try/except, only document `pip install tiktoken` | Rejected | Host + CI still chars/4; not “default”. |
| Per-call HuggingFace / remote count APIs | Rejected | NG2. |
| Approximate Claude/Gemini as `None` → always chars/4 | Rejected | Worse than today’s cl100k try-path for the same strings. Document approximation. |
| Open `project_config.json` inside `estimate_tokens` | Rejected | Hot path (compressor loops). File IO + parse on every chunk. CLI/check_files load once. |
| Change return type to `TokenEstimate` | Rejected | Breaks every caller. Parallel `describe_estimate` is enough. |
| Ship P8-08 + P8-14 together | Rejected this cycle | Two done-criteria; supervisor constants need their own tests. |
| 3.10.0 | Rejected | No new product surface. Same patch rule as 3.9.2 / 3.9.3. |

---

## Compatibility

- **Callers:** positional `estimate_tokens(text)` identical. Keyword-only extras.
- **Empty string:** tiktoken 0 vs fallback 1 vs compressor 0 — unchanged split, tested.
- **Existing test `>= 50` for 400 chars:** holds for both estimators (cl100k of `"abcd"*100` is ~100; chars/4 is 100).
- **Live `.agent/`:** never commit. Example json only.
- **Consumer:** remains symlink to SSOT; no vendor.

---

## Testing

New file `memory/test_context_budget.py` (CI is `pytest memory/`). Cases:

| Test | Setup | Assert |
|------|-------|--------|
| `test_fallback_when_tiktoken_missing` | monkeypatch import / `_tiktoken_missing` | `"abcd"*4` → 4; `describe_estimate` estimator `chars_div_4`, encoding null |
| `test_empty_fallback_is_one` | no tiktoken | `estimate_tokens("") == 1` |
| `test_encoding_for_model_prefixes` | pure | `gpt-4o-mini` → o200k; `gpt-4-turbo` → cl100k; `grok-4` → cl100k; `unknown-xyz` → cl100k |
| `test_explicit_encoding_overrides_model` | fake encoder cache | `model="gpt-4o", encoding="cl100k_base"` uses cl100k |
| `test_env_encoding` | `AGENTIX_TOKEN_ENCODING` | wins over model |
| `test_tiktoken_cl100k_if_installed` | `pytest.importorskip("tiktoken")` | `estimate_tokens("hello")` == `len(enc.encode("hello"))`; estimator tiktoken |
| `test_tiktoken_gpt4o_o200k_if_installed` | importorskip | `model="gpt-4o"` uses o200k; count may differ from cl100k on a long unicode string |
| `test_check_files_report_estimator` | tmp file | report has estimator/encoding/model keys |
| `test_cli_model_flag` | `cli(["check", "--files", p, "--budget", "10", "--model", "grok"])` | JSON stdout has model grok; exit 0 |
| `test_bad_encoding_falls_back` | encoding=`not_a_real_encoding` | chars_div_4, no exception |

Do **not** require network. Fake tiktoken: insert a tiny module in `sys.modules["tiktoken"]` with `get_encoding(name)` returning an object whose `encode` returns `list(text)` (tokens = chars) so tests can distinguish cache keys without the wheel.

`test_state_and_handoff.test_estimate_tokens` stays (regression).

---

## Security & Privacy

| Topic | Handling |
|-------|----------|
| Prompt text | Never logged. |
| tiktoken | Local BPE; no network in the library’s encode path we call. |
| Env | `AGENTIX_TOKEN_ENCODING` / `AGENTIX_TOKEN_MODEL` are non-secret names. |

---

## Rollout / PRs

| PR | Contents | VERSION |
|----|----------|---------|
| PR1 | `context_budget.py` API + cache + tests (hermetic + skipif) | unchanged 3.9.3 |
| PR2 | CLI flags, `check_files` metadata, `project_config.example.json` keys | unchanged |
| PR3 | `pyproject.toml` extras, docs, VERSION **3.9.4**, CHANGELOG (incl. pxpipe-agy Unreleased catch-up), ROADMAP, badges | 3.9.4 last |

Human gate. No auto-merge to `main`. Dual remotes: `github` may use default proxy; `origin` (Bitbucket) `env -u http_proxy -u https_proxy -u ALL_PROXY`. Consumer stays symlink. Do not merge messenger or pxpipe-agy-docs worktrees.

Worktree (this fire): `/home/unhex/.grok/worktrees/project-agentic-loop-template/v394-token-estimate` on `feature/v3.9.4-token-estimate-20260825` from `origin/main` `d729b1a`.

---

## Spec self-review

1. **Placeholders:** none. Encoding names, extras pin, resolution order, test names, PR split are explicit.
2. **Consistency:** G2 default cl100k = grok = today’s try-path. G3 fallback formula unchanged. G6 extra not required dep.
3. **Scope:** single module + tests + example json + docs + packaging extras. No supervisor cap DI (P8-14). No locking (P8-11).
4. **Ambiguity:** Claude/Gemini use cl100k **approximation**, documented. `estimate_tokens` does not read project_config (hot path). CLI/check_files do.

---

## Open questions (none blocking)

Operator who needs a true Claude tokenizer waits for P8-10/embeddings-era or sets a custom workflow outside this patch. No user decision required to implement G1–G8.
