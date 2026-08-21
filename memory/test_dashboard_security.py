# -*- coding: utf-8 -*-
"""Host/peer loopback и /health — без записи на диск."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.security import is_loopback_address, is_loopback_host


def test_health_ok(dashboard_client):
    r = dashboard_client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_host_evil_dot_com_403(dashboard_client):
    r = dashboard_client.get("/health", headers={"Host": "evil.com"})
    assert r.status_code == 403
    assert "dashboard is loopback-only" in r.text


def test_host_nip_io_rebinding_403(dashboard_client):
    r = dashboard_client.get("/health", headers={"Host": "127.0.0.1.nip.io"})
    assert r.status_code == 403
    assert "dashboard is loopback-only" in r.text


def test_root_renders_loop(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Agentix Control" in r.text
    assert "Loop" in r.text


def test_is_loopback_host_rejects_rebinding():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("127.0.0.1:8112")
    assert is_loopback_host("localhost")
    assert is_loopback_host("localhost:8112")
    assert is_loopback_host("::1")
    assert is_loopback_host("[::1]:8112")
    assert is_loopback_address("127.0.0.8")
    assert not is_loopback_host("127.0.0.1.nip.io")
    assert not is_loopback_host("evil.com")
    assert not is_loopback_host("10.0.0.2")
    assert not is_loopback_address("10.0.0.2")
    assert not is_loopback_host("127.evil.com")


def test_non_loopback_peer_403(tmp_path: Path):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

    app = create_app(workdir=tmp_path)

    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = ("10.0.0.2", 9)
        await app(scope, receive, send)

    with TestClient(asgi, base_url="http://127.0.0.1:8112") as client:
        r = client.get("/health")
        assert r.status_code == 403
        assert "dashboard is loopback-only" in r.text


def test_serve_rejects_non_loopback_bind(tmp_path: Path):
    from memory.dashboard.server import BindError, serve

    try:
        serve(workdir=tmp_path, host="0.0.0.0", port=8112)
    except BindError as exc:
        msg = str(exc)
        assert "SR-04" in msg
        assert "0.0.0.0" in msg
        return
    raise AssertionError("expected BindError")
