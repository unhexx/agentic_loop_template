# P8-10 Playbook embeddings ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship P8-10 as Agentix **3.12.0**: hybrid playbook ranking wraps the 0.2 relevance term with optional HTTP OpenAI-compatible embeddings; empty `embeddings` extra; `playbooks.relevance=substring|embed`; fail-open to substring; cache `.agent/PLAYBOOKS.embeddings.json` under `agent_lock(name="playbooks")`.

**Architecture:** HTTP, cosine, and cache live in `memory/playbooks_embed.py`. `memory/playbooks.py` stays well under 1000 lines and keeps `select_bullets(..., agent_dir=)` unchanged. Default omit/`substring` must match 3.11.4 scores. Weights stay 0.5 effectiveness + 0.3 recency + 0.2 relevance. Config is the real switch; the extra is an empty era marker (do not import-check it).

**Tech Stack:** Python 3.10+, stdlib `urllib` / `hashlib` / `json`. Existing `memory.agent_lock.agent_lock`. No numpy, no sentence-transformers, no new required dep.

**Spec:** [`../specs/2026-08-27-p8-10-playbook-embeddings-design.md`](../specs/2026-08-27-p8-10-playbook-embeddings-design.md)

**Out of scope:** Hub SaaS, MCP, messenger, P8-12 loaders, MultiLLM product wiring, ACE curate/dedup embeddings, CLI `--rank`, `rank=` kwarg, local GPU extra, fail-closed ranking, new lock name `playbooks_embed`.

**House rules:** comments and commit messages in natural Russian (`DEVELOPMENT_STANDARDS.md` §1). Public names English. Do not mention AI/agents in commits. Do not commit live `.agent/` (HUB_INDEX, PLAYBOOKS, ledger, leases). `VERSION` stays **3.11.4** until the last commit of this plan. Dual remotes: `github` default proxy; `origin` `env -u http_proxy -u https_proxy -u ALL_PROXY`.

---

## File map

| Path | Action |
|------|--------|
| `memory/playbooks_embed.py` | Create — cosine, HTTP `/v1/embeddings`, cache, lock merge |
| `memory/test_playbooks_embed.py` | Create — helper tests + G1–G8 hermetic urllib mocks |
| `memory/playbooks.py` | `load_config` keys; wrap 0.2 term; fail-open; no signature change |
| `.agent/project_config.example.json` | `"relevance": "substring"`; omit secrets |
| `pyproject.toml` | `embeddings = []` extra |
| `docs/architecture.md` | One Playbooks-row clause only |
| `VERSION` | `3.12.0` (last commit) |
| `CHANGELOG.md` | `[3.12.0]` section |
| `ROADMAP.md` | Drop P8-10 Future bullet; milestone v3.12.0; badge |
| `README.md`, `README.ru.md`, `docs/README.md`, `docs/ru/README.md` | Version badges + one embeddings sentence |

Do not edit `memory/supervisor.py`, `memory/dashboard/**`, `Agent-Init.*`, CLI flags, `curate_from_reflection`, or `seed_initial_playbooks`.

**Interpreter:** prefer `.venv/bin/python`. Worktrees may use SSOT `/home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python`. Prefix tests with `PYTHONPATH=.`. No live network.

**Topo:** Task 1–2 (PR1 helpers) → Task 3–4 (PR2 wire) → Task 5 (PR3 extra + 3.12.0).

---

## Public helpers (lock these names)

```python
def cosine_01(a: List[float], b: List[float]) -> Optional[float]:
    """Cosine in [-1,1] mapped to [0,1] via (x+1)/2. Dim mismatch or zero norm → None."""

def embed_texts(
    texts: List[str],
    *,
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = 5.0,
) -> List[List[float]]:
    """POST {base_url}/v1/embeddings. Raises on HTTP/JSON errors (caller fail-opens)."""

def cache_path(agent_dir: Optional[Path] = None) -> Path:
    """agent_dir/PLAYBOOKS.embeddings.json or cwd .agent/PLAYBOOKS.embeddings.json."""

def cache_key(model: str, text: str) -> str:
    """sha256(model + '\\n' + text) hex."""

def resolve_embed_settings(
    cfg: Dict[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """(base_url, model, api_key) or None if URL or key missing."""

def vectors_for_texts(
    query: str,
    texts: Sequence[str],
    *,
    agent_dir: Optional[Path],
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = 5.0,
) -> Tuple[Optional[List[float]], Dict[str, Optional[List[float]]]]:
    """Unlocked cache read → one HTTP for misses → lock name=playbooks, re-read, tmp+replace."""
```

Cache JSON: `{"model": str, "vectors": {hex_sha256: [float, ...]}}`. Never persist the API key.

URL resolution: non-empty `playbooks.embedding_base_url` → env `AGENTIX_EMBED_BASE` → missing.  
Key: config `embedding_api_key` → `AGENTIX_EMBED_API_KEY` → `OPENAI_API_KEY` → missing.  
Model: config `embedding_model` or `"text-embedding-3-small"`.  
`embedding_base_url` is the origin (no `/v1`); code always appends `/v1/embeddings`.

---

### Task 1: Failing helper tests

**Files:**
- Create: `memory/test_playbooks_embed.py`

- [ ] **Step 1: Write helper tests** (must fail until `memory/playbooks_embed.py` exists)

```python
# -*- coding: utf-8 -*-
"""Гибридный рейтинг playbooks: косинус, HTTP-эмбеддинги, кэш, fail-open."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List

import pytest

from memory.agent_lock import lock_path

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_embed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTIX_EMBED_BASE", raising=False)
    monkeypatch.delenv("AGENTIX_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import memory.playbooks as pb

    monkeypatch.setattr(pb, "_EMBED_FALLBACK_WARNED", False, raising=False)


class _FakeResp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_cosine_01_unit_and_mapped() -> None:
    from memory.playbooks_embed import cosine_01

    assert cosine_01([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_01([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.5)
    assert cosine_01([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)
    assert cosine_01([1.0, 0.0], [1.0, 0.0, 0.0]) is None
    assert cosine_01([0.0, 0.0], [1.0, 0.0]) is None
    assert cosine_01([], []) is None


def test_cache_path_and_key(tmp_path: Path) -> None:
    from memory.playbooks_embed import cache_key, cache_path

    agent = tmp_path / ".agent"
    assert cache_path(agent) == agent / "PLAYBOOKS.embeddings.json"
    assert cache_path(None) == Path(".agent/PLAYBOOKS.embeddings.json")
    a = cache_key("text-embedding-3-small", "hello")
    b = cache_key("text-embedding-3-small", "hello")
    c = cache_key("other-model", "hello")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_load_cache_model_mismatch_and_corrupt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from memory.playbooks_embed import (
        _load_cache_unlocked,
        cache_key,
        cache_path,
    )

    agent = tmp_path / ".agent"
    agent.mkdir()
    path = cache_path(agent)
    path.write_text(
        json.dumps({"model": "old-model", "vectors": {cache_key("old-model", "x"): [1.0]}}),
        encoding="utf-8",
    )
    assert _load_cache_unlocked(agent, "text-embedding-3-small") == {}

    path.write_text("{not-json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory.playbooks"):
        assert _load_cache_unlocked(agent, "text-embedding-3-small") == {}
    assert path.with_suffix(".json.bak").is_file()
    assert any("corrupt" in r.message.lower() for r in caplog.records)


def test_embed_texts_posts_v1_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from memory.playbooks_embed import embed_texts

    seen: list[tuple[str, float | None, dict]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data.decode("utf-8"))
        seen.append((req.full_url, timeout, body))
        assert headers.get("authorization") == "Bearer sk-test"
        assert headers.get("content-type") == "application/json"
        n = len(body["input"])
        return _FakeResp(
            {
                "data": [
                    {"embedding": [1.0, 0.0], "index": 1},
                    {"embedding": [0.0, 1.0], "index": 0},
                ]
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    vecs = embed_texts(
        ["q", "b"],
        base_url="https://example.test",
        model="text-embedding-3-small",
        api_key="sk-test",
        timeout=5.0,
    )
    assert seen[0][0] == "https://example.test/v1/embeddings"
    assert seen[0][1] == 5.0
    assert seen[0][2]["model"] == "text-embedding-3-small"
    assert vecs == [[0.0, 1.0], [1.0, 0.0]]  # sorted by index


def test_embed_texts_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from memory.playbooks_embed import embed_texts

    def boom(*args: object, **kwargs: object):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        embed_texts(
            ["q"],
            base_url="https://example.test",
            model="text-embedding-3-small",
            api_key="sk-test",
        )


def test_resolve_embed_settings_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    from memory.playbooks_embed import resolve_embed_settings

    assert resolve_embed_settings({}, environ={}) is None
    assert (
        resolve_embed_settings(
            {"embedding_base_url": "https://ex.test"},
            environ={},
        )
        is None
    )
    got = resolve_embed_settings(
        {
            "embedding_base_url": "https://cfg.test",
            "embedding_api_key": "cfg-key",
            "embedding_model": "m-cfg",
        },
        environ={
            "AGENTIX_EMBED_BASE": "https://env.test",
            "AGENTIX_EMBED_API_KEY": "env-key",
            "OPENAI_API_KEY": "oa-key",
        },
    )
    assert got == ("https://cfg.test", "m-cfg", "cfg-key")
    got2 = resolve_embed_settings(
        {},
        environ={
            "AGENTIX_EMBED_BASE": "https://env.test",
            "OPENAI_API_KEY": "oa-key",
        },
    )
    assert got2 == ("https://env.test", "text-embedding-3-small", "oa-key")


def test_vectors_for_texts_http_then_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks_embed import cache_path, vectors_for_texts

    agent = tmp_path / ".agent"
    agent.mkdir()
    calls = {"n": 0}

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        calls["n"] += 1
        body = json.loads(req.data.decode("utf-8"))
        data = [
            {"embedding": [float(i + 1), 0.0], "index": i}
            for i, _t in enumerate(body["input"])
        ]
        return _FakeResp({"data": data})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    qv1, by1 = vectors_for_texts(
        "query-one",
        ["alpha", "beta"],
        agent_dir=agent,
        base_url="https://example.test",
        model="text-embedding-3-small",
        api_key="sk-test",
    )
    assert calls["n"] == 1
    assert qv1 == [1.0, 0.0]
    assert by1["alpha"] == [2.0, 0.0]
    cache = cache_path(agent)
    raw = json.loads(cache.read_text(encoding="utf-8"))
    assert raw["model"] == "text-embedding-3-small"
    assert "api_key" not in raw
    assert "sk-test" not in cache.read_text(encoding="utf-8")

    qv2, by2 = vectors_for_texts(
        "query-one",
        ["alpha", "beta"],
        agent_dir=agent,
        base_url="https://example.test",
        model="text-embedding-3-small",
        api_key="sk-test",
    )
    assert calls["n"] == 1
    assert qv2 == qv1
    assert by2 == by1


def test_vectors_cache_agent_dir_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks_embed import cache_path, vectors_for_texts

    agent = tmp_path / "iso" / ".agent"
    agent.mkdir(parents=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        return _FakeResp({"data": [{"embedding": [1.0], "index": 0}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    vectors_for_texts(
        "only-query",
        [],
        agent_dir=agent,
        base_url="https://example.test",
        model="text-embedding-3-small",
        api_key="sk-test",
    )
    assert cache_path(agent).is_file()
    assert not (cwd / ".agent" / "PLAYBOOKS.embeddings.json").exists()


def test_vectors_lock_held_during_cache_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks_embed import vectors_for_texts

    agent = tmp_path / ".agent"
    agent.mkdir()
    lp = lock_path(agent, "playbooks")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "PLAYBOOKS.embeddings.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        return _FakeResp({"data": [{"embedding": [1.0], "index": 0}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    vectors_for_texts(
        "q",
        [],
        agent_dir=agent,
        base_url="https://example.test",
        model="text-embedding-3-small",
        api_key="sk-test",
    )
    assert seen and any(seen)
    assert not lp.exists()
```

- [ ] **Step 2: Run helper tests (expect FAIL)**

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py -q
```

Expected: FAIL on collection/import (`ModuleNotFoundError: memory.playbooks_embed` or `ImportError`).

Do not implement production code in this task.

---

### Task 2: Implement `memory/playbooks_embed.py`

**Files:**
- Create: `memory/playbooks_embed.py`

- [ ] **Step 3: Write the module** (complete file)

HTTP happens **outside** the lock. After a successful embed of misses: acquire `agent_lock(parent, name="playbooks")`, re-read cache, merge, tmp+replace, release. Do not call `_load_index` / `_save_index`. Do not catch `TimeoutError` from `agent_lock`. Do not log query text, `Authorization`, or API keys. Logger name is `memory.playbooks` so fail-open WARNING tests share one logger.

```python
# -*- coding: utf-8 -*-
"""HTTP-эмбеддинги и кэш векторов для рейтинга playbooks (P8-10)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from memory.agent_lock import agent_lock
from memory.logutil import get_logger

log = get_logger("memory.playbooks")

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_TIMEOUT = 5.0
CACHE_NAME = "PLAYBOOKS.embeddings.json"


def cache_path(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт."""
    if agent_dir is not None:
        return Path(agent_dir) / CACHE_NAME
    return Path(".agent") / CACHE_NAME


def cache_key(model: str, text: str) -> str:
    """Ключ кэша: sha256(model + LF + text)."""
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


def cosine_01(a: List[float], b: List[float]) -> Optional[float]:
    """Косинус [-1, 1] → [0, 1] через (x+1)/2. Размерность/нуль → None."""
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return None
    c = dot / (math.sqrt(na) * math.sqrt(nb))
    if c > 1.0:
        c = 1.0
    elif c < -1.0:
        c = -1.0
    return (c + 1.0) / 2.0


def _strip_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def resolve_embed_settings(
    cfg: Dict[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """URL из конфига, затем AGENTIX_EMBED_BASE. Ключ: конфиг → AGENTIX_EMBED_API_KEY → OPENAI_API_KEY."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    url = _strip_or_none(cfg.get("embedding_base_url")) or _strip_or_none(
        env.get("AGENTIX_EMBED_BASE")
    )
    model = _strip_or_none(cfg.get("embedding_model")) or DEFAULT_MODEL
    key = (
        _strip_or_none(cfg.get("embedding_api_key"))
        or _strip_or_none(env.get("AGENTIX_EMBED_API_KEY"))
        or _strip_or_none(env.get("OPENAI_API_KEY"))
    )
    if not url or not key:
        return None
    return url, model, key


def embed_texts(
    texts: List[str],
    *,
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> List[List[float]]:
    """POST {origin}/v1/embeddings. Ошибки HTTP/JSON — исключение, без ловли здесь."""
    if not texts:
        return []
    url = f"{base_url.rstrip('/')}/v1/embeddings"
    payload = json.dumps({"input": texts, "model": model}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("embed_http") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError("embed_http")
    ordered = sorted(
        enumerate(data),
        key=lambda pair: int(pair[1].get("index", pair[0]))
        if isinstance(pair[1], dict)
        else pair[0],
    )
    out: List[List[float]] = []
    for _, item in ordered:
        if not isinstance(item, dict) or "embedding" not in item:
            raise ValueError("embed_http")
        vec = item["embedding"]
        if not isinstance(vec, list) or not vec:
            raise ValueError("embed_http")
        out.append([float(x) for x in vec])
    if len(out) != len(texts):
        raise ValueError("embed_http")
    return out


def _empty_vectors() -> Dict[str, List[float]]:
    return {}


def _load_cache_unlocked(agent_dir: Optional[Path], model: str) -> Dict[str, List[float]]:
    """Чтение без lock. Чужой model — пустой dict (не мешаем размерности). Битый файл → bak."""
    path = cache_path(agent_dir)
    if not path.exists():
        return _empty_vectors()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("playbooks embeddings cache corrupt, renaming to bak: %s", path)
        try:
            path.rename(path.with_suffix(".json.bak"))
        except Exception:
            pass
        return _empty_vectors()
    if not isinstance(raw, dict):
        return _empty_vectors()
    if raw.get("model") != model:
        return _empty_vectors()
    vecs = raw.get("vectors") or {}
    if not isinstance(vecs, dict):
        return _empty_vectors()
    out: Dict[str, List[float]] = {}
    for key, val in vecs.items():
        if isinstance(key, str) and isinstance(val, list) and val:
            try:
                out[key] = [float(x) for x in val]
            except (TypeError, ValueError):
                continue
    return out


def _write_cache_unlocked(
    agent_dir: Optional[Path],
    model: str,
    vectors: Dict[str, List[float]],
) -> None:
    """tmp+replace. Ключ API не пишем."""
    path = cache_path(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    body = {"model": model, "vectors": vectors}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _playbooks_lock(agent_dir: Optional[Path]):
    """Тот же name=playbooks на родителе индекса — не второй lock."""
    return agent_lock(cache_path(agent_dir).parent, name="playbooks")


def vectors_for_texts(
    query: str,
    texts: Sequence[str],
    *,
    agent_dir: Optional[Path],
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[List[float]], Dict[str, Optional[List[float]]]]:
    """Кэш без lock → один HTTP по пропускам → секция playbooks, merge, tmp+replace."""
    unique: List[str] = list(dict.fromkeys(texts))
    cached = _load_cache_unlocked(agent_dir, model)
    qkey = cache_key(model, query)
    missing: List[str] = []
    if qkey not in cached:
        missing.append(query)
    for text in unique:
        if cache_key(model, text) not in cached:
            missing.append(text)
    missing = list(dict.fromkeys(missing))

    new_vecs: Dict[str, List[float]] = {}
    if missing:
        embedded = embed_texts(
            missing,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
        for text, vec in zip(missing, embedded):
            new_vecs[cache_key(model, text)] = vec

    if new_vecs:
        with _playbooks_lock(agent_dir):
            fresh = _load_cache_unlocked(agent_dir, model)
            fresh.update(new_vecs)
            _write_cache_unlocked(agent_dir, model, fresh)
            cached = fresh
    else:
        cached = {**cached, **new_vecs}

    query_vec = cached.get(qkey)
    by_text: Dict[str, Optional[List[float]]] = {
        text: cached.get(cache_key(model, text)) for text in unique
    }
    return query_vec, by_text
```

- [ ] **Step 4: Re-run helper tests (expect PASS)**

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py -q
```

Expected: PASS (helper tests only; integration tests are Task 3).

- [ ] **Step 5: Commit PR1**

```bash
git add memory/playbooks_embed.py memory/test_playbooks_embed.py
git commit -m "Добавил HTTP-эмбеддинги и кэш векторов для рейтинга playbooks"
```

Do not add live `.agent/`. Do not bump `VERSION`.

---

### Task 3: Failing `select_bullets` tests

**Files:**
- Modify: `memory/test_playbooks_embed.py` (append below the helper tests)

- [ ] **Step 6: Append G1–G8 integration tests**

Keep the imports and `_isolate_embed_env` / `_FakeResp` from Task 1. Append:

```python
def _agent(tmp_path: Path) -> Path:
    agent = tmp_path / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    return agent


def _write_playbooks(agent: Path, bullets: List[dict]) -> None:
    payload = {
        "playbooks": {
            "t": {
                "scope": "global",
                "bullets": bullets,
                "last_curated": "2026-01-01T00:00:00+00:00",
            }
        },
        "updated_at": "2026-01-01T00:00:00+00:00",
        "version": "3.3-playbooks",
    }
    (agent / "PLAYBOOKS.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _legacy_score(eff: float, recency: float, relevance: float) -> float:
    return round(0.5 * eff + 0.3 * recency + 0.2 * relevance, 3)


STALE = "1970-01-01T00:00:00+00:00"  # recency 0.3 vs now[:10]


def test_default_substring_matches_legacy(tmp_path: Path) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-hit",
                "content": "Always pin the git remote before clone",
                "tags": ["git"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
            {
                "id": "b-miss",
                "content": "Unrelated weather advisory",
                "tags": ["climate"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
        ],
    )
    rows = {
        r["id"]: r
        for r in select_bullets("git", agent_dir=agent, min_effect=0.0, k=5)
    }
    assert rows["b-hit"]["_score"] == _legacy_score(0.8, 0.3, 0.9)
    assert rows["b-miss"]["_score"] == _legacy_score(0.8, 0.3, 0.3)


def test_unknown_relevance_is_substring(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    (agent / "project_config.json").write_text(
        json.dumps({"playbooks": {"relevance": "bogus"}}), encoding="utf-8"
    )
    _write_playbooks(
        agent,
        [
            {
                "id": "b-1",
                "content": "git remote pin",
                "tags": ["git"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    with caplog.at_level(logging.WARNING, logger="memory.playbooks"):
        rows = select_bullets("git", agent_dir=agent, min_effect=0.0, k=1)
    assert rows[0]["_score"] == _legacy_score(0.8, 0.3, 0.9)
    assert any("relevance_unknown" in r.message for r in caplog.records)


def _write_embed_cfg(agent: Path) -> None:
    (agent / "project_config.json").write_text(
        json.dumps(
            {
                "playbooks": {
                    "relevance": "embed",
                    "embedding_base_url": "https://example.test",
                    "embedding_model": "text-embedding-3-small",
                    "embedding_api_key": "sk-test",
                }
            }
        ),
        encoding="utf-8",
    )


def _parallel_http(monkeypatch: pytest.MonkeyPatch, calls: dict) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        calls["n"] = calls.get("n", 0) + 1
        body = json.loads(req.data.decode("utf-8"))
        data = []
        for i, text in enumerate(body["input"]):
            if text == "commit workflow" or "synonym" in text:
                data.append({"embedding": [1.0, 0.0], "index": i})
            else:
                data.append({"embedding": [0.0, 1.0], "index": i})
        return _FakeResp({"data": data})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_embed_uses_cosine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-syn",
                "content": "branch synonym of vcs pin",
                "tags": ["process"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
            {
                "id": "b-orth",
                "content": "unrelated weather advisory",
                "tags": ["climate"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
        ],
    )
    calls: dict = {}
    _parallel_http(monkeypatch, calls)
    rows = select_bullets(
        "commit workflow", agent_dir=agent, min_effect=0.0, k=5
    )
    by_id = {r["id"]: r for r in rows}
    assert calls["n"] >= 1
    assert by_id["b-syn"]["_score"] == _legacy_score(0.8, 0.3, 1.0)
    assert by_id["b-orth"]["_score"] == _legacy_score(0.8, 0.3, 0.5)


def test_embed_cache_second_select_no_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks import select_bullets
    from memory.playbooks_embed import cache_path

    agent = _agent(tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-syn",
                "content": "branch synonym of vcs pin",
                "tags": ["process"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    calls: dict = {}
    _parallel_http(monkeypatch, calls)
    select_bullets("commit workflow", agent_dir=agent, min_effect=0.0, k=1)
    n1 = calls["n"]
    select_bullets("commit workflow", agent_dir=agent, min_effect=0.0, k=1)
    assert calls["n"] == n1
    assert cache_path(agent).is_file()
    assert not (cwd / ".agent" / "PLAYBOOKS.embeddings.json").exists()


def test_embed_http_fail_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-1",
                "content": "Always pin the git remote before clone",
                "tags": ["git"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )

    def boom(*args: object, **kwargs: object):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with caplog.at_level(logging.WARNING, logger="memory.playbooks"):
        rows = select_bullets("git", agent_dir=agent, min_effect=0.0, k=1)
    assert rows[0]["_score"] == _legacy_score(0.8, 0.3, 0.9)
    assert any("embed_http" in r.message for r in caplog.records)
    joined = " ".join(r.message for r in caplog.records)
    assert "sk-test" not in joined
    assert "commit workflow" not in joined


def test_embed_unconfigured_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    (agent / "project_config.json").write_text(
        json.dumps({"playbooks": {"relevance": "embed"}}), encoding="utf-8"
    )
    _write_playbooks(
        agent,
        [
            {
                "id": "b-1",
                "content": "git remote pin",
                "tags": ["git"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    with caplog.at_level(logging.WARNING, logger="memory.playbooks"):
        rows = select_bullets("git", agent_dir=agent, min_effect=0.0, k=1)
    assert rows[0]["_score"] == _legacy_score(0.8, 0.3, 0.9)
    assert any("embed_unconfigured" in r.message for r in caplog.records)


def test_embed_empty_query_uses_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-1",
                "content": "x",
                "tags": [],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    calls: dict = {"n": 0}

    def fake_urlopen(*args: object, **kwargs: object):
        calls["n"] += 1
        raise AssertionError("empty query must not HTTP")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rows = select_bullets("", agent_dir=agent, min_effect=0.0, k=1)
    assert calls["n"] == 0
    assert rows[0]["_score"] == _legacy_score(0.8, 0.3, 0.3)


def test_embed_dim_mismatch_that_bullet_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-ok",
                "content": "branch synonym of vcs pin",
                "tags": ["process"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
            {
                "id": "b-bad",
                "content": "unrelated weather advisory",
                "tags": ["climate"],
                "effectiveness": 0.8,
                "last_used": STALE,
            },
        ],
    )

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        body = json.loads(req.data.decode("utf-8"))
        data = []
        for i, text in enumerate(body["input"]):
            if "weather" in text:
                data.append({"embedding": [1.0, 0.0, 0.0], "index": i})
            else:
                data.append({"embedding": [1.0, 0.0], "index": i})
        return _FakeResp({"data": data})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rows = {
        r["id"]: r
        for r in select_bullets(
            "commit workflow", agent_dir=agent, min_effect=0.0, k=5
        )
    }
    assert rows["b-ok"]["_score"] == _legacy_score(0.8, 0.3, 1.0)
    assert rows["b-bad"]["_score"] == _legacy_score(0.8, 0.3, 0.3)


def test_embed_agent_dir_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks import select_bullets
    from memory.playbooks_embed import cache_path

    agent = _agent(tmp_path / "iso")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-syn",
                "content": "branch synonym of vcs pin",
                "tags": ["process"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    _parallel_http(monkeypatch, {})
    rows = select_bullets("commit workflow", agent_dir=agent, min_effect=0.0, k=1)
    assert rows[0]["_score"] == _legacy_score(0.8, 0.3, 1.0)
    assert cache_path(agent).is_file()
    assert not (cwd / ".agent").exists()


def test_embed_lock_held_during_cache_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory.playbooks import select_bullets

    agent = _agent(tmp_path)
    _write_embed_cfg(agent)
    _write_playbooks(
        agent,
        [
            {
                "id": "b-syn",
                "content": "branch synonym of vcs pin",
                "tags": ["process"],
                "effectiveness": 0.8,
                "last_used": STALE,
            }
        ],
    )
    lp = lock_path(agent, "playbooks")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "PLAYBOOKS.embeddings.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    _parallel_http(monkeypatch, {})
    select_bullets("commit workflow", agent_dir=agent, min_effect=0.0, k=1)
    assert seen and any(seen)
    assert not lp.exists()


def test_playbooks_py_under_1000_lines() -> None:
    text = (REPO / "memory" / "playbooks.py").read_text(encoding="utf-8")
    assert text.count("\n") < 1000
```

- [ ] **Step 7: Run integration tests (expect FAIL on embed path)**

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py -q
```

Expected: helper tests PASS; `test_embed_uses_cosine` / `test_embed_unconfigured_falls_back` / `test_unknown_relevance_is_substring` FAIL because `load_config` has no `relevance` and `_score_bullet` still uses substring only (cosine scores will not match 1.0 / 0.5). `test_default_substring_matches_legacy` should already PASS on 3.11.4 behavior.

---

### Task 4: Wire `select_bullets` + config + example JSON

**Files:**
- Modify: `memory/playbooks.py` (`load_config`, `_score_bullet`, `select_bullets`)
- Modify: `.agent/project_config.example.json` (playbooks section)

- [ ] **Step 8: Extend `load_config` defaults and copy loop**

In `load_config`, add keys to the default dict:

```python
        "scopes": ["global", "role:*", "tool:*", "phase:*"],
        "relevance": "substring",
        "embedding_base_url": None,
        "embedding_model": "text-embedding-3-small",
        "embedding_api_key": None,
```

Change the copy loop to:

```python
                for k in (
                    "enabled",
                    "auto_curate",
                    "max_bullets_per_playbook",
                    "default_k",
                    "min_effectiveness",
                    "relevance",
                    "embedding_base_url",
                    "embedding_model",
                    "embedding_api_key",
                ):
                    if k in pb:
                        cfg[k] = pb[k]
```

- [ ] **Step 9: Wrap the 0.2 term; fail-open in `select_bullets`**

Keep `_score_bullet` for the default path. Add `_relevance` and a process-level warn flag. Lazy-import `playbooks_embed` only when `relevance == "embed"`. Catch `TimeoutError` from the lock and re-raise; any other exception → substring + WARNING `embed_http` once. Do not log the query or the key.

Replace `_score_bullet` and `select_bullets` with:

```python
_EMBED_FALLBACK_WARNED = False


def _warn_embed_fallback(reason: str) -> None:
    """Один WARNING на процесс: только код причины, без запроса и ключа."""
    global _EMBED_FALLBACK_WARNED
    if _EMBED_FALLBACK_WARNED:
        return
    _EMBED_FALLBACK_WARNED = True
    log.warning("playbooks relevance fallback to substring: %s", reason)


def _substring_relevance(query_lower: str, bullet: Dict[str, Any]) -> float:
    content = (bullet.get("content") or "").lower()
    tags = " ".join(bullet.get("tags", [])).lower()
    return 0.9 if query_lower in content or query_lower in tags else 0.3


def _relevance(
    query_lower: str,
    bullet: Dict[str, Any],
    *,
    mode: str,
    query_vec: Optional[List[float]],
    vec: Optional[List[float]],
) -> float:
    if mode == "embed" and query_vec is not None and vec is not None:
        from memory.playbooks_embed import cosine_01

        mapped = cosine_01(query_vec, vec)
        if mapped is not None:
            return mapped
    return _substring_relevance(query_lower, bullet)


def _score_bullet(
    bullet: Dict[str, Any],
    query_lower: str,
    now_ts: str,
    *,
    mode: str = "substring",
    query_vec: Optional[List[float]] = None,
    vec: Optional[List[float]] = None,
) -> float:
    """ACE-style scoring: 0.5 effectiveness + 0.3 recency + 0.2 relevance."""
    eff = float(bullet.get("effectiveness", 0.5))
    last = bullet.get("last_used") or "1970-01-01T00:00:00+00:00"
    recency = 0.7 if last > now_ts[:10] else 0.3
    relevance = _relevance(
        query_lower, bullet, mode=mode, query_vec=query_vec, vec=vec
    )
    return 0.5 * eff + 0.3 * recency + 0.2 * relevance


def select_bullets(
    query: str,
    scopes: Optional[List[str]] = None,
    k: int = 5,
    min_effect: float = 0.5,
    agent_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Выбирает лучшие bullets для запроса и скоупов. Возвращает отсортированные по score."""
    cfg = load_config(agent_dir)
    if not cfg.get("enabled"):
        return []

    index = _load_index(agent_dir)
    pbs = index.get("playbooks", {})
    now = _now_iso()
    q = query.lower()

    raw: List[Tuple[Dict[str, Any], str]] = []
    for pid, pb in pbs.items():
        pb_scope = pb.get("scope", "")
        if scopes and not any(
            s in pb_scope or pb_scope.startswith(s.split(":")[0]) for s in scopes
        ):
            continue
        for b in pb.get("bullets", []):
            if float(b.get("effectiveness", 0)) < min_effect:
                continue
            raw.append((b, pid))

    mode = str(cfg.get("relevance") or "substring").strip().lower()
    if mode not in ("substring", "embed"):
        _warn_embed_fallback("relevance_unknown")
        mode = "substring"

    query_vec: Optional[List[float]] = None
    vec_by_text: Dict[str, Optional[List[float]]] = {}
    if mode == "embed" and not (query or "").strip():
        mode = "substring"
    elif mode == "embed":
        from memory.playbooks_embed import resolve_embed_settings, vectors_for_texts

        settings = resolve_embed_settings(cfg)
        if settings is None:
            _warn_embed_fallback("embed_unconfigured")
            mode = "substring"
        else:
            base_url, model, api_key = settings
            texts = [str(b.get("content") or "") for b, _pid in raw]
            try:
                query_vec, vec_by_text = vectors_for_texts(
                    query,
                    texts,
                    agent_dir=agent_dir,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                )
            except TimeoutError:
                raise
            except Exception:
                _warn_embed_fallback("embed_http")
                mode = "substring"
                query_vec = None
                vec_by_text = {}

    candidates: List[Tuple[float, Dict[str, Any], str]] = []
    for b, pid in raw:
        vec = vec_by_text.get(str(b.get("content") or ""))
        score = _score_bullet(
            b, q, now, mode=mode, query_vec=query_vec, vec=vec
        )
        candidates.append((score, b, pid))

    candidates.sort(key=lambda x: x[0], reverse=True)
    result = []
    for score, b, pid in candidates[:k]:
        r = dict(b)
        r["_score"] = round(score, 3)
        r["_playbook"] = pid
        result.append(r)
    return result
```

Need `List` already imported; add nothing to the CLI. `curate_from_reflection` / `seed_initial_playbooks` unchanged.

- [ ] **Step 10: Example config**

In `.agent/project_config.example.json`, inside `"playbooks"`, add `"relevance": "substring"` after `"min_effectiveness"`. Do **not** add URL/key fields (JSON has no comments; secrets stay out of the example). Document them in CHANGELOG in Task 5.

```json
    "min_effectiveness": 0.5,
    "relevance": "substring",
    "scopes": ["global", "role:*", "tool:*", "phase:*"]
```

- [ ] **Step 11: Tests green**

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py memory/test_playbooks_lock.py -q
```

Expected: PASS.

Then:

```bash
PYTHONPATH=. python -m pytest -q memory/
```

Expected: PASS (ignore unrelated proxy flakes; retry `memory/test_playbooks_embed.py` must stay green). Confirm `wc -l memory/playbooks.py` is still well under 1000.

- [ ] **Step 12: Commit PR2**

```bash
git add memory/playbooks.py memory/test_playbooks_embed.py .agent/project_config.example.json
git commit -m "Подключил опциональный эмбеддинг к рейтингу playbooks с откатом на подстроку"
```

Do not add live `.agent/` indexes. Do not bump `VERSION`.

---

### Task 5: Extra + 3.12.0 release

**Files:**
- Modify: `pyproject.toml`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `ROADMAP.md`
- Modify: `README.md`, `README.ru.md`, `docs/README.md`, `docs/ru/README.md` (badges)
- Modify: `docs/architecture.md` (one Playbooks-row clause)

- [ ] **Step 13: Failing extra assertion — add to `memory/test_playbooks_embed.py`**

```python
def test_embeddings_extra_is_empty_marker() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "embeddings = []" in text or "embeddings = [\n]" in text.replace("\r\n", "\n")
```

Run `PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py::test_embeddings_extra_is_empty_marker -q` — expect FAIL.

- [ ] **Step 14: `pyproject.toml` extra**

Under `[project.optional-dependencies]`, after `tokens`, add:

```toml
embeddings = []
```

Keep `dev` / `tokens` / `dashboard` unchanged. Do not add numpy/tiktoken to `embeddings`. Do not add an import-check of the extra.

- [ ] **Step 15: VERSION, CHANGELOG, ROADMAP, badges, architecture row**

`VERSION` → `3.12.0`.

`CHANGELOG.md` insert above `[Unreleased]`:

```markdown
## [3.12.0] - 2026-08-27

### Added
- Optional playbook embeddings ranking: empty extra `embeddings`, config `playbooks.relevance=embed`, HTTP OpenAI-compatible `POST {base}/v1/embeddings` via stdlib urllib, cosine for the 0.2 ACE term, cache `.agent/PLAYBOOKS.embeddings.json` under `agent_lock(name="playbooks")`. Fail-open to substring. Helpers in `memory/playbooks_embed.py`.
- Design spec: [`docs/superpowers/specs/2026-08-27-p8-10-playbook-embeddings-design.md`](docs/superpowers/specs/2026-08-27-p8-10-playbook-embeddings-design.md)

### Changed
- `VERSION` → 3.12.0
- ROADMAP: P8-10 Future bullet removed; milestone v3.12.0

Minor, not a 3.11.5 patch: new extra + config keys. Default omit/`substring` scores match 3.11.4. `select_bullets` signature unchanged. Env: `AGENTIX_EMBED_BASE`, `AGENTIX_EMBED_API_KEY` (then `OPENAI_API_KEY`). Config keys `embedding_base_url` / `embedding_model` / `embedding_api_key` are not secrets in `project_config.example.json`.
```

`ROADMAP.md`:
- Badge `version-3.11.4` → `version-3.12.0`
- Status line: add `· **v3.12.0 P8-10 playbook embeddings** — **COMPLETE**` before `· **Next:** Future`
- Future list: delete `- Playbook embeddings ranking (P8-10)`
- Milestones table: insert row `| **v3.12.0** | P8-10: optional embeddings extra; hybrid 0.2 cosine; fail-open substring |` above v3.11.4

`README.md` / `README.ru.md` / `docs/README.md` / `docs/ru/README.md`: badge `version-3.11.4` → `version-3.12.0`.

`README.md` Features **Packaging** cell: add ` / `.[embeddings]`` next to dashboard/tokens extras. Features **Self-improvement** cell: keep ACE; do not rewrite the table.

`docs/README.md` Version paragraph: `**Agentix 3.11.4**` → `**Agentix 3.12.0**` and add one clause: `P8-10 optional playbook embeddings ranking (\`playbooks.relevance=embed\`).`

`docs/ru/README.md`: bump the version badge; add the same clause in Russian: `P8-10 опциональный рейтинг playbooks эмбеддингами (\`playbooks.relevance=embed\`).`

`docs/architecture.md` Playbooks row only (do not edit other rows):

```markdown
| Playbooks | `.agent/PLAYBOOKS.json` | Knowledge bullets (ACE scoring; optional embeddings when `playbooks.relevance=embed`) |
```

One docs sentence for operators (already in CHANGELOG): `pip install "agentix[embeddings]"` plus `playbooks.relevance=embed` and `AGENTIX_EMBED_BASE` / API key. Do not rewrite architecture beyond that row.

- [ ] **Step 16: Full memory pytest + line cap**

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py memory/test_playbooks_lock.py -q
PYTHONPATH=. python -m pytest -q memory/
```

Expected: PASS. `test_embeddings_extra_is_empty_marker` PASS. `test_playbooks_py_under_1000_lines` PASS.

- [ ] **Step 17: Commit PR3 (release only)**

```bash
git add pyproject.toml VERSION CHANGELOG.md ROADMAP.md README.md README.ru.md docs/README.md docs/ru/README.md docs/architecture.md memory/test_playbooks_embed.py
git commit -m "Поднял версию до 3.12.0: опциональные эмбеддинги в рейтинге playbooks"
```

Do not add live `.agent/`. Push:

```bash
env -u http_proxy -u https_proxy -u ALL_PROXY git push origin HEAD:main
git push github HEAD:main
```

Use the remotes this clone actually has; `github` is the default proxy remote, `origin` goes out with proxies unset.

---

## Pytest (canonical)

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py memory/test_playbooks_lock.py -q
PYTHONPATH=. python -m pytest -q memory/
```

SSOT interpreter if the worktree has no `.venv`:

```bash
PYTHONPATH=. /home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python -m pytest memory/test_playbooks_embed.py memory/test_playbooks_lock.py -q
```

No live network. Hermetic urllib mocks only.

---

## Done when

- Default omit / `substring` scores match 3.11.4 on the same fixture (G1).
- `relevance=embed` uses `(cosine+1)/2` for the 0.2 term when HTTP succeeds (G2). Weights 0.5 / 0.3 / 0.2 unchanged.
- Extra `embeddings = []` exists; config is the switch; no import-check of the extra (G3).
- HTTP is stdlib urllib `POST {base}/v1/embeddings`; cosine has no numpy (G4).
- Fail-open: missing URL/key, timeout, non-200, bad JSON → WARNING once on `memory.playbooks` with `embed_unconfigured` / `embed_http`, no query, no key; `select_bullets` does not raise for ranking. Lock `TimeoutError` still propagates (G5).
- Cache `.agent/PLAYBOOKS.embeddings.json` keyed by sha256(model + "\n" + text); write under `agent_lock` `name="playbooks"`; `agent_dir=` same as playbooks (G6).
- `memory/playbooks_embed.py` exists; `playbooks.py` < 1000 lines; no CLI `--rank`; `select_bullets` signature unchanged (G7).
- `memory/test_playbooks_embed.py` green; VERSION **3.12.0** only on the last commit (G8).
- ROADMAP Future no longer lists P8-10. No live `.agent/` in commits.

---

## Self-review

- Spec G1–G8 each have tests and a task (Tasks 3–5 for select path, Tasks 1–2 for helpers).
- NG1–NG7 stay out of scope: no GPU extra, no weight retune, no curate embeddings, no Hub/MCP, no fail-closed, no second lock name, no query/key logs.
- Names match across tasks: `cosine_01`, `embed_texts`, `cache_path`, `cache_key`, `resolve_embed_settings`, `vectors_for_texts`, `_load_cache_unlocked`, `_write_cache_unlocked`, `_warn_embed_fallback`, reasons `embed_unconfigured` / `embed_http` / `relevance_unknown`.
- No TBD / “implement later” / “similar to Task N”.
- VERSION 3.12.0 is Task 5 only.
