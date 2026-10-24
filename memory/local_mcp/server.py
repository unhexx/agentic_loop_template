# -*- coding: utf-8 -*-
"""Stdio MCP server. The stdin reader never waits on a tool child."""

from __future__ import annotations

import atexit
import importlib.metadata
import logging
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from memory.local_mcp.framing import (
    FramingError,
    _open_wake_pair,
    read_message,
    try_write_message,
    write_message,
)
from memory.local_mcp.registry import catalog_tools, has_tool
from memory.local_mcp.validate import (
    argument_errors,
    format_argument_errors,
    result_errors,
)
from memory.logutil import configure_logging

log = logging.getLogger("memory.local_mcp")

# Same pause as memory.adapters.proc._KILL_GRACE_S. That module is not edited.
_KILL_GRACE_S = 2.0

_NEGOTIATED = frozenset({"2025-06-18", "2025-11-25"})
_DEFAULT_PROTOCOL = "2025-11-25"
_SERVER_NAME = "agentix-local"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# The serve() in progress. SIGTERM, SIGINT, and atexit share one handler.
_ACTIVE: Server | None = None

# Captured once at serve(). A tool argument cannot widen it.
_ALLOW_ROOT: Path | None = None


def _id_ok(req_id: Any) -> bool:
    """JSON-RPC id: string, whole number, or null. Booleans are not numbers."""
    if req_id is None or isinstance(req_id, str):
        return True
    if isinstance(req_id, bool):
        return False
    if isinstance(req_id, int):
        return True
    if isinstance(req_id, float):
        return math.isfinite(req_id) and req_id.is_integer()
    return False


def _response_id(message: Mapping[str, Any]) -> Any:
    """Id to echo. A missing or illegal id is null. The original value is kept."""
    if "id" not in message:
        return None
    req_id = message["id"]
    if _id_ok(req_id):
        return req_id
    return None


def server_version() -> str:
    """Installed distribution version, else the repo VERSION file."""
    try:
        return importlib.metadata.version("agentix")
    except importlib.metadata.PackageNotFoundError:
        pass
    except Exception:
        log.warning("importlib.metadata version lookup failed", exc_info=True)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "0.0.0"


def kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child session, wait, then SIGKILL.

    The pgid is the child's session (``start_new_session=True``), not the
    server's. Grace matches ``memory.adapters.proc._KILL_GRACE_S``.
    """
    if proc.pid is None:
        return
    if sys.platform == "win32":
        _kill_direct(proc)
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        return
    try:
        if pgid == os.getpgrp():
            log.error("refusing to signal the server process group")
            return
    except (ProcessLookupError, PermissionError, OSError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + _KILL_GRACE_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _kill_direct(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + _KILL_GRACE_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, OSError):
        pass


@dataclass
class CallContext:
    """Per-call cancel signal. ``progress_token`` is only ``_meta.progressToken``."""

    cancel: threading.Event
    progress_token: Any = None
    protocol: str = _DEFAULT_PROTOCOL
    progress: Callable[..., None] | None = None


@dataclass
class ToolResult:
    text: str
    structured: dict
    is_error: bool


Executor = Callable[[str, Mapping[str, Any], CallContext], ToolResult]


def refused_pipeline_result(
    arguments: Mapping[str, Any],
    config_backend: str | None = None,
) -> dict:
    """Schema-valid not_implemented result. Does not start a backend.

    Job and scm come from ``disabled_result``. An omitted ``config_backend``
    is the call's backend, so a jenkins call does not report ``act``.
    """
    # Lazy: config must not import this module, and probe imports it at load.
    from memory.local_mcp.config import disabled_result

    backend = arguments["backend"]
    if config_backend is None:
        config_backend = backend
    result = disabled_result(arguments, config_backend)
    result["skipped"] = False
    result["log_path"] = f".agent/local-pipeline-runs/not-implemented-{backend}.log"
    result["error"] = {
        "code": "not_implemented",
        "message": "run_local_pipeline is not implemented",
    }
    problems = result_errors(result)
    if problems:
        raise RuntimeError("refused result failed schema: " + "; ".join(problems))
    return result


def _summary_line(result: Mapping[str, Any]) -> str:
    backend = result["backend"]
    job = result["job"]
    if isinstance(job, dict) and "job_name" in job:
        label = f"{backend} {job['job_name']}"
    else:
        label = str(backend)
    return f"{label} exit={result['exit_code']} duration_ms={result['duration_ms']}"


def _startup_allow_root(allow_root: Path | None) -> Path:
    """Explicit root wins. A relative AGENTIX_PROJECT_ROOT is not a root."""
    if allow_root is not None:
        return Path(allow_root).resolve()
    env_root = os.environ.get("AGENTIX_PROJECT_ROOT")
    if isinstance(env_root, str) and Path(env_root).is_absolute():
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def default_executor(
    name: str,
    arguments: Mapping[str, Any],
    ctx: CallContext,
) -> ToolResult:
    """Catalog tools. ``act``, ``self_hosted``, and ``jenkins`` run."""
    if name == "run_local_pipeline":
        # Imported here: probe.py imports this module at load time.
        from memory.local_mcp.pipeline import run_local_pipeline

        root = _ALLOW_ROOT if _ALLOW_ROOT is not None else Path.cwd().resolve()
        structured = run_local_pipeline(
            arguments,
            allow_root=root,
            progress=ctx.progress,
            protocol=ctx.protocol,
            cancel=ctx.cancel,
        )
        return ToolResult(
            text=_summary_line(structured),
            structured=structured,
            is_error=structured.get("ok") is not True,
        )
    from memory.local_mcp.tools.local import dispatch

    root = _ALLOW_ROOT if _ALLOW_ROOT is not None else Path.cwd().resolve()
    # Omitted stays the server root. A present string cannot widen it.
    raw_dir = arguments.get("working_directory")
    workdir = Path(raw_dir) if isinstance(raw_dir, str) else root
    return dispatch(
        name,
        arguments,
        workdir=workdir,
        allow_root=root,
        cancel=ctx.cancel,
    )


class _Flight:
    def __init__(self, request_id: Any) -> None:
        self.request_id = request_id
        self.cancel = threading.Event()
        self.done = threading.Event()


class _Gate:
    """One tools/call at a time. ping does not take the slot."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._flight: _Flight | None = None

    def current_busy(self) -> bool:
        with self._mu:
            return self._flight is not None

    def begin(self, request_id: Any) -> _Flight | None:
        with self._mu:
            if self._flight is not None:
                return None
            flight = _Flight(request_id)
            self._flight = flight
            return flight

    def finish(self, flight: _Flight) -> None:
        with self._mu:
            if self._flight is flight:
                self._flight = None
        flight.done.set()

    def cancel(self, request_id: Any) -> None:
        with self._mu:
            flight = self._flight
            if flight is not None and flight.request_id == request_id:
                flight.cancel.set()

    def cancel_current(self) -> _Flight | None:
        with self._mu:
            flight = self._flight
            if flight is not None:
                flight.cancel.set()
            return flight


def _initialize_result(protocol: str, version: str) -> dict:
    return {
        "protocolVersion": protocol,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": _SERVER_NAME, "version": version},
    }


def _negotiate(params: Any) -> str:
    if isinstance(params, dict):
        offered = params.get("protocolVersion")
        if isinstance(offered, str) and offered in _NEGOTIATED:
            return offered
    return _DEFAULT_PROTOCOL


def _tools_list_result(params: Any) -> dict | None:
    """Return a page, or None when params are not an object the spec allows."""
    cursor = None
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None
    if "cursor" in params and params["cursor"] is not None:
        cursor = params["cursor"]
        if not isinstance(cursor, str):
            return None
    if cursor:
        return {"tools": []}
    return {"tools": catalog_tools()}


def _progress_token(params: Mapping[str, Any]) -> Any:
    meta = params.get("_meta")
    if isinstance(meta, dict) and "progressToken" in meta:
        return meta["progressToken"]
    return None


class Server:
    def __init__(
        self,
        stdin: TextIO,
        stdout: TextIO,
        executor: Executor,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._executor = executor
        self._gate = _Gate()
        self._protocol = _DEFAULT_PROTOCOL
        self._version = server_version()
        # Socket, not a pipe: Windows select() can wait on it.
        self._wake_r, self._wake_w = _open_wake_pair()

    def run(self) -> int:
        reader = threading.Thread(
            target=self._read_loop,
            name="local-mcp-stdin",
            daemon=False,
        )
        reader.start()
        try:
            try:
                reader.join()
            except KeyboardInterrupt:
                self.shutdown_wait()
                return 128 + int(signal.SIGINT)
            self.shutdown_wait()
            return 0
        finally:
            self._close_wake()

    def shutdown_wait(self) -> None:
        """Cancel the in-flight call and wait for its child group to die."""
        self._wake()
        flight = self._gate.cancel_current()
        if flight is not None:
            flight.done.wait(timeout=_KILL_GRACE_S + 3.0)

    def _wake(self) -> None:
        sock = self._wake_w
        if not isinstance(sock, socket.socket):
            return
        try:
            sock.send(b"\x01")
        except OSError:
            pass

    def _close_wake(self) -> None:
        for attr in ("_wake_r", "_wake_w"):
            sock = getattr(self, attr, None)
            if not isinstance(sock, socket.socket):
                continue
            setattr(self, attr, None)
            try:
                sock.close()
            except OSError:
                pass

    def _read_loop(self) -> None:
        while True:
            try:
                message = read_message(self._stdin, wake_fd=self._wake_r)
            except EOFError:
                self._gate.cancel_current()
                return
            except (FramingError, UnicodeDecodeError):
                # A bad frame, including non-UTF-8. Keep reading.
                self._error(None, PARSE_ERROR, "Parse error")
                continue
            except Exception:
                log.exception("stdin read failed")
                self._error(None, INTERNAL_ERROR, "Internal error")
                return
            try:
                self._dispatch(message)
            except Exception:
                log.exception("dispatch failed")
                req_id = _response_id(message) if isinstance(message, dict) else None
                self._error(req_id, INTERNAL_ERROR, "Internal error")

    def _dispatch(self, message: Any) -> None:
        if isinstance(message, list) or not isinstance(message, dict):
            self._error(None, INVALID_REQUEST, "Invalid Request")
            return
        req_id = _response_id(message)
        if message.get("jsonrpc") != "2.0":
            self._error(req_id, INVALID_REQUEST, "Invalid Request")
            return
        method = message.get("method")
        if not isinstance(method, str) or not method:
            self._error(req_id, INVALID_REQUEST, "Invalid Request")
            return
        # Notifications omit id. A present null is a request whose id is null.
        if "id" not in message:
            self._notification(method, message.get("params"))
            return
        if not _id_ok(message["id"]):
            self._error(None, INVALID_REQUEST, "Invalid Request")
            return
        req_id = message["id"]
        params = message.get("params")
        if self._gate.current_busy() and method != "ping":
            self._error(req_id, INVALID_REQUEST, "request_in_flight")
            return
        if method == "ping":
            self._result(req_id, {})
            return
        if method == "initialize":
            self._protocol = _negotiate(params)
            self._result(req_id, _initialize_result(self._protocol, self._version))
            return
        if method == "tools/list":
            page = _tools_list_result(params)
            if page is None:
                self._error(req_id, INVALID_PARAMS, "Invalid params")
                return
            self._result(req_id, page)
            return
        if method == "tools/call":
            self._start_call(req_id, params)
            return
        self._error(req_id, METHOD_NOT_FOUND, "Method not found")

    def _notification(self, method: str, params: Any) -> None:
        if method == "notifications/initialized":
            return
        if method == "notifications/cancelled":
            if isinstance(params, dict) and "requestId" in params:
                self._gate.cancel(params["requestId"])
            return

    def _start_call(self, req_id: Any, params: Any) -> None:
        if not isinstance(params, dict):
            self._error(req_id, INVALID_PARAMS, "Invalid params")
            return
        name = params.get("name")
        if not isinstance(name, str) or not name:
            self._error(req_id, INVALID_PARAMS, "Invalid params")
            return
        if "arguments" in params and params["arguments"] is not None:
            if not isinstance(params["arguments"], dict):
                self._error(req_id, INVALID_PARAMS, "Invalid params")
                return
            arguments = params["arguments"]
        else:
            arguments = {}
        if not has_tool(name):
            self._error(req_id, INVALID_PARAMS, "Invalid params: unknown tool")
            return
        try:
            errors = argument_errors(name, arguments)
        except Exception:
            log.exception("schema check failed")
            self._error(req_id, INTERNAL_ERROR, "Internal error")
            return
        if errors:
            self._error(
                req_id,
                INVALID_PARAMS,
                "Invalid params: " + format_argument_errors(errors),
            )
            return
        flight = self._gate.begin(req_id)
        if flight is None:
            self._error(req_id, INVALID_REQUEST, "request_in_flight")
            return
        token = _progress_token(params)
        ctx = CallContext(
            cancel=flight.cancel,
            progress_token=token,
            protocol=self._protocol,
            progress=self._progress_sender(token) if token is not None else None,
        )
        worker = threading.Thread(
            target=self._run_call,
            args=(flight, name, arguments, ctx),
            name="local-mcp-tool",
            daemon=False,
        )
        worker.start()

    def _run_call(
        self,
        flight: _Flight,
        name: str,
        arguments: dict,
        ctx: CallContext,
    ) -> None:
        try:
            outcome = self._executor(name, arguments, ctx)
            self._result(
                flight.request_id,
                {
                    "content": [{"type": "text", "text": outcome.text}],
                    "structuredContent": outcome.structured,
                    "isError": outcome.is_error,
                },
            )
        except Exception:
            log.exception("tools/call failed")
            try:
                self._error(flight.request_id, INTERNAL_ERROR, "Internal error")
            except Exception:
                log.exception("error response failed")
        finally:
            self._gate.finish(flight)

    def _progress_sender(self, token: Any) -> Callable[..., bool]:
        """One notifications/progress line. ``total`` is omitted."""

        def emit(amount: int, message: str | None = None) -> bool:
            params: dict[str, Any] = {
                "progressToken": token,
                "progress": int(amount),
            }
            if isinstance(message, str):
                # stream_cli already flattened and clipped. A second cut hides a late marker.
                params["message"] = message
            try:
                # False: the pipe is full. Do not block the tool thread or the stdout lock.
                return try_write_message(
                    self._stdout,
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": params,
                    },
                )
            except (BrokenPipeError, OSError, FramingError):
                log.warning("progress notification failed")
                return False

        return emit

    def _result(self, req_id: Any, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": code, "message": message},
            }
        )

    def _send(self, message: dict) -> None:
        try:
            write_message(self._stdout, message)
        except (BrokenPipeError, OSError):
            log.warning("stdout write failed")


def shutdown_handler(signum: int | None = None, _frame: object | None = None) -> None:
    """SIGTERM, SIGINT, and atexit all enter here.

    Cancel the in-flight call and wait until its executor has reaped the
    child process group. A signal then exits. atexit must not raise.
    """
    server = _ACTIVE
    if server is not None:
        server.shutdown_wait()
    if signum is not None:
        raise SystemExit(128 + int(signum))


def _arm_shutdown_signals() -> list[tuple[int, Any]]:
    """Install shutdown_handler. Signals only work on the main thread."""
    if threading.current_thread() is not threading.main_thread():
        return []
    armed: list[tuple[int, Any]] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.signal(sig, shutdown_handler)
        except (ValueError, OSError):
            continue
        armed.append((sig, previous))
    return armed


def _restore_shutdown_signals(previous: list[tuple[int, Any]]) -> None:
    for sig, handler in previous:
        try:
            if signal.getsignal(sig) is shutdown_handler:
                signal.signal(sig, handler)
        except (ValueError, OSError):
            continue


def serve(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    executor: Executor | None = None,
    allow_root: Path | None = None,
) -> int:
    """Speak newline-delimited JSON-RPC until stdin hits EOF.

    ``configure_logging`` runs once here. Tools must not call it again.
    SIGTERM, SIGINT, and atexit call ``shutdown_handler``.
    ``allow_root`` is fixed for the process; tool arguments cannot widen it.
    """
    global _ACTIVE, _ALLOW_ROOT
    configure_logging()
    log.info("local mcp serve")
    server = Server(
        sys.stdin if stdin is None else stdin,
        sys.stdout if stdout is None else stdout,
        default_executor if executor is None else executor,
    )
    previous = _ACTIVE
    previous_root = _ALLOW_ROOT
    _ACTIVE = server
    _ALLOW_ROOT = _startup_allow_root(allow_root)
    previous_signals = _arm_shutdown_signals()
    atexit.register(shutdown_handler)
    try:
        return server.run()
    finally:
        atexit.unregister(shutdown_handler)
        _restore_shutdown_signals(previous_signals)
        _ACTIVE = previous
        _ALLOW_ROOT = previous_root
