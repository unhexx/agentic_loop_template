# -*- coding: utf-8 -*-
"""Оценка токенов: fallback chars/4, tiktoken, префиксы моделей."""

from __future__ import annotations

import json

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
