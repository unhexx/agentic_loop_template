#!/usr/bin/env python3
"""Сайдкар между agy (Gemini REST) и pxpipe (префикс /google-ai-studio).

Вход  :8101  agy  /v1beta/models/{id}:generateContent
             →    /google-ai-studio/v1beta/models/{canonical}:…
Выход :8102  pxpipe шлёт сюда /google-ai-studio/…
             →    https://generativelanguage.googleapis.com/v1beta/…

Только loopback. Тела запросов и ключи в лог не пишем.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

HOST = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
IN_PORT = int(os.environ.get("SHIM_IN_PORT", "8101"))
OUT_PORT = int(os.environ.get("SHIM_OUT_PORT", "8102"))
PXPIPE_URL = os.environ.get("PXPIPE_AGY_URL", "http://127.0.0.1:8103").rstrip("/")
GOOGLE_UPSTREAM = os.environ.get(
    "GOOGLE_UPSTREAM", "https://generativelanguage.googleapis.com"
).rstrip("/")
MAX_BODY = int(os.environ.get("SHIM_MAX_BODY", str(32 * 1024 * 1024)))
HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "expect",
    "host",
    "content-length",
}

# Идентификаторы agy → measured-ключ pxpipe + thinkingLevel Gemini 3.
SUFFIX_MAP = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}

_lock = threading.Lock()
_stats: dict[str, Any] = {
    "inbound": 0,
    "outbound": 0,
    "rewrites": 0,
    "last": None,
}

ROUTE = (
    r"^/(?:google-ai-studio/)?"
    r"(v1beta|v1)/models/"
    r"([^/:]+)"
    r":(generateContent|streamGenerateContent|countTokens)$"
)


def log(msg: str) -> None:
    print(f"[pxpipe-agy-shim] {msg}", file=sys.stderr, flush=True)


def parse_route(path: str) -> tuple[str, str, str] | None:
    """Версия, модель, метод; path без query."""
    m = re.match(ROUTE, path)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def canonicalize(model: str) -> tuple[str, str | None]:
    """Снимает -high/-medium/-low с gemini-3.7-flash-*."""
    lower = model.lower()
    for suf, level in SUFFIX_MAP.items():
        tail = f"-{suf}"
        if lower.endswith(tail) and lower[: -len(tail)] == "gemini-3.7-flash":
            return "gemini-3.7-flash", level
    return model, None


def inject_thinking(body: bytes, level: str | None) -> bytes:
    if not level or not body:
        return body
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(data, dict):
        return body
    gen = data.get("generationConfig")
    if not isinstance(gen, dict):
        gen = {}
        data["generationConfig"] = gen
    existing = gen.get("thinkingConfig") or gen.get("thinking_config")
    if isinstance(existing, dict) and (
        existing.get("thinkingLevel") or existing.get("thinking_level")
    ):
        return body
    gen["thinkingConfig"] = {"thinkingLevel": level}
    if "model" in data and isinstance(data["model"], str):
        canon, _ = canonicalize(data["model"].rsplit("/", 1)[-1])
        prefix = "models/" if data["model"].startswith("models/") else ""
        data["model"] = prefix + canon
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def filter_headers(raw: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in raw:
        if k.lower() in HOP:
            continue
        out.append((k, v))
    return out


def split_url(url: str) -> tuple[str, str, int, bool]:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError(f"bad url: {url}")
    port = p.port or (443 if p.scheme == "https" else 80)
    return p.hostname, p.path, port, p.scheme == "https"


def forward_stream(
    handler: BaseHTTPRequestHandler,
    method: str,
    target_base: str,
    path_qs: str,
    headers: list[tuple[str, str]],
    body: bytes,
    timeout: float = 300.0,
) -> None:
    host, _, port, tls = split_url(target_base)
    if tls:
        ctx = ssl.create_default_context()
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ctx
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        hdrs = {k: v for k, v in headers}
        hdrs["Host"] = f"{host}:{port}" if port not in (80, 443) else host
        hdrs["Connection"] = "close"
        if body:
            hdrs["Content-Length"] = str(len(body))
        conn.request(method, path_qs, body=body or None, headers=hdrs)
        resp = conn.getresponse()
        handler.send_response(resp.status)
        skip = {"transfer-encoding", "connection", "keep-alive", "content-length"}
        length = resp.length
        for k, v in resp.getheaders():
            if k.lower() in skip:
                continue
            handler.send_header(k, v)
        handler.send_header("Connection", "close")
        if length is not None:
            handler.send_header("Content-Length", str(length))
        handler.end_headers()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
        handler.wfile.flush()
    finally:
        conn.close()


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        log(f"{self.address_string()} {fmt % args}")

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or "0")
        if n < 0 or n > MAX_BODY:
            self.send_error(413, "body too large")
            return b""
        return self.rfile.read(n) if n else b""

    def _health(self) -> None:
        payload = json.dumps(
            {
                "ok": True,
                "host": HOST,
                "in_port": IN_PORT,
                "out_port": OUT_PORT,
                "pxpipe": PXPIPE_URL,
                "stats": _stats,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _reject(self, code: int, msg: str) -> None:
        payload = json.dumps({"error": {"type": "forbidden", "message": msg}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/health", "/"):
            self._health()
            return
        self._reject(404, "not allowed")

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] in ("/health", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self._reject(404, "not allowed")


class InboundHandler(_Base):
    """Сторона agy: Gemini REST → путь google-ai-studio для pxpipe."""

    def do_POST(self) -> None:  # noqa: N802
        path_only, _, qs = self.path.partition("?")
        parsed = parse_route(path_only)
        if not parsed:
            self._reject(404, "only Gemini generateContent/stream/countTokens")
            return
        version, model, method = parsed
        canon, level = canonicalize(model)
        body = self._read_body()
        if level:
            body = inject_thinking(body, level)
        out_path = f"/google-ai-studio/{version}/models/{canon}:{method}"
        if qs:
            out_path = f"{out_path}?{qs}"
        with _lock:
            _stats["inbound"] += 1
            if level:
                _stats["rewrites"] += 1
            _stats["last"] = {
                "from_model": model,
                "to_model": canon,
                "thinkingLevel": level,
                "method": method,
                "out_path": out_path.split("?", 1)[0],
                "ts": int(time.time()),
            }
        log(
            f"IN {method} {model} -> {canon} thinking={level or '-'} "
            f"pxpipe{out_path.split('?', 1)[0]}"
        )
        try:
            forward_stream(
                self,
                "POST",
                PXPIPE_URL,
                out_path,
                filter_headers(list(self.headers.items())),
                body,
            )
        except OSError as exc:
            log(f"pxpipe unreachable: {exc}")
            self._reject(502, "pxpipe-agy unreachable")


class OutboundHandler(_Base):
    """Сторона pxpipe: снимаем /google-ai-studio и идём в Google."""

    def do_POST(self) -> None:  # noqa: N802
        path_only, _, qs = self.path.partition("?")
        if path_only.startswith("/google-ai-studio/"):
            path_only = path_only[len("/google-ai-studio") :]
        parsed = parse_route(path_only)
        if not parsed:
            self._reject(404, "only Gemini generateContent/stream/countTokens")
            return
        version, model, method = parsed
        canon, level = canonicalize(model)
        if level:
            model = canon
            path_only = f"/{version}/models/{model}:{method}"
        out_path = path_only
        if qs:
            out_path = f"{out_path}?{qs}"
        body = self._read_body()
        with _lock:
            _stats["outbound"] += 1
        log(f"OUT {method} {model} -> {GOOGLE_UPSTREAM}{path_only}")
        try:
            forward_stream(
                self,
                "POST",
                GOOGLE_UPSTREAM,
                out_path,
                filter_headers(list(self.headers.items())),
                body,
            )
        except OSError as exc:
            log(f"google unreachable: {exc}")
            self._reject(502, "google upstream unreachable")


def _serve(handler: type[BaseHTTPRequestHandler], port: int) -> ThreadingHTTPServer:
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing non-loopback HOST={HOST!r}")
    httpd = ThreadingHTTPServer((HOST, port), handler)
    httpd.daemon_threads = True
    return httpd


def main() -> None:
    log(f"inbound  {HOST}:{IN_PORT}  -> {PXPIPE_URL}")
    log(f"outbound {HOST}:{OUT_PORT} -> {GOOGLE_UPSTREAM}")
    inbound = _serve(InboundHandler, IN_PORT)
    outbound = _serve(OutboundHandler, OUT_PORT)
    t = threading.Thread(target=outbound.serve_forever, name="out", daemon=True)
    t.start()
    try:
        inbound.serve_forever()
    except KeyboardInterrupt:
        pass
    inbound.shutdown()
    outbound.shutdown()


if __name__ == "__main__":
    main()
