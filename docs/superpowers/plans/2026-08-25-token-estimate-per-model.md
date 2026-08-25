# Token Estimate (tiktoken / per-model) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:using-git-worktrees before the first edit. Spec is already on this branch.

**Goal:** Ship P8-08 as Agentix **3.9.4** (patch): `estimate_tokens` per-model encoding, tiktoken extra on `dev`/`tokens`, chars/4 fallback, CLI/report metadata; no wizard/product surface.

**Architecture:** `memory/context_budget.py` stays the only tokenizer SSOT. Keyword-only `model=` / `encoding=` on `estimate_tokens`; `describe_estimate` for CLI. Prefix map (gpt-4o → o200k, grok/default → cl100k). No project_config IO on the hot path. Callers unchanged.

**Tech Stack:** Python 3.10+, optional `tiktoken>=0.7,<1`, `pytest memory/`, stdlib fallback `max(1, len//4)`.

**Branch / isolation:** Worktree already exists: `/home/unhex/.grok/worktrees/project-agentic-loop-template/v394-token-estimate` on `feature/v3.9.4-token-estimate-20260825` from `origin/main` `d729b1a` (VERSION 3.9.3). Continue **in that worktree**. Do **not** commit on dirty SSOT `main`. Do **not** `git add -A`. Do **not** start newportal Go rewrite. Do **not** vendor the consumer symlink. Do **not** merge messenger or pxpipe-agy-docs worktrees. Human gate — no auto-merge to `main`.

**Spec:** [`docs/superpowers/specs/2026-08-25-token-estimate-per-model-design.md`](../specs/2026-08-25-token-estimate-per-model-design.md)

---

## File map

| Path | Action |
|------|--------|
| `memory/test_context_budget.py` | Create (Task 2, extend Task 5) |
| `memory/context_budget.py` | Modify API + cache + CLI (Tasks 3, 5) |
| `memory/test_state_and_handoff.py` | Keep `test_estimate_tokens` (no change unless it fails) |
| `.agent/project_config.example.json` | Add `encoding` / `model` null keys (Task 5) |
| `pyproject.toml` | extras `tokens` + pin on `dev` (Task 6) |
| `PROMPT_COMPRESSION_GUIDE.md` | Replace `len//3` snippet (Task 6) |
| `docs/architecture.md` | One packaging/budget row (Task 6) |
| spec + this plan | Already added (Task 1) |
| `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md`, `memory/README.md` | Task 7 only |
| `.agent/project_config.json`, `.agent/PERFORMANCE_*`, `.agent/PLAYBOOKS*` | **Never add** |

**Out of scope:** `memory/supervisor.py` caps (P8-14), `run-parallel` locking (P8-11), messenger, pxpipe-agy code, newportal, `aservice24_22-08-2026`. CHANGELOG 3.9.4 **text** may mention the already-merged pxpipe-agy recipe (catch-up only).

---

### Task 1: Confirm worktree (docs already on branch)

**Files:**
- Already created: `docs/superpowers/specs/2026-08-25-token-estimate-per-model-design.md`
- Already created: `docs/superpowers/plans/2026-08-25-token-estimate-per-model.md`

- [ ] **Step 1: Confirm isolation**

```bash
cd /home/unhex/.grok/worktrees/project-agentic-loop-template/v394-token-estimate
git rev-parse --short HEAD
git branch --show-current
cat VERSION
git status --short
```

Expected: HEAD is `d729b1a` or a later docs-only commit on this branch; branch `feature/v3.9.4-token-estimate-20260825`; VERSION `3.9.3`; no `.agent/` dirt. If this directory is missing, recreate:

```bash
cd /home/unhex/_PROJECT/agentic_loop_template
git fetch origin main
git worktree add /home/unhex/.grok/worktrees/project-agentic-loop-template/v394-token-estimate -b feature/v3.9.4-token-estimate-20260825 origin/main
```

- [ ] **Step 2: Baseline tests**

```bash
cd /home/unhex/.grok/worktrees/project-agentic-loop-template/v394-token-estimate
PYTHONPATH=. python -m pytest memory/ -q
```

Expected: pass (3.9.3 baseline was 185 passed, 6 skipped). If the worktree has no `.venv`, use SSOT interpreter:

```bash
PYTHONPATH=. /home/unhex/_PROJECT/agentic_loop_template/.venv/bin/pytest memory/ -q
```

- [ ] **Step 3: Commit spec+plan if still uncommitted**

```bash
git add docs/superpowers/specs/2026-08-25-token-estimate-per-model-design.md \
        docs/superpowers/plans/2026-08-25-token-estimate-per-model.md
git commit -m "Добавил спецификацию и план оценки токенов P8-08"
```

Do not add `.agent/`.

---

### Task 2: Failing tests for estimator API (PR1 RED)

**Files:**
- Create: `memory/test_context_budget.py`

- [ ] **Step 1: Write the failing test file**

Create `memory/test_context_budget.py` with exactly this content (TDD: code under test does not have `describe_estimate` / `encoding_for_model` yet):

```python
# -*- coding: utf-8 -*-
"""Оценка токенов: fallback chars/4, tiktoken, префиксы моделей."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memory import context_budget as cb


@pytest.fixture
def clean_cache():
    cb._reset_encoder_cache()
    yield
    cb._reset_encoder_cache()


def test_encoding_for_model_prefixes():
    assert cb.encoding_for_model("gpt-4o-mini") == "o200k_base"
    assert cb.encoding_for_model("gpt-4-turbo") == "cl100k_base"
    assert cb.encoding_for_model("grok-4") == "cl100k_base"
    assert cb.encoding_for_model("unknown-xyz") == "cl100k_base"
    assert cb.encoding_for_model("GPT-4O") == "o200k_base"


def test_fallback_when_tiktoken_missing(monkeypatch, clean_cache):
    monkeypatch.setattr(cb, "_tiktoken_missing", True)
    monkeypatch.setattr(cb, "_encoders", {})
    assert cb.estimate_tokens("abcd" * 4) == 4
    d = cb.describe_estimate("abcd" * 4)
    assert d.estimator == "chars_div_4"
    assert d.encoding is None
    assert d.tokens == 4


def test_empty_fallback_is_one(monkeypatch, clean_cache):
    monkeypatch.setattr(cb, "_tiktoken_missing", True)
    assert cb.estimate_tokens("") == 1


def test_explicit_encoding_overrides_model(monkeypatch, clean_cache):
    class _Enc:
        def encode(self, text):
            return [1, 2, 3]

    monkeypatch.setattr(cb, "_tiktoken_missing", False)
    monkeypatch.setattr(cb, "_encoders", {"cl100k_base": _Enc()})
    d = cb.describe_estimate("hi", model="gpt-4o", encoding="cl100k_base")
    assert d.tokens == 3
    assert d.encoding == "cl100k_base"
    assert d.estimator == "tiktoken"
    assert d.model == "gpt-4o"


def test_env_encoding(monkeypatch, clean_cache):
    class _Enc:
        def encode(self, text):
            return list(text)

    monkeypatch.setattr(cb, "_tiktoken_missing", False)
    monkeypatch.setattr(cb, "_encoders", {"cl100k_base": _Enc()})
    monkeypatch.setenv("AGENTIX_TOKEN_ENCODING", "cl100k_base")
    monkeypatch.delenv("AGENTIX_TOKEN_MODEL", raising=False)
    d = cb.describe_estimate("ab", model="gpt-4o")
    assert d.encoding == "cl100k_base"
    assert d.tokens == 2


def test_check_files_report_estimator(tmp_path, monkeypatch, clean_cache):
    monkeypatch.setattr(cb, "_tiktoken_missing", True)
    f = tmp_path / "a.md"
    f.write_text("hello ", encoding="utf-8")
    report = cb.check_files([f], budget=10)
    assert report["estimator"] == "chars_div_4"
    assert "encoding" in report
    assert "model" in report
    assert report["total_tokens"] >= 1


def test_cli_model_flag(tmp_path, monkeypatch, capsys, clean_cache):
    monkeypatch.setattr(cb, "_tiktoken_missing", True)
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    rc = cb.cli(["check", "--files", str(f), "--budget", "10", "--model", "grok"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == "grok"
    assert out["estimator"] == "chars_div_4"


def test_bad_encoding_falls_back(monkeypatch, clean_cache):
    monkeypatch.setattr(cb, "_tiktoken_missing", False)
    monkeypatch.setattr(cb, "_encoders", {})

    def _boom(name):
        raise KeyError(name)

    monkeypatch.setattr(cb, "_load_encoder", _boom)
    d = cb.describe_estimate("abcd", encoding="not_a_real_encoding")
    assert d.estimator == "chars_div_4"
    assert d.tokens == 1


@pytest.mark.skipif(pytest.importorskip("tiktoken") is None, reason="tiktoken extra")
def test_tiktoken_cl100k_if_installed(clean_cache):
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    text = "hello"
    assert cb.estimate_tokens(text, encoding="cl100k_base") == len(enc.encode(text))
    d = cb.describe_estimate(text, encoding="cl100k_base")
    assert d.estimator == "tiktoken"
    assert d.encoding == "cl100k_base"


@pytest.mark.skipif(pytest.importorskip("tiktoken") is None, reason="tiktoken extra")
def test_tiktoken_gpt4o_o200k_if_installed(clean_cache):
    import tiktoken

    text = "hello " * 50
    o200 = len(tiktoken.get_encoding("o200k_base").encode(text))
    assert cb.estimate_tokens(text, model="gpt-4o") == o200
    assert cb.describe_estimate(text, model="gpt-4o").encoding == "o200k_base"
```

Fix the skipif: `pytest.importorskip` inside `skipif` is awkward. Use:

```python
tiktoken = pytest.importorskip("tiktoken")  # at start of those two tests, not skipif
```

Replace the two `@pytest.mark.skipif(pytest.importorskip...` tests with:

```python
def test_tiktoken_cl100k_if_installed(clean_cache):
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("cl100k_base")
    text = "hello"
    assert cb.estimate_tokens(text, encoding="cl100k_base") == len(enc.encode(text))
    d = cb.describe_estimate(text, encoding="cl100k_base")
    assert d.estimator == "tiktoken"
    assert d.encoding == "cl100k_base"


def test_tiktoken_gpt4o_o200k_if_installed(clean_cache):
    tiktoken = pytest.importorskip("tiktoken")
    text = "hello " * 50
    o200 = len(tiktoken.get_encoding("o200k_base").encode(text))
    assert cb.estimate_tokens(text, model="gpt-4o") == o200
    assert cb.describe_estimate(text, model="gpt-4o").encoding == "o200k_base"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. python -m pytest memory/test_context_budget.py -q
```

Expected: FAIL with `ImportError` / `AttributeError` (`_reset_encoder_cache` or `encoding_for_model` not defined). Not a collection error in pytest itself.

- [ ] **Step 3: Do not commit a red suite** if the implementer prefers one commit after green — either is fine. Prefer commit after Task 3 green. Skip commit here.

---

### Task 3: Minimal estimator implementation (PR1 GREEN)

**Files:**
- Modify: `memory/context_budget.py`

- [ ] **Step 1: Replace `estimate_tokens` and add helpers**

Keep `file_tokens`, `check_files`, `cold_start_default_files`, `cli` working. New/changed top of `memory/context_budget.py` after the existing imports:

```python
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from memory.logutil import get_logger

log = get_logger("memory.context_budget")

Estimator = Literal["tiktoken", "chars_div_4"]

@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimator: Estimator
    encoding: Optional[str]
    model: Optional[str]

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

_encoders: dict[str, Any] = {}
_tiktoken_missing = False
_logged_fallback: set[str] = set()


def _reset_encoder_cache() -> None:
    """Сброс кэша энкодера — только для тестов."""
    global _tiktoken_missing
    _encoders.clear()
    _logged_fallback.clear()
    _tiktoken_missing = False


def encoding_for_model(model: str) -> str:
    key = (model or "").strip().lower()
    for prefix, enc in _MODEL_PREFIXES:
        if key.startswith(prefix):
            return enc
    return _DEFAULT_ENCODING


def _fallback_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _load_encoder(name: str) -> Any:
    import tiktoken  # type: ignore

    return tiktoken.get_encoding(name)


def _encoder(name: str) -> Any | None:
    global _tiktoken_missing
    if _tiktoken_missing:
        return None
    if name in _encoders:
        return _encoders[name]
    try:
        enc = _load_encoder(name)
    except ImportError:
        _tiktoken_missing = True
        if name not in _logged_fallback:
            log.debug("tiktoken missing; chars/4 fallback")
            _logged_fallback.add(name)
        return None
    except Exception as exc:
        if name not in _logged_fallback:
            log.debug("tiktoken encoding %s unavailable: %s", name, exc)
            _logged_fallback.add(name)
        return None
    _encoders[name] = enc
    return enc


def resolve_encoding(
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Вернуть (имя_кодировки, модель). Без чтения project_config."""
    enc = (encoding or os.environ.get("AGENTIX_TOKEN_ENCODING") or "").strip() or None
    mdl = (model or os.environ.get("AGENTIX_TOKEN_MODEL") or "").strip() or None
    if enc:
        return enc, mdl or _DEFAULT_MODEL
    if not mdl:
        mdl = _DEFAULT_MODEL
    return encoding_for_model(mdl), mdl


def describe_estimate(
    text: str,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> TokenEstimate:
    enc_name, mdl = resolve_encoding(model=model, encoding=encoding)
    codec = _encoder(enc_name)
    if codec is not None:
        try:
            n = len(codec.encode(text))
            return TokenEstimate(tokens=n, estimator="tiktoken", encoding=enc_name, model=mdl)
        except Exception as exc:
            if enc_name not in _logged_fallback:
                log.debug("tiktoken encode failed for %s: %s", enc_name, exc)
                _logged_fallback.add(enc_name)
    return TokenEstimate(
        tokens=_fallback_tokens(text),
        estimator="chars_div_4",
        encoding=None,
        model=mdl,
    )


def estimate_tokens(
    text: str,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> int:
    return describe_estimate(text, model=model, encoding=encoding).tokens
```

Module docstring (replace the heuristic line):

```
Оценка токенов для cold-start / next_input_files.

Порядок: encoding= → AGENTIX_TOKEN_ENCODING → модель (model= / AGENTIX_TOKEN_MODEL / grok)
→ cl100k_base. gpt-4o/o1/o3 → o200k_base. Нет tiktoken — max(1, len//4).
```

Comments/docstrings in **Russian** (DEVELOPMENT_STANDARDS §1). Public names stay English.

Update `file_tokens` to pass through optional model/encoding if easy; otherwise `file_tokens` can keep calling `estimate_tokens(text)` with defaults. For Task 3, `check_files` must already put estimator metadata on the report so `test_check_files_report_estimator` and `test_cli_model_flag` pass — those are PR1 tests.

In `check_files` after computing `rows`:

```python
    probe = describe_estimate("", model=model, encoding=encoding) if False else None
```

Do not use a dummy empty string for estimator (empty + tiktoken → 0 but estimator still tiktoken). Use:

```python
def check_files(
    files: List[Path],
    budget: int,
    compress: bool = False,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Dict[str, Any]:
    rows = [file_tokens(p, model=model, encoding=encoding) for p in files]
    ...
    meta = describe_estimate("x", model=model, encoding=encoding)
    report["estimator"] = meta.estimator
    report["encoding"] = meta.encoding
    report["model"] = meta.model
```

And `file_tokens`:

```python
def file_tokens(
    path: Path,
    max_read: int = 2_000_000,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Dict[str, Any]:
    ...
        "tokens": estimate_tokens(text, model=model, encoding=encoding),
```

CLI in Task 3: add `--model` and `--encoding` on both subparsers so `test_cli_model_flag` passes:

```python
    for p in (p_check, p_cold):
        p.add_argument("--model", default=None)
        p.add_argument("--encoding", default=None)
```

Pass into `check_files(..., model=args.model, encoding=args.encoding)`.

`_load_encoder` must exist as a named function so `test_bad_encoding_falls_back` can monkeypatch it.

- [ ] **Step 2: Run the new tests**

```bash
PYTHONPATH=. python -m pytest memory/test_context_budget.py memory/test_state_and_handoff.py::test_estimate_tokens memory/test_compressor.py -q
```

Expected: PASS. If tiktoken is not installed, the two `importorskip` tests SKIP. Fallback tests must PASS.

- [ ] **Step 3: Full `memory/` regression**

```bash
PYTHONPATH=. python -m pytest memory/ -q
```

Expected: previous count plus new tests; no new fails.

- [ ] **Step 4: Commit PR1**

```bash
git add memory/context_budget.py memory/test_context_budget.py
git commit -m "Добавил помодельную оценку токенов и fallback chars/4"
```

---

### Task 4: Reviewer checkpoint (PR1)

- [ ] Re-read spec G1–G5 vs the diff. Confirm `estimate_tokens("x")` does not open `project_config.json` (`grep project_config memory/context_budget.py` → no match except comments). Confirm no prompt text in log calls.

---

### Task 5: Config example keys only (PR2)

**Files:**
- Modify: `.agent/project_config.example.json` (not live `project_config.json`)
- Modify: `memory/context_budget.py` CLI help strings if missing from Task 3

PR2 is small because CLI metadata landed in PR1 to keep tests green. This task is **example json + docstring** only. If Task 3 already added CLI flags, do not duplicate.

- [ ] **Step 1: Example json**

In `.agent/project_config.example.json` `context_budget` object add:

```json
    "encoding": null,
    "model": null
```

Keep existing three keys. `null` means “use G4 defaults”.

- [ ] **Step 2: Test that example json still parses**

```bash
python -c "import json; json.load(open('.agent/project_config.example.json'))"
```

Expected: no exception.

- [ ] **Step 3: Commit PR2**

```bash
git add .agent/project_config.example.json
git commit -m "Добавил encoding и model в пример context_budget"
```

If CLI flags were **not** in PR1, add them here and extend tests; then `git add memory/context_budget.py memory/test_context_budget.py` too.

---

### Task 6: Packaging extra + docs (PR3 code/docs, still VERSION 3.9.3)

**Files:**
- Modify: `pyproject.toml`
- Modify: `PROMPT_COMPRESSION_GUIDE.md` (the `len//3` snippet around line 301)
- Modify: `docs/architecture.md` Core Components table

- [ ] **Step 1: extras**

In `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0,<9", "tiktoken>=0.7,<1"]
tokens = ["tiktoken>=0.7,<1"]
dashboard = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "python-multipart>=0.0.9",
  "httpx>=0.27",
]
```

Do not add tiktoken to `[project].dependencies`.

- [ ] **Step 2: compression guide**

Replace the block starting `**Token budget estimation (tiktoken-like, approximate for planning):**` through the `len(text) // 3` snippet with:

```markdown
**Token budget estimation:**

Use `python -m memory.context_budget check --files … --budget N` (optional `--model` / `--encoding`).
Runtime SSOT is `memory.context_budget.estimate_tokens`: tiktoken when the `tokens`/`dev` extra is installed, otherwise `max(1, len(text)//4)`. Default encoding `cl100k_base` (grok); `gpt-4o` / `o1` / `o3` → `o200k_base`.
```

- [ ] **Step 3: architecture row**

In `docs/architecture.md` Core Components table, extend the Packaging row or add:

```
| Context budget | `memory/context_budget.py` | tiktoken extra (`dev`/`tokens`); fallback chars/4; per-model encoding |
```

Also mention extras on the Packaging row: `.[dev]` now includes tiktoken.

- [ ] **Step 4: Tests still pass without installing tiktoken yet**

```bash
PYTHONPATH=. python -m pytest memory/test_context_budget.py -q
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml PROMPT_COMPRESSION_GUIDE.md docs/architecture.md
git commit -m "Подключил extra tiktoken и поправил документацию оценки токенов"
```

Do **not** bump VERSION in this commit.

---

### Task 7: VERSION 3.9.4 + changelog + badges (PR3 final)

**Files:**
- `VERSION` → `3.9.4`
- `CHANGELOG.md`
- `ROADMAP.md`
- `README.md` badge
- `docs/README.md` badge
- `memory/README.md` badge

- [ ] **Step 1: VERSION**

Write `3.9.4` (single line, no extra newline games beyond the file’s existing newline).

- [ ] **Step 2: CHANGELOG**

Replace the current `[Unreleased]` pxpipe-agy bullet by folding it into **3.9.4**. Shape:

```markdown
## [Unreleased]

## [3.9.4] - 2026-08-25

### Added
- P8-08 token estimate: per-model encoding (`gpt-4o`/`o1`/`o3` → `o200k_base`, default `cl100k_base`), `describe_estimate` / CLI `--model` `--encoding`, extras `tokens` and tiktoken pin on `dev`. Fallback remains `max(1, len//4)` when tiktoken is missing.
- Optional host recipe for Antigravity CLI (`agy`) + second pxpipe: `scripts/pxpipe-agy/` shim and `agy-pxpipe` wrapper, systemd examples `pxpipe-agy*.service.example`. Images `gemini-3.7-flash-high` / `-medium` without touching the Grok imager on `:8100`. README + `docs/proxy.md` Foreign CLIs. (Landed on main in `103976c`; changelog catch-up.)

### Changed
- `VERSION` → 3.9.4
- ROADMAP: P8-08 token estimate removed from Future

Patch, not 3.10.0: no wizard default change, no new product surface, estimator + extra only.
```

Keep the existing `## [3.9.3]` section intact below.

- [ ] **Step 3: ROADMAP**

- Badge `version-3.9.3` → `3.9.4`
- Future list: **delete** the line `- Token estimate (tiktoken / per-model) (P8-08)`
- Milestones: add `| **v3.9.4** | Token estimate: tiktoken extra, per-model encoding, chars/4 fallback |` **above** v3.9.3
- Status line: keep Next = Future. Date may become 2026-08-25.
- P8 leftover sentence “token estimate, docs i18n, concurrency” → “docs i18n, concurrency, …”

- [ ] **Step 4: Badges**

Replace `version-3.9.3` with `version-3.9.4` in:

- `README.md`
- `docs/README.md`
- `memory/README.md`
- `ROADMAP.md` (already)

Grep to confirm no leftover `version-3.9.3` except historical changelog:

```bash
rg -n "version-3\.9\.3" README.md docs/README.md memory/README.md ROADMAP.md
```

Expected: no matches after the edit.

- [ ] **Step 5: Tests + version read**

```bash
PYTHONPATH=. python -m pytest memory/ -q
cat VERSION
```

Expected: green; `3.9.4`.

- [ ] **Step 6: Commit**

```bash
git add VERSION CHANGELOG.md ROADMAP.md README.md docs/README.md memory/README.md
git commit -m "Обновил версию до 3.9.4: оценка токенов P8-08"
```

---

### Task 8: Push both remotes, open GitHub PR, do not merge

- [ ] **Step 1: Status hygiene**

```bash
git status --short
git log --oneline origin/main..HEAD
```

Expected: clean except maybe untracked junk — do not add `.agent/`. Several commits on the feature branch.

- [ ] **Step 2: Push GitHub**

```bash
git push -u github HEAD:feature/v3.9.4-token-estimate-20260825
```

- [ ] **Step 3: Push origin (Bitbucket; unset SOCKS)**

```bash
env -u http_proxy -u https_proxy -u ALL_PROXY git push -u origin HEAD:feature/v3.9.4-token-estimate-20260825
```

- [ ] **Step 4: GitHub PR**

```bash
gh pr create --repo unhexx/agentic_loop_template \
  --base main \
  --head feature/v3.9.4-token-estimate-20260825 \
  --title "v3.9.4: P8-08 token estimate (tiktoken / per-model)" \
  --body "$(cat <<'EOF'
Spec: docs/superpowers/specs/2026-08-25-token-estimate-per-model-design.md

- estimate_tokens keyword-only model/encoding; describe_estimate for CLI
- extras tokens + tiktoken on dev; fallback max(1, len//4)
- pytest memory/ (hermetic fallback + importorskip tiktoken)
- Patch, not 3.10.0. No wizard change. No .agent/ dirt. Consumer remains symlink.

Do not auto-merge.
EOF
)"
```

Do **not** `gh pr merge`. Human gate.

---

## Verification

```bash
PYTHONPATH=. python -m pytest memory/ -q
# optional, after pip install -e ".[dev]":
PYTHONPATH=. python -m pytest memory/test_context_budget.py -q
```

Done when: tests green, VERSION 3.9.4, CHANGELOG has P8-08 + pxpipe-agy catch-up, ROADMAP Future no longer lists P8-08, PR open, main not fast-forwarded by the agent.

---

## Plan self-review

1. **Spec coverage:** G1–G8 each have a task (API T3, extras T6, VERSION T7, tests T2/T3). NG4–NG9 listed as out of scope.
2. **Placeholders:** none. Test bodies, toml pin, changelog shape, push remotes are explicit.
3. **Types:** `TokenEstimate`, `describe_estimate`, `_load_encoder`, `_reset_encoder_cache` used consistently from tests through implementation.
