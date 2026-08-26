# -*- coding: utf-8 -*-
"""Юнит-тесты сборки лимитов промпта из env и context_budget."""
from __future__ import annotations

import logging

from memory.prompt_caps import (
    DEFAULT_KNOWLEDGE_BUDGET_TOKENS,
    DEFAULT_PROMPT_BODY_CHARS,
    DEFAULT_PROMPT_TOKEN_CAP,
    DEFAULT_SNAP_JSON_CHARS,
    ENV_PROMPT_BODY_CHARS,
    ENV_SNAP_JSON_CHARS,
    PromptCaps,
    _WARNED_KEYS,
    resolve_prompt_caps,
)


def _defaults() -> PromptCaps:
    return PromptCaps()


def test_empty_cfg_and_none_are_defaults():
    assert resolve_prompt_caps(None) == _defaults()
    assert resolve_prompt_caps({}) == _defaults()
    assert resolve_prompt_caps(None, environ={}) == _defaults()


def test_context_budget_ints_override():
    caps = resolve_prompt_caps(
        {
            "context_budget": {
                "prompt_body_chars": 100,
                "snap_json_chars": 200,
                "knowledge_budget_tokens": 300,
                "prompt_token_cap": 400,
            }
        },
        environ={},
    )
    assert caps.prompt_body_chars == 100
    assert caps.snap_json_chars == 200
    assert caps.knowledge_budget_tokens == 300
    assert caps.prompt_token_cap == 400


def test_env_overrides_config():
    cfg = {
        "context_budget": {
            "prompt_body_chars": 50,
            "snap_json_chars": 60,
        }
    }
    caps = resolve_prompt_caps(
        cfg,
        environ={
            ENV_PROMPT_BODY_CHARS: "20",
            ENV_SNAP_JSON_CHARS: "30",
        },
    )
    assert caps.prompt_body_chars == 20
    assert caps.snap_json_chars == 30
    assert caps.knowledge_budget_tokens == DEFAULT_KNOWLEDGE_BUDGET_TOKENS
    assert caps.prompt_token_cap == DEFAULT_PROMPT_TOKEN_CAP


def test_invalid_values_fall_back_per_key():
    caps = resolve_prompt_caps(
        {
            "context_budget": {
                "prompt_body_chars": "nope",
                "snap_json_chars": 0,
                "knowledge_budget_tokens": -5,
                "prompt_token_cap": 900,
            }
        },
        environ={},
    )
    assert caps.prompt_body_chars == DEFAULT_PROMPT_BODY_CHARS
    assert caps.snap_json_chars == DEFAULT_SNAP_JSON_CHARS
    assert caps.knowledge_budget_tokens == DEFAULT_KNOWLEDGE_BUDGET_TOKENS
    assert caps.prompt_token_cap == 900

    caps = resolve_prompt_caps(
        {
            "context_budget": {
                "prompt_body_chars": True,
                "snap_json_chars": False,
                "knowledge_budget_tokens": 3.14,
                "prompt_token_cap": "nope",
            }
        },
        environ={},
    )
    assert caps == _defaults()


def test_numeric_string_accepted():
    caps = resolve_prompt_caps(
        {"context_budget": {"snap_json_chars": "4000"}},
        environ={},
    )
    assert caps.snap_json_chars == 4000
    caps = resolve_prompt_caps(
        {"context_budget": {"prompt_body_chars": 8000}},
        environ={ENV_PROMPT_BODY_CHARS: "4000"},
    )
    assert caps.prompt_body_chars == 4000


def test_missing_or_non_dict_context_budget_defaults():
    assert resolve_prompt_caps({"supervisor": {}}, environ={}) == _defaults()
    assert resolve_prompt_caps({"context_budget": "nope"}, environ={}) == _defaults()
    assert resolve_prompt_caps({"context_budget": None}, environ={}) == _defaults()
    assert resolve_prompt_caps({"context_budget": [1, 2]}, environ={}) == _defaults()


def test_invalid_logs_warning_once(caplog):
    _WARNED_KEYS.discard("prompt_body_chars")
    cfg = {"context_budget": {"prompt_body_chars": "nope", "snap_json_chars": 1234}}
    with caplog.at_level(logging.WARNING, logger="memory.prompt_caps"):
        caps = resolve_prompt_caps(cfg, environ={})
    assert caps.prompt_body_chars == DEFAULT_PROMPT_BODY_CHARS
    assert caps.snap_json_chars == 1234
    assert "prompt_body_chars" in caplog.text
    assert "nope" in caplog.text
    n_first = sum(1 for r in caplog.records if "prompt_body_chars" in r.getMessage())
    assert n_first == 1
    with caplog.at_level(logging.WARNING, logger="memory.prompt_caps"):
        resolve_prompt_caps(cfg, environ={})
    n_second = sum(1 for r in caplog.records if "prompt_body_chars" in r.getMessage())
    assert n_second == 1
