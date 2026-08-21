# -*- coding: utf-8 -*-
"""FastAPI-приложение дашборда и точка запуска uvicorn."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from memory.dashboard.actions import register_actions
from memory.dashboard.broadcaster import WSBroadcaster
from memory.dashboard.config import DashboardConfig, bind_host_allowed, load_config
from memory.dashboard.read_model import DashboardStore
from memory.dashboard.routes import register_routes
from memory.dashboard.security import (
    MAX_BODY_BYTES,
    content_length_too_large,
    csrf_ok,
    extract_request_token,
    extract_token,
    generate_csrf_token,
    is_loopback_address,
    is_loopback_host,
    is_same_origin,
    origin_tuple,
    set_csrf_cookie,
    set_token_cookie,
    token_ok,
)
from memory.dashboard.watcher import Watcher

# Пауза между heartbeat; тесты подменяют константу, не ждут 25 с.
HEARTBEAT_S = 25.0
WS_CLOSE_TOKEN = 4401
WS_CLOSE_ORIGIN = 4403


class BindError(RuntimeError):
    """Не loopback или порт уже занят."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raise_if_addr_in_use(host: str, port: int) -> None:
    # uvicorn при EADDRINUSE делает sys.exit(1); ловим заранее.
    try:
        with socket.create_connection((host, int(port)), timeout=0.2):
            occupied = True
    except OSError:
        occupied = False
    if not occupied:
        return
    hint = ""
    if int(port) == 8110:
        hint = "; :8110 — шлюз Agentix, дашборд по умолчанию :8112"
    raise BindError(f"порт {host}:{port} занят{hint}")


def _ws_page_origin(websocket: WebSocket) -> Optional[tuple[str, str, int]]:
    """ws:// → http://, чтобы Origin страницы совпал с апгрейдом."""
    raw = str(websocket.base_url)
    if raw.startswith("ws://"):
        raw = "http://" + raw[5:]
    elif raw.startswith("wss://"):
        raw = "https://" + raw[6:]
    return origin_tuple(raw)


def ws_origin_ok(websocket: WebSocket) -> bool:
    """Нет Origin — ок (curl). Есть — только same-origin loopback."""
    origin = websocket.headers.get("origin")
    if origin is None or origin == "":
        return True
    got = origin_tuple(origin)
    if got is None:
        return False
    scheme, hostname, _port = got
    if scheme not in {"http", "https"}:
        return False
    if not is_loopback_host(hostname):
        return False
    exp = _ws_page_origin(websocket)
    if exp is None:
        return False
    return got == exp


def extract_ws_token(websocket: WebSocket) -> Optional[str]:
    """X-API-Token → Bearer → cookie agentix_token → ?token=."""
    return extract_token(websocket.headers, websocket.cookies, websocket.query_params)


def create_app(workdir: Optional[Path] = None) -> FastAPI:
    cfg = load_config(workdir=workdir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        watcher: Watcher = app.state.watcher
        task = asyncio.create_task(watcher.run(), name="dashboard-watcher")
        app.state.watcher_task = task
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Agentix Control", lifespan=lifespan)
    app.state.config = cfg
    app.state.workdir = cfg.workdir
    # явные пути в store, не chdir — TestClient живёт в том же процессе, что и соседние тесты
    app.state.store = DashboardStore(cfg.workdir)
    app.state.broadcaster = WSBroadcaster()
    app.state.watcher = Watcher(cfg.workdir, app.state.broadcaster)
    app.state.csrf_token = generate_csrf_token()

    @app.middleware("http")
    async def _loopback_only(request: Request, call_next):
        # каждый запрос, включая /health: и peer, и Host
        peer = request.client.host if request.client else None
        host = request.headers.get("host", "")
        if not is_loopback_address(peer) or not is_loopback_host(host):
            return JSONResponse(
                {"detail": "dashboard is loopback-only"},
                status_code=403,
            )
        cfg_now: DashboardConfig = request.app.state.config
        provided = extract_request_token(request)
        if not token_ok(cfg_now.token, provided):
            resp = JSONResponse({"detail": "unauthorized"}, status_code=401)
            _stamp_cookies(request, resp, provided)
            return resp
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if content_length_too_large(request.headers, MAX_BODY_BYTES):
                return JSONResponse({"detail": "payload too large"}, status_code=413)
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "payload too large"}, status_code=413)
            if not is_same_origin(request):
                return JSONResponse({"detail": "cross-origin rejected"}, status_code=403)
            expected_csrf = str(getattr(request.app.state, "csrf_token", "") or "")
            if not csrf_ok(request, expected_csrf):
                return JSONResponse({"detail": "csrf rejected"}, status_code=403)
        response = await call_next(request)
        _stamp_cookies(request, response, provided)
        return response

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.websocket("/ws/ui")
    async def ws_ui(websocket: WebSocket) -> None:
        # HTTP-middleware апгрейд не видит — peer/Host/Origin/токен здесь.
        peer = websocket.client.host if websocket.client else None
        host = websocket.headers.get("host", "")
        if not is_loopback_address(peer) or not is_loopback_host(host):
            await websocket.close(code=WS_CLOSE_ORIGIN)
            return
        if not ws_origin_ok(websocket):
            await websocket.close(code=WS_CLOSE_ORIGIN)
            return
        cfg_now: DashboardConfig = websocket.app.state.config
        if not token_ok(cfg_now.token, extract_ws_token(websocket)):
            await websocket.close(code=WS_CLOSE_TOKEN)
            return
        bc: WSBroadcaster = websocket.app.state.broadcaster
        await bc.connect(websocket)
        try:
            await websocket.send_json(
                {
                    "type": "connected",
                    "clients": bc.client_count(),
                    "workdir": websocket.app.state.workdir.name,
                    "ts": _iso_now(),
                }
            )
            while True:
                await asyncio.sleep(HEARTBEAT_S)
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "ts": _iso_now(),
                        "clients": bc.client_count(),
                    }
                )
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            await bc.disconnect(websocket)

    register_routes(app)
    register_actions(app)
    return app


def _stamp_cookies(request: Request, response, provided) -> None:
    csrf = str(getattr(request.app.state, "csrf_token", "") or "")
    if csrf:
        set_csrf_cookie(response, csrf)
    cfg_now: DashboardConfig = request.app.state.config
    expected = (cfg_now.token or "").strip()
    if expected and token_ok(expected, provided):
        set_token_cookie(response, expected)


def serve(
    *,
    workdir: Optional[Path] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    cfg: DashboardConfig = load_config(workdir=workdir, host=host, port=port)
    if not bind_host_allowed(cfg.host):
        raise BindError(
            f"дашборд только loopback (TeleGrok SR-04), отказ: {cfg.host}"
        )
    _raise_if_addr_in_use(cfg.host, int(cfg.port))
    app = create_app(workdir=cfg.workdir)
    import uvicorn

    uvicorn.run(
        app,
        host=cfg.host,
        port=int(cfg.port),
        workers=1,
        reload=False,
    )
