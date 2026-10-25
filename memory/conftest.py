# -*- coding: utf-8 -*-
"""Фикстуры memory/: без импорта FastAPI на уровне модуля."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


def _memory_logger_rows() -> list[tuple]:
    rows = []
    seen: set[str] = set()
    names = [
        name
        for name in logging.root.manager.loggerDict
        if name == "memory" or name.startswith("memory.")
    ]
    names.append("memory")
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        lg = logging.getLogger(name)
        rows.append(
            (
                lg,
                list(lg.handlers),
                list(lg.filters),
                lg.level,
                lg.propagate,
                lg.disabled,
            )
        )
    return rows


@pytest.fixture(autouse=True)
def _isolate_memory_logging():
    """Drop handlers and propagate left by configure_logging().

    That call sets memory.propagate = False and attaches a stderr handler.
    Later caplog checks then see an empty record, and the handler can point
    at a capture stream pytest has already closed.
    """
    before = _memory_logger_rows()
    known = {id(lg) for lg, *_rest in before}
    yield
    for lg, handlers, filters, level, propagate, disabled in before:
        extra = [handler for handler in list(lg.handlers) if handler not in handlers]
        lg.handlers[:] = list(handlers)
        lg.filters[:] = list(filters)
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled
        for handler in extra:
            try:
                handler.close()
            except Exception:
                pass
    for name, obj in list(logging.root.manager.loggerDict.items()):
        if name != "memory" and not name.startswith("memory."):
            continue
        lg = obj if isinstance(obj, logging.Logger) else logging.getLogger(name)
        if id(lg) in known:
            continue
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        lg.filters.clear()
        lg.setLevel(logging.NOTSET)
        lg.propagate = True
        lg.disabled = False


@pytest.fixture
def dashboard_client(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

    # матрица токена живёт в своих тестах; здесь пустой токен и порт 8112
    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    monkeypatch.setenv("AGENTIX_DASHBOARD_ALLOW_OPEN", "1")
    monkeypatch.setenv("AGENTIX_DASHBOARD_PORT", "8112")

    prev = os.getcwd()
    app = create_app(workdir=tmp_path)

    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = ("127.0.0.1", 9)
        await app(scope, receive, send)

    try:
        with TestClient(asgi, base_url="http://127.0.0.1:8112") as client:
            yield client
    finally:
        os.chdir(prev)
