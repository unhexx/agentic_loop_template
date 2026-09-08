# -*- coding: utf-8 -*-
"""Клиент SearXNG в CLI стека: JSON, таймаут, fail-open без живого контейнера."""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from memory import stack as stack_mod


class _Resp:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_search_parses_json_results(monkeypatch):
    body = json.dumps(
        {
            "results": [
                {
                    "title": "LangGraph Agent",
                    "url": "https://example.test/lg",
                    "content": "autonomous research",
                    "engine": "arxiv",
                }
            ]
        }
    ).encode("utf-8")

    def fake_open(req, timeout=0):  # noqa: ANN001
        assert "format=json" in req.full_url
        assert "q=langgraph" in req.full_url
        return _Resp(body)

    monkeypatch.setattr(stack_mod, "urlopen", fake_open)
    hits = stack_mod.query("langgraph", base_url="http://127.0.0.1:8080")
    assert len(hits) == 1
    assert hits[0]["title"] == "LangGraph Agent"
    assert hits[0]["url"].startswith("https://")


def test_search_fail_open_on_network(monkeypatch):
    def boom(*args, **kwargs):  # noqa: ANN001
        raise URLError("down")

    monkeypatch.setattr(stack_mod, "urlopen", boom)
    hits = stack_mod.query("anything")
    assert hits == []


def test_search_rejects_non_loopback_default():
    with pytest.raises(ValueError):
        stack_mod.query("x", base_url="http://8.8.8.8:8080")


def test_search_rejects_loopback_wrong_port():
    # 8112 — Control Plane, не SearXNG; allowlist обязан включать порт.
    with pytest.raises(ValueError):
        stack_mod.query("x", base_url="http://127.0.0.1:8112")


def test_search_cli_json(monkeypatch, capsys):
    monkeypatch.setattr(
        stack_mod,
        "query",
        lambda q, **kw: [{"title": "t", "url": "http://127.0.0.1/x", "content": "c"}],
    )
    rc = stack_mod.cli(["search", "--q", "test", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["title"] == "t"
