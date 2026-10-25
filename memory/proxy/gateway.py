# -*- coding: utf-8 -*-
"""
Шлюз Agentix: loopback :8110 → хостовый pxpipe :8100.

stdlib ThreadingHTTPServer. Тело стрима копируем чанками по 8 КиБ,
полный буфер SSE не копим. 0.0.0.0 не слушаем.
"""

from __future__ import annotations

import hmac
import json
import os
import ssl
import time
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from memory.logutil import get_logger
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
MAX_BODY_BYTES = 8 * 1024 * 1024
MIN_TOKEN_LEN = 16
SECRET_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "x-api-token",
        "x-auth-token",
    }
)
log = get_logger("memory.proxy.gateway")


class BindError(RuntimeError):
    """Пытались слушать не loopback."""


class AuthConfigError(RuntimeError):
    """Пустой токен без явного opt-in."""


def _within(candidate: Path, base: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def resolve_project_root(
    headers: Optional[Dict[str, str]] = None,
    env_root: Optional[str] = None,
    *,
    allow_header: bool = False,
) -> Optional[Path]:
    """Корень только из явного workdir. Заголовок — путь внутри него."""
    env_raw = (env_root or "").strip()
    if not env_raw:
        return None
    base = Path(env_raw).expanduser()
    try:
        base_resolved = base.resolve()
    except OSError:
        return None
    if not base_resolved.is_dir():
        return None
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    header_raw = hdrs.get("x-agentix-root", "").strip() if allow_header else ""
    if header_raw:
        cand = Path(header_raw).expanduser()
        if cand.exists() and _within(cand, base_resolved):
            return cand.resolve()
    return base_resolved


def configured_token(explicit: Optional[str] = None) -> str:
    if explicit is not None:
        return explicit.strip()
    return os.environ.get("AGENTIX_GATEWAY_TOKEN", "").strip()


def configured_allow_open(explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("AGENTIX_GATEWAY_ALLOW_OPEN", "").strip() == "1"


def max_body_bytes() -> int:
    raw = os.environ.get("AGENTIX_GATEWAY_MAX_BODY", "").strip()
    if not raw:
        return MAX_BODY_BYTES
    try:
        n = int(raw)
    except ValueError:
        return MAX_BODY_BYTES
    return n if n > 0 else MAX_BODY_BYTES


def token_matches(expected: str, provided: Optional[str]) -> bool:
    exp = (expected or "").strip()
    if not exp or not provided:
        return False
    return hmac.compare_digest(str(provided), exp)


def extract_gateway_token(headers: Dict[str, str]) -> Optional[str]:
    hdrs = {str(k).lower(): str(v) for k, v in headers.items()}
    raw = hdrs.get("x-api-token", "").strip()
    if raw:
        return raw
    auth = hdrs.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return None


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
            if not self._require_auth(dict(self.headers.items())):
                return
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

    def _allow_root_header(self) -> bool:
        return bool(getattr(self.server, "agentix_allow_root_header", False))

    def _project_root(self, headers: Dict[str, str]) -> Optional[Path]:
        return resolve_project_root(
            headers,
            self._env_root(),
            allow_header=self._allow_root_header(),
        )

    def _authorized(self, headers: Dict[str, str]) -> bool:
        if bool(getattr(self.server, "agentix_allow_open", False)):
            return True
        expected = str(getattr(self.server, "agentix_token", "") or "")
        return token_matches(expected, extract_gateway_token(headers))

    def _require_auth(self, headers: Dict[str, str]) -> bool:
        if self._authorized(headers):
            return True
        self._json(401, {"error": "unauthorized"})
        return False

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
        headers = {k: v for k, v in self.headers.items()}
        root = self._project_root(headers)
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
        limit = int(getattr(self.server, "agentix_max_body", MAX_BODY_BYTES))
        raw = self.headers.get("Content-Length")
        if raw is None or str(raw).strip() == "":
            return b""
        try:
            length = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("bad content-length") from exc
        if length < 0 or length > limit:
            raise ValueError("body too large")
        return self.rfile.read(length)

    def _proxy(self) -> None:
        path = self.path.split("?", 1)[0]
        if not path.startswith("/v1/"):
            self._json(404, {"error": "not found", "path": path})
            return
        headers = {k: v for k, v in self.headers.items()}
        if not self._require_auth(headers):
            return
        cfg = self._cfg()
        try:
            body = self._read_body() if self.command in {"POST", "PUT", "PATCH"} else b""
        except ValueError as exc:
            code = 413 if "too large" in str(exc) else 400
            self._json(code, {"error": str(exc)})
            return
        root = self._project_root(headers)
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
            except Exception as exc:
                log.warning("process_request failed path=%s: %s", path, exc)
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
        strip_secrets = False
        if not tcp_ok(host, port, timeout=CONNECT_TIMEOUT):
            if mode == "preferred":
                fallback = str(cfg.get("upstream_fallback") or "")
                if fallback:
                    upstream = fallback
                    strip_secrets = True
                else:
                    self._fail_upstream(path, headers, meta, t0, root)
                    return
            else:
                self._fail_upstream(path, headers, meta, t0, root)
                return

        try:
            status, content_type, collected = self._forward(
                upstream,
                path,
                headers,
                body,
                idle_timeout=float(cfg.get("timeout_s") or 900),
                strip_secrets=strip_secrets,
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
        strip_secrets: bool = False,
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
            if strip_secrets and k.lower() in SECRET_HEADERS:
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
        token: str = "",
        allow_open: bool = False,
        allow_root_header: bool = False,
        max_body: int = MAX_BODY_BYTES,
    ) -> None:
        super().__init__(addr, GatewayHandler)
        self.agentix_cfg = cfg
        self.agentix_upstream = upstream
        self.agentix_env_root = env_root
        self.agentix_quiet = bool(quiet)
        self.agentix_ssl_context = ssl_context
        self.agentix_token = token
        self.agentix_allow_open = bool(allow_open)
        self.agentix_allow_root_header = bool(allow_root_header)
        self.agentix_max_body = int(max_body)


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
    token: Optional[str] = None,
    allow_open: Optional[bool] = None,
) -> AgentixServer:
    if not bind_host_allowed(host):
        raise BindError(f"шлюз только loopback, отказ: {host}")
    cfg = load_proxy_config(workdir)
    up = (upstream or cfg.get("pxpipe_base") or DEFAULT_PXPIPE_BASE).rstrip("/")
    env_root = str(workdir.resolve()) if workdir is not None else None
    got_token = configured_token(token)
    open_ok = configured_allow_open(allow_open)
    if got_token and len(got_token) < MIN_TOKEN_LEN:
        raise AuthConfigError(f"AGENTIX_GATEWAY_TOKEN короче {MIN_TOKEN_LEN} символов")
    return AgentixServer(
        (host, int(port)),
        cfg,
        up,
        env_root=env_root,
        quiet=quiet,
        ssl_context=ssl_context,
        token=got_token,
        allow_open=open_ok,
        allow_root_header=workdir is not None,
        max_body=max_body_bytes(),
    )


def serve(
    host: str = GATEWAY_HOST,
    port: int = GATEWAY_PORT,
    *,
    upstream: Optional[str] = None,
    workdir: Optional[Path] = None,
) -> None:
    token = configured_token()
    allow = configured_allow_open()
    if not token and not allow:
        raise AuthConfigError(
            "AGENTIX_GATEWAY_TOKEN пуст; задайте его или AGENTIX_GATEWAY_ALLOW_OPEN=1"
        )
    httpd = make_server(
        host,
        port,
        upstream=upstream,
        workdir=workdir,
        token=token,
        allow_open=allow,
    )
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
