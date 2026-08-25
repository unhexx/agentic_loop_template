# -*- coding: utf-8 -*-
"""Редактура BLACKBOX_* без DASHBOARD_TOKEN — отдельно от проверки, что адаптер сам не пишет секреты."""

from __future__ import annotations

import logging

from memory.dashboard.redact import RedactFilter, redact_tokens
from memory.logutil import _CHILD_LOGGERS, configure_logging, get_logger


def test_redact_tokens_masks_blackbox_api_key_without_dashboard_token(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    raw = "BLACKBOX_API_KEY=sk-secret-canary-key"
    out = redact_tokens(raw)
    assert "sk-secret-canary-key" not in out
    assert "BLACKBOX_API_KEY=" in out
    assert "****" in out


def test_child_loggers_include_memory_adapters():
    assert "memory.adapters" in _CHILD_LOGGERS


def test_adapters_logger_masks_blackbox_env_without_dashboard_token(
    monkeypatch, caplog
):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    configure_logging()
    log = get_logger("memory.adapters")
    assert any(isinstance(f, RedactFilter) for f in log.filters)
    # memory.propagate=False — без хендлера на самом логгере caplog пустой
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)
    with caplog.at_level(logging.INFO, logger="memory.adapters"):
        log.info("spawn BLACKBOX_API_KEY=sk-secret-canary-key exe=/tmp/fake")
    text = caplog.text
    assert "BLACKBOX_API_KEY=" in text
    assert "exe=/tmp/fake" in text
    assert "****" in text
    assert "sk-secret-canary-key" not in text
