# -*- coding: utf-8 -*-
"""Фикстуры memory/: без импорта FastAPI на уровне модуля."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def dashboard_client(tmp_path: Path):
    pytest.importorskip("fastapi")
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

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
