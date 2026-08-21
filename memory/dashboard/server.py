# -*- coding: utf-8 -*-
"""FastAPI-приложение дашборда и точка запуска uvicorn."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from memory.dashboard.config import DashboardConfig, bind_host_allowed, load_config
from memory.dashboard.render import render_page
from memory.dashboard.security import is_loopback_address, is_loopback_host


class BindError(RuntimeError):
    """Пытались слушать не loopback."""


def create_app(workdir: Optional[Path] = None) -> FastAPI:
    cfg = load_config(workdir=workdir)
    app = FastAPI(title="Agentix Control")
    app.state.config = cfg
    app.state.workdir = cfg.workdir

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
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/")
    async def loop_page() -> HTMLResponse:
        wd: Path = app.state.workdir
        html = render_page(
            "loop.html",
            title="Loop",
            csrf="",
            year=str(datetime.now(timezone.utc).year),
            conn_dot="WS: polling",
            workdir_name=wd.name,
            workdir_path=str(wd),
        )
        return HTMLResponse(html)

    return app


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
    app = create_app(workdir=cfg.workdir)
    import uvicorn

    uvicorn.run(
        app,
        host=cfg.host,
        port=int(cfg.port),
        workers=1,
        reload=False,
    )
