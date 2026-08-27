# P8-10 Playbook embeddings ranking — Design (Agentix v3.12.0)

**Title:** Hybrid playbook ranking: optional embeddings extra, HTTP OpenAI-compatible vectors, fail-open to substring  
**Author:** Agentix SSOT cycle fire  
**Date:** 2026-08-27  
**Status:** Accepted for implementation (this fire)  
**Repo / home:** `agentic_loop_template` (Agentix harness), package `memory`  
**Baseline:** VERSION **3.11.4**, `main` `bad9862` (P8-12 module split).  
**Target version:** **3.12.0** (minor: new optional extra + config keys. Not 3.13.0. Not a 3.11.5 patch.)  
**House style:** match [2026-08-27-p8-12-module-split-design.md](2026-08-27-p8-12-module-split-design.md) / [2026-08-27-ng11-agent-dir-harvester-di-design.md](2026-08-27-ng11-agent-dir-harvester-di-design.md).  
**Canonical landing path:** `docs/superpowers/specs/2026-08-27-p8-10-playbook-embeddings-design.md`

This document is the execute-plan input for **P8-10**, parked as “Playbook embeddings ranking.” It does **not** reopen Hub SaaS, MCP, messenger, P8-12 loaders, MultiLLM product wiring, or ACE curate/dedup.

---

## Decision (this fire)

| Option | What | Verdict |
|--------|------|---------|
| A. Hard-require embeddings | `select_bullets` always embeds | Rejected. Breaks stdlib consumers. |
| B. Stdlib TF-IDF / hashing only | No HTTP, no extra | Rejected. Weak leftover fulfillment. |
| C. Embeddings for curate/dedup only | ACE paper de-dup, not ranking | Rejected. ROADMAP names ranking. |
| D. Config-only, no extra, patch 3.11.5 | Same HTTP + fail-open | Rejected. P8 leftover was the extra era. |
| E. `rank=` kwarg / CLI `--rank` | Default path frozen; callers opt in per call | Rejected. More API than config. |
| **F. Extra + config, hybrid 0.5/0.3/0.2, HTTP, fail-open, 3.12.0** | Empty `embeddings` extra; `playbooks.relevance=substring\|embed`; cosine when HTTP works | **Accepted.** |

---

## Overview

`select_bullets` scores `0.5 * effectiveness + 0.3 * recency + 0.2 * relevance`. Relevance is binary substring (0.9 / 0.3). Required deps stay jsonschema-only.

P8-10 wraps the **0.2 term** only. Default `playbooks.relevance` is `substring` (today’s formula). When config is `embed`, HTTP OpenAI-compatible embeddings fill that term via cosine. Missing extra, missing URL/key, timeout, or bad response: WARNING once, substring. `select_bullets` signature unchanged. Vectors cache under `.agent/PLAYBOOKS.embeddings.json`. HTTP/cosine/cache live in `memory/playbooks_embed.py` so `playbooks.py` (508 lines) does not approach 1000.

---

## Background and motivation

### Current state (verified 2026-08-27 on `bad9862`)

| Layer | What exists | Gap vs P8-10 |
|-------|-------------|--------------|
| Score | `_score_bullet`: 0.5 eff + 0.3 recency + 0.2 relevance. Relevance 0.9 iff `query_lower in content or tags` else 0.3 (`memory/playbooks.py:215-225`) | No vector term. |
| Deps | `jsonschema` required; extras `dev`, `tokens` (tiktoken). No embeddings extra | P8 leftover extra. |
| Lock | `agent_lock(..., name="playbooks")` on index parent | Cache writes must not skip the lock. |
| Config | `playbooks.enabled`, `auto_curate`, `max_bullets_per_playbook`, `default_k`, `min_effectiveness`, `scopes` | No `relevance` / embedding URL. |
| ROADMAP | Future: Playbook embeddings ranking (P8-10) | This fire. |
| VERSION | 3.11.4 | Minor **3.12.0**. |

Pain: a query that does not occur as a substring of a good bullet (synonym, paraphrase) always gets relevance 0.3. Effectiveness and recency still dominate, but the 0.2 term cannot distinguish near-misses.

---

## Goals and non-goals

### Goals

| ID | Goal |
|----|------|
| G1 | Default omit `playbooks.relevance` (or value `substring`): `_score_bullet` matches 3.11.4 on the same fixture (eff, recency, 0.9/0.3). |
| G2 | `playbooks.relevance=embed` uses cosine for the 0.2 term when HTTP embeddings succeed. Weights 0.5 / 0.3 / 0.2 unchanged. |
| G3 | Extra `[project.optional-dependencies] embeddings = []` (empty marker). Config is the real switch. Docs: `pip install "agentix[embeddings]"` plus config. |
| G4 | HTTP: POST `{base}/v1/embeddings` via stdlib `urllib`. Model + key from config/env. Timeout 5s. No required numpy; cosine in `playbooks_embed.py`. |
| G5 | Fail-open: missing URL/key, timeout, non-200, bad JSON, dim mismatch → WARNING once on `memory.playbooks` (no query body, no API key), then substring. `select_bullets` does not raise for ranking. |
| G6 | Cache `.agent/PLAYBOOKS.embeddings.json` keyed by sha256(model + "\\n" + text). Write under `agent_lock` on the playbooks index parent, `name="playbooks"`. `agent_dir=` same as playbooks. |
| G7 | Embed HTTP/cache/cosine in `memory/playbooks_embed.py`. `playbooks.py` stays well under 1000 lines. `select_bullets(..., agent_dir=)` signature unchanged. No CLI `--rank`. |
| G8 | Tests in `memory/test_playbooks_embed.py` (hermetic urllib mock). VERSION **3.12.0** only in the release commit. |

### Non-goals

| ID | Non-goal | Why |
|----|----------|-----|
| NG1 | sentence-transformers, fastembed, GPU, numpy extra | HTTP only. |
| NG2 | Change 0.5 / 0.3 weights or recency heuristic | Wrap the 0.2 term only. |
| NG3 | Embeddings for `curate_from_reflection` de-dup | Different leftover. |
| NG4 | Hub SaaS, MCP, messenger, P8-12 reopen, MultiLLM-use | Other leftovers. |
| NG5 | Fail-closed ranking; `rank=` kwarg; `--rank` CLI | Config + fail-open. |
| NG6 | New lock name `playbooks_embed` | Same parent as index; `name="playbooks"` (not reentrant: cache write uses unlocked helpers inside the existing section, or a separate acquire after select’s read). See design §3. |
| NG7 | Logging query text or API keys | WARNING is key name + reason only. |

---

## Proposed design

### 1. Config

`load_config` adds:

```python
"relevance": "substring",  # or "embed"
"embedding_base_url": None,
"embedding_model": "text-embedding-3-small",
"embedding_api_key": None,
```

Resolution for URL: non-empty `playbooks.embedding_base_url` → env `AGENTIX_EMBED_BASE` → no default (missing → fallback).  
Key: config `embedding_api_key` → `AGENTIX_EMBED_API_KEY` → `OPENAI_API_KEY` → missing → fallback.  
Never persist the key into the cache file.

`project_config.example.json` documents `relevance: "substring"` and the three embed keys as comments or nulls (JSON has no comments: ship `"relevance": "substring"` and omit secrets; document the rest in `docs` one sentence / CHANGELOG).

### 2. Extra

```toml
[project.optional-dependencies]
embeddings = []
```

Empty on purpose (HTTP is stdlib). Feature is **config** `relevance=embed`. Extra exists so ROADMAP “extra era” has a pip extra name. Do not import-check the extra (metadata extras are not reliably detectable).

### 3. `memory/playbooks_embed.py`

Public (used by playbooks only):

```python
def cosine_01(a: list[float], b: list[float]) -> float:
    """Cosine in [-1,1] mapped to [0,1] via (x+1)/2. Dim mismatch or zero norm → None."""

def embed_texts(texts: list[str], *, base_url: str, model: str, api_key: str | None, timeout: float = 5.0) -> list[list[float]]:
    """POST {base_url}/v1/embeddings. Raises on HTTP/JSON errors (caller fail-opens)."""

def cache_path(agent_dir: Optional[Path]) -> Path:
    return Path(agent_dir) / "PLAYBOOKS.embeddings.json" if agent_dir is not None else Path(".agent/PLAYBOOKS.embeddings.json")
```

Cache JSON: `{"model": str, "vectors": {hex_sha256: [float, ...]}}`. If file `model` ≠ current model, ignore entries (do not mix dims). Corrupt: bak + empty, WARNING.

**Lock:** `select_bullets` today reads the index via `_load_index` (holds playbooks lock then releases). Embed cache miss: acquire `agent_lock(index_parent, name="playbooks")`, re-read cache, embed missing, tmp+replace cache, release. Do not call `_save_index` / `_load_index` from inside that section (not reentrant). Index JSON is not rewritten on embed cache fill.

### 4. Score path

```python
def _relevance(query_lower: str, bullet: dict, *, mode: str, query_vec, vec) -> float:
    if mode == "embed" and query_vec is not None and vec is not None:
        c = cosine_01(query_vec, vec)
        if c is not None:
            return c
    content = (bullet.get("content") or "").lower()
    tags = " ".join(bullet.get("tags", [])).lower()
    return 0.9 if query_lower in content or query_lower in tags else 0.3
```

Batch: one embeddings request for the query plus all cache-miss bullet texts in this `select_bullets` call (cap batch to texts that passed `min_effect` / scope). Empty query: substring path (cosine undefined).

### 5. `playbooks.py` wiring

`load_config` reads the new keys. `select_bullets` loads cfg; if `relevance == "embed"`, try `playbooks_embed` once per call; on any exception or missing URL/key, `_warn_embed_fallback(reason)` once per process (`_EMBED_FALLBACK_WARNED` flag), substring.

Do not add `agent_dir=` to `_score_bullet` beyond passing precomputed vecs. Curate / seed / export_hub unchanged.

### 6. CLI

No new flags. Operators set project_config. `python -m memory.playbooks select --query ...` uses config.

---

## Data flow

```
select_bullets(query, agent_dir=)
  load_config → relevance
  load playbooks index (existing lock)
  if embed:
    read cache (unlocked)
    missing texts + query → HTTP
    on success: lock playbooks → merge cache tmp+replace → release
    cosine for 0.2
  else / fail: substring 0.2
  sort by 0.5e+0.3r+0.2rel, take k
```

---

## Error handling

| Case | Behavior |
|------|----------|
| `relevance` omitted / `substring` / unknown | substring. Unknown: WARNING once, treat as substring. |
| `embed` but no URL or key | substring + WARNING `embed_unconfigured`. |
| HTTP timeout / non-200 / JSON | substring + WARNING `embed_http`. No exception out of `select_bullets`. |
| Dim mismatch / zero vector | that bullet uses substring; others may still cosine. |
| Cache corrupt | bak, empty cache, WARNING; may refetch. |
| Lock timeout | `TimeoutError` from `agent_lock` (do not catch in playbooks_embed). |

Do not log `Authorization` or query strings.

---

## Testing

New `memory/test_playbooks_embed.py`. Hermetic. `tmp_path` + `agent_dir=` + `monkeypatch.chdir`.

| Test | Assert |
|------|--------|
| `test_default_substring_matches_legacy` | no config key; fixture bullet+query; score equals 0.5*eff+0.3*rec+0.2*0.9 or 0.3 |
| `test_embed_uses_cosine` | fake urllib returns known vecs; score uses (cos+1)/2 for 0.2; HTTP called |
| `test_embed_cache_second_select_no_http` | second `select_bullets` same texts: HTTP count unchanged; cache file under `agent_dir` |
| `test_embed_http_fail_falls_back` | urllib raises; no exception; substring score; caplog WARNING on `memory.playbooks` |
| `test_embed_agent_dir_not_cwd` | chdir elsewhere; cache not in cwd `.agent` |
| `test_embed_lock_held_during_cache_replace` | wrap `Path.replace` for `PLAYBOOKS.embeddings.json`; lock `playbooks` present at replace; absent after |

Canonical:

```bash
PYTHONPATH=. python -m pytest memory/test_playbooks_embed.py memory/test_playbooks_lock.py -q
```

Then `pytest -q memory/` before push. SSOT `.venv` if needed. No live network.

---

## Security and privacy

API keys from env/config only. Cache is floats + hashes, not secrets. WARNING must not include the query or the key. Stdlib HTTPS uses default cert verification.

---

## Alternatives considered

| Option | Verdict | Why |
|--------|---------|-----|
| **Hybrid + empty extra + HTTP + fail-open + playbooks_embed.py + 3.12.0** | **Chosen** | Meets leftover; stdlib consumers unchanged; playbooks.py stays under 1k. |
| Local sentence-transformers extra | Rejected | Heavy, download, poor Init fit. |
| Fail-closed when extra/config set | Rejected | `select_bullets` is on the role hot path. |
| TF-IDF stdlib | Rejected | Not embeddings. |
| Patch 3.11.5 no extra | Rejected | Extra is the P8-10 era marker. |
| Dedup-only | Rejected | Ranking leftover. |

---

## Compatibility

- Omit config: identical scores to 3.11.4 (G1).
- `select_bullets` / `curate_from_reflection` / CLI args: unchanged.
- Wizard / proxy / `--concurrent`: unchanged.
- Required deps: still jsonschema only.

---

## Docs and version

This fire (spec only): this file. `VERSION` stays **3.11.4**.

Release commit:

- `VERSION` → `3.12.0`
- CHANGELOG `[3.12.0]`: optional `embeddings` extra; `playbooks.relevance=embed`; HTTP OpenAI-compat; fail-open substring; cache `PLAYBOOKS.embeddings.json`
- ROADMAP: drop P8-10 Future bullet; milestone v3.12.0
- README / docs badges; `project_config.example.json` `relevance`
- Do not rewrite architecture beyond one row: playbook select can use embeddings when configured

Do not commit live `.agent/`.

---

## Key Decisions

1. **3.12.0** because a new extra + config keys. Public `select_bullets` signature unchanged.
2. **Wrap the 0.2 term only.** Do not retune 0.5/0.3.
3. **Fail-open to substring.** Hot path never raises for ranking.
4. **HTTP OpenAI-compat, stdlib urllib.** No GPU extra.
5. **Empty `embeddings` extra** as the named opt-in group; **config `relevance`** is the actual switch (extras metadata is not a reliable gate).
6. **Cache + lock `name="playbooks"`** on the index parent; not a second lock name.
7. **`playbooks_embed.py`** so `playbooks.py` does not grow toward 1000 lines.
8. **No CLI `--rank`.**

---

## Open questions

None. Closed 2026-08-27:

| # | Question | Decision |
|---|----------|----------|
| Q1 | Done criterion | Hybrid wrap, optional extra, 3.12.0 |
| Q2 | Vector source | HTTP OpenAI-compatible |
| Q3 | Failure | Fail-open substring |
| Q4 | Ship shape | Extra + config; extract embed helpers |

---

## PR Plan

| PR | Title | Files | Depends |
|----|--------|-------|---------|
| PR0 | this spec | this file | — |
| PR1 | Embed helpers + tests | `memory/playbooks_embed.py`, `memory/test_playbooks_embed.py` | — |
| PR2 | Wire `select_bullets` + config + example json | `memory/playbooks.py`, `memory/test_playbooks_lock.py` (if cache lock), `.agent/project_config.example.json` | PR1 |
| PR3 | 3.12.0 VERSION, CHANGELOG, ROADMAP, badges, pyproject extra | `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `pyproject.toml`, README badges | PR1+PR2 |

Topo: PR1 → PR2 → PR3. Human gate. Dual remotes: `github` default proxy; `origin` `env -u http_proxy -u https_proxy -u ALL_PROXY`.
