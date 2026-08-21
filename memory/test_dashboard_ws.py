# -*- coding: utf-8 -*-
"""WebSocket /ws/ui, watcher и HTMX ws-refresh. Без pytest-asyncio."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.broadcaster import WSBroadcaster
from memory.dashboard.server import (
    WS_CLOSE_ORIGIN,
    WS_CLOSE_TOKEN,
    create_app,
)
from memory.dashboard.watcher import WATCHED_FILES, Watcher

_WS = "ws://127.0.0.1:8112/ws/ui"


def _asgi_loopback(app):
    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = ("127.0.0.1", 9)
        await app(scope, receive, send)

    return asgi


def _client(tmp_path: Path):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    app = create_app(workdir=tmp_path)
    return TestClient(_asgi_loopback(app), base_url="http://127.0.0.1:8112")


def _ws_disconnect():
    from starlette.websockets import WebSocketDisconnect

    return WebSocketDisconnect


class _FakeWS:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.accepted = False
        self.sent = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, msg) -> None:
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(msg)


class _Sink:
    def __init__(self) -> None:
        self.events = []

    async def broadcast(self, message) -> int:
        self.events.append(message)
        return 1


def test_ws_connect_sends_connected(dashboard_client):
    with dashboard_client.websocket_connect(_WS) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert "clients" in msg
        assert "ts" in msg
        assert "workdir" in msg
        assert isinstance(msg["clients"], int)


def test_ws_heartbeat_json(dashboard_client, monkeypatch):
    monkeypatch.setattr("memory.dashboard.server.HEARTBEAT_S", 0.05)
    with dashboard_client.websocket_connect(_WS) as ws:
        first = ws.receive_json()
        assert first["type"] == "connected"
        second = ws.receive_json()
        assert second["type"] == "heartbeat"
        assert "ts" in second
        assert "clients" in second


def test_ws_origin_absent_allowed(dashboard_client):
    with dashboard_client.websocket_connect(_WS) as ws:
        assert ws.receive_json()["type"] == "connected"


def test_ws_origin_same_loopback_allowed(dashboard_client):
    with dashboard_client.websocket_connect(
        _WS, headers={"Origin": "http://127.0.0.1:8112"}
    ) as ws:
        assert ws.receive_json()["type"] == "connected"


def test_ws_origin_reject_4403(dashboard_client):
    with pytest.raises(_ws_disconnect()) as ei:
        with dashboard_client.websocket_connect(
            _WS, headers={"Origin": "http://evil.com"}
        ) as ws:
            ws.receive_json()
    assert ei.value.code == WS_CLOSE_ORIGIN


def test_ws_origin_nip_io_reject_4403(dashboard_client):
    with pytest.raises(_ws_disconnect()) as ei:
        with dashboard_client.websocket_connect(
            _WS, headers={"Origin": "http://127.0.0.1.nip.io:8112"}
        ) as ws:
            ws.receive_json()
    assert ei.value.code == WS_CLOSE_ORIGIN


def test_ws_wrong_token_4401(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _client(tmp_path) as client:
        with pytest.raises(_ws_disconnect()) as ei:
            with client.websocket_connect(_WS) as ws:
                ws.receive_json()
        assert ei.value.code == WS_CLOSE_TOKEN


def test_ws_missing_token_query_ok_when_set(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _client(tmp_path) as client:
        with client.websocket_connect(_WS + "?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "connected"


def test_ws_header_token_ok(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _client(tmp_path) as client:
        with client.websocket_connect(_WS, headers={"X-API-Token": "s3cret"}) as ws:
            assert ws.receive_json()["type"] == "connected"


def test_ws_cookie_token_ok(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _client(tmp_path) as client:
        with client.websocket_connect(
            _WS, headers={"Cookie": "agentix_token=s3cret"}
        ) as ws:
            assert ws.receive_json()["type"] == "connected"


def test_ws_empty_token_disables_check(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    with _client(tmp_path) as client:
        with client.websocket_connect(_WS) as ws:
            assert ws.receive_json()["type"] == "connected"


def test_loop_page_has_ws_client_and_refresh(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "htmx.trigger(document.body, 'ws-refresh')" in body
    assert "/ws/ui" in body
    assert "WS: live" in body
    assert "WS: polling" in body
    assert "ws-refresh from:body" in body
    assert 'hx-trigger="load, every 5s, ws-refresh from:body"' in body
    assert 'id="conn-dot"' in body


def test_watched_set_skips_loop_state_md():
    assert "LOOP_STATE.md" not in WATCHED_FILES
    assert "LOOP_STATE.json" in WATCHED_FILES
    assert "last_handoff.json" in WATCHED_FILES
    assert "STOP" in WATCHED_FILES


def test_watcher_state_and_stop_signals(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    sink = _Sink()
    w = Watcher(tmp_path, sink, poll_s=0, debounce_s=0)  # type: ignore[arg-type]
    w.prime()
    (agent / "LOOP_STATE.json").write_text(
        json.dumps(
            {"status": "IN_PROGRESS", "active_role": "Coder", "cycle_number": 12}
        ),
        encoding="utf-8",
    )
    asyncio.run(w.tick())
    types = [e["type"] for e in sink.events]
    assert "state:changed" in types
    st = next(e for e in sink.events if e["type"] == "state:changed")
    assert st["path"] == "LOOP_STATE.json"
    assert st["loop_status"] == "IN_PROGRESS"
    assert st["role"] == "Coder"
    assert st["cycle"] == 12

    sink.events.clear()
    (agent / "STOP").write_text("1", encoding="utf-8")
    asyncio.run(w.tick())
    assert any(e["type"] == "stop:set" for e in sink.events)

    sink.events.clear()
    (agent / "STOP").unlink()
    asyncio.run(w.tick())
    assert any(e["type"] == "stop:cleared" for e in sink.events)

    sink.events.clear()
    (agent / "LOOP_STATE.md").write_text("# projection", encoding="utf-8")
    asyncio.run(w.tick())
    assert sink.events == []


def test_watcher_handoff_changed(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    sink = _Sink()
    w = Watcher(tmp_path, sink, poll_s=0, debounce_s=0)  # type: ignore[arg-type]
    w.prime()
    (agent / "last_handoff.json").write_text(
        json.dumps({"status": "IN_PROGRESS", "role": "Coder"}),
        encoding="utf-8",
    )
    asyncio.run(w.tick())
    ev = sink.events[0]
    assert ev["type"] == "handoff:changed"
    assert ev["handoff_status"] == "IN_PROGRESS"
    assert ev["role"] == "Coder"


def test_broadcaster_drops_dead_clients():
    async def _run():
        b = WSBroadcaster()
        live = _FakeWS()
        dead = _FakeWS(fail=True)
        await b.connect(live)  # type: ignore[arg-type]
        await b.connect(dead)  # type: ignore[arg-type]
        assert b.client_count() == 2
        sent = await b.broadcast({"type": "state:changed"})
        assert sent == 1
        assert b.client_count() == 1
        assert live.sent == [{"type": "state:changed"}]
        assert dead not in b._clients

        empty = WSBroadcaster()
        assert await empty.broadcast({"type": "x"}) == 0

    asyncio.run(_run())


def test_dashboard_ws_sources_do_not_import_runner():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "memory/dashboard/server.py",
        "memory/dashboard/watcher.py",
        "memory/dashboard/broadcaster.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "run_loop" not in text
        assert "get_adapter" not in text
