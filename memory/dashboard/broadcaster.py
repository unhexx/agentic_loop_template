# -*- coding: utf-8 -*-
"""Рассылка JSON-сигналов UI-клиентам по WebSocket."""

from __future__ import annotations

import asyncio
from typing import Any, Set

from fastapi import WebSocket


class WSBroadcaster:
    """Пул живых сокетов: accept, рассылка, выкидывание мёртвых."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        """Принимает апгрейд и кладёт сокет в пул."""
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def connect(self, ws: WebSocket) -> None:
        await self.register(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        await self.unregister(ws)

    async def broadcast(self, message: dict[str, Any]) -> int:
        """Шлёт JSON всем; мёртвые вычищаются. Возвращает число доставленных."""
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return 0
        sent = 0
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
        return sent

    def client_count(self) -> int:
        return len(self._clients)
