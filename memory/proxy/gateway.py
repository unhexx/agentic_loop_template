# -*- coding: utf-8 -*-
"""
Шлюз Agentix: loopback :8110 → хостовый pxpipe :8100.

stdlib ThreadingHTTPServer. Тело стрима копируем чанками по 8 КиБ,
полный буфер SSE не копим. 0.0.0.0 не слушаем.
"""

from __future__ import annotations

import json
import ssl
import time
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from memory.proxy.audit import redact_headers
from memory.proxy.config import (
    DEFAULT_GATEWAY_BASE,
    DEFAULT_PXPIPE_BASE,
    GATEWAY_HOST,
    GATEWAY_PORT,
    host_header,
    load_proxy_config,
    split_host_port,
)
from memory.proxy.health import probe_pxpipe, tcp_ok
from memory.proxy.middleware import (
    is_middleware_path,
    maybe_store_cache,
    process_request,
    write_audit,
)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
CHUNK = 8192
CONNECT_TIMEOUT = 2.0


class BindError(RuntimeError):
    """Пытались слушать не loopback."""


def resolve_project_root(
    headers: Optional[Dict[str, str]] = None,
    env_root: Optional[str] = None,
) -> Optional[Path]:
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    raw = hdrs.get("x-agentix-root") or (env_root or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None


def _open_upstream(
    url: str,
    idle_timeout: float,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> HTTPConnection:
    parsed = urlparse(url if "://" in url else "http://" + url)
    scheme = parsed.scheme or "http"
    host, port = split_host_port(url, 443 if scheme == "https" else 80)
    if scheme == "https":
        ctx = ssl_context or ssl.create_default_context()
        conn: HTTPConnection = HTTPSConnection(
            host, port, timeout=CONNECT_TIMEOUT, context=ctx
        )
    else:
        conn = HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
    conn.connect()
    if conn.sock is not None:
        conn.sock.settimeout(idle_timeout)
    return conn


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "AgentixGateway"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # без Authorization в access-логе
        if getattr(self.server, "agentix_quiet", False):
            return
        BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/healthz", "/health"}:
            self._healthz()
            return
        if path == "/stats":
            self._stats()
            return
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()

    def _cfg(self) -> Dict[str, Any]:
        return getattr(self.server, "agentix_cfg", load_proxy_config())  # type: ignore[attr-defined]

    def _upstream_base(self) -> str:
        return str(
            getattr(self.server, "agentix_upstream", None)  # type: ignore[attr-defined]
            or self._cfg().get("pxpipe_base")
            or DEFAULT_PXPIPE_BASE
        )

    def _env_root(self) -> Optional[str]:
        return getattr(self.server, "agentix_env_root", None)  # type: ignore[attr-defined]

    def _json(self, code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _healthz(self) -> None:
        cfg = self._cfg()
        px = probe_pxpipe(cfg)
        self._json(
            200,
            {
                "ok": True,
                "pxpipe_ok": bool(px.get("ok")),
                "pxpipe": px,
                "gateway": DEFAULT_GATEWAY_BASE,
            },
        )

    def _stats(self) -> None:
        cfg = self._cfg()
        root = resolve_project_root(dict(self.headers.items()), self._env_root())
        from memory.proxy.cache import stats as cache_stats

        self._json(
            200,
            {
                "mode": cfg.get("mode"),
                "pxpipe_base": cfg.get("pxpipe_base"),
                "cache": cache_stats(root),
            },
        )

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _proxy(self) -> None:
        path = self.path.split("?", 1)[0]
        if not path.startswith("/v1/"):
            self._json(404, {"error": "not found", "path": path})
            return
        cfg = self._cfg()
        headers = {k: v for k, v in self.headers.items()}
        body = self._read_body() if self.command in {"POST", "PUT", "PATCH"} else b""
        root = resolve_project_root(headers, self._env_root())
        meta: Dict[str, Any] = {
            "sha256": "",
            "cache_hit": False,
            "distill": "none",
            "bytes_in": len(body),
        }
        t0 = time.monotonic()
        if self.command == "POST" and is_middleware_path(path):
            try:
                body, meta = process_request(
                    body, path=path, headers=headers, cfg=cfg, project_root=root
                )
            except Exception:
                pass
            if meta.get("cache_hit") and meta.get("cached_body") is not None:
                cached = bytes(meta["cached_body"])
                self.send_response(int(meta.get("cached_status") or 200))
                self.send_header(
                    "Content-Type",
                    str(meta.get("cached_type") or "application/json"),
                )
                self.send_header("Content-Length", str(len(cached)))
                self.send_header("X-Agentix-Cache", "hit")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(cached)
                write_audit(
                    root,
                    {
                        "path": path,
                        "method": self.command,
                        "status": int(meta.get("cached_status") or 200),
                        "cache_hit": True,
                        "sha256": meta.get("sha256"),
                        "bytes_in": meta.get("bytes_in"),
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                        "headers": redact_headers(headers),
                    },
                )
                return

        upstream = self._upstream_base()
        host, port = split_host_port(upstream, 8100)
        mode = str(cfg.get("mode") or "required")
        if not tcp_ok(host, port, timeout=CONNECT_TIMEOUT):
            if mode == "preferred":
                fallback = str(cfg.get("upstream_fallback") or "")
                if fallback:
                    upstream = fallback
                else:
                    self._fail_upstream(path, headers, meta, t0, root)
                    return
            else:
                self._fail_upstream(path, headers, meta, t0, root)
                return

        try:
            status, content_type, collected = self._forward(
                upstream, path, headers, body, idle_timeout=float(cfg.get("timeout_s") or 900)
            )
        except Exception as exc:
            write_audit(
                root,
                {
                    "path": path,
                    "method": self.command,
                    "status": 502,
                    "error": str(exc)[:300],
                    "sha256": meta.get("sha256"),
                    "bytes_in": meta.get("bytes_in"),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "headers": redact_headers(headers),
                    "distill": meta.get("distill"),
                },
            )
            self._json(502, {"error": "upstream failed", "detail": str(exc)[:200]})
            return

        duration = int((time.monotonic() - t0) * 1000)
        stream = False
        try:
            if body:
                stream = bool(json.loads(body.decode("utf-8")).get("stream"))
        except Exception:
            stream = False
        if collected is not None:
            maybe_store_cache(
                project_root=root,
                cfg=cfg,
                meta=meta,
                status=status,
                content_type=content_type,
                response_body=collected,
                request_obj_stream=stream,
            )
        write_audit(
            root,
            {
                "path": path,
                "method": self.command,
                "status": status,
                "cache_hit": False,
                "sha256": meta.get("sha256"),
                "bytes_in": meta.get("bytes_in"),
                "bytes_out": len(collected) if collected is not None else None,
                "duration_ms": duration,
                "distill": meta.get("distill"),
                "headers": redact_headers(headers),
            },
        )

    def _fail_upstream(
        self,
        path: str,
        headers: Dict[str, str],
        meta: Dict[str, Any],
        t0: float,
        root: Optional[Path],
    ) -> None:
        write_audit(
            root,
            {
                "path": path,
                "method": self.command,
                "status": 502,
                "error": "pxpipe down",
                "sha256": meta.get("sha256"),
                "bytes_in": meta.get("bytes_in"),
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "headers": redact_headers(headers),
            },
        )
        self._json(
            502,
            {
                "error": "pxpipe unavailable",
                "mode": self._cfg().get("mode"),
                "hint": "start pxpipe or set AGENTIX_PROXY=0",
            },
        )

    def _forward(
        self,
        upstream: str,
        path: str,
        headers: Dict[str, str],
        body: bytes,
        idle_timeout: float,
    ) -> Tuple[int, str, Optional[bytes]]:
        parsed = urlparse(upstream if "://" in upstream else "http://" + upstream)
        scheme = parsed.scheme or "http"
        host, port = split_host_port(upstream, 443 if scheme == "https" else 80)
        fwd: Dict[str, str] = {}
        for k, v in headers.items():
            if k.lower() in HOP_BY_HOP:
                continue
            if k.lower() == "host":
                continue
            fwd[k] = v
        fwd["Host"] = host_header(host, port, scheme)
        fwd["Connection"] = "close"
        if body:
            fwd["Content-Length"] = str(len(body))
        ssl_ctx = getattr(self.server, "agentix_ssl_context", None)
        conn = _open_upstream(upstream, idle_timeout, ssl_context=ssl_ctx)
        try:
            url_path = self.path
            conn.request(self.command, url_path, body=body or None, headers=fwd)
            resp = conn.getresponse()
            status = int(resp.status)
            content_type = resp.getheader("Content-Type") or "application/octet-stream"
            self.send_response(status)
            for hk, hv in resp.getheaders():
                if hk.lower() in HOP_BY_HOP or hk.lower() == "content-length":
                    continue
                self.send_header(hk, hv)
            self.send_header("Connection", "close")
            self.end_headers()
            collected = bytearray()
            store = status < 400
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                if store:
                    collected.extend(chunk)
                    if len(collected) > 2_000_000:
                        store = False
                        collected = bytearray()
            return status, content_type, bytes(collected) if store else None
        finally:
            try:
                conn.close()
            except Exception:
                pass


class AgentixServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        addr: Tuple[str, int],
        cfg: Dict[str, Any],
        upstream: str,
        env_root: Optional[str] = None,
        quiet: bool = False,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        super().__init__(addr, GatewayHandler)
        self.agentix_cfg = cfg
        self.agentix_upstream = upstream
        self.agentix_env_root = env_root
        self.agentix_quiet = bool(quiet)
        self.agentix_ssl_context = ssl_context


def bind_host_allowed(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in {"127.0.0.1", "localhost", "::1"}


def make_server(
    host: str = GATEWAY_HOST,
    port: int = GATEWAY_PORT,
    *,
    upstream: Optional[str] = None,
    workdir: Optional[Path] = None,
    quiet: bool = False,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> AgentixServer:
    if not bind_host_allowed(host):
        raise BindError(f"шлюз только loopback, отказ: {host}")
    cfg = load_proxy_config(workdir)
    up = (upstream or cfg.get("pxpipe_base") or DEFAULT_PXPIPE_BASE).rstrip("/")
    env_root = str(workdir) if workdir is not None else None
    return AgentixServer(
        (host, int(port)),
        cfg,
        up,
        env_root=env_root,
        quiet=quiet,
        ssl_context=ssl_context,
    )


def serve(
    host: str = GATEWAY_HOST,
    port: int = GATEWAY_PORT,
    *,
    upstream: Optional[str] = None,
    workdir: Optional[Path] = None,
) -> None:
    httpd = make_server(host, port, upstream=upstream, workdir=workdir)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
