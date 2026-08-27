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
