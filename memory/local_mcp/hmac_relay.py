# -*- coding: utf-8 -*-
"""Same-machine loopback checker for a Jenkins controller.

Bitbucket cannot reach 127.0.0.1. Setting webhook_relay does not publish 8114.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping

from memory.dashboard.config import bind_host_allowed
from memory.local_mcp.audit import local_pipeline_webhook_reject
from memory.local_mcp.backends.act import _regular_under
from memory.local_mcp.backends.jenkins import _parameter_names, trigger_job
from memory.local_mcp.config import load_local_pipeline
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

_HOST = "127.0.0.1"
_PORT = 8114
_FORBIDDEN_PORTS = frozenset({8100, 8101, 8102, 8103, 8110, 8112})
_BODY_CAP = 1024 * 1024
_REPLAY_TTL_S = 600.0
_ID_MAX = 200
_HEX = re.compile(r"^[0-9a-f]+$")
_REASONS = frozenset({"bad_signature", "missing_request_id", "replay"})


def bind_permitted(host: object, port: object) -> bool:
    """Concrete bind is 127.0.0.1:8114. Other loopback names still fail."""
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        return False
    if port in _FORBIDDEN_PORTS or port != _PORT:
        return False
    if host != _HOST:
        return False
    return bind_host_allowed(host)


class _Replay:
    def __init__(self, ttl_s: float = _REPLAY_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def seen(self, request_id: str, now: float) -> bool:
        with self._lock:
            expired = [key for key, stamp in self._seen.items() if now - stamp >= self.ttl_s]
            for key in expired:
                del self._seen[key]
            if request_id in self._seen:
                return True
            self._seen[request_id] = now
            return False

    def release(self, request_id: str) -> None:
        with self._lock:
            self._seen.pop(request_id, None)


class Relay:
    def __init__(
        self,
        workdir: Path,
        section: Mapping,
        secret: str,
        environ: Mapping[str, str],
    ) -> None:
        self.workdir = workdir
        self.section = section
        self.secret = secret
        self.environ = environ
        self.replay = _Replay()

    def handle(
        self,
        body: bytes,
        headers: Mapping[str, str] | object,
        *,
        now: float | None = None,
    ) -> tuple[int, bytes]:
        moment = time.monotonic() if now is None else now
        if len(body) > _BODY_CAP:
            return 413, b""
        if not _signed(body, self.secret, headers):
            self._reject("bad_signature")
            return 401, b""
        request_id = _request_id(headers)
        if request_id is None:
            self._reject("missing_request_id")
            return 401, b""
        if self.replay.seen(request_id, moment):
            self._reject("replay")
            return 401, b""
        # One attempt. The relay does not retry; a failed trigger may be resent.
        try:
            error = trigger_job(self.section, environ=self.environ)
        except Exception:
            self.replay.release(request_id)
            log.warning("jenkins webhook trigger failed")
            return 502, b""
        if error is not None:
            self.replay.release(request_id)
            log.warning("jenkins webhook trigger refused")
            return 502, b""
        return 200, b"ok"

    def _reject(self, reason: str) -> None:
        if reason not in _REASONS:
            return
        try:
            local_pipeline_webhook_reject(self.workdir, reason=reason)
        except Exception:
            log.warning("webhook reject audit failed")


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    if isinstance(value, str):
        return value
    return None


def _hex_mac(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch in text for ch in "\r\n\x00"):
        return None
    if text.lower().startswith("sha256="):
        text = text.split("=", 1)[1].strip()
    elif "=" in text:
        return None
    text = text.lower()
    if _HEX.fullmatch(text) is None:
        return None
    return text


def _signature_matches(body: bytes, secret: str, header: str | None) -> bool:
    presented = _hex_mac(header)
    if presented is None:
        return False
    try:
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    except Exception:
        return False
    # compare_digest raises or leaks when the lengths differ. Reject first.
    if len(presented) != len(expected):
        return False
    try:
        return hmac.compare_digest(presented, expected)
    except Exception:
        return False


def _signed(body: bytes, secret: str, headers: object) -> bool:
    first = _header(headers, "X-Hub-Signature")
    second = _header(headers, "X-Hub-Signature-256")
    return _signature_matches(body, secret, first) or _signature_matches(body, secret, second)


def _request_id(headers: object) -> str | None:
    raw = _header(headers, "X-Request-Id")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > _ID_MAX:
        return None
    if any(ch in text for ch in "\r\n\x00"):
        return None
    return text


def _mode_strict(mode: int) -> bool:
    bits = stat.S_IMODE(mode)
    # 0600 or stricter: owner read, optional owner write, nothing else.
    if bits & ~0o600:
        return False
    return bool(bits & 0o400)


def _clean_secret(text: str) -> str | None:
    if not text or any(ch in text for ch in "\r\n\x00"):
        return None
    if len(text) > 4096:
        return None
    return text


def _env_secret(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    return _clean_secret(raw)


def _file_secret(workdir: Path, section: Mapping) -> str | None:
    jenkins = section.get("jenkins")
    if not isinstance(jenkins, dict):
        return None
    raw = jenkins.get("webhook_secret_file")
    if not isinstance(raw, str) or not raw.strip():
        return None
    # Loose or escaped paths are not opened.
    path = _regular_under(raw, workdir, workdir)
    if path is None:
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if not _mode_strict(mode):
        return None
    # 4097 bytes shows the file is over the cap. Do not read the rest.
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError:
        return None
    try:
        chunks: list[bytes] = []
        remaining = 4097
        while remaining:
            try:
                piece = os.read(fd, remaining)
            except OSError:
                return None
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > 4096 or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return _clean_secret(text)


def load_secret(workdir: Path, section: Mapping, environ: Mapping[str, str]) -> str | None:
    """Env wins. A set but unusable env value does not fall back to the file."""
    if "BITBUCKET_WEBHOOK_SECRET" in environ:
        return _env_secret(environ.get("BITBUCKET_WEBHOOK_SECRET"))
    return _file_secret(workdir, section)


def prepare(workdir: Path, environ: Mapping[str, str]) -> tuple[str | None, Relay | None]:
    cfg = load_local_pipeline(Path(workdir))
    if not cfg.enabled:
        return "local_pipeline_disabled", None
    section = cfg.section
    if cfg.config_backend != "jenkins":
        return "webhook_relay_off", None
    jenkins = section.get("jenkins")
    if not isinstance(jenkins, dict) or jenkins.get("webhook_relay") is not True:
        return "webhook_relay_off", None
    secret = load_secret(Path(workdir), section, environ)
    if secret is None:
        return "webhook_secret_missing", None
    # No webhook field is a Jenkins parameter. A named allowlist has no values here.
    if _parameter_names(section):
        return "webhook_parameters_required", None
    return None, Relay(Path(workdir), section, secret, environ)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        body = self._body()
        if body is None:
            return
        status, payload = self.server.relay.handle(body, self.headers)  # type: ignore[attr-defined]
        self._reply(status, payload)

    def do_GET(self) -> None:
        self._reply(404, b"")

    def log_message(self, fmt: str, *args: object) -> None:
        del fmt, args

    def _body(self) -> bytes | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._reply(401, b"")
            relay = getattr(self.server, "relay", None)
            if relay is not None:
                relay._reject("bad_signature")
            return None
        try:
            size = int(raw)
        except ValueError:
            self._reply(400, b"")
            return None
        if isinstance(size, bool) or size < 0 or size > _BODY_CAP:
            self._reply(413, b"")
            return None
        try:
            return self.rfile.read(size)
        except OSError:
            self._reply(400, b"")
            return None

    def _reply(self, status: int, payload: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except OSError:
            return


def serve(
    workdir: Path,
    *,
    environ: Mapping[str, str] | None = None,
    host: str = _HOST,
    port: int = _PORT,
    ready: threading.Event | None = None,
    bound: list | None = None,
) -> int:
    """Bind only after the gate passes. Not started by the MCP ``serve`` command."""
    env = os.environ if environ is None else environ
    error, relay = prepare(Path(workdir), env)
    if error is not None or relay is None:
        sys.stderr.write((error or "webhook_secret_missing") + "\n")
        return 2
    if not bind_permitted(host, port):
        sys.stderr.write("webhook_bind_rejected\n")
        return 2
    try:
        httpd = _Server((host, port), _Handler)
    except OSError:
        sys.stderr.write("webhook_bind_rejected\n")
        return 2
    httpd.relay = relay  # type: ignore[attr-defined]
    if bound is not None:
        bound.append(httpd)
    if ready is not None:
        ready.set()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


def main() -> int:
    return serve(Path.cwd())
