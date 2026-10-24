# -*- coding: utf-8 -*-
"""Hermetic stdio MCP tests. No Docker, no network, no live GitHub."""

from __future__ import annotations

import inspect
import io
import json
import logging
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from jsonschema import Draft202012Validator

from memory.local_mcp.framing import (
    STDOUT_LOCK,
    FramingError,
    dumps_message,
    loads_message,
    read_message,
    try_write_message,
    write_frame,
    write_message,
)
from memory.local_mcp.registry import catalog_tools
from memory.local_mcp.server import (
    CallContext,
    ToolResult,
    default_executor,
    kill_process_group,
    refused_pipeline_result,
    serve,
    server_version,
    shutdown_handler,
)
from memory.local_mcp.tools.local import dispatch
from memory.local_mcp.audit import (
    local_pipeline_cancelled,
    local_pipeline_probe_failed,
    local_pipeline_run,
    local_pipeline_webhook_reject,
)
from memory.local_mcp.__main__ import main as local_mcp_main
from memory.agent_lock import agent_lock
from memory.local_mcp.config import disabled_result, load_local_pipeline
from memory.local_mcp.pipeline import _prune, _timeout_s, run_local_pipeline
from memory.local_mcp.stream import _LineQueue, stream_cli
from memory.local_mcp.probe import (
    PROBE_TIMEOUT_S,
    list_linux_processes,
    probe_act,
    probe_backend,
    probe_jenkins,
    probe_self_hosted,
)
from memory.local_mcp.redact import (
    LocalRedactFilter,
    check_environment,
    redact_text,
)
from memory.local_mcp.validate import load_schema, result_errors
from memory.dashboard.redact import RedactFilter
from memory.logutil import _CHILD_LOGGERS, configure_logging, get_logger

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO / "memory" / "local_mcp" / "schemas"

TOOL_NAMES = [
    "run_local_pipeline",
    "git_preflight",
    "validate_handoff",
    "state_snapshot",
    "state_append_delta",
    "state_metrics_log",
    "memory_info",
    "memory_query",
    "memory_snapshot",
    "memory_update",
    "knowledge_query",
    "knowledge_stats",
    "playbooks_select",
    "playbooks_list",
    "proxy_health",
    "proxy_stats",
    "integrations_status",
    "pytest_run",
    "docker_compose",
]

DESTRUCTIVE = {
    "run_local_pipeline",
    "pytest_run",
    "state_append_delta",
    "state_metrics_log",
    "memory_update",
}
OPEN_WORLD = {"run_local_pipeline", "pytest_run", "git_preflight"}

ACT_PROBE_FAILURE = {
    "ok": False,
    "skipped": False,
    "backend": "act",
    "config_backend": "act",
    "exit_code": 2,
    "duration_ms": 40,
    "timed_out": False,
    "cancelled": False,
    "truncated": False,
    "lines_dropped": 0,
    "queue_risk": False,
    "status": "refused",
    "job": {
        "workflow_file": ".github/workflows/agentix-loop.yml",
        "job_name": "harness",
    },
    "log_tail": "",
    "log_path": ".agent/local-pipeline-runs/20261011T120000Z-act-a1b2c3d4.log",
    "probe": {
        "docker": False,
        "docker_server": None,
        "act_version": "0.2.70",
        "runner_image": "catthehacker/ubuntu:act-22.04",
    },
    "warnings": [],
    "status_target": "local",
    "status_posted_by": None,
    "scm": {"kind": None, "host": "local", "ref": None, "commit": None},
    "error": {"code": "docker_unreachable", "message": "docker info exited 1"},
}

_SLEEPER_SRC = """\
import os
import subprocess
import sys
import time
from pathlib import Path

ready = Path(sys.argv[1])
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
ready.write_text("{} {}".format(os.getpid(), child.pid), encoding="utf-8")
time.sleep(120)
"""


def _result_validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema("local_pipeline_result"))


def _assert_schema_ok(payload: dict) -> None:
    errors = list(_result_validator().iter_errors(payload))
    assert errors == [], [err.message for err in errors]


def _jenkins_result(job: dict) -> dict:
    return {
        "ok": False,
        "skipped": False,
        "backend": "jenkins",
        "config_backend": "act",
        "exit_code": 2,
        "duration_ms": 1,
        "timed_out": False,
        "cancelled": False,
        "truncated": False,
        "lines_dropped": 0,
        "queue_risk": False,
        "status": "refused",
        "job": job,
        "log_tail": "",
        "log_path": ".agent/local-pipeline-runs/example-jenkins.log",
        "probe": {},
        "warnings": [],
        "status_target": "bitbucket_commit_status",
        "status_posted_by": None,
        "scm": {"kind": None, "host": "bitbucket", "ref": None, "commit": None},
        "error": {"code": "not_implemented", "message": "jenkins is not implemented"},
    }


class StdioSession:
    """In-process client. The server thread is the only stdin reader."""

    def __init__(self, executor=None, allow_root: Path | None = None) -> None:
        self.executor = executor
        self.allow_root = allow_root
        self.code: Optional[int] = None
        self.exc: Optional[BaseException] = None
        self._q: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._to_server = None
        self._from_server = None

    def __enter__(self) -> "StdioSession":
        server_read_fd, client_write_fd = os.pipe()
        client_read_fd, server_write_fd = os.pipe()
        self._to_server = os.fdopen(
            client_write_fd, "w", encoding="utf-8", buffering=1
        )
        self._from_server = os.fdopen(
            client_read_fd, "r", encoding="utf-8", buffering=1
        )
        server_in = os.fdopen(server_read_fd, "r", encoding="utf-8", buffering=1)
        server_out = os.fdopen(server_write_fd, "w", encoding="utf-8", buffering=1)

        def read_stdout() -> None:
            try:
                assert self._from_server is not None
                for line in self._from_server:
                    self._q.put(line)
            except Exception as exc:
                self._q.put(exc)
            finally:
                self._q.put(None)

        def run_server() -> None:
            try:
                self.code = serve(
                    stdin=server_in,
                    stdout=server_out,
                    executor=self.executor,
                    allow_root=self.allow_root,
                )
            except BaseException as exc:
                self.exc = exc
            finally:
                try:
                    server_out.close()
                except Exception:
                    pass
                try:
                    server_in.close()
                except Exception:
                    pass

        threading.Thread(target=read_stdout, name="local-mcp-test-out", daemon=True).start()
        self._thread = threading.Thread(
            target=run_server, name="local-mcp-test-serve", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(self, obj: dict) -> None:
        assert self._to_server is not None
        self._to_server.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self._to_server.flush()

    def send_raw(self, text: str) -> None:
        assert self._to_server is not None
        self._to_server.write(text)
        self._to_server.flush()

    def send_bytes(self, payload: bytes) -> None:
        assert self._to_server is not None
        self._to_server.flush()
        os.write(self._to_server.fileno(), payload)

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        req_id: Any = 1,
    ) -> dict:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self.send(message)
        return self.read()

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def read(self, timeout: float = 5) -> dict:
        item = self._q.get(timeout=timeout)
        if item is None:
            raise EOFError("server stdout closed")
        if isinstance(item, BaseException):
            raise item
        return json.loads(item)

    def expect_quiet(self, timeout: float = 0.2) -> None:
        try:
            extra = self._q.get(timeout=timeout)
        except queue.Empty:
            return
        raise AssertionError(f"unexpected stdout: {extra!r}")

    def close_stdin(self) -> None:
        if self._to_server is not None and not self._to_server.closed:
            self._to_server.close()

    def close(self) -> None:
        self.close_stdin()
        if self._thread is not None:
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                raise AssertionError("serve did not exit after stdin EOF")
        if self.exc is not None:
            raise self.exc


@pytest.fixture
def session():
    with StdioSession() as client:
        yield client


def _cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "memory.local_mcp", *args],
        cwd=REPO,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, "AGENTIX_LOG_LEVEL": "INFO"},
    )


def test_framing_rejects_embedded_newlines():
    encoded = dumps_message({"text": "a\nb"})
    assert "\n" not in encoded
    assert "\\n" in encoded
    with pytest.raises(FramingError, match="embedded newline"):
        loads_message('{"a": 1}\n{"b": 2}')
    with pytest.raises(FramingError, match="embedded newline"):
        loads_message('{"a": 1}\r{"b": 2}')
    import io

    buf = io.StringIO()
    with pytest.raises(FramingError, match="embedded newline"):
        write_frame(buf, '{"a": 1}\n{"b": 2}')
    assert buf.getvalue() == ""
    write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {}})
    assert buf.getvalue().count("\n") == 1


def test_progress_write_does_not_block_on_a_full_pipe():
    import fcntl

    read_fd, write_fd = os.pipe()
    try:
        try:
            fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
        except OSError:
            pass
        fp = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        raise
    frame = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progressToken": "t", "progress": 1, "message": "hello"},
    }
    try:
        started = time.monotonic()
        wrote = 0
        while time.monotonic() - started < 2:
            if not try_write_message(fp, frame):
                break
            wrote += 1
        else:
            raise AssertionError("progress write never refused a full pipe")
        assert wrote > 0
        assert time.monotonic() - started < 1.5
        os.read(read_fd, 65536)
        write_message(fp, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        assert try_write_message(fp, frame) is True
        raw = os.read(read_fd, 65536)
        lines = [line for line in raw.decode("utf-8").splitlines() if line]
        bodies = [json.loads(line) for line in lines]
        assert any("result" in body for body in bodies)
        assert any(body.get("method") == "notifications/progress" for body in bodies)
    finally:
        fp.close()
        os.close(read_fd)


def test_stdout_lock_serializes_writes():
    import io

    buf = io.StringIO()
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with STDOUT_LOCK:
            started.set()
            assert release.wait(timeout=2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(timeout=2)
    done = threading.Event()

    def writer() -> None:
        write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {}})
        done.set()

    worker = threading.Thread(target=writer)
    worker.start()
    time.sleep(0.05)
    assert not done.is_set()
    release.set()
    assert done.wait(timeout=2)
    thread.join(timeout=2)
    worker.join(timeout=2)
    assert buf.getvalue().count("\n") == 1


def test_server_version_prefers_distribution_then_file():
    import importlib.metadata

    file_version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    try:
        dist = importlib.metadata.version("agentix")
    except importlib.metadata.PackageNotFoundError:
        dist = None
    got = server_version()
    if dist is None:
        assert got == file_version
    else:
        assert got == dist
    assert got


@pytest.mark.parametrize(
    ("offered", "chosen"),
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2025-03-26", "2025-11-25"),
        ("1999-01-01", "2025-11-25"),
    ],
)
def test_negotiate_protocol(session: StdioSession, offered: str, chosen: str):
    resp = session.request(
        "initialize",
        {
            "protocolVersion": offered,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    result = resp["result"]
    assert result["protocolVersion"] == chosen
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "agentix-local"
    assert result["serverInfo"]["version"] == server_version()
    assert "nextCursor" not in result


def test_batch_is_single_invalid_request(session: StdioSession):
    session.send_raw(
        '[{"jsonrpc":"2.0","id":1,"method":"ping"},'
        '{"jsonrpc":"2.0","id":2,"method":"ping"}]\n'
    )
    msg = session.read()
    assert isinstance(msg, dict)
    assert msg["id"] is None
    assert msg["error"] == {"code": -32600, "message": "Invalid Request"}
    assert "result" not in msg
    session.expect_quiet()
    pong = session.request("ping", req_id=3)
    assert pong == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_parse_error_then_ping(session: StdioSession):
    session.send_raw("{not-json}\n")
    msg = session.read()
    assert msg["error"]["code"] == -32700
    pong = session.request("ping", req_id="abc")
    assert pong["id"] == "abc"
    assert pong["result"] == {}


def test_read_message_bad_utf8_does_not_wedge_the_next_line():
    import io

    raw = b"\xff\n" + b'{"jsonrpc":"2.0","id":1.0,"method":"ping"}\n'
    wrapper = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    with pytest.raises(FramingError, match="invalid utf-8"):
        read_message(wrapper)
    assert read_message(wrapper) == {"jsonrpc": "2.0", "id": 1.0, "method": "ping"}
    text = io.StringIO('{"jsonrpc":"2.0","method":"ping"}\n')
    assert read_message(text)["method"] == "ping"


def test_select_oserror_is_not_eof(monkeypatch):
    """WinError 10038 from select() is not stdin EOF. A ping still gets a pong."""
    import memory.local_mcp.framing as framing

    def fail_select(*_args, **_kwargs):
        raise OSError(
            10038, "An operation was attempted on something that is not a socket"
        )

    monkeypatch.setattr(framing.select, "select", fail_select)
    with StdioSession() as client:
        pong = client.request("ping", req_id=1)
        assert pong == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_direct_read_eagain_and_eintr_are_not_eof(monkeypatch):
    """Would-block and EINTR on the direct-read path are not stdin EOF."""
    import memory.local_mcp.framing as framing

    read_fd, write_fd = os.pipe()
    fp = os.fdopen(read_fd, "r", encoding="utf-8")
    os.write(write_fd, b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    real_read = os.read
    seen = {"n": 0}

    def fail_select(*_args, **_kwargs):
        raise OSError(
            10038, "An operation was attempted on something that is not a socket"
        )

    def flaky(fd, size):
        if fd == read_fd:
            seen["n"] += 1
            if seen["n"] == 1:
                raise BlockingIOError(11, "Resource temporarily unavailable")
            if seen["n"] == 2:
                raise InterruptedError(4, "Interrupted system call")
        return real_read(fd, size)

    monkeypatch.setattr(framing.select, "select", fail_select)
    monkeypatch.setattr(framing.os, "read", flaky)
    try:
        assert read_message(fp) == {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
        }
        os.close(write_fd)
        write_fd = -1
        with pytest.raises(EOFError):
            read_message(fp)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        fp.close()
    assert seen["n"] >= 3


def test_direct_read_oserror_is_not_eof(monkeypatch):
    """A non-retryable read error is not reported as stdin EOF."""
    import memory.local_mcp.framing as framing

    read_fd, write_fd = os.pipe()
    fp = os.fdopen(read_fd, "r", encoding="utf-8")
    os.close(write_fd)

    def fail_select(*_args, **_kwargs):
        raise OSError(
            10038, "An operation was attempted on something that is not a socket"
        )

    def boom(_fd, _size):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(framing.select, "select", fail_select)
    monkeypatch.setattr(framing.os, "read", boom)
    try:
        with pytest.raises(OSError, match="Input/output error"):
            read_message(fp)
    finally:
        fp.close()


def test_non_utf8_is_parse_error_and_session_continues(session: StdioSession):
    session.send_bytes(b"\xff\n")
    msg = session.read()
    assert msg["id"] is None
    assert msg["error"] == {"code": -32700, "message": "Parse error"}
    pong = session.request("ping", req_id=3)
    assert pong == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_only_a_missing_id_is_a_notification(session: StdioSession):
    session.send({"jsonrpc": "2.0", "method": "ping"})
    session.expect_quiet()
    session.send({"jsonrpc": "2.0", "id": None, "method": "ping"})
    null_id = session.read()
    assert null_id["id"] is None
    assert null_id["result"] == {}
    assert "error" not in null_id
    session.send({"jsonrpc": "2.0", "id": 1.0, "method": "ping"})
    one = session.read()
    assert type(one["id"]) is float
    assert one["id"] == 1.0
    assert one["result"] == {}
    session.send({"jsonrpc": "2.0", "id": -2.0, "method": "tools/list", "params": {}})
    listed = session.read()
    assert type(listed["id"]) is float
    assert listed["id"] == -2.0
    assert listed["result"]["tools"]
    session.send({"jsonrpc": "2.0", "id": 1.0, "method": "no/such"})
    missing = session.read()
    assert type(missing["id"]) is float
    assert missing["id"] == 1.0
    assert missing["error"]["code"] == -32601
    for bad in (True, False, 1.5):
        session.send({"jsonrpc": "2.0", "id": bad, "method": "ping"})
        rejected = session.read()
        assert rejected["id"] is None
        assert rejected["error"] == {"code": -32600, "message": "Invalid Request"}
    pong = session.request("ping", req_id=4)
    assert pong["id"] == 4


def test_unknown_method(session: StdioSession):
    resp = session.request("no/such", req_id=4)
    assert resp["error"]["code"] == -32601
    assert resp["id"] == 4


def test_initialized_notification_has_no_response(session: StdioSession):
    session.notify("notifications/initialized")
    pong = session.request("ping", req_id=5)
    assert pong["id"] == 5


def test_tools_list_matches_catalog_and_cursor(session: StdioSession):
    first = session.request("tools/list", {})["result"]
    assert "nextCursor" not in first
    assert [tool["name"] for tool in first["tools"]] == TOOL_NAMES
    assert first["tools"] == catalog_tools()
    for tool in first["tools"]:
        assert tool["inputSchema"]["additionalProperties"] is False
        ann = tool["annotations"]
        destructive = tool["name"] in DESTRUCTIVE
        assert ann["destructiveHint"] is destructive
        assert ann["readOnlyHint"] is not destructive
        assert ann["idempotentHint"] is not destructive
        assert ann["openWorldHint"] is (tool["name"] in OPEN_WORLD)
    empty = session.request("tools/list", {"cursor": "more"})["result"]
    assert empty == {"tools": []}
    assert "nextCursor" not in empty


def test_list_tools_stdout_is_one_object():
    proc = _cli("list-tools")
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    text = proc.stdout
    assert text.endswith("\n")
    assert "\n" not in text[:-1]
    payload = json.loads(text)
    assert list(payload) == ["tools"]
    assert payload["tools"] == catalog_tools()
    with StdioSession() as session:
        page = session.request("tools/list")["result"]
    assert page["tools"] == payload["tools"]


def _assert_run_json(stdout: str) -> dict:
    assert stdout.endswith("\n")
    assert "\n" not in stdout[:-1]
    payload = json.loads(stdout)
    assert result_errors(payload) == []
    return payload


def test_later_pr_subcommands_exit_2():
    proc = _cli("run", "--backend", "act")
    assert proc.returncode == 2
    assert "later PR" not in proc.stderr
    assert "later PR" not in proc.stdout
    payload = _assert_run_json(proc.stdout)
    assert payload["exit_code"] == 2
    assert payload["skipped"] is True
    assert payload["backend"] == "act"
    assert payload["error"]["code"] == "local_pipeline_disabled"
    assert payload["job"]["job_name"] == "harness"
    relay = _cli("jenkins-relay")
    assert relay.returncode == 2
    assert relay.stdout == ""
    assert "later PR" not in relay.stderr
    assert relay.stderr == "local_pipeline_disabled\n"
    proc = _cli("probe")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "probe is a later PR" not in proc.stderr


def _forbid_spawn(monkeypatch) -> None:
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)


def test_run_cli_help_lists_flags():
    proc = _cli("run", "--help")
    assert proc.returncode == 0
    assert "Later PR" not in proc.stdout
    assert "--runner-label" in proc.stdout
    assert "--jenkinsfile" in proc.stdout
    assert "--workflow-file" in proc.stdout


def test_run_cli_inserts_config_backend_and_labels(tmp_path: Path, monkeypatch, capsys):
    _forbid_spawn(monkeypatch)
    monkeypatch.setenv("AGENTIX_PROJECT_ROOT", str(tmp_path))
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "jenkins"}},
    )
    code = local_mcp_main(
        ["run", "--workdir", str(tmp_path), "--job-path", "folder/name"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "backend_field_mismatch" not in captured.err
    payload = _assert_run_json(captured.out)
    assert payload["backend"] == "jenkins"
    assert payload["config_backend"] == "jenkins"
    assert payload["error"]["code"] == "local_pipeline_disabled"
    assert payload["job"] == {"job_path": "folder/name"}

    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "self_hosted"}},
    )
    code = local_mcp_main(
        [
            "run",
            "--workdir",
            str(tmp_path),
            "--runner-label",
            "linux",
            "--runner-label",
            "x64",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    payload = _assert_run_json(captured.out)
    assert payload["backend"] == "self_hosted"
    assert payload["job"] == {"runner_labels": ["linux", "x64"]}


def test_run_cli_unknown_backend_defaults_act(tmp_path: Path, monkeypatch, capsys):
    _forbid_spawn(monkeypatch)
    monkeypatch.setenv("AGENTIX_PROJECT_ROOT", str(tmp_path))
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "nope"}},
    )
    code = local_mcp_main(
        [
            "run",
            "--workdir",
            str(tmp_path),
            "--workflow-file",
            "w.yml",
            "--job-name",
            "job",
            "--timeout-s",
            "3",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    payload = _assert_run_json(captured.out)
    assert payload["backend"] == "act"
    assert payload["config_backend"] == "act"
    assert payload["job"] == {"workflow_file": "w.yml", "job_name": "job"}
    assert payload["error"]["code"] == "local_pipeline_disabled"


def test_run_cli_rejects_empty_cross_and_unknown(tmp_path: Path, monkeypatch, capsys):
    _forbid_spawn(monkeypatch)
    monkeypatch.setenv("AGENTIX_PROJECT_ROOT", str(tmp_path))
    _write_project_config(tmp_path, {"local_pipeline": {"enabled": False, "backend": "act"}})
    cases = (
        ["run", "--backend", "act", "--job-name", ""],
        ["run", "--backend", "act", "--runner-label", "linux"],
        ["run", "--backend", "jenkins", "--workdir", str(tmp_path)],
        [
            "run",
            "--backend",
            "jenkins",
            "--workdir",
            str(tmp_path),
            "--job-path",
            "a",
            "--jenkinsfile",
            "J",
        ],
        ["run", "--backend", "self_hosted", "--workdir", str(tmp_path)],
        ["run", "--backend", "act", "--timeout-s", "0"],
        ["run", "--backend", "act", "--no-such"],
    )
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "act"}},
    )
    for argv in cases:
        code = local_mcp_main(argv)
        captured = capsys.readouterr()
        assert code == 2, argv
        assert captured.out == "", argv
        assert "backend_field_mismatch" not in captured.err, argv
        assert "later PR" not in captured.err, argv
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "self_hosted"}},
    )
    code = local_mcp_main(["run", "--workdir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid arguments\n"


def test_cli_unknown_flags_do_not_start(tmp_path: Path, monkeypatch, capsys):
    """Unknown flags are an error. They must not start serve, probe, or the relay."""

    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("started")

    monkeypatch.setattr("memory.local_mcp.server.serve", boom)
    monkeypatch.setattr("memory.local_mcp.hmac_relay.main", boom)
    monkeypatch.setattr("memory.local_mcp.probe.probe_backend", boom)
    monkeypatch.setattr("memory.local_mcp.pipeline.run_local_pipeline", boom)
    cases = (
        ["serve", "--workdir", str(tmp_path)],
        ["list-tools", "--json"],
        ["probe", "--backend", "act", "--typo"],
        ["jenkins-relay", "--bind", "0.0.0.0"],
        ["run", "--backend", "act", "--no-such"],
    )
    for argv in cases:
        code = local_mcp_main(argv)
        captured = capsys.readouterr()
        assert code == 2, argv
        assert captured.out == "", argv
        assert "unrecognized arguments" in captured.err, argv
        assert "later PR" not in captured.err, argv


def test_run_cli_does_not_read_backend_outside_allow_root(
    tmp_path: Path, monkeypatch, capsys
):
    _forbid_spawn(monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    _write_project_config(
        outside,
        {"local_pipeline": {"enabled": False, "backend": "jenkins"}},
    )
    monkeypatch.setenv("AGENTIX_PROJECT_ROOT", str(root))
    code = local_mcp_main(["run", "--workdir", str(outside)])
    captured = capsys.readouterr()
    assert code == 2
    payload = _assert_run_json(captured.out)
    assert payload["backend"] == "act"
    assert payload["exit_code"] == 2


def test_serve_cli_stdout_is_only_jsonrpc():
    proc = _cli(
        "serve",
        stdin=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        + "\n",
    )
    assert proc.returncode == 0, proc.stderr
    assert "local mcp serve" in proc.stderr
    assert "local mcp serve" not in proc.stdout
    assert proc.stdout.count("\n") == 1
    msg = json.loads(proc.stdout)
    assert msg["result"]["protocolVersion"] == "2025-11-25"
    assert msg["result"]["serverInfo"]["name"] == "agentix-local"
    assert msg["result"]["capabilities"]["tools"]["listChanged"] is False


def test_configure_logging_once_and_handler_is_stderr(monkeypatch):
    import memory.local_mcp.server as server_mod

    root = logging.getLogger("memory")
    saved = list(root.handlers)
    root.handlers.clear()
    calls = {"n": 0}
    real = server_mod.configure_logging

    def wrapped() -> None:
        calls["n"] += 1
        real()

    monkeypatch.setattr(server_mod, "configure_logging", wrapped)
    seen = {"during_tool": None}

    def executor(name, arguments, ctx):
        del name, arguments, ctx
        seen["during_tool"] = calls["n"]
        return ToolResult(
            text="memory_info is not implemented",
            structured={
                "ok": False,
                "error": {
                    "code": "not_implemented",
                    "message": "memory_info is not implemented",
                },
            },
            is_error=True,
        )

    try:
        with StdioSession(executor) as session:
            resp = session.request(
                "tools/call",
                {"name": "memory_info", "arguments": {}},
            )
        assert calls["n"] == 1
        assert seen["during_tool"] == 1
        assert resp["result"]["isError"] is True
        handlers = logging.getLogger("memory").handlers
        assert handlers
        for handler in handlers:
            assert type(handler) is logging.StreamHandler
            assert not isinstance(handler, logging.FileHandler)
            assert handler.stream is sys.stderr
            assert handler.stream is not sys.stdout
    finally:
        root.handlers[:] = saved


def test_extra_property_is_invalid_params_before_executor():
    called: list[str] = []

    def executor(name, arguments, ctx):
        del arguments, ctx
        called.append(name)
        raise AssertionError("executor ran")

    with StdioSession(executor) as session:
        resp = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {
                    "backend": "act",
                    "working_directory": str(Path.cwd()),
                    "nope": True,
                },
            },
        )
        assert called == []
        assert resp["error"]["code"] == -32602
        other = session.request(
            "tools/call",
            {"name": "memory_info", "arguments": {"extra": 1}},
            req_id=2,
        )
        assert other["error"]["code"] == -32602
        assert called == []
        token = session.request(
            "tools/call",
            {
                "name": "memory_info",
                "arguments": {"progressToken": "tok"},
            },
            req_id=3,
        )
        assert token["error"]["code"] == -32602


@pytest.mark.parametrize(
    "arguments",
    [
        {"backend": "act", "working_directory": ""},
        {"backend": "github", "working_directory": str(Path.cwd())},
        {"working_directory": str(Path.cwd())},
        {"backend": "act", "working_directory": str(Path.cwd()), "job_path": "a"},
        {"backend": "act", "working_directory": str(Path.cwd()), "runner_labels": ["linux"]},
        {"backend": "act", "working_directory": str(Path.cwd()), "jenkinsfile_path": "J"},
        {"backend": "self_hosted", "working_directory": str(Path.cwd())},
        {
            "backend": "self_hosted",
            "working_directory": str(Path.cwd()),
            "runner_labels": ["linux"],
            "job_name": "harness",
        },
        {"backend": "jenkins", "working_directory": str(Path.cwd())},
        {
            "backend": "jenkins",
            "working_directory": str(Path.cwd()),
            "job_path": "a",
            "jenkinsfile_path": "J",
        },
        {"backend": "jenkins", "working_directory": str(Path.cwd()), "workflow_file": "w.yml"},
        {"backend": "act", "working_directory": str(Path.cwd()), "timeout_s": 0},
        {"backend": "act", "working_directory": str(Path.cwd()), "job_name": ""},
    ],
)
def test_pipeline_schema_violations_are_32602(session: StdioSession, arguments: dict):
    resp = session.request(
        "tools/call",
        {"name": "run_local_pipeline", "arguments": arguments},
    )
    assert resp["error"]["code"] == -32602
    assert "result" not in resp


def test_other_tool_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    record = tmp_path / "docker-argv"
    env_record = tmp_path / "docker-env"
    token = "ghp_FAKELOCALPIPELINETOKEN123456"
    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(record))}\n"
        f"env > {shlex.quote(str(env_record))}\n"
        "exit 0\n",
    )
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    )
    monkeypatch.setenv("JENKINS_API_TOKEN", token)
    monkeypatch.setenv("HOME", "/tmp/operator-home")
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/rootless.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "rootless")
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp/docker-config")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/tmp/docker-certs")
    with StdioSession(allow_root=root) as session:
        _assert_other_tool_bounds(session, record, env_record, token)


def _assert_other_tool_bounds(
    session: StdioSession, record: Path, env_record: Path, token: str
) -> None:
    ok = session.request(
        "tools/call",
        {
            "name": "state_append_delta",
            "arguments": {"text": "note", "role": "Coder"},
        },
    )
    assert ok["result"]["isError"] is False
    body = ok["result"]["structuredContent"]
    assert body["ok"] is True
    assert body["delta"]["role"] == "Coder"
    assert body["delta"]["text"] == "note"
    assert "\n" not in ok["result"]["content"][0]["text"]
    bad_role = session.request(
        "tools/call",
        {
            "name": "state_append_delta",
            "arguments": {"text": "note", "role": "coder"},
        },
        req_id=2,
    )
    assert bad_role["error"]["code"] == -32602
    long_text = session.request(
        "tools/call",
        {
            "name": "state_append_delta",
            "arguments": {"text": "x" * 501, "role": "Tester"},
        },
        req_id=3,
    )
    assert long_text["error"]["code"] == -32602
    window = session.request(
        "tools/call",
        {"name": "state_snapshot", "arguments": {"window": 21}},
        req_id=4,
    )
    assert window["error"]["code"] == -32602
    update = session.request(
        "tools/call",
        {
            "name": "memory_update",
            "arguments": {
                "category": "Testing",
                "description": "edge",
                "distillation": "nope",
            },
        },
        req_id=5,
    )
    assert update["error"]["code"] == -32602
    query = session.request(
        "tools/call",
        {"name": "memory_query", "arguments": {"top": 0, "category": "c" * 121}},
        req_id=6,
    )
    assert query["error"]["code"] == -32602
    scopes = session.request(
        "tools/call",
        {
            "name": "playbooks_select",
            "arguments": {"query": "git", "scopes": ["workflow:full-cycle"]},
        },
        req_id=7,
    )
    assert scopes["error"]["code"] == -32602
    compose = session.request(
        "tools/call",
        {"name": "docker_compose", "arguments": {"action": "up"}},
        req_id=8,
    )
    assert compose["error"]["code"] == -32602
    tail = session.request(
        "tools/call",
        {"name": "docker_compose", "arguments": {"action": "ps", "tail": 10}},
        req_id=9,
    )
    assert tail["error"]["code"] == -32602
    assert not record.exists()
    logs = session.request(
        "tools/call",
        {"name": "docker_compose", "arguments": {"action": "logs", "tail": 10}},
        req_id=10,
    )
    assert logs["result"]["isError"] is False
    assert logs["result"]["structuredContent"]["exit_code"] == 0
    assert record.read_text(encoding="utf-8").splitlines()[:4] == [
        "compose",
        "logs",
        "--tail",
        "10",
    ]
    child_env = env_record.read_text(encoding="utf-8")
    assert token not in child_env
    assert token not in record.read_text(encoding="utf-8")
    lines = child_env.splitlines()
    assert not any(line.startswith("JENKINS_API_TOKEN=") for line in lines)
    assert not any(line.startswith("HOME=") for line in lines)
    for key, value in (
        ("DOCKER_HOST", "unix:///tmp/rootless.sock"),
        ("DOCKER_CONTEXT", "rootless"),
        ("DOCKER_CONFIG", "/tmp/docker-config"),
        ("DOCKER_CERT_PATH", "/tmp/docker-certs"),
    ):
        assert f"{key}={value}" in child_env.splitlines()
    handoff = session.request(
        "tools/call",
        {
            "name": "validate_handoff",
            "arguments": {"path": "a.json", "handoff": {}},
        },
        req_id=11,
    )
    assert handoff["error"]["code"] == -32602
    knowledge = session.request(
        "tools/call",
        {"name": "knowledge_query", "arguments": {"db": "/tmp/x.sqlite"}},
        req_id=12,
    )
    assert knowledge["error"]["code"] == -32602


def test_local_mcp_doc_does_not_refuse_enabled_backends():
    import memory.local_mcp as local_mcp

    text = local_mcp.__doc__ or ""
    assert "stay refused" not in text
    assert "one local job" not in text


def test_progress_token_is_only_meta():
    seen: dict[str, Any] = {}

    def executor(name, arguments, ctx: CallContext):
        seen["name"] = name
        seen["arguments"] = dict(arguments)
        seen["token"] = ctx.progress_token
        if name == "memory_info":
            return ToolResult(
                text="memory_info ok",
                structured={"ok": True, "error": None},
                is_error=False,
            )
        return default_executor(name, arguments, ctx)

    with StdioSession(executor) as session:
        resp = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {"backend": "act", "working_directory": str(Path.cwd())},
                "_meta": {"progressToken": "tok-1"},
                "progressToken": "ignore-me",
            },
        )
        assert seen["token"] == "tok-1"
        assert "progressToken" not in seen["arguments"]
        assert resp["result"]["isError"] is True
        assert resp["result"]["structuredContent"]["error"]["code"] == "local_pipeline_disabled"
        session.expect_quiet()
        resp = session.request(
            "tools/call",
            {
                "name": "memory_info",
                "arguments": {},
                "progressToken": "not-meta",
            },
            req_id=2,
        )
        assert seen["token"] is None
        assert resp["result"]["isError"] is False
        assert resp["result"]["content"][0]["text"] == "memory_info ok"


@pytest.mark.parametrize(
    ("arguments", "job", "status_target", "host", "text", "error_code", "skipped"),
    [
        (
            {"backend": "act", "working_directory": str(Path.cwd())},
            {
                "workflow_file": ".github/workflows/agentix-loop.yml",
                "job_name": "harness",
            },
            "local",
            "local",
            "act harness exit=2 duration_ms=0",
            "local_pipeline_disabled",
            True,
        ),
        (
            {
                "backend": "act",
                "working_directory": str(Path.cwd()),
                "workflow_file": "wf.yml",
                "job_name": "unit",
            },
            {"workflow_file": "wf.yml", "job_name": "unit"},
            "local",
            "local",
            "act unit exit=2 duration_ms=0",
            "local_pipeline_disabled",
            True,
        ),
        (
            {
                "backend": "self_hosted",
                "working_directory": str(Path.cwd()),
                "runner_labels": ["linux", "self-hosted"],
            },
            {"runner_labels": ["linux", "self-hosted"]},
            "github_actions_ui",
            "github",
            "self_hosted exit=2 duration_ms=0",
            "local_pipeline_disabled",
            True,
        ),
        (
            {
                "backend": "jenkins",
                "working_directory": str(Path.cwd()),
                "job_path": "team/job",
            },
            {"job_path": "team/job"},
            "bitbucket_commit_status",
            "bitbucket",
            "jenkins exit=2 duration_ms=0",
            "local_pipeline_disabled",
            True,
        ),
        (
            {
                "backend": "jenkins",
                "working_directory": str(Path.cwd()),
                "jenkinsfile_path": "Jenkinsfile",
            },
            {"jenkinsfile_path": "Jenkinsfile"},
            "bitbucket_commit_status",
            "bitbucket",
            "jenkins exit=2 duration_ms=0",
            "local_pipeline_disabled",
            True,
        ),
    ],
)
def test_run_local_pipeline_not_implemented_validates(
    session: StdioSession,
    arguments: dict,
    job: dict,
    status_target: str,
    host: str,
    text: str,
    error_code: str,
    skipped: bool,
):
    resp = session.request(
        "tools/call",
        {"name": "run_local_pipeline", "arguments": arguments},
    )
    assert "error" not in resp
    result = resp["result"]
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": text}]
    body = result["structuredContent"]
    assert result_errors(body) == []
    _assert_schema_ok(body)
    assert body["ok"] is False
    assert body["skipped"] is skipped
    assert body["cancelled"] is False
    assert body["timed_out"] is False
    assert body["truncated"] is False
    assert body["lines_dropped"] == 0
    assert body["queue_risk"] is False
    assert body["exit_code"] == 2
    assert body["status"] == "refused"
    assert body["duration_ms"] >= 0
    assert body["backend"] == arguments["backend"]
    assert body["config_backend"] == "act"
    assert body["job"] == job
    assert body["status_target"] == status_target
    assert body["status_posted_by"] is None
    assert body["warnings"] == []
    assert body["log_tail"] == ""
    assert body["probe"] == {}
    assert body["log_path"].startswith(".agent/local-pipeline-runs/")
    assert not body["log_path"].startswith("/")
    assert body["scm"] == {"kind": None, "host": host, "ref": None, "commit": None}
    assert body["error"]["code"] == error_code


def test_refusal_does_not_spawn(monkeypatch, session: StdioSession):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    resp = session.request(
        "tools/call",
        {
            "name": "run_local_pipeline",
            "arguments": {"backend": "act", "working_directory": str(Path.cwd())},
        },
    )
    assert resp["result"]["structuredContent"]["error"]["code"] == "local_pipeline_disabled"
    assert resp["result"]["structuredContent"]["skipped"] is True
    direct = refused_pipeline_result(
        {"backend": "act", "working_directory": str(Path.cwd()), "job_name": "harness"}
    )
    assert direct["job"]["job_name"] == "harness"
    assert result_errors(direct) == []


def _schema_has_keyword(node: object, keyword: str) -> bool:
    if isinstance(node, dict):
        if keyword in node:
            return True
        return any(_schema_has_keyword(value, keyword) for value in node.values())
    if isinstance(node, list):
        return any(_schema_has_keyword(value, keyword) for value in node)
    return False


def test_result_schema_act_example_and_jenkins_job():
    text = (SCHEMA_DIR / "local_pipeline_result.schema.json").read_text(encoding="utf-8")
    schema = json.loads(text)
    assert schema["additionalProperties"] is False
    assert not _schema_has_keyword(schema, "unevaluatedProperties")
    assert schema["properties"]["job"]["type"] == "object"
    _assert_schema_ok(ACT_PROBE_FAILURE)
    _assert_schema_ok(_jenkins_result({"job_path": "team/job"}))
    _assert_schema_ok(_jenkins_result({"jenkinsfile_path": "Jenkinsfile"}))
    both = _jenkins_result({"job_path": "team/job", "jenkinsfile_path": "Jenkinsfile"})
    neither = _jenkins_result({})
    assert list(_result_validator().iter_errors(both))
    assert list(_result_validator().iter_errors(neither))


def test_internal_error_releases_the_slot():
    def executor(name, arguments, ctx):
        del name, arguments, ctx
        raise RuntimeError("boom")

    with StdioSession(executor) as session:
        resp = session.request(
            "tools/call",
            {"name": "memory_info", "arguments": {}},
        )
        assert resp["error"]["code"] == -32603
        assert resp["error"]["message"] == "Internal error"
        deadline = time.monotonic() + 1
        released = None
        req_id = 2
        while time.monotonic() < deadline:
            released = session.request("tools/list", {}, req_id=req_id)
            req_id += 1
            if "result" in released:
                break
            assert released["error"]["message"] == "request_in_flight"
        assert released is not None and "result" in released
        assert released["result"]["tools"]


def _sleeper_executor(tmp_path: Path, spawned: threading.Event, holder: dict):
    script = tmp_path / "sleeper.py"
    script.write_text(_SLEEPER_SRC, encoding="utf-8")
    ready = tmp_path / "ready"

    def executor(name, arguments, ctx: CallContext):
        del name, arguments
        proc = subprocess.Popen(
            [sys.executable, str(script), str(ready)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        holder["proc"] = proc
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if ready.is_file() and ready.read_text(encoding="utf-8").strip():
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"sleeper exited early: {proc.returncode}")
                time.sleep(0.02)
            else:
                raise RuntimeError("sleeper did not become ready")
            holder["pgid"] = os.getpgid(proc.pid)
            holder["ready"] = ready.read_text(encoding="utf-8").strip()
            spawned.set()
            ctx.cancel.wait()
        finally:
            kill_process_group(proc)
        return ToolResult(
            text="cancelled",
            structured={"ok": False, "cancelled": True, "exit_code": 130},
            is_error=True,
        )

    return executor


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} still alive")


def _assert_group_dead(pgid: int) -> None:
    deadline = time.monotonic() + 2
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"pgid {pgid} still alive")
        time.sleep(0.05)


def _start_sleeper_call(session: StdioSession, spawned: threading.Event, holder: dict):
    session.send(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "run_local_pipeline",
                "arguments": {"backend": "act", "working_directory": str(Path.cwd())},
            },
        }
    )
    assert spawned.wait(timeout=5)
    proc = holder["proc"]
    assert proc.poll() is None
    pgid = holder["pgid"]
    assert pgid == proc.pid
    assert pgid != os.getpgrp()
    return proc, pgid


def test_signal_and_atexit_call_the_shutdown_handler(monkeypatch):
    import io
    import signal

    import memory.local_mcp.server as server_mod

    registered: list = []
    hooked: list = []

    def capture_register(func, *args, **kwargs):
        del args, kwargs
        registered.append(func)

    def capture_signal(sig, handler):
        hooked.append((sig, handler))
        return signal.SIG_DFL

    monkeypatch.setattr(server_mod.atexit, "register", capture_register)
    monkeypatch.setattr(server_mod.atexit, "unregister", lambda *args, **kwargs: None)
    monkeypatch.setattr(server_mod.signal, "signal", capture_signal)
    assert serve(stdin=io.StringIO(""), stdout=io.StringIO()) == 0
    assert registered == [shutdown_handler]
    assert hooked == [
        (signal.SIGTERM, shutdown_handler),
        (signal.SIGINT, shutdown_handler),
    ]
    assert shutdown_handler() is None
    with pytest.raises(SystemExit) as term:
        shutdown_handler(signal.SIGTERM, None)
    assert term.value.code == 128 + signal.SIGTERM
    with pytest.raises(SystemExit) as interrupt:
        shutdown_handler(signal.SIGINT, None)
    assert interrupt.value.code == 128 + signal.SIGINT


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_cancel_kills_sleeper_group(tmp_path: Path):
    spawned = threading.Event()
    holder: dict = {}
    try:
        with StdioSession(_sleeper_executor(tmp_path, spawned, holder)) as session:
            proc, pgid = _start_sleeper_call(session, spawned, holder)
            parent_pid, child_pid = (int(part) for part in holder["ready"].split())
            session.notify(
                "notifications/cancelled",
                {"requestId": 999, "reason": "wrong id"},
            )
            pong = session.request("ping", req_id=8)
            assert pong["id"] == 8
            assert pong["result"] == {}
            assert proc.poll() is None
            session.notify("notifications/cancelled", {"requestId": 7})
            resp = session.read(timeout=5)
            assert resp["id"] == 7
            assert resp["result"]["isError"] is True
            body = resp["result"]["structuredContent"]
            assert body["exit_code"] == 130
            assert body["cancelled"] is True
            assert body["ok"] is False
            _assert_group_dead(pgid)
            _assert_dead(parent_pid)
            _assert_dead(child_pid)
    finally:
        proc = holder.get("proc")
        if proc is not None and proc.poll() is None:
            kill_process_group(proc)


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_sigterm_handler_kills_sleeper_group(tmp_path: Path):
    import signal

    spawned = threading.Event()
    holder: dict = {}
    try:
        with StdioSession(_sleeper_executor(tmp_path, spawned, holder)) as session:
            proc, pgid = _start_sleeper_call(session, spawned, holder)
            parent_pid, child_pid = (int(part) for part in holder["ready"].split())
            assert proc.poll() is None
            with pytest.raises(SystemExit) as caught:
                shutdown_handler(signal.SIGTERM, None)
            assert caught.value.code == 128 + signal.SIGTERM
            _assert_group_dead(pgid)
            _assert_dead(parent_pid)
            _assert_dead(child_pid)
            os.kill(os.getpid(), 0)
            assert pgid != os.getpgrp()
            resp = session.read(timeout=5)
            assert resp["id"] == 7
            assert resp["result"]["isError"] is True
            body = resp["result"]["structuredContent"]
            assert body["exit_code"] == 130
            assert body["cancelled"] is True
            assert body["ok"] is False
    finally:
        proc = holder.get("proc")
        if proc is not None and proc.poll() is None:
            kill_process_group(proc)


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_stdin_eof_kills_sleeper_group(tmp_path: Path):
    spawned = threading.Event()
    holder: dict = {}
    try:
        with StdioSession(_sleeper_executor(tmp_path, spawned, holder)) as session:
            proc, pgid = _start_sleeper_call(session, spawned, holder)
            parent_pid, child_pid = (int(part) for part in holder["ready"].split())
            assert proc.poll() is None
            session.close_stdin()
            resp = session.read(timeout=5)
            assert resp["id"] == 7
            body = resp["result"]["structuredContent"]
            assert resp["result"]["isError"] is True
            assert body["exit_code"] == 130
            assert body["cancelled"] is True
            assert body["ok"] is False
            _assert_group_dead(pgid)
            _assert_dead(parent_pid)
            _assert_dead(child_pid)
    finally:
        proc = holder.get("proc")
        if proc is not None and proc.poll() is None:
            kill_process_group(proc)


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_ping_during_call_does_not_wait_for_sleeper(tmp_path: Path):
    spawned = threading.Event()
    holder: dict = {}
    try:
        with StdioSession(_sleeper_executor(tmp_path, spawned, holder)) as session:
            proc, _pgid = _start_sleeper_call(session, spawned, holder)
            started = time.monotonic()
            session.send({"jsonrpc": "2.0", "id": 8, "method": "ping"})
            pong = session.read(timeout=2)
            elapsed = time.monotonic() - started
            assert pong["id"] == 8
            assert pong["result"] == {}
            assert elapsed < 1.5
            assert proc.poll() is None
            busy = session.request(
                "tools/list",
                {},
                req_id=9,
            )
            assert busy["error"] == {
                "code": -32600,
                "message": "request_in_flight",
            }
            assert proc.poll() is None
            again = session.request(
                "tools/call",
                {"name": "memory_info", "arguments": {}},
                req_id=10,
            )
            assert again["error"]["message"] == "request_in_flight"
            assert proc.poll() is None
            session.notify("notifications/cancelled", {"requestId": 7})
            done = session.read(timeout=5)
            assert done["result"]["structuredContent"]["exit_code"] == 130
    finally:
        proc = holder.get("proc")
        if proc is not None and proc.poll() is None:
            kill_process_group(proc)


_FAKE_GHP = "ghp_FAKELOCALPIPELINETOKEN123456"
_FAKE_GHO = "gho_FAKELOCALPIPELINETOKEN123456"
_FAKE_GHU = "ghu_FAKELOCALPIPELINETOKEN123456"
_FAKE_GHS = "ghs_FAKELOCALPIPELINETOKEN123456"
_FAKE_GHR = "ghr_FAKELOCALPIPELINETOKEN123456"
_FAKE_PAT = "github_pat_FAKELOCALPIPELINETOKEN123456"
_GITHUB_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
_AUDIT_DETAIL_KEYS = frozenset(
    {"backend", "exit_code", "duration_ms", "error_code", "job_name", "job_path"}
)


def _write_project_config(workdir: Path, body: dict) -> None:
    agent = workdir / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "project_config.json").write_text(
        json.dumps(body),
        encoding="utf-8",
    )


def _capture_local_mcp(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("memory.local_mcp")
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)


def test_local_pipeline_missing_section_is_disabled_act(tmp_path: Path, caplog):
    _capture_local_mcp(caplog)
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        missing = load_local_pipeline(tmp_path)
    assert missing.enabled is False
    assert missing.config_backend == "act"
    assert missing.section == {}
    assert caplog.records == []

    _write_project_config(
        tmp_path,
        {
            "integrations": {
                "issue_tracker": {"enabled": True, "provider": "linear"},
            }
        },
    )
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        no_key = load_local_pipeline(tmp_path)
    assert no_key.enabled is False
    assert no_key.config_backend == "act"
    assert no_key.section == {}
    assert caplog.records == []


def test_local_pipeline_enabled_false_copies_jenkins(tmp_path: Path, caplog):
    _capture_local_mcp(caplog)
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "jenkins"}},
    )
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        cfg = load_local_pipeline(tmp_path)
    assert cfg.enabled is False
    assert cfg.config_backend == "jenkins"
    assert cfg.section["backend"] == "jenkins"
    assert caplog.records == []


def test_local_pipeline_enabled_self_hosted(tmp_path: Path):
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": True, "backend": "self_hosted"}},
    )
    cfg = load_local_pipeline(tmp_path)
    assert cfg.enabled is True
    assert cfg.config_backend == "self_hosted"


def test_local_pipeline_unknown_backend_is_not_stored(tmp_path: Path, caplog):
    _capture_local_mcp(caplog)
    _write_project_config(
        tmp_path,
        {
            "local_pipeline": {
                "enabled": True,
                "backend": "circle",
                "act": {"timeout_s": 1800},
            }
        },
    )
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        cfg = load_local_pipeline(tmp_path)
    assert cfg.enabled is False
    assert cfg.config_backend == "act"
    blob = json.dumps(
        {
            "enabled": cfg.enabled,
            "config_backend": cfg.config_backend,
            "section": cfg.section,
        }
    )
    assert "circle" not in blob
    assert "circle" not in repr(cfg)
    assert cfg.section["act"]["timeout_s"] == 1800
    assert "backend" not in cfg.section
    assert caplog.records
    assert all(rec.levelno == logging.WARNING for rec in caplog.records)
    assert all(rec.name == "memory.local_mcp" for rec in caplog.records)
    assert "circle" not in caplog.text


def test_local_pipeline_enabled_string_is_off(tmp_path: Path, caplog):
    _capture_local_mcp(caplog)
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": "true", "backend": "act"}},
    )
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        cfg = load_local_pipeline(tmp_path)
    assert cfg.enabled is False
    assert cfg.config_backend == "act"
    assert caplog.records == []


def test_disabled_result_validates_and_fourth_backend_does_not(monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    cases = (
        (
            {"backend": "act", "working_directory": str(Path.cwd())},
            "jenkins",
        ),
        (
            {
                "backend": "self_hosted",
                "working_directory": str(Path.cwd()),
                "runner_labels": ["self-hosted", "linux"],
            },
            "act",
        ),
        (
            {
                "backend": "jenkins",
                "working_directory": str(Path.cwd()),
                "job_path": "team/job",
            },
            "self_hosted",
        ),
    )
    schema = load_schema("local_pipeline_result")
    assert schema["properties"]["config_backend"]["enum"] == [
        "act",
        "self_hosted",
        "jenkins",
    ]
    assert schema["properties"]["backend"]["enum"] == [
        "act",
        "self_hosted",
        "jenkins",
    ]
    for arguments, config_backend in cases:
        body = disabled_result(arguments, config_backend)
        refused = refused_pipeline_result(arguments)
        assert result_errors(body) == []
        assert body["ok"] is False
        assert body["skipped"] is True
        assert body["exit_code"] == 2
        assert body["status"] == "refused"
        assert body["duration_ms"] == 0
        assert body["timed_out"] is False
        assert body["cancelled"] is False
        assert body["truncated"] is False
        assert body["lines_dropped"] == 0
        assert body["queue_risk"] is False
        assert body["log_tail"] == ""
        assert body["warnings"] == []
        assert body["probe"] == {}
        assert body["status_posted_by"] is None
        assert body["error"]["code"] == "local_pipeline_disabled"
        assert _FAKE_GHP not in body["error"]["message"]
        assert body["backend"] == arguments["backend"]
        assert body["config_backend"] == config_backend
        assert body["job"] == refused["job"]
        assert body["status_target"] == refused["status_target"]
        assert body["scm"] == refused["scm"]
        assert refused["config_backend"] == arguments["backend"]
        if arguments["backend"] == "jenkins":
            assert refused["config_backend"] == "jenkins"
        kept = refused_pipeline_result(arguments, config_backend)
        assert kept["config_backend"] == config_backend
        assert kept["job"] == body["job"]
        assert kept["status_target"] == body["status_target"]
        assert kept["scm"] == body["scm"]
        fourth = dict(body)
        fourth["config_backend"] = "local"
        assert result_errors(fourth)
        another = dict(body)
        another["config_backend"] = "circle"
        assert result_errors(another)


@pytest.mark.parametrize(
    "key",
    [
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "DASHBOARD_TOKEN",
        "JENKINS_API_TOKEN",
    ],
)
def test_environment_denylist_rejects_keys(key: str):
    rejected = check_environment({key: _FAKE_GHP, "MY_OK": "plain"})
    assert rejected is not None
    assert rejected.code == "secret_key_rejected"
    assert _FAKE_GHP not in rejected.message
    assert "ghp_" not in rejected.message
    assert "-----BEGIN " not in rejected.message


def test_environment_denylist_accepts_my_github_token_and_rejects_values():
    assert check_environment({"MY_GITHUB_TOKEN": "normal-value"}) is None
    assert check_environment({"CUSTOM_NAME": "-----BEGIN"}) is None
    for secret in (
        "prefix " + _FAKE_GHP + " suffix",
        "-----BEGIN RSA PRIVATE KEY-----",
        "x " + _FAKE_PAT + " y",
        "prefix " + _FAKE_GHO + " suffix",
        "prefix " + _FAKE_GHU + " suffix",
        "prefix " + _FAKE_GHS + " suffix",
        "prefix " + _FAKE_GHR + " suffix",
    ):
        rejected = check_environment({"MY_GITHUB_TOKEN": "normal-value", "CUSTOM_NAME": secret})
        assert rejected is not None
        assert rejected.code == "secret_value_rejected"
        assert secret not in rejected.message
        assert _FAKE_GHP not in rejected.message
        assert _FAKE_PAT not in rejected.message
        for marker in _GITHUB_PREFIXES:
            assert marker not in rejected.message
        assert "-----BEGIN " not in rejected.message


def test_audit_allowlist_drops_fake_token(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 4}),
        encoding="utf-8",
    )
    clean = local_pipeline_run(
        tmp_path,
        backend="jenkins",
        exit_code=1,
        duration_ms=15,
        error_code="job_failed",
        job_path="team/job",
    )
    assert clean["role"] == "ci"
    assert clean["cycle"] == 4
    assert clean["action"] == "local_pipeline_run"
    assert clean["details"] == {
        "backend": "jenkins",
        "exit_code": 1,
        "duration_ms": 15,
        "error_code": "job_failed",
        "job_path": "team/job",
    }
    assert isinstance(clean["details"]["exit_code"], int)
    assert isinstance(clean["details"]["duration_ms"], int)
    assert not isinstance(clean["details"]["exit_code"], bool)

    leaked = local_pipeline_run(
        tmp_path,
        backend="act",
        exit_code=2,
        duration_ms=0,
        error_code="local_pipeline_disabled",
        job_name=_FAKE_GHP,
        leaked=_FAKE_GHP,
    )
    assert _FAKE_GHP not in json.dumps(leaked)
    assert "job_name" not in leaked["details"]
    assert set(leaked["details"]) <= _AUDIT_DETAIL_KEYS

    probed = local_pipeline_probe_failed(
        tmp_path,
        backend="act",
        exit_code=2,
        duration_ms=3,
        error_code="docker_unreachable",
        job_path=_FAKE_GHP,
    )
    assert probed["action"] == "local_pipeline_probe_failed"
    assert "job_path" not in probed["details"]

    cancelled = local_pipeline_cancelled(
        tmp_path,
        backend="self_hosted",
        exit_code=130,
        duration_ms=9,
        error_code=_FAKE_GHP,
        job_name="harness",
    )
    assert cancelled["action"] == "local_pipeline_cancelled"
    assert "error_code" not in cancelled["details"]
    assert cancelled["details"]["job_name"] == "harness"

    rejected = local_pipeline_webhook_reject(tmp_path, reason=_FAKE_GHP)
    assert rejected["action"] == "local_pipeline_webhook_reject"
    assert rejected["role"] == "ci"
    assert rejected["details"] == {}

    text = (agent / "AUDIT_LOG.json").read_text(encoding="utf-8")
    assert _FAKE_GHP not in text
    data = json.loads(text)
    for entry in data["entries"]:
        assert entry["role"] == "ci"
        assert entry["cycle"] == 4
        assert set(entry["details"]) <= _AUDIT_DETAIL_KEYS | {"reason"}
        for value in entry["details"].values():
            assert isinstance(value, (str, int))
            assert not isinstance(value, bool)


def test_audit_cycle_bool_and_string_are_zero(tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": True}),
        encoding="utf-8",
    )
    entry = local_pipeline_run(
        tmp_path,
        backend="act",
        exit_code=2,
        duration_ms=0,
        error_code="local_pipeline_disabled",
    )
    assert entry["cycle"] == 0
    (agent / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": "4"}),
        encoding="utf-8",
    )
    again = local_pipeline_webhook_reject(tmp_path, reason="bad_hmac")
    assert again["cycle"] == 0
    assert again["details"] == {"reason": "bad_hmac"}


def test_redact_text_and_local_mcp_logger_drop_github_tokens(caplog):
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBSECRET\n-----END RSA PRIVATE KEY-----"
    raw = (
        f"before {pem} after {_FAKE_GHP} {_FAKE_PAT} "
        f"{_FAKE_GHO} {_FAKE_GHU} {_FAKE_GHS} {_FAKE_GHR} "
        "AWS_SECRET_ACCESS_KEY=supersecretvalue MY_GITHUB_TOKEN=keep-me"
    )
    out = redact_text(raw)
    assert _FAKE_GHP not in out
    assert _FAKE_PAT not in out
    assert _FAKE_GHO not in out
    assert _FAKE_GHU not in out
    assert _FAKE_GHS not in out
    assert _FAKE_GHR not in out
    for marker in _GITHUB_PREFIXES:
        assert marker not in out
    assert "-----BEGIN " not in out
    assert "MIIBSECRET" not in out
    assert "supersecretvalue" not in out
    assert "AWS_SECRET_ACCESS_KEY=" in out
    assert "MY_GITHUB_TOKEN=keep-me" in out
    assert "before " in out

    configure_logging()
    log = get_logger("memory.local_mcp")
    assert "memory.local_mcp" in _CHILD_LOGGERS
    assert any(isinstance(filt, RedactFilter) for filt in log.filters)
    local_filters = [filt for filt in log.filters if isinstance(filt, LocalRedactFilter)]
    assert len(local_filters) == 1
    names = [type(filt).__name__ for filt in log.filters]
    assert names.index("LocalRedactFilter") < names.index("RedactFilter")
    other = get_logger("memory.integrations")
    assert not any(isinstance(filt, LocalRedactFilter) for filt in other.filters)
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)
    with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
        log.info(
            "saw %s %s %s %s %s %s",
            _FAKE_GHP,
            _FAKE_PAT,
            _FAKE_GHO,
            _FAKE_GHU,
            _FAKE_GHS,
            _FAKE_GHR,
        )
    assert _FAKE_GHP not in caplog.text
    assert _FAKE_PAT not in caplog.text
    assert _FAKE_GHO not in caplog.text
    assert _FAKE_GHU not in caplog.text
    assert _FAKE_GHS not in caplog.text
    assert _FAKE_GHR not in caplog.text
    for marker in _GITHUB_PREFIXES:
        assert marker not in caplog.text


def test_redact_assignment_masks_value_with_spaces():
    quoted = redact_text('AWS_SECRET_ACCESS_KEY="super secret"')
    single = redact_text("AWS_SECRET_ACCESS_KEY='super secret'")
    unquoted = redact_text(
        "AWS_SECRET_ACCESS_KEY=super secret MY_GITHUB_TOKEN=keep-me"
    )
    both = redact_text("AWS_SECRET=super secret GITHUB_TOKEN=also secret here")
    kept = redact_text("MY_GITHUB_TOKEN=keep me")
    assert quoted == "AWS_SECRET_ACCESS_KEY=****"
    assert single == "AWS_SECRET_ACCESS_KEY=****"
    assert unquoted == "AWS_SECRET_ACCESS_KEY=**** MY_GITHUB_TOKEN=keep-me"
    assert both == "AWS_SECRET=**** GITHUB_TOKEN=****"
    assert kept == "MY_GITHUB_TOKEN=keep me"
    assert "super" not in quoted
    assert "secret" not in unquoted
    assert "also" not in both


def test_redact_text_masks_pem_body_without_footer():
    body = "MIIEowIBAAKCAQEA0Y1234567890abcdefghijklmn"
    more = "opqrstuvwxyz1234567890ABCDEFGHIJKLMN=="
    open_block = (
        "prefix log text\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"{body}\n"
        f"{more}\n"
        "after-open-body\n"
    )
    out = redact_text(open_block)
    assert body not in out
    assert more not in out
    assert "-----BEGIN " not in out
    assert "prefix log text" in out
    # No footer, so the rest of the buffer stays inside the block.
    assert "after-open-body" not in out

    closed = (
        "keep-before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "Proc-Type: 4,ENCRYPTED\n"
        f"{body}\n"
        "-----END RSA PRIVATE KEY-----\n"
        "keep-after\n"
    )
    masked = redact_text(closed)
    assert body not in masked
    assert "Proc-Type" not in masked
    assert "-----BEGIN " not in masked
    assert "-----END " not in masked
    assert "keep-before" in masked
    assert "keep-after" in masked


def _capture_formatted(log: logging.Logger):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    return stream, handler


def test_dashboard_pat_tail_is_masked_before_keep_edges(monkeypatch, caplog):
    pat = _FAKE_PAT
    tail = pat[-4:]
    monkeypatch.setenv("DASHBOARD_TOKEN", pat)
    configure_logging()
    log = get_logger("memory.local_mcp")
    names = [type(filt).__name__ for filt in log.filters]
    assert names.index("LocalRedactFilter") < names.index("RedactFilter")
    stream, handler = _capture_formatted(log)
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
            log.info("pat %s", pat)
    finally:
        log.removeHandler(handler)
    rendered = stream.getvalue()
    assert tail not in caplog.text
    assert tail not in rendered
    assert "github_pat_" not in caplog.text
    assert "github_pat_" not in rendered
    assert pat not in rendered


def test_non_marker_dashboard_token_keeps_edges(monkeypatch, caplog):
    token = "abcdEFGHijklWXYZ"
    monkeypatch.setenv("DASHBOARD_TOKEN", token)
    configure_logging()
    log = get_logger("memory.local_mcp")
    stream, handler = _capture_formatted(log)
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
            log.info("tok %s", token)
    finally:
        log.removeHandler(handler)
    rendered = stream.getvalue()
    assert token not in rendered
    assert token not in caplog.text
    assert "abcd****WXYZ" in rendered
    assert "abcd****WXYZ" in caplog.text


def test_exception_text_drops_github_tokens(caplog):
    configure_logging()
    log = get_logger("memory.local_mcp")
    stream, handler = _capture_formatted(log)
    if caplog.handler not in log.handlers:
        log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
            log.info(
                "boom",
                exc_info=(RuntimeError, RuntimeError(f"{_FAKE_GHP} {_FAKE_PAT}"), None),
            )
    finally:
        log.removeHandler(handler)
    rendered = stream.getvalue()
    assert "ghp_" not in caplog.text
    assert "github_pat_" not in caplog.text
    assert "ghp_" not in rendered
    assert "github_pat_" not in rendered
    assert _FAKE_GHP not in rendered
    assert _FAKE_PAT not in rendered


def test_example_config_parses_and_gitignore_lists_run_artifacts():
    example = REPO / ".agent" / "project_config.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    keys = list(data)
    assert keys[keys.index("local_pipeline") - 1] == "_local_pipeline_comment"
    assert isinstance(data["_local_pipeline_comment"], str)
    assert "act is the default" in data["_local_pipeline_comment"]
    section = data["local_pipeline"]
    assert section["enabled"] is False
    assert section["backend"] == "act"
    assert section["notify"] is False
    assert section["act"]["timeout_s"] == 1800
    assert section["act"]["pull"] is False
    assert section["self_hosted"]["timeout_s"] == 1800
    assert section["self_hosted"]["allow_start"] is False
    assert section["self_hosted"]["runner_dir"] is None
    assert section["jenkins"]["timeout_s"] == 1800
    assert section["jenkins"]["crumb_required"] is True
    assert section["jenkins"]["parameters"] == []
    assert section["jenkins"]["credential_ids"]["bitbucket_checkout"] is None
    assert section["jenkins"]["credential_ids"]["bitbucket_status"] is None
    assert section["jenkins"]["webhook_relay"] is False
    assert section["scm"]["kind"] == "server"
    assert section["scm"]["bitbucket_url"] is None
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for line in (".secrets", ".vars", ".agent/local-pipeline-runs/"):
        assert line in lines


_RUNNER_LOG = (
    "2020-02-11 14:57:17Z: Listening for Jobs\n"
    "Current runner version: '2.319.1'\n"
)
_LEAK_ROOT = "/tmp/leak-root-9f3a"
_LEAK_NAME = "leak-name-9f3a"
_FAKE_JENKINS_TOKEN = "jenkins-token-not-real-9f3a"


def _assert_probe_result(result: dict, backend: str) -> None:
    _assert_schema_ok(result)
    assert result["backend"] == backend
    assert result["skipped"] is False
    assert result["log_tail"] == ""
    assert result["log_path"] == f".agent/local-pipeline-runs/probe-{backend}.log"
    assert result["warnings"] == []
    assert result["status_posted_by"] is None
    assert result["timed_out"] is False
    assert result["cancelled"] is False
    assert result["truncated"] is False
    assert result["lines_dropped"] == 0
    assert isinstance(result["duration_ms"], int) and result["duration_ms"] >= 0
    assert result["scm"]["kind"] is None
    assert result["scm"]["ref"] is None
    assert result["scm"]["commit"] is None
    assert result["error"] is None or result["error"]["code"] != "local_pipeline_disabled"
    if result["ok"]:
        assert result["exit_code"] == 0
        assert result["status"] == "succeeded"
        assert result["error"] is None
        assert result["queue_risk"] is False
    else:
        assert result["exit_code"] == 2
        assert result["status"] == "refused"
        assert set(result["error"]) == {"code", "message"}
        assert result["queue_risk"] is (result["error"]["code"] == "runner_offline")
    blob = json.dumps(result)
    assert _FAKE_JENKINS_TOKEN not in blob
    assert "Basic " not in blob
    assert _LEAK_ROOT not in blob
    assert ".credentials" not in blob


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _act_env(bin_dir: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    env.update(extra)
    return env


def _probe_proc(tmp_path: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    child_env = dict(env)
    existing = child_env.get("PYTHONPATH", "")
    parts = [str(REPO)]
    if existing:
        parts.append(existing)
    child_env["PYTHONPATH"] = os.pathsep.join(parts)
    return subprocess.run(
        [sys.executable, "-m", "memory.local_mcp", "probe", *args, "--workdir", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=child_env,
    )


def test_probe_timeout_default_is_five_seconds():
    assert PROBE_TIMEOUT_S == 5.0
    assert inspect.signature(probe_act).parameters["timeout_s"].default == 5.0
    assert inspect.signature(probe_jenkins).parameters["timeout_s"].default == 5.0
    assert inspect.signature(probe_self_hosted).parameters["timeout_s"].default == 5.0
    assert inspect.signature(probe_backend).parameters["timeout_s"].default == 5.0
    assert list_linux_processes.__defaults__ == (Path("/proc"),)


def test_probe_act_success_hides_docker_paths_and_parent_env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    record = tmp_path / "child-env.json"
    act_record = tmp_path / "act-child-env.json"
    _write_exe(
        bin_dir / "docker",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(record)!r}).write_text(\n"
        "    json.dumps({\"argv\": sys.argv, \"env\": dict(os.environ)}),\n"
        "    encoding=\"utf-8\",\n"
        ")\n"
        f"sys.stdout.write('Name: {_LEAK_NAME}\\n')\n"
        f"sys.stdout.write('Docker Root Dir: {_LEAK_ROOT}\\n')\n"
        "sys.stdout.write('ServerVersion: 27.1.1\\n')\n"
        f"sys.stderr.write({_LEAK_ROOT!r})\n",
    )
    _write_exe(
        bin_dir / "act",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(act_record)!r}).write_text(\n"
        "    json.dumps({\"argv\": sys.argv, \"env\": dict(os.environ)}),\n"
        "    encoding=\"utf-8\",\n"
        ")\n"
        "sys.stdout.write('act version 0.2.70\\n')\n",
    )
    _write_project_config(
        tmp_path,
        {
            "local_pipeline": {
                "enabled": False,
                "backend": "jenkins",
                "act": {
                    "workflow_file": ".github/workflows/custom.yml",
                    "job_name": "custom-job",
                    "runner_image": "example.invalid/act:pin",
                },
            }
        },
    )
    docker_env = {
        "DOCKER_HOST": "unix:///tmp/rootless.sock",
        "DOCKER_CONTEXT": "rootless",
        "DOCKER_CONFIG": "/tmp/docker-config",
        "DOCKER_CERT_PATH": "/tmp/docker-certs",
    }
    env = _act_env(
        bin_dir,
        JENKINS_API_TOKEN=_FAKE_JENKINS_TOKEN,
        HOME="/home/operator",
        **docker_env,
    )
    result = probe_act(tmp_path, environ=env)
    _assert_probe_result(result, "act")
    assert result["ok"] is True
    assert result["config_backend"] == "jenkins"
    assert result["status_target"] == "local"
    assert result["scm"]["host"] == "local"
    assert result["skipped"] is False
    assert result["job"] == {
        "workflow_file": ".github/workflows/custom.yml",
        "job_name": "custom-job",
    }
    assert result["probe"] == {
        "docker": True,
        "docker_server": "27.1.1",
        "act_version": "act version 0.2.70",
        "runner_image": "example.invalid/act:pin",
    }
    assert _LEAK_NAME not in json.dumps(result)
    child = json.loads(record.read_text(encoding="utf-8"))
    act_child = json.loads(act_record.read_text(encoding="utf-8"))
    assert child["argv"][1:] == ["info"]
    assert act_child["argv"][1:] == ["--version"]
    # CPython may insert LC_CTYPE. Socket variables are forwarded when set.
    # Parent HOME and the Jenkins token must not. The rest of the parent stays out.
    allowed = {"PATH", "LC_CTYPE", *docker_env}
    for item in (child, act_child):
        assert item["env"]["PATH"] == str(bin_dir)
        for key, value in docker_env.items():
            assert item["env"][key] == value
        assert set(item["env"]) <= allowed
        assert "JENKINS_API_TOKEN" not in item["env"]
        assert "HOME" not in item["env"]
        assert _FAKE_JENKINS_TOKEN not in json.dumps(item)
    assert not (tmp_path / ".agent" / "local-pipeline-runs" / "probe-act.log").exists()


def test_probe_act_parses_spaced_server_version(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import sys\n"
        "sys.stdout.write('Server:\\n Server Version: 27.1.1\\n')\n",
    )
    _write_exe(bin_dir / "act", "import sys\nsys.stdout.write('0.2.70\\n')\n")
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["ok"] is True
    assert result["probe"]["docker_server"] == "27.1.1"


def test_probe_act_parses_json_server_version(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import json, sys\n"
        "json.dump({'ServerVersion': '27.5.1', 'DockerRootDir': %r, 'Name': %r}, sys.stdout)\n"
        % (_LEAK_ROOT, _LEAK_NAME),
    )
    _write_exe(bin_dir / "act", "import sys\nsys.stdout.write('0.2.71\\n')\n")
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["probe"]["docker"] is True
    assert result["probe"]["docker_server"] == "27.5.1"
    assert result["probe"]["act_version"] == "0.2.71"
    assert result["probe"]["runner_image"] == "catthehacker/ubuntu:act-22.04"
    assert result["job"] == {
        "workflow_file": ".github/workflows/agentix-loop.yml",
        "job_name": "harness",
    }


def test_probe_act_json_string_server_version(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import json, sys\njson.dump('27.5.2', sys.stdout)\n",
    )
    _write_exe(bin_dir / "act", "import sys\nsys.stdout.write('0.2.72\\n')\n")
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["probe"]["docker_server"] == "27.5.2"


def test_probe_act_docker_failure_does_not_run_act(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "act-ran"
    _write_exe(
        bin_dir / "docker",
        "import sys\n"
        f"sys.stdout.write('ServerVersion: 27.1.1\\nDocker Root Dir: {_LEAK_ROOT}\\n')\n"
        "raise SystemExit(1)\n",
    )
    _write_exe(
        bin_dir / "act",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
    )
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["error"] == {
        "code": "docker_unreachable",
        "message": "Install and start the Docker daemon. act cannot run.",
    }
    assert result["probe"] == {
        "docker": False,
        "docker_server": None,
        "act_version": None,
        "runner_image": "catthehacker/ubuntu:act-22.04",
    }
    assert not marker.exists()


def test_probe_act_missing_docker(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["error"]["code"] == "docker_unreachable"


def test_probe_act_missing_binary(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import sys\nsys.stdout.write('ServerVersion: 27.1.1\\n')\n",
    )
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["error"] == {
        "code": "act_missing",
        "message": "Install nektos/act. Do not fall back to gh workflow run.",
    }
    assert result["probe"]["docker"] is True
    assert result["probe"]["docker_server"] == "27.1.1"
    assert result["probe"]["act_version"] is None


@pytest.mark.parametrize("body", ["raise SystemExit(1)\n", "import sys\n"])
def test_probe_act_version_unreadable(tmp_path: Path, body: str):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import sys\nsys.stdout.write('ServerVersion: 27.1.1\\n')\n",
    )
    _write_exe(bin_dir / "act", body)
    result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["error"] == {
        "code": "act_version_unreadable",
        "message": "Binary is not act, or it crashed.",
    }
    assert result["probe"]["act_version"] is None
    assert result["probe"]["docker"] is True


def test_probe_act_timeout_kills_process_group(tmp_path: Path):
    import signal

    bin_dir = tmp_path / "bin"
    ready = tmp_path / "ready"
    _write_exe(
        bin_dir / "docker",
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"ready = Path({str(ready)!r})\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "ready.write_text(\n"
        "    '{} {} {}\\n'.format(os.getpid(), child.pid, os.getpgid(0)),\n"
        "    encoding='utf-8',\n"
        ")\n"
        "time.sleep(120)\n",
    )
    pgid = None
    try:
        result = probe_act(tmp_path, timeout_s=0.5, environ=_act_env(bin_dir))
        _assert_probe_result(result, "act")
        assert result["error"]["code"] == "docker_unreachable"
        text = ready.read_text(encoding="utf-8").split()
        parent_pid, child_pid, pgid = (int(part) for part in text)
        assert pgid == parent_pid
        assert pgid != os.getpgrp()
        _assert_group_dead(pgid)
        _assert_dead(child_pid)
        _assert_dead(parent_pid)
    finally:
        if pgid is not None and pgid != os.getpgrp():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_probe_act_requires_linux_does_not_spawn(monkeypatch, tmp_path: Path):
    import memory.local_mcp.probe as probe_mod

    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(probe_mod.sys, "platform", "win32")
    monkeypatch.setattr(probe_mod.subprocess, "Popen", boom)
    result = probe_act(tmp_path)
    _assert_probe_result(result, "act")
    assert result["error"] == {
        "code": "act_requires_linux",
        "message": "Run act on Linux. Jenkins HTTP from Windows is still allowed.",
    }
    assert result["probe"]["docker"] is False
    assert result["probe"]["act_version"] is None


def test_probe_act_cli_stdout_is_one_json_object(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import sys\n"
        f"sys.stderr.write({_LEAK_ROOT!r})\n"
        "sys.stdout.write('ServerVersion: 24.0.7\\n')\n",
    )
    _write_exe(bin_dir / "act", "import sys\nsys.stdout.write('0.2.70\\n')\n")
    env = _act_env(bin_dir, JENKINS_API_TOKEN=_FAKE_JENKINS_TOKEN)
    env.pop("PYTHONPATH", None)
    proc = _probe_proc(tmp_path, "--backend", "act", env=env)
    assert proc.returncode == 0, proc.stderr
    assert _FAKE_JENKINS_TOKEN not in proc.stderr
    assert "Basic " not in proc.stderr
    assert "Authorization" not in proc.stderr
    assert _LEAK_ROOT not in proc.stderr
    assert _FAKE_JENKINS_TOKEN not in proc.stdout
    assert proc.stdout.endswith("\n")
    assert proc.stdout.count("\n") == 1
    payload = json.loads(proc.stdout)
    assert proc.returncode == payload["exit_code"]
    _assert_probe_result(payload, "act")
    assert payload["probe"]["docker_server"] == "24.0.7"
    assert payload["probe"]["act_version"] == "0.2.70"


def test_probe_act_does_not_take_pipeline_lock(tmp_path: Path):
    from memory.agent_lock import agent_lock

    bin_dir = tmp_path / "bin"
    _write_exe(
        bin_dir / "docker",
        "import sys\nsys.stdout.write('ServerVersion: 27.1.1\\n')\n",
    )
    _write_exe(bin_dir / "act", "import sys\nsys.stdout.write('0.2.70\\n')\n")
    with agent_lock(tmp_path / ".agent", name="local_pipeline", timeout=0.2):
        result = probe_act(tmp_path, environ=_act_env(bin_dir))
    _assert_probe_result(result, "act")
    assert result["ok"] is True


def _runner_config(tmp_path: Path, runner: Path, **self_hosted: Any) -> None:
    body = {"runner_dir": str(runner)}
    body.update(self_hosted)
    _write_project_config(
        tmp_path,
        {"local_pipeline": {"enabled": False, "backend": "self_hosted", "self_hosted": body}},
    )


def _write_runner_log(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _listener(runner: Path, *, comm: str = "Runner.Listener", exe: str | None = None, cwd: str | None = None):
    return SimpleNamespace(
        comm=comm,
        exe=exe if exe is not None else str(runner / "bin" / "Runner.Listener"),
        cwd=cwd if cwd is not None else str(runner),
    )


def test_probe_self_hosted_missing_dir_and_unregistered(tmp_path: Path):
    missing = probe_self_hosted(tmp_path, [])
    _assert_probe_result(missing, "self_hosted")
    assert missing["error"]["code"] == "runner_log_missing"
    assert missing["probe"] == {"runner_log_fresh": False}
    assert missing["queue_risk"] is False
    assert missing["job"] == {"runner_labels": []}

    runner = tmp_path / "runner"
    runner.mkdir()
    _runner_config(tmp_path, runner)
    secret = "secret-runner-body"
    (runner / "credentials").write_text("cred-secret-marker", encoding="utf-8")
    (runner / ".credentials").write_text("dot-cred-secret-marker", encoding="utf-8")
    absent = probe_self_hosted(tmp_path, [])
    _assert_probe_result(absent, "self_hosted")
    assert absent["error"] == {
        "code": "runner_not_registered",
        "message": "Human runs config.sh with a token from the GitHub UI.",
    }
    assert absent["probe"] == {"runner_log_fresh": False}
    assert not (runner / ".runner").exists()
    assert secret not in json.dumps(absent)
    assert "cred-secret-marker" not in json.dumps(absent)
    assert "dot-cred-secret-marker" not in json.dumps(absent)

    (runner / ".runner").write_text('{"token": "%s"}' % secret, encoding="utf-8")
    no_log = probe_self_hosted(tmp_path, [_listener(runner)])
    _assert_probe_result(no_log, "self_hosted")
    assert no_log["error"]["code"] == "runner_log_missing"
    assert no_log["probe"] == {"runner_log_fresh": False}
    assert secret not in json.dumps(no_log)


@pytest.mark.parametrize("newer", ["pages", "flat"])
def test_probe_self_hosted_reads_newer_runner_log(tmp_path: Path, newer: str):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    flat = runner / "_diag" / "Runner_20241010-213029-utc.log"
    pages = runner / "_diag" / "pages" / "Runner_20241010-213029-utc.log"
    worker = runner / "_diag" / "Worker_20241010-213029-utc.log"
    phrase = _RUNNER_LOG
    other = "Current runner version: '2.319.1'\n"
    if newer == "pages":
        _write_runner_log(flat, other, now - 30)
        _write_runner_log(pages, phrase, now - 10)
    else:
        _write_runner_log(pages, other, now - 30)
        _write_runner_log(flat, phrase, now - 10)
    _write_runner_log(worker, "no listener line\n", now - 1)
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=None)
    result = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(result, "self_hosted")
    assert result["error"] == {
        "code": "runner_label_unknown",
        "message": "Copy the runner's labels into config. The log is not parsed for them.",
    }
    assert result["queue_risk"] is False
    assert result["probe"] == {"runner_log_fresh": True}
    assert "2.319.1" not in json.dumps(result)
    assert "Listening for Jobs" not in json.dumps(result)


def test_probe_self_hosted_relative_runner_dir(tmp_path: Path):
    runner = tmp_path / "rel-runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _write_project_config(
        tmp_path,
        {
            "local_pipeline": {
                "enabled": True,
                "backend": "self_hosted",
                "self_hosted": {"runner_dir": "rel-runner", "labels": []},
            }
        },
    )
    result = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(result, "self_hosted")
    assert result["error"]["code"] == "runner_label_unknown"
    assert result["probe"] == {"runner_log_fresh": True}
    assert result["config_backend"] == "self_hosted"


def test_probe_self_hosted_offline_outside_and_stale(tmp_path: Path):
    runner = tmp_path / "runner"
    outside = tmp_path / "other"
    outside.mkdir()
    now = 1_700_000_000.0
    log = runner / "_diag" / "Runner_20241010-213029-utc.log"
    _write_runner_log(log, _RUNNER_LOG, now - 5)
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux", "self-hosted"])
    outside_proc = _listener(
        runner,
        exe=str(outside / "Runner.Listener"),
        cwd=str(outside),
    )
    wrong_name = {
        "comm": "python",
        "exe": str(outside / "python"),
        "cwd": str(runner),
    }
    offline = probe_self_hosted(tmp_path, [outside_proc, wrong_name], now=now)
    _assert_probe_result(offline, "self_hosted")
    assert offline["error"]["code"] == "runner_offline"
    assert offline["queue_risk"] is True
    assert "will not enroll" in offline["error"]["message"]
    assert offline["job"] == {"runner_labels": ["linux", "self-hosted"]}

    os.utime(log, (now - 5000, now - 5000))
    stale = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(stale, "self_hosted")
    assert stale["error"] == {
        "code": "runner_log_stale",
        "message": "Listener may be stuck. Read the log. Do not mint a new token.",
    }
    assert stale["probe"] == {"runner_log_fresh": False}
    assert stale["queue_risk"] is False

    os.utime(log, (now - 5, now - 5))
    log.write_text("Current runner version: '2.319.1'\n", encoding="utf-8")
    os.utime(log, (now - 5, now - 5))
    quiet = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(quiet, "self_hosted")
    assert quiet["error"]["code"] == "runner_offline"
    assert quiet["queue_risk"] is True


def test_probe_self_hosted_growing_log_is_not_stale(tmp_path: Path, monkeypatch):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    old = now - 5000
    log = runner / "_diag" / "Runner_20241010-213029-utc.log"
    _write_runner_log(log, _RUNNER_LOG, old)
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"])
    real_stat = Path.stat
    sizes: list[int] = []

    def recording_stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self == log:
            sizes.append(st.st_size)
        return st

    monkeypatch.setattr(Path, "stat", recording_stat)
    same = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(same, "self_hosted")
    assert same["error"]["code"] == "runner_log_stale"
    assert same["probe"] == {"runner_log_fresh": False}
    assert len(sizes) >= 2
    assert sizes[-1] == sizes[-2]

    sizes.clear()

    class _Grow:
        samples: tuple[int, int]

        def __iter__(self):
            before = os.stat(log).st_size
            with open(log, "ab") as handle:
                handle.write(b"\n")
            # write() refreshes mtime. Restore it so only growth can clear stale.
            os.utime(log, (old, old))
            after = os.stat(log).st_size
            self.samples = (before, after)
            yield _listener(runner)

    growing = _Grow()
    grew = probe_self_hosted(tmp_path, growing, now=now)
    _assert_probe_result(grew, "self_hosted")
    assert grew["ok"] is True
    assert grew["error"] is None
    assert grew["probe"] == {"runner_log_fresh": True}
    assert growing.samples[1] > growing.samples[0]
    assert len(sizes) >= 2
    assert sizes[-1] > sizes[-2]
    assert now - os.stat(log).st_mtime > 900


def test_probe_self_hosted_label_mismatch_and_success(tmp_path: Path):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "pages" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux", "self-hosted"])
    proc = {
        "comm": "run.sh",
        "exe": str(runner / "run.sh"),
        "cwd": str(tmp_path),
    }
    mismatch = probe_self_hosted(
        tmp_path,
        [proc],
        requested_labels=["Linux"],
        now=now,
    )
    _assert_probe_result(mismatch, "self_hosted")
    assert mismatch["error"]["code"] == "runner_label_mismatch"
    assert mismatch["probe"] == {"runner_log_fresh": True}
    assert mismatch["queue_risk"] is False

    extra = probe_self_hosted(
        tmp_path,
        [proc],
        requested_labels=["linux", "gpu"],
        now=now,
    )
    assert extra["error"]["code"] == "runner_label_mismatch"
    assert extra["probe"] == {"runner_log_fresh": True}

    ok = probe_self_hosted(
        tmp_path,
        [proc],
        requested_labels=["linux"],
        now=now,
    )
    _assert_probe_result(ok, "self_hosted")
    assert ok["ok"] is True
    assert ok["probe"] == {"runner_log_fresh": True}
    assert ok["queue_risk"] is False
    assert ok["status_target"] == "github_actions_ui"
    assert ok["scm"]["host"] == "github"
    assert ok["job"] == {"runner_labels": ["linux", "self-hosted"]}

    cli_default = probe_self_hosted(tmp_path, [proc], requested_labels=(), now=now)
    _assert_probe_result(cli_default, "self_hosted")
    assert cli_default["ok"] is True


def test_probe_self_hosted_windows_ignores_empty_process_list(monkeypatch, tmp_path: Path):
    import memory.local_mcp.probe as probe_mod

    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"])
    linux = probe_self_hosted(tmp_path, [], now=now)
    _assert_probe_result(linux, "self_hosted")
    assert linux["error"]["code"] == "runner_offline"
    assert linux["queue_risk"] is True

    monkeypatch.setattr(probe_mod.sys, "platform", "win32")
    windows = probe_self_hosted(tmp_path, [], now=now)
    _assert_probe_result(windows, "self_hosted")
    assert windows["ok"] is True
    assert windows["queue_risk"] is False
    assert windows["error"] is None
    assert windows["probe"] == {"runner_log_fresh": True}


def test_probe_self_hosted_windows_skips_configured_systemd_unit(
    monkeypatch, tmp_path: Path
):
    import memory.local_mcp.probe as probe_mod

    monkeypatch.setattr(probe_mod.sys, "platform", "win32")
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(
        tmp_path,
        runner,
        labels=["linux"],
        systemd_unit="actions.runner.unit",
        systemd_scope="user",
    )
    bin_dir = tmp_path / "bin"
    record = tmp_path / "systemctl-argv.json"
    _install_systemctl(bin_dir, record, active=False)
    windows = probe_self_hosted(
        tmp_path,
        [],
        now=now,
        environ={"PATH": str(bin_dir)},
    )
    _assert_probe_result(windows, "self_hosted")
    assert not record.exists()
    assert windows["ok"] is True
    assert windows["queue_risk"] is False
    assert windows["error"] is None
    assert windows["probe"] == {"runner_log_fresh": True}


def test_probe_self_hosted_runsvc_basename_outside_cwd(tmp_path: Path):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"])
    proc = SimpleNamespace(
        comm="bash",
        exe=str(runner / "bin" / "runsvc.sh"),
        cwd=str(tmp_path),
    )
    result = probe_self_hosted(tmp_path, [proc], now=now)
    _assert_probe_result(result, "self_hosted")
    assert result["ok"] is True


def _install_systemctl(bin_dir: Path, record: Path, *, active: bool) -> None:
    flag = "active\n" if active else "inactive\n"
    code = "0" if active else "3"
    _write_exe(
        bin_dir / "systemctl",
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path(%s).write_text(json.dumps(sys.argv), encoding='utf-8')\n"
        "sys.stdout.write(%s)\n"
        "raise SystemExit(%s)\n" % (json.dumps(str(record)), json.dumps(flag), code),
    )


@pytest.mark.parametrize("scope", ["system", "user", None])
@pytest.mark.parametrize("active", [True, False])
def test_probe_self_hosted_systemctl_scope(tmp_path: Path, scope: str | None, active: bool):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    fields: dict[str, Any] = {
        "labels": ["linux"],
        "systemd_unit": "user.runner.service",
    }
    if scope is not None:
        fields["systemd_scope"] = scope
    _runner_config(tmp_path, runner, **fields)
    bin_dir = tmp_path / "bin"
    record = tmp_path / "systemctl-argv.json"
    _install_systemctl(bin_dir, record, active=active)
    result = probe_self_hosted(
        tmp_path,
        [_listener(runner)],
        now=now,
        environ={"PATH": str(bin_dir)},
    )
    _assert_probe_result(result, "self_hosted")
    argv = json.loads(record.read_text(encoding="utf-8"))
    # A shebang records the script path as argv[0]. The flags are what we passed.
    assert Path(argv[0]).name == "systemctl"
    if scope == "user":
        assert argv[1:] == ["--user", "is-active", "--", "user.runner.service"]
    else:
        assert argv[1:] == ["is-active", "--", "user.runner.service"]
        assert "--user" not in argv
    if active:
        assert result["ok"] is True
        assert result["queue_risk"] is False
        assert result["error"] is None
    else:
        assert result["error"]["code"] == "runner_offline"
        assert result["queue_risk"] is True
        assert result["ok"] is False


def test_systemd_user_spawn_keeps_session_bus(monkeypatch):
    import memory.local_mcp.probe as probe_mod

    captured: dict[str, Any] = {}

    def fake_popen(argv, **kwargs):
        del argv
        captured["env"] = dict(kwargs.get("env") or {})
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(probe_mod.subprocess, "Popen", fake_popen)
    section = {
        "self_hosted": {
            "systemd_unit": "actions.runner.unit",
            "systemd_scope": "user",
        }
    }
    assert (
        probe_mod._systemd_active(
            section,
            {
                "PATH": "/usr/bin",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                "JENKINS_API_TOKEN": "secret-token",
                "LD_PRELOAD": "/tmp/libpreload.so",
            },
            None,
        )
        is False
    )
    env = captured["env"]
    assert env["PATH"] == "/usr/bin"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert "JENKINS_API_TOKEN" not in env
    assert "LD_PRELOAD" not in env
    assert "secret-token" not in json.dumps(env)


def test_probe_self_hosted_systemctl_fn_does_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("systemctl spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "pages" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(
        tmp_path,
        runner,
        labels=["linux"],
        systemd_unit="actions.runner.svc",
        systemd_scope="user",
    )
    seen: dict[str, list[str]] = {}

    def fake(argv: list[str]) -> int:
        seen["argv"] = list(argv)
        return 0

    result = probe_self_hosted(
        tmp_path,
        [_listener(runner)],
        now=now,
        systemctl_fn=fake,
    )
    _assert_probe_result(result, "self_hosted")
    assert result["ok"] is True
    assert seen["argv"] == [
        "systemctl",
        "--user",
        "is-active",
        "--",
        "actions.runner.svc",
    ]


@pytest.mark.parametrize("unit", ["--version", "--help", "-h"])
def test_probe_self_hosted_option_shaped_unit_is_not_active(
    tmp_path: Path, monkeypatch, unit: str
):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("systemctl spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"], systemd_unit=unit)
    result = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(result, "self_hosted")
    assert result["ok"] is False
    assert result["error"]["code"] == "runner_offline"
    assert result["queue_risk"] is True


def test_probe_self_hosted_spent_budget_does_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("systemctl spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(
        tmp_path,
        runner,
        labels=["linux"],
        systemd_unit="actions.runner.unit",
    )

    def systemctl_fn(argv: list[str]) -> int:
        del argv
        raise AssertionError("systemctl_fn called")

    result = probe_self_hosted(
        tmp_path,
        [_listener(runner)],
        now=now,
        timeout_s=0,
        systemctl_fn=systemctl_fn,
    )
    _assert_probe_result(result, "self_hosted")
    assert result["error"]["code"] == "runner_offline"
    assert result["probe"] == {"runner_log_fresh": False}
    assert result["queue_risk"] is True
    assert result["timed_out"] is False


def test_probe_backend_passes_self_hosted_budget(tmp_path: Path, monkeypatch):
    import memory.local_mcp.probe as probe_mod

    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("systemctl spawned")

    monkeypatch.setattr(probe_mod.subprocess, "Popen", boom)
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(
        tmp_path,
        runner,
        labels=["linux"],
        systemd_unit="actions.runner.unit",
    )
    result = probe_backend(
        "self_hosted",
        tmp_path,
        processes=[_listener(runner)],
        now=now,
        timeout_s=0,
    )
    _assert_probe_result(result, "self_hosted")
    assert result["error"]["code"] == "runner_offline"
    assert result["probe"] == {"runner_log_fresh": False}


def test_probe_self_hosted_systemctl_budget_kills_group(tmp_path: Path):
    import signal

    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(
        tmp_path,
        runner,
        labels=["linux"],
        systemd_unit="actions.runner.unit",
    )
    bin_dir = tmp_path / "bin"
    ready = tmp_path / "ready"
    _write_exe(
        bin_dir / "systemctl",
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"ready = Path({str(ready)!r})\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "ready.write_text(\n"
        "    '{} {} {}\\n'.format(os.getpid(), child.pid, os.getpgid(0)),\n"
        "    encoding='utf-8',\n"
        ")\n"
        "time.sleep(120)\n",
    )
    pgid = None
    try:
        started = time.monotonic()
        result = probe_self_hosted(
            tmp_path,
            [_listener(runner)],
            now=now,
            timeout_s=0.4,
            environ={"PATH": str(bin_dir)},
        )
        elapsed = time.monotonic() - started
        _assert_probe_result(result, "self_hosted")
        assert result["error"]["code"] == "runner_offline"
        assert result["probe"] == {"runner_log_fresh": False}
        assert result["queue_risk"] is True
        assert elapsed < 3
        text = ready.read_text(encoding="utf-8").split()
        parent_pid, child_pid, pgid = (int(part) for part in text)
        assert pgid == parent_pid
        assert pgid != os.getpgrp()
        _assert_group_dead(pgid)
        _assert_dead(child_pid)
        _assert_dead(parent_pid)
    finally:
        if pgid is not None and pgid != os.getpgrp():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_probe_self_hosted_log_read_past_budget_is_offline(tmp_path: Path, monkeypatch):
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    log = runner / "_diag" / "Runner_20241010-213029-utc.log"
    _write_runner_log(log, _RUNNER_LOG, now - 5)
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"])
    real_open = Path.open

    def opening(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self == log:
            original = handle.read

            def blocked(size=-1):
                time.sleep(0.6)
                return original(size)

            handle.read = blocked
        return handle

    monkeypatch.setattr(Path, "open", opening)
    started = time.monotonic()
    result = probe_self_hosted(
        tmp_path,
        [_listener(runner)],
        now=now,
        timeout_s=0.1,
    )
    elapsed = time.monotonic() - started
    _assert_probe_result(result, "self_hosted")
    assert result["error"]["code"] == "runner_offline"
    assert result["probe"] == {"runner_log_fresh": False}
    assert result["ok"] is False
    assert elapsed < 3


def test_list_linux_processes_stops_at_deadline(tmp_path: Path):
    import memory.local_mcp.probe as probe_mod

    proc_root = tmp_path / "proc"
    pid = proc_root / "1"
    pid.mkdir(parents=True)
    (pid / "comm").write_text("sleep\n", encoding="utf-8")
    with pytest.raises(probe_mod._BudgetExpired):
        list_linux_processes(proc_root, deadline=time.monotonic() - 1)


def test_probe_self_hosted_without_unit_does_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    runner = tmp_path / "runner"
    now = 1_700_000_000.0
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text("{}\n", encoding="utf-8")
    _runner_config(tmp_path, runner, labels=["linux"])
    result = probe_self_hosted(tmp_path, [_listener(runner)], now=now)
    _assert_probe_result(result, "self_hosted")
    assert result["ok"] is True
    assert result["queue_risk"] is False


def test_list_linux_processes_reads_fake_proc(tmp_path: Path):
    proc_root = tmp_path / "proc"
    runner = tmp_path / "runner"
    exe_target = runner / "bin" / "Runner.Listener"
    exe_target.parent.mkdir(parents=True)
    exe_target.write_text("", encoding="utf-8")
    runner.mkdir(exist_ok=True)
    pid = proc_root / "4321"
    pid.mkdir(parents=True)
    (pid / "comm").write_text("Runner.Listener\n", encoding="utf-8")
    (pid / "exe").symlink_to(exe_target)
    (pid / "cwd").symlink_to(runner)
    (proc_root / "not-a-pid").mkdir()
    (proc_root / "99").write_text("file", encoding="utf-8")
    rows = list_linux_processes(proc_root)
    assert len(rows) == 1
    assert rows[0]["comm"] == "Runner.Listener"
    assert rows[0]["exe"] is not None and Path(rows[0]["exe"]).resolve() == exe_target.resolve()
    assert rows[0]["cwd"] is not None and Path(rows[0]["cwd"]).resolve() == runner.resolve()


def test_probe_cli_self_hosted_uses_lister_not_inline_proc(monkeypatch, tmp_path: Path, capsys):
    import memory.local_mcp.probe as probe_mod

    called = {}

    def fake_list(proc_root: Path = Path("/proc"), **_kwargs):
        called["root"] = proc_root
        called["kwargs"] = dict(_kwargs)
        return []

    monkeypatch.setattr(probe_mod, "list_linux_processes", fake_list)
    code = local_mcp_main(["probe", "--backend", "self_hosted", "--workdir", str(tmp_path)])
    captured = capsys.readouterr()
    assert called["root"] == Path("/proc")
    assert code == 2
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    payload = json.loads(captured.out)
    assert code == payload["exit_code"]
    _assert_probe_result(payload, "self_hosted")
    assert payload["error"]["code"] == "runner_log_missing"


def _jenkins_server(spec: dict):
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.server.hits += 1
            self.server.paths.append(self.path)
            self.server.auth = self.headers.get("Authorization")
            code = spec["status"]
            if code in (301, 302, 303, 307, 308):
                self.send_response(code)
                self.send_header("Location", spec["location"])
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            body = spec.get("body", b'{"mode":"NORMAL"}')
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    @contextmanager
    def _running():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.hits = 0
        httpd.paths = []
        httpd.auth = None
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    return _running()


def test_probe_jenkins_200_401_403_and_redirect(tmp_path: Path):
    import base64

    with _jenkins_server({"status": 200, "body": b'{"mode":"NORMAL","secret":"%s"}' % _FAKE_JENKINS_TOKEN.encode()}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            tmp_path,
            {
                "local_pipeline": {
                    "enabled": False,
                    "backend": "jenkins",
                    "jenkins": {"url": base, "job_path": "team/job", "jenkinsfile_path": "Jenkinsfile"},
                }
            },
        )
        user = "alice"
        token = _FAKE_JENKINS_TOKEN
        result = probe_jenkins(
            tmp_path,
            environ={"JENKINS_USER": user, "JENKINS_API_TOKEN": token, "JENKINS_URL": "http://127.0.0.1:1"},
        )
        _assert_probe_result(result, "jenkins")
        assert result["ok"] is True
        assert result["skipped"] is False
        assert result["error"] is None
        assert result["probe"] == {"jenkins": True}
        assert result["config_backend"] == "jenkins"
        assert result["status_target"] == "bitbucket_commit_status"
        assert result["scm"]["host"] == "bitbucket"
        assert result["job"] == {"job_path": "team/job"}
        expected = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode("ascii")
        assert server.auth == expected
        assert server.hits == 1
        assert server.paths == ["/api/json"]
        assert token not in json.dumps(result)
        assert expected not in json.dumps(result)

    big = tmp_path / "over-one-mib"
    big.mkdir()
    pad = "j" * (1024 * 1024 + 4096)
    body = ('{"mode":"NORMAL","job":"' + pad + '"}').encode()
    assert len(body) > 1024 * 1024
    with _jenkins_server({"status": 200, "body": body}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            big,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        result = probe_jenkins(big, timeout_s=5.0, environ={})
        _assert_probe_result(result, "jenkins")
        assert result["ok"] is True
        assert result["error"] is None
        assert result["probe"] == {"jenkins": True}
        assert server.paths == ["/api/json"]
        assert "jjjj" not in json.dumps(result)

    only_token = tmp_path / "only-token"
    only_token.mkdir()
    with _jenkins_server({"status": 200}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            only_token,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        result = probe_jenkins(only_token, environ={"JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN})
        _assert_probe_result(result, "jenkins")
        assert result["ok"] is True
        assert server.auth is None
        assert result["job"] == {"job_path": ""}

    for status in (401, 403):
        work = tmp_path / f"auth-{status}"
        work.mkdir()
        with _jenkins_server({"status": status, "body": b"denied auth-body-marker"}) as server:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            _write_project_config(
                work,
                {
                    "local_pipeline": {
                        "enabled": False,
                        "backend": "jenkins",
                        "jenkins": {"url": base, "jenkinsfile_path": "Jenkinsfile"},
                    }
                },
            )
            result = probe_jenkins(work, environ={})
            _assert_probe_result(result, "jenkins")
            assert result["error"] == {
                "code": "jenkins_auth",
                "message": "Fix JENKINS_USER / JENKINS_API_TOKEN in the environment.",
            }
            assert result["job"] == {"jenkinsfile_path": "Jenkinsfile"}
            assert "auth-body-marker" not in json.dumps(result)
            assert server.hits == 1


def test_probe_jenkins_drip_does_not_reset_the_deadline(tmp_path: Path):
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = b'{"a":1}'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for index, byte in enumerate(payload):
                    if index:
                        time.sleep(0.45)
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
            except OSError:
                return

        def log_message(self, fmt, *args):
            return

    @contextmanager
    def _running():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    with _running() as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            tmp_path,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        started = time.monotonic()
        result = probe_jenkins(tmp_path, timeout_s=0.6, environ={})
        elapsed = time.monotonic() - started
        _assert_probe_result(result, "jenkins")
        assert result["error"]["code"] == "jenkins_unreachable"
        assert elapsed < 2.0


def test_probe_jenkins_redirect_is_not_followed(tmp_path: Path):
    with _jenkins_server({"status": 302, "location": "http://example.test/api/json"}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            tmp_path,
            {"local_pipeline": {"enabled": True, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        result = probe_jenkins(tmp_path, timeout_s=2.0, environ={})
        _assert_probe_result(result, "jenkins")
        assert result["error"] == {
            "code": "jenkins_redirect_rejected",
            "message": "Pin jenkins.url to the controller origin.",
        }
        assert server.hits == 1
        assert server.paths == ["/api/json"]


def test_probe_jenkins_same_origin_redirect_is_unreachable(tmp_path: Path):
    holder: dict[str, str] = {}

    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.server.hits += 1
            self.send_response(302)
            self.send_header("Location", holder["location"])
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    @contextmanager
    def _running():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.hits = 0
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    with _running() as server:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        holder["location"] = f"{base}/elsewhere"
        _write_project_config(
            tmp_path,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        result = probe_jenkins(tmp_path, environ={})
        _assert_probe_result(result, "jenkins")
        assert result["error"]["code"] == "jenkins_unreachable"
        assert server.hits == 1


def test_probe_jenkins_bad_port_does_not_raise(tmp_path: Path):
    token = "userinfo-token-9f3a"
    _write_project_config(
        tmp_path,
        {
            "local_pipeline": {
                "enabled": False,
                "backend": "jenkins",
                "jenkins": {"url": f"http://alice:{token}@127.0.0.1:99999"},
            }
        },
    )
    result = probe_jenkins(tmp_path, environ={})
    _assert_probe_result(result, "jenkins")
    assert result["error"]["code"] == "jenkins_unreachable"
    assert token not in result["error"]["message"]
    assert "alice" not in result["error"]["message"]
    assert "99999" not in result["error"]["message"]

    work = tmp_path / "redirect-port"
    work.mkdir()
    with _jenkins_server(
        {"status": 302, "location": "http://example.test:99999/api/json"}
    ) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            work,
            {
                "local_pipeline": {
                    "enabled": False,
                    "backend": "jenkins",
                    "jenkins": {"url": base},
                }
            },
        )
        redirected = probe_jenkins(work, environ={})
        _assert_probe_result(redirected, "jenkins")
        assert redirected["error"]["code"] == "jenkins_redirect_rejected"
        assert "99999" not in redirected["error"]["message"]
        assert server.hits == 1


def test_probe_jenkins_closed_port_non_json_and_missing_url(tmp_path: Path):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    _write_project_config(
        tmp_path,
        {
            "local_pipeline": {
                "enabled": False,
                "backend": "act",
                "jenkins": {"url": f"http://127.0.0.1:{port}"},
            }
        },
    )
    closed = probe_jenkins(tmp_path, timeout_s=1.0, environ={})
    _assert_probe_result(closed, "jenkins")
    assert closed["error"]["code"] == "jenkins_unreachable"
    assert closed["config_backend"] == "act"
    assert closed["probe"] == {"jenkins": False}

    work = tmp_path / "array"
    work.mkdir()
    with _jenkins_server({"status": 200, "body": b"[1,2]"}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            work,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        bad = probe_jenkins(work, environ={})
        _assert_probe_result(bad, "jenkins")
        assert bad["error"]["code"] == "jenkins_unreachable"

    plain = tmp_path / "plain"
    plain.mkdir()
    with _jenkins_server({"status": 200, "body": b"not-json"}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            plain,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": base}}},
        )
        text = probe_jenkins(plain, environ={})
        assert text["error"]["code"] == "jenkins_unreachable"

    missing = tmp_path / "missing-url"
    missing.mkdir()
    _write_project_config(
        missing,
        {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": None}}},
    )
    none = probe_jenkins(missing, environ={})
    _assert_probe_result(none, "jenkins")
    assert none["error"] == {
        "code": "jenkins_unreachable",
        "message": "Controller down, or URL/DNS/firewall.",
    }

    from_env = tmp_path / "from-env"
    from_env.mkdir()
    with _jenkins_server({"status": 200}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            from_env,
            {"local_pipeline": {"enabled": False, "backend": "jenkins", "jenkins": {"url": None}}},
        )
        found = probe_jenkins(from_env, environ={"JENKINS_URL": base})
        _assert_probe_result(found, "jenkins")
        assert found["ok"] is True
        assert server.paths == ["/api/json"]

    file_url = tmp_path / "file-url"
    file_url.mkdir()
    secret_file = file_url / "secret.txt"
    secret_file.write_text("file-secret-marker", encoding="utf-8")
    _write_project_config(
        file_url,
        {
            "local_pipeline": {
                "enabled": False,
                "backend": "jenkins",
                "jenkins": {"url": secret_file.as_uri()},
            }
        },
    )
    rejected = probe_jenkins(file_url, environ={})
    _assert_probe_result(rejected, "jenkins")
    assert rejected["error"]["code"] == "jenkins_unreachable"
    assert "file-secret-marker" not in json.dumps(rejected)


def test_probe_jenkins_cli_stderr_has_no_token(tmp_path: Path):
    with _jenkins_server({"status": 200, "body": b'{"mode":"NORMAL"}'}) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _write_project_config(
            tmp_path,
            {
                "local_pipeline": {
                    "enabled": False,
                    "backend": "jenkins",
                    "jenkins": {"url": base, "job_path": "team/job"},
                }
            },
        )
        env = os.environ.copy()
        env["JENKINS_USER"] = "alice"
        env["JENKINS_API_TOKEN"] = _FAKE_JENKINS_TOKEN
        env.pop("JENKINS_URL", None)
        proc = _probe_proc(tmp_path, "--backend", "jenkins", env=env)
    assert proc.returncode == 0, proc.stderr
    assert _FAKE_JENKINS_TOKEN not in proc.stdout
    assert _FAKE_JENKINS_TOKEN not in proc.stderr
    assert "Basic " not in proc.stderr
    assert "Authorization" not in proc.stderr
    assert proc.stdout.endswith("\n")
    assert proc.stdout.count("\n") == 1
    payload = json.loads(proc.stdout)
    _assert_probe_result(payload, "jenkins")
    assert payload["ok"] is True
    assert payload["error"] is None
    assert "local_pipeline_disabled" not in proc.stdout


def _write_exec(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def test_knowledge_query_ignores_db_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    other = tmp_path / "other" / "knowledge.sqlite"
    monkeypatch.setenv("AGENTIX_KNOWLEDGE_DB", str(other))
    called = {"n": 0}

    def boom(*args, **kwargs):
        del args, kwargs
        called["n"] += 1
        raise AssertionError("db_path called")

    monkeypatch.setattr("memory.knowledge.db_path", boom)
    missing = dispatch("knowledge_query", {"q": "hello"}, workdir=work)
    assert missing.is_error is True
    assert missing.structured["error"]["code"] == "knowledge_db_missing"
    assert not other.exists()
    assert not (work / ".agent" / "knowledge").exists()
    assert called["n"] == 0

    forced = work / ".agent" / "knowledge" / "knowledge.sqlite"
    forced.parent.mkdir(parents=True)
    # Header only: query is mocked. An empty file is knowledge_db_missing.
    forced.write_bytes(b"SQLite format 3\x00")
    seen: dict[str, Any] = {}

    def fake_query(*, q=None, category=None, source=None, top=5, db=None, readonly=False):
        del category, source, readonly
        seen["q"] = q
        seen["top"] = top
        seen["db"] = db
        return [{"title": "t"}]

    monkeypatch.setattr("memory.knowledge.query", fake_query)
    found = dispatch(
        "knowledge_query",
        {"q": "hello", "top": 2, "db": str(other)},
        workdir=work,
    )
    assert found.is_error is False
    assert seen["q"] == "hello"
    assert seen["top"] == 2
    assert Path(seen["db"]).resolve() == forced.resolve()
    assert not other.exists()
    assert called["n"] == 0

    stats_seen: dict[str, Any] = {}

    def fake_stats(db=None, *, readonly=False):
        del readonly
        stats_seen["db"] = db
        return {"entries": 0, "tokens": 0, "by_category": []}

    monkeypatch.setattr("memory.knowledge.stats", fake_stats)
    stats = dispatch("knowledge_stats", {"db": str(other)}, workdir=work)
    assert stats.is_error is False
    assert Path(stats_seen["db"]).resolve() == forced.resolve()
    assert called["n"] == 0


def test_knowledge_empty_or_nondb_stays_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    db = work / ".agent" / "knowledge" / "knowledge.sqlite"
    db.parent.mkdir(parents=True)

    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("db_path called")

    monkeypatch.setattr("memory.knowledge.db_path", boom)

    db.write_bytes(b"")
    for name, args in (
        ("knowledge_query", {"q": "hello"}),
        ("knowledge_stats", {}),
    ):
        result = dispatch(name, args, workdir=work)
        assert result.is_error is True
        assert result.structured["error"]["code"] == "knowledge_db_missing"
        assert db.read_bytes() == b""

    raw = b"not-a-database"
    db.write_bytes(raw)
    for name, args in (
        ("knowledge_query", {"q": "hello"}),
        ("knowledge_stats", {}),
    ):
        result = dispatch(name, args, workdir=work)
        assert result.is_error is True
        assert result.structured["error"]["code"] == "knowledge_db_missing"
        assert db.read_bytes() == raw

    corrupt = b"SQLite format 3\x00xxxx"
    db.write_bytes(corrupt)
    for name, args in (
        ("knowledge_query", {"q": "hello"}),
        ("knowledge_stats", {}),
    ):
        result = dispatch(name, args, workdir=work)
        assert result.is_error is True
        assert result.structured["error"]["code"] == "knowledge_db_missing"
        assert db.read_bytes() == corrupt
    assert not (tmp_path / "other").exists()


def test_knowledge_read_does_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import memory.knowledge as knowledge

    work = _repo(tmp_path)
    db = work / ".agent" / "knowledge" / "knowledge.sqlite"
    knowledge.upsert(
        source="docs/readme.md",
        title="Hello",
        content="world",
        db=db,
        cwd=work,
    )
    before = db.read_bytes()
    listing = sorted(p.name for p in db.parent.iterdir())

    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("db_path called")

    monkeypatch.setattr("memory.knowledge.db_path", boom)
    found = dispatch("knowledge_query", {"q": "Hello", "top": 5}, workdir=work)
    assert found.is_error is False
    titles = [row["title"] for row in found.structured["results"]]
    assert titles == ["Hello"]
    assert db.read_bytes() == before
    stats = dispatch("knowledge_stats", {}, workdir=work)
    assert stats.is_error is False
    assert stats.structured["entries"] == 1
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == listing


def test_docker_compose_up_does_not_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    record = tmp_path / "docker-argv"
    bin_dir = tmp_path / "bin"
    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(record))}\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    for action in ("up", "down", "build", "rm", "exec"):
        if record.exists():
            record.unlink()
        result = dispatch("docker_compose", {"action": action}, workdir=work)
        assert result.is_error is True
        assert result.structured["error"]["code"] == "docker_compose_rejected"
        assert result.structured["exit_code"] == 2
        assert not record.exists()


def test_docker_compose_success_returns_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    token = "ghp_FAKELOCALPIPELINETOKEN123456"
    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        "printf '%s\\n' 'LOG_MARKER_9f3a'\n"
        "printf '%s\\n' 'err-note' >&2\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    for action in ("logs", "ps", "config"):
        args: dict[str, Any] = {"action": action}
        if action == "logs":
            args["tail"] = 10
        result = dispatch("docker_compose", args, workdir=work)
        assert result.is_error is False
        assert result.text == "docker_compose exit=0"
        assert result.structured["ok"] is True
        assert result.structured["error"] is None
        assert result.structured["exit_code"] == 0
        assert result.structured["stdout"] == "LOG_MARKER_9f3a\n"
        assert result.structured["stderr"] == "err-note\n"
        assert "tail" not in result.structured
        assert "LOG_MARKER_9f3a" in json.dumps(result.structured)

    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        f"printf '%s\\n' 'LOG_MARKER_9f3a {token}'\n"
        "exit 0\n",
    )
    redacted = dispatch(
        "docker_compose", {"action": "logs", "tail": 1}, workdir=work
    )
    assert redacted.is_error is False
    assert redacted.structured["exit_code"] == 0
    blob = json.dumps(redacted.structured)
    assert token not in blob
    assert "ghp_" not in blob
    assert "LOG_MARKER_9f3a" in redacted.structured["stdout"]
    assert "stderr" not in redacted.structured

    fake = tmp_path / "py"
    _write_exec(
        fake,
        "#!/bin/sh\nprintf '%s\\n' 'PYTEST_MARKER_9f3a'\nexit 0\n",
    )
    monkeypatch.setattr(sys, "executable", str(fake))
    py = dispatch(
        "pytest_run",
        {"paths": ["tests/test_a.py"], "flags": ["-q"]},
        workdir=work,
    )
    assert py.is_error is False
    assert py.text == "pytest_run exit=0"
    assert set(py.structured) == {"ok", "error", "exit_code"}
    assert "PYTEST_MARKER_9f3a" not in json.dumps(py.structured)
    assert "stdout" not in py.structured

    monkeypatch.setattr(
        "memory.local_mcp.tools.local.redact_text",
        lambda text: text,
    )
    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        "printf '%s\\n' '-----BEGIN PRIVATE KEY-----'\n"
        "printf '%s\\n' 'ghp_STILLHERE' >&2\n"
        "exit 0\n",
    )
    hidden = dispatch("docker_compose", {"action": "ps"}, workdir=work)
    assert hidden.is_error is False
    assert hidden.text == "docker_compose exit=0"
    assert hidden.structured["exit_code"] == 0
    assert "stdout" not in hidden.structured
    assert "stderr" not in hidden.structured
    hidden_blob = json.dumps(hidden.structured)
    assert "-----BEGIN " not in hidden_blob
    assert "ghp_" not in hidden_blob


def test_pytest_run_argv_and_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    record = tmp_path / "argv.txt"
    fake = tmp_path / "py"
    _write_exec(
        fake,
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$0\" \"$@\" > {shlex.quote(str(record))}\n"
        "exit 0\n",
    )
    monkeypatch.setattr(sys, "executable", str(fake))

    def run(flags, paths=None):
        if record.exists():
            record.unlink()
        args: dict[str, Any] = {"flags": flags}
        if paths is not None:
            args["paths"] = paths
        return dispatch("pytest_run", args, workdir=work)

    path = work / "tests" / "test_a.py"
    result = run(["--maxfail=1"], ["tests/test_a.py"])
    assert result.is_error is False
    argv = record.read_text(encoding="utf-8").splitlines()
    assert argv[:4] == [str(fake), "-m", "pytest", str(path.resolve())]
    assert argv[4:] == ["--maxfail=1"]

    result = run(["--maxfail", "50"], ["tests/test_a.py"])
    assert result.is_error is False
    assert record.read_text(encoding="utf-8").splitlines()[-2:] == ["--maxfail", "50"]

    for rejected in (["--maxfail"], ["--maxfail=0"], ["--maxfail=51"], ["-kexpr"]):
        result = run(rejected, ["tests/test_a.py"])
        assert result.is_error is True
        assert result.structured["error"]["code"] == "pytest_flag_rejected"
        assert result.structured["exit_code"] == 2
        assert not record.exists()

    result = run(["-k", "test_ok[bar]:*"], ["tests/test_a.py"])
    assert result.is_error is False
    assert record.read_text(encoding="utf-8").splitlines()[-2:] == [
        "-k",
        "test_ok[bar]:*",
    ]

    venv_record = tmp_path / "venv-argv.txt"
    venv = work / ".venv" / "bin" / "python"
    _write_exec(
        venv,
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$0\" \"$@\" > {shlex.quote(str(venv_record))}\n"
        "exit 0\n",
    )
    if record.exists():
        record.unlink()
    result = dispatch(
        "pytest_run",
        {"paths": ["a.py"], "flags": ["-q"]},
        workdir=work,
    )
    assert result.is_error is False
    venv_argv = venv_record.read_text(encoding="utf-8").splitlines()
    assert venv_argv[0] == str(venv)
    assert venv_argv[1:3] == ["-m", "pytest"]
    assert venv_argv[3] == str((work / "a.py").resolve())
    assert venv_argv[4:] == ["-q"]
    assert not record.exists()


def test_pytest_run_pipeline_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    (agent / "local_pipeline.lock").write_text(
        f"{os.getpid()} token\n", encoding="ascii"
    )
    record = tmp_path / "argv.txt"
    fake = tmp_path / "py"
    _write_exec(
        fake,
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$0\" \"$@\" > {shlex.quote(str(record))}\n"
        "exit 0\n",
    )
    monkeypatch.setattr(sys, "executable", str(fake))
    result = dispatch(
        "pytest_run",
        {"paths": ["test_x.py"], "flags": ["-q"]},
        workdir=work,
    )
    assert result.is_error is True
    assert result.structured["error"]["code"] == "pipeline_busy"
    assert not record.exists()
    assert (agent / "local_pipeline.lock").is_file()
    assert lock_name_is_local_pipeline(agent)


def lock_name_is_local_pipeline(agent: Path) -> bool:
    from memory.agent_lock import lock_path

    return lock_path(agent, "local_pipeline") == agent / "local_pipeline.lock"


def test_git_preflight_toplevel_outside_allow_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    bin_dir = tmp_path / "bin"
    seen = tmp_path / "git-ran"
    spawned = tmp_path / "bash-ran"
    _write_exec(
        bin_dir / "git",
        "#!/bin/sh\n"
        f"printf 'ran\\n' > {shlex.quote(str(seen))}\n"
        f"printf '%s\\n' {shlex.quote(str(outside))}\n"
        "exit 0\n",
    )
    _write_exec(
        bin_dir / "bash",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(spawned))}\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    result = dispatch(
        "git_preflight",
        {"strict_multi_repo": True},
        workdir=work,
        allow_root=work,
    )
    assert result.is_error is True
    assert result.structured["error"]["code"] == "path_escape"
    assert result.structured["exit_code"] == 2
    assert seen.is_file()
    assert not spawned.exists()


def test_git_preflight_script_symlink_outside_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    outside = tmp_path / "outside"
    marker = outside / "preflight-marker"
    script = outside / "preflight_git.sh"
    _write_exec(
        script,
        "#!/bin/sh\n"
        f"printf 'ran home=%s\\n' \"$HOME\" > {shlex.quote(str(marker))}\n"
        "exit 0\n",
    )
    scripts = work / "scripts"
    scripts.mkdir()
    (scripts / "preflight_git.sh").symlink_to(script)
    bin_dir = tmp_path / "bin"
    _write_exec(
        bin_dir / "git",
        "#!/bin/sh\n"
        f"printf '%s\\n' {shlex.quote(str(work))}\n"
        "exit 0\n",
    )
    _write_exec(
        bin_dir / "bash",
        "#!/bin/sh\n"
        f"printf 'ran home=%s\\n' \"$HOME\" > {shlex.quote(str(marker))}\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", "/operator-home")
    result = dispatch(
        "git_preflight",
        {"strict_multi_repo": False},
        workdir=work,
        allow_root=work,
    )
    assert result.is_error is True
    assert result.structured["error"]["code"] == "path_escape"
    assert result.structured["exit_code"] == 2
    assert not marker.exists()


def test_playbooks_disabled_is_not_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text(
        json.dumps({"playbooks": {"enabled": False}}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def select_bullets(*args, **kwargs):
        del args, kwargs
        calls.append("select")
        return []

    def list_playbooks(*args, **kwargs):
        del args, kwargs
        calls.append("list")
        return []

    monkeypatch.setattr("memory.playbooks.select_bullets", select_bullets)
    monkeypatch.setattr("memory.playbooks.list_playbooks", list_playbooks)
    selected = dispatch("playbooks_select", {"query": "git"}, workdir=work)
    listed = dispatch("playbooks_list", {}, workdir=work)
    assert selected.is_error is True
    assert selected.structured["error"]["code"] == "playbooks_disabled"
    assert selected.structured != []
    assert listed.is_error is True
    assert listed.structured["error"]["code"] == "playbooks_disabled"
    assert calls == []


def test_playbooks_embed_does_not_call_select(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text(
        json.dumps({"playbooks": {"enabled": True, "relevance": "embed"}}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def select_bullets(*args, **kwargs):
        del args, kwargs
        calls.append("select")
        return []

    monkeypatch.setattr("memory.playbooks.select_bullets", select_bullets)
    result = dispatch("playbooks_select", {"query": "git"}, workdir=work)
    assert result.is_error is True
    assert result.structured["error"]["code"] == "playbooks_embed_refused"
    assert calls == []


def test_memory_snapshot_drops_keys_not_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    huge = "x" * 40000
    seen: dict[str, Any] = {}

    def fake_snapshot(cwd=None):
        seen["cwd"] = cwd
        return {
            "workspace_id": "ws",
            "file": "/tmp/mem.md",
            "llm_ontology": {"blob": huge},
            "recent_distillations": [{"date": "d", "summary": huge}],
            "patterns": {"tiny": [{"description": "a", "count": 1}]},
        }

    monkeypatch.setattr("memory.store.snapshot", fake_snapshot)
    result = dispatch("memory_snapshot", {}, workdir=work)
    body = result.structured
    assert isinstance(body, dict)
    assert result.is_error is False
    assert body["truncated"] is True
    assert body["dropped"][0] == "llm_ontology"
    assert body["dropped"].index("llm_ontology") < body["dropped"].index(
        "recent_distillations"
    )
    assert "llm_ontology" not in body
    assert body["workspace_id"] == "ws"
    assert body["file"] == "/tmp/mem.md"
    parsed = json.loads(json.dumps(body))
    assert isinstance(parsed, dict)
    assert parsed["truncated"] is True
    assert len(json.dumps(body).encode("utf-8")) <= 32768
    assert Path(seen["cwd"]) == work.resolve()


def test_memory_update_audit_drops_fake_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    (work / ".agent").mkdir()
    token = "ghp_FAKELOCALPIPELINETOKEN123456"
    seen: dict[str, Any] = {}

    def fake_update(new_patterns=None, distillation=None, cwd=None):
        seen["new_patterns"] = new_patterns
        seen["distillation"] = distillation
        seen["cwd"] = cwd
        return {
            "workspace_id": "ws",
            "patterns_merged": 1,
            "compaction": {},
            "file": "f",
        }

    monkeypatch.setattr("memory.store.update_memory", fake_update)
    description = "note " + token
    result = dispatch(
        "memory_update",
        {"category": "Testing", "description": description},
        workdir=work,
    )
    assert result.is_error is False
    assert seen["new_patterns"] == [
        {"category": "Testing", "description": description}
    ]
    assert seen["distillation"] is None
    assert Path(seen["cwd"]) == work.resolve()
    text = (work / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8")
    assert token not in text
    assert "ghp_" not in text
    entry = json.loads(text)["entries"][-1]
    assert entry["action"] == "local_mcp_memory_update"
    assert entry["role"] == "ci"
    assert entry["details"].get("category") == "Testing"
    assert "description" not in entry["details"]


def test_validate_handoff_loads_json_then_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    seen: dict[str, Any] = {}

    def fake_validate(data, strict_done=True):
        seen["data"] = data
        seen["strict_done"] = strict_done
        return False, ["nope"]

    monkeypatch.setattr("memory.validate_handoff.validate_handoff", fake_validate)
    payload = {"task_id": "T-1", "status": "BLOCKED"}
    (work / "h.json").write_text(json.dumps(payload), encoding="utf-8")
    loaded = dispatch(
        "validate_handoff",
        {"path": "h.json"},
        workdir=work,
    )
    assert loaded.is_error is False
    assert seen["data"] == payload
    assert seen["strict_done"] is True
    assert loaded.structured["valid"] is False
    assert loaded.structured["errors"] == ["nope"]

    inline = {"status": "IN_PROGRESS"}
    again = dispatch(
        "validate_handoff",
        {"handoff": inline, "strict_done": False},
        workdir=work,
    )
    assert again.is_error is False
    assert seen["data"] == inline
    assert seen["strict_done"] is False
    assert again.structured["valid"] is False

    seen.clear()
    outside = tmp_path / "out.json"
    outside.write_text("{}", encoding="utf-8")
    escaped = dispatch(
        "validate_handoff",
        {"path": str(outside)},
        workdir=work,
    )
    assert escaped.is_error is True
    assert escaped.structured["error"]["code"] == "path_escape"
    assert escaped.structured["exit_code"] == 2
    assert "data" not in seen


def test_memory_query_passes_top_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _repo(tmp_path)
    seen: dict[str, Any] = {}

    def fake_query(category=None, top_n=5, contains=None, cwd=None):
        seen.update(category=category, top_n=top_n, contains=contains, cwd=cwd)
        return []

    monkeypatch.setattr("memory.store.query_memory", fake_query)
    result = dispatch(
        "memory_query",
        {"top": 7, "category": "Testing", "contains": "edge"},
        workdir=work,
    )
    assert result.is_error is False
    assert seen["top_n"] == 7
    assert seen["category"] == "Testing"
    assert seen["contains"] == "edge"
    assert Path(seen["cwd"]) == work.resolve()
    omitted = dispatch("memory_query", {}, workdir=work)
    assert omitted.is_error is False
    assert seen["top_n"] == 5


def test_symlink_agent_outside_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / ".agent").symlink_to(outside)
    updates = {"n": 0}
    selects = {"n": 0}

    def fake_update(**kwargs):
        del kwargs
        updates["n"] += 1
        return {"ok": True}

    def fake_select(*args, **kwargs):
        del args, kwargs
        selects["n"] += 1
        return []

    queries = {"n": 0}

    def fake_query(**kwargs):
        del kwargs
        queries["n"] += 1
        return []

    monkeypatch.setattr("memory.store.update_memory", fake_update)
    monkeypatch.setattr("memory.playbooks.select_bullets", fake_select)
    monkeypatch.setattr("memory.knowledge.query", fake_query)
    delta = dispatch(
        "state_append_delta",
        {"text": "n", "role": "Coder"},
        workdir=work,
    )
    assert delta.is_error is True
    assert delta.structured["error"]["code"] == "path_escape"
    assert delta.structured["exit_code"] == 2
    assert list(outside.iterdir()) == []
    updated = dispatch(
        "memory_update",
        {"category": "Testing", "description": "x"},
        workdir=work,
    )
    assert updated.structured["error"]["code"] == "path_escape"
    assert updated.structured["exit_code"] == 2
    assert updates["n"] == 0
    assert not (outside / "AUDIT_LOG.json").exists()
    selected = dispatch("playbooks_select", {"query": "git"}, workdir=work)
    assert selected.structured["error"]["code"] == "path_escape"
    assert selects["n"] == 0
    queried = dispatch("knowledge_query", {"q": "hello"}, workdir=work)
    assert queried.structured["error"]["code"] == "path_escape"
    assert queries["n"] == 0
    assert not (outside / "knowledge").exists()
    assert list(outside.iterdir()) == []


def test_symlink_knowledge_db_outside_is_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_db = outside / "knowledge.sqlite"
    outside_db.write_bytes(b"")
    agent = work / ".agent"
    agent.mkdir()
    (agent / "knowledge").symlink_to(outside)
    calls = {"n": 0}

    def fake_query(**kwargs):
        del kwargs
        calls["n"] += 1
        return []

    def fake_stats(**kwargs):
        del kwargs
        calls["n"] += 1
        return {"entries": 0}

    monkeypatch.setattr("memory.knowledge.query", fake_query)
    monkeypatch.setattr("memory.knowledge.stats", fake_stats)
    queried = dispatch("knowledge_query", {"q": "hello"}, workdir=work)
    assert queried.is_error is True
    assert queried.structured["error"]["code"] == "path_escape"
    assert queried.structured["exit_code"] == 2
    assert calls["n"] == 0
    assert outside_db.read_bytes() == b""
    stats = dispatch("knowledge_stats", {}, workdir=work)
    assert stats.structured["error"]["code"] == "path_escape"
    assert calls["n"] == 0
    assert outside_db.read_bytes() == b""


def test_proxy_stats_symlink_cache_is_path_escape(tmp_path: Path):
    work = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_db = outside / "proxy_cache.sqlite"
    outside_db.write_bytes(b"")
    agent = work / ".agent"
    agent.mkdir()
    (agent / "proxy_cache.sqlite").symlink_to(outside_db)
    stats = dispatch("proxy_stats", {}, workdir=work)
    assert stats.is_error is True
    assert stats.structured["error"]["code"] == "path_escape"
    assert stats.structured["exit_code"] == 2
    assert outside_db.read_bytes() == b""

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").mkdir()
    outside_agent = tmp_path / "outside-agent"
    outside_agent.mkdir()
    (linked / ".agent").symlink_to(outside_agent)
    again = dispatch("proxy_stats", {}, workdir=linked)
    assert again.is_error is True
    assert again.structured["error"]["code"] == "path_escape"
    assert again.structured["exit_code"] == 2
    assert not (outside_agent / "proxy_cache.sqlite").exists()
    assert list(outside_agent.iterdir()) == []
    health = dispatch("proxy_health", {}, workdir=linked)
    assert health.is_error is True
    assert health.structured["error"]["code"] == "path_escape"
    assert health.structured["exit_code"] == 2
    assert list(outside_agent.iterdir()) == []


def test_proxy_bad_port_is_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text(
        json.dumps({"proxy": {"pxpipe_base": "http://127.0.0.1:99999"}}),
        encoding="utf-8",
    )
    for key in (
        "AGENTIX_PXPIPE_URL",
        "AGENTIX_GATEWAY_URL",
        "GROK_CLI_CHAT_PROXY_BASE_URL",
        "AGENTIX_PROXY",
        "AGENTIX_PROXY_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    health = dispatch("proxy_health", {}, workdir=work)
    assert health.is_error is True
    assert health.structured["error"]["code"] == "proxy_probe_failed"
    stats = dispatch("proxy_stats", {}, workdir=work)
    assert stats.is_error is True
    assert stats.structured["error"]["code"] == "proxy_probe_failed"


def test_proxy_non_loopback_host_is_not_dialed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    dials: list = []

    def record(address, *args, **kwargs):
        del args, kwargs
        dials.append(address)
        raise OSError("blocked")

    monkeypatch.setattr("memory.proxy.health.socket.create_connection", record)
    keys = (
        "AGENTIX_PXPIPE_URL",
        "AGENTIX_GATEWAY_URL",
        "GROK_CLI_CHAT_PROXY_BASE_URL",
        "AGENTIX_PROXY",
        "AGENTIX_PROXY_MODE",
    )

    def reset_env() -> None:
        for key in keys:
            monkeypatch.delenv(key, raising=False)
        dials.clear()

    def write_proxy(proxy: dict) -> None:
        (agent / "project_config.json").write_text(
            json.dumps({"proxy": proxy}),
            encoding="utf-8",
        )

    def assert_refused(result) -> None:
        assert result.is_error is True
        assert result.structured["error"]["code"] == "proxy_probe_failed"
        blob = result.text + json.dumps(result.structured)
        assert "192.0.2.1" not in blob
        assert "198.51.100.1" not in blob

    def assert_not_external() -> None:
        for address in dials:
            assert "192.0.2.1" not in str(address)
            assert "198.51.100.1" not in str(address)

    reset_env()
    write_proxy(
        {"pxpipe_base": "http://192.0.2.1:9", "listen": "198.51.100.1:9"}
    )
    assert_refused(dispatch("proxy_health", {}, workdir=work))
    assert_refused(dispatch("proxy_stats", {}, workdir=work))
    assert dials == []

    reset_env()
    write_proxy(
        {
            "pxpipe_base": "http://127.0.0.1:8100",
            "listen": "127.0.0.1:8110",
        }
    )
    monkeypatch.setenv("AGENTIX_PXPIPE_URL", "http://192.0.2.1:9")
    monkeypatch.setenv("AGENTIX_GATEWAY_URL", "http://198.51.100.1:9")
    assert_refused(dispatch("proxy_health", {}, workdir=work))
    assert_refused(dispatch("proxy_stats", {}, workdir=work))
    assert dials == []
    assert_not_external()

    reset_env()
    write_proxy({"pxpipe_base": "http://127.0.0.1:1", "listen": "127.0.0.1:1"})
    monkeypatch.setenv("GROK_CLI_CHAT_PROXY_BASE_URL", "http://192.0.2.1:9/v1")
    assert_refused(dispatch("proxy_health", {}, workdir=work))
    assert dials == []

    reset_env()
    write_proxy({"pxpipe_base": "http://localhost:1", "listen": "[::1]:1"})
    health = dispatch("proxy_health", {}, workdir=work)
    assert health.structured.get("error", {}).get("code") != "proxy_probe_failed"
    assert dials
    assert {addr[0] for addr in dials} <= {"127.0.0.1", "::1", "localhost"}
    assert_not_external()


def test_embedded_nul_path_is_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    record = tmp_path / "argv.txt"
    fake = tmp_path / "py"
    _write_exec(
        fake,
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$0\" \"$@\" > {shlex.quote(str(record))}\n"
        "exit 0\n",
    )
    monkeypatch.setattr(sys, "executable", str(fake))
    handoff = dispatch(
        "validate_handoff",
        {"path": "a\x00.json"},
        workdir=work,
    )
    assert handoff.is_error is True
    assert handoff.structured["error"]["code"] == "path_escape"
    assert handoff.structured["exit_code"] == 2
    pytest_result = dispatch(
        "pytest_run",
        {"paths": ["a\x00.py"], "flags": ["-q"]},
        workdir=work,
    )
    assert pytest_result.is_error is True
    assert pytest_result.structured["error"]["code"] == "path_escape"
    assert pytest_result.structured["exit_code"] == 2
    assert not record.exists()


def test_subprocess_failure_tail_redacts_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    token = "ghp_FAKELOCALPIPELINETOKEN123456"
    bin_dir = tmp_path / "bin"
    _write_exec(
        bin_dir / "docker",
        "#!/bin/sh\n"
        f"printf '%s\\n' 'compose failed {token}' >&2\n"
        "exit 1\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    result = dispatch("docker_compose", {"action": "ps"}, workdir=work)
    assert result.is_error is True
    assert result.structured["error"]["code"] == "docker_compose_failed"
    assert token not in result.text
    assert "\n" not in result.text
    blob = json.dumps(result.structured)
    assert token not in blob
    assert "ghp_" not in blob
    assert "compose failed" in result.structured["tail"]


def test_memory_snapshot_packed_size_and_partial_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work = _repo(tmp_path)
    snap = {"workspace_id": "ws", "file": "f", "blob": ""}
    while len(json.dumps(snap, ensure_ascii=False).encode("utf-8")) < 32760:
        snap["blob"] += "x"
    packed = dict(snap)
    packed["truncated"] = False
    packed["dropped"] = []
    assert len(json.dumps(snap, ensure_ascii=False).encode("utf-8")) <= 32768
    assert len(json.dumps(packed, ensure_ascii=False).encode("utf-8")) > 32768

    def fake_snapshot(cwd=None):
        del cwd
        return dict(snap)

    monkeypatch.setattr("memory.store.snapshot", fake_snapshot)
    bounded = dispatch("memory_snapshot", {}, workdir=work)
    body = bounded.structured
    assert body["truncated"] is True
    assert "blob" in body["dropped"]
    assert "blob" not in body
    assert len(json.dumps(body, ensure_ascii=False).encode("utf-8")) <= 32768

    partial = {
        "workspace_id": "ws",
        "file": "f",
        "llm_ontology": {"blob": "y" * 40000},
        "recent_distillations": [
            {"date": "d", "summary": "ok"},
            {"date": "d", "summary": "z" * 40000},
        ],
    }

    def fake_partial(cwd=None):
        del cwd
        return partial

    monkeypatch.setattr("memory.store.snapshot", fake_partial)
    again = dispatch("memory_snapshot", {}, workdir=work)
    kept = again.structured
    assert kept["truncated"] is True
    assert kept["dropped"].count("recent_distillations") == 1
    assert kept["dropped"].index("llm_ontology") < kept["dropped"].index(
        "recent_distillations"
    )
    assert len(kept["recent_distillations"]) == 1
    assert len(json.dumps(kept, ensure_ascii=False).encode("utf-8")) <= 32768


def test_tool_modules_have_no_print_call():
    root = REPO / "memory" / "local_mcp" / "tools"
    sources = list(root.glob("*.py"))
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "print(" not in text


import re
import signal
import tempfile

_HARNESS_YML = (
    "name: t\n"
    "on: push\n"
    "jobs:\n"
    "  harness:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - uses: actions/setup-python@v5\n"
)
_DEFAULT_IMAGE = "catthehacker/ubuntu:act-22.04"
_DOCKER_SRC = """\
import json, os, sys, time
from pathlib import Path
record = Path(__RECORD__)
missing = Path(__MISSING__)
slow = Path(__SLOW__)
dead = Path(__DEAD__)
cid = __CID__
argv = sys.argv[1:]
with record.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "HOME": os.environ.get("HOME"), "XDG": os.environ.get("XDG_CONFIG_HOME"), "DOCKER_HOST": os.environ.get("DOCKER_HOST")}) + "\\n")
if argv[:1] == ["info"]:
    sys.stdout.write("Server Version: 24.0.9\\n")
    raise SystemExit(0)
if argv[:2] == ["image", "inspect"]:
    if slow.is_file():
        time.sleep(30)
    if dead.is_file():
        host = os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
        sock = host[len("unix://"):] if host.startswith("unix://") else host
        sys.stderr.write(
            "failed to connect to the docker API at "
            + host
            + "; check if the path is correct and if the daemon is running: dial unix "
            + sock
            + ": connect: no such file or directory\\n"
        )
        raise SystemExit(1)
    if missing.is_file():
        name = argv[-1] if argv else ""
        sys.stderr.write("Error response from daemon: No such image: " + name + "\\n")
        raise SystemExit(1)
    raise SystemExit(0)
if argv[:1] == ["ps"]:
    sys.stdout.write(cid + "\\n")
    raise SystemExit(0)
if argv[:2] == ["rm", "-f"]:
    raise SystemExit(0)
sys.stderr.write("unexpected\\n")
raise SystemExit(1)
"""
_ACT_SRC = """\
import json, os, signal, stat, sys, time
from pathlib import Path
record = Path(__RECORD__)
ready = Path(__READY__)
mode_file = Path(__MODE__)
legacy = Path(__LEGACY__)
help_status = Path(__HELP__)
argv = sys.argv[1:]
entry = {"argv": argv}
if "push" in argv:
    entry["cwd"] = os.getcwd()
    entry["HOME"] = os.environ.get("HOME")
    entry["XDG"] = os.environ.get("XDG_CONFIG_HOME")
    entry["env_keys"] = sorted(os.environ)
    files = {}
    for flag in ("--secret-file", "--env-file", "--var-file", "--input-file"):
        if flag in argv:
            path = argv[argv.index(flag) + 1]
            st = os.stat(path)
            files[flag] = {"path": path, "size": st.st_size, "mode": stat.S_IMODE(st.st_mode)}
    entry["files"] = files
with record.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\\n")
if "--version" in argv:
    sys.stdout.write("act version 0.2.70\\n")
    raise SystemExit(0)
if "--help" in argv:
    if help_status.is_file():
        status = help_status.read_text(encoding="utf-8").strip()
        if status == "fail":
            sys.stderr.write("help-broke\\n")
            raise SystemExit(1)
        if status == "sleep":
            time.sleep(30)
            raise SystemExit(0)
    text = "usage\\n"
    if not legacy.is_file():
        text += "--container-daemon-socket\\n"
    sys.stdout.write(text)
    raise SystemExit(0)
mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.is_file() else "ok"
if mode == "fail":
    sys.stderr.write("only-err\\n")
    raise SystemExit(3)
if mode == "sleep":
    child = os.fork()
    if child == 0:
        time.sleep(120)
        os._exit(0)
    ready.write_text(
        json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "child": child}),
        encoding="utf-8",
    )
    time.sleep(120)
    raise SystemExit(0)
if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.write_text(
        json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "t": time.time()}),
        encoding="utf-8",
    )
    time.sleep(120)
    raise SystemExit(0)
if mode == "both":
    sys.stdout.write("hello-out\\n")
    sys.stderr.write("hello-err\\n")
    raise SystemExit(0)
sys.stdout.write("hello-out\\n")
raise SystemExit(0)
"""


def _jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _enable_act(work: Path, act: dict | None = None) -> None:
    body: dict = {"enabled": True, "backend": "act"}
    if act:
        body["act"] = act
    _write_project_config(work, {"local_pipeline": body})


def _workflow(work: Path, text: str = _HARNESS_YML) -> Path:
    path = work / ".github" / "workflows" / "agentix-loop.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _install_backend_fakes(bin_dir: Path, record_dir: Path) -> dict:
    bin_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "docker": record_dir / "docker.jsonl",
        "act": record_dir / "act.jsonl",
        "ready": record_dir / "ready.json",
        "mode": record_dir / "mode",
        "legacy": record_dir / "legacy",
        "missing": record_dir / "missing-image",
        "slow": record_dir / "slow-inspect",
        "dead": record_dir / "dead-socket",
        "help": record_dir / "help-status",
    }
    docker = (
        _DOCKER_SRC.replace("__RECORD__", json.dumps(str(paths["docker"])))
        .replace("__MISSING__", json.dumps(str(paths["missing"])))
        .replace("__SLOW__", json.dumps(str(paths["slow"])))
        .replace("__DEAD__", json.dumps(str(paths["dead"])))
        .replace("__CID__", json.dumps("c0ffee123"))
    )
    act = (
        _ACT_SRC.replace("__RECORD__", json.dumps(str(paths["act"])))
        .replace("__READY__", json.dumps(str(paths["ready"])))
        .replace("__MODE__", json.dumps(str(paths["mode"])))
        .replace("__LEGACY__", json.dumps(str(paths["legacy"])))
        .replace("__HELP__", json.dumps(str(paths["help"])))
    )
    _write_exe(bin_dir / "docker", docker)
    _write_exe(bin_dir / "act", act)
    return paths


def _reset_fakes(paths: dict) -> None:
    for key in ("docker", "act", "ready", "mode", "legacy", "missing", "slow", "dead", "help"):
        path = paths[key]
        if path.is_file() or path.is_symlink():
            path.unlink()


def _fake_env(bin_dir: Path, **extra: str) -> dict:
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env.pop("ACT_SECRETS_FILE", None)
    env.update(extra)
    return env


def _pushes(path: Path) -> list:
    return [row for row in _jsonl(path) if "push" in row.get("argv", [])]


def _run_act(work: Path, bin_dir: Path, arguments: dict | None = None, **extra: str) -> dict:
    args = {"backend": "act", "working_directory": str(work)}
    if arguments:
        args.update(arguments)
    return run_local_pipeline(
        args,
        allow_root=work,
        environ=_fake_env(bin_dir, **extra),
    )


def _audit_entries(work: Path) -> list:
    path = work / ".agent" / "AUDIT_LOG.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _prepare_act(tmp_path: Path, name: str, act: dict | None = None, text: str = _HARNESS_YML):
    work = tmp_path / name
    work.mkdir()
    _enable_act(work, act)
    _workflow(work, text)
    (work / ".env").write_text("AWS_SECRET_ACCESS_KEY=not-a-real-secret\n", encoding="utf-8")
    (work / ".actrc").write_text("--privileged\n", encoding="utf-8")
    return work


def test_pipeline_modules_have_no_print_call():
    root = REPO / "memory" / "local_mcp"
    for rel in (
        "stream.py",
        "pipeline.py",
        "notify.py",
        "backends/act.py",
        "backends/self_hosted.py",
        "backends/jenkins.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "print(" not in text
        assert "configure_logging(" not in text


def test_act_push_argv_scratch_and_streams(tmp_path: Path, caplog):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / ".actrc").write_text("--privileged\n", encoding="utf-8")
    work = _prepare_act(tmp_path, "repo")
    (work / ".secrets").write_text("TOKEN=not-used\n", encoding="utf-8")
    paths["mode"].write_text("both", encoding="utf-8")
    _capture_local_mcp(caplog)
    env = _fake_env(bin_dir)
    env["GITHUB_TOKEN"] = _FAKE_GHP
    env["GH_TOKEN"] = "gh-not-real"
    env["LINEAR_API_KEY"] = "linear-not-real"
    env["SAFE_MARKER"] = "1"
    with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
        result = run_local_pipeline(
            {
                "backend": "act",
                "working_directory": str(work),
                "environment_variables": {"SAFE_KEY": "plain"},
            },
            allow_root=work,
            environ=env,
        )
    assert result_errors(result) == []
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["status"] == "succeeded"
    assert result["error"] is None
    assert result["skipped"] is False
    assert result["cancelled"] is False
    assert result["timed_out"] is False
    assert result["queue_risk"] is False
    assert result["status_target"] == "local"
    assert result["status_posted_by"] is None
    assert result["scm"] == {"kind": None, "host": "local", "ref": None, "commit": None}
    assert result["job"] == {
        "workflow_file": ".github/workflows/agentix-loop.yml",
        "job_name": "harness",
    }
    assert result["warnings"] == []
    assert result["probe"]["docker"] is True
    assert result["probe"]["docker_server"] == "24.0.9"
    assert result["probe"]["act_version"] == "act version 0.2.70"
    assert result["probe"]["runner_image"] == _DEFAULT_IMAGE
    assert "stdout: hello-out" in result["log_tail"]
    assert "stderr: hello-err" in result["log_tail"]
    assert not result["log_path"].startswith("/")
    assert result["log_path"].startswith(".agent/local-pipeline-runs/")
    log_file = work / result["log_path"]
    saved = json.loads(log_file.with_suffix(".json").read_text(encoding="utf-8"))
    assert saved == result
    assert (log_file.stat().st_mode & 0o777) == 0o600
    pushes = _pushes(paths["act"])
    assert len(pushes) == 1
    argv = pushes[0]["argv"]
    assert "push" in argv
    assert argv[argv.index("-C") + 1] == str(work.resolve())
    workflow = argv[argv.index("-W") + 1]
    assert Path(workflow).is_absolute()
    assert Path(workflow).is_relative_to(work.resolve())
    assert argv[argv.index("-j") + 1] == "harness"
    assert argv[argv.index("-P") + 1] == "ubuntu-latest=" + _DEFAULT_IMAGE
    socket = argv.index("--container-daemon-socket")
    assert argv[socket + 1] == "-"
    assert "--pull=false" in argv
    assert argv[argv.index("--network") + 1] == "bridge"
    assert "--no-cache-server" in argv
    assert "--privileged" not in argv
    assert "-e" not in argv
    assert "--eventpath" not in argv
    assert "-s" not in argv
    assert "--reuse" not in argv
    assert "--bind" not in argv
    assert not any("github.com" in part for part in argv)
    assert not any("AWS_SECRET_ACCESS_KEY" in part for part in argv)
    labels = [part for part in argv if part.startswith("--container-options=")]
    assert len(labels) == 1
    assert re.fullmatch(
        r"--container-options=--label=agentix\.local_mcp\.run=[0-9a-f]{8}",
        labels[0],
    )
    files = pushes[0]["files"]
    for flag, name in (
        ("--env-file", "empty.env"),
        ("--var-file", "empty.vars"),
        ("--input-file", "empty.input"),
    ):
        info = files[flag]
        assert Path(info["path"]).is_absolute()
        assert Path(info["path"]).name == name
        assert info["size"] == 0
        assert info["mode"] == 0o600
        assert info["path"] != str((work / ".env").resolve())
    secret = files["--secret-file"]
    assert secret["path"] == str((work / ".secrets").resolve())
    assert "--env=SAFE_KEY=plain" in argv
    cwd = pushes[0]["cwd"]
    assert cwd == pushes[0]["HOME"] == pushes[0]["XDG"]
    assert "agentix-act-" in cwd
    assert not Path(cwd).resolve().is_relative_to(work.resolve())
    assert Path(cwd).resolve() != home.resolve()
    assert set(pushes[0]["env_keys"]) <= {
        "PATH",
        "LANG",
        "USER",
        "LOGNAME",
        "DOCKER_HOST",
        "HOME",
        "XDG_CONFIG_HOME",
        "LC_CTYPE",
    }
    assert "SAFE_KEY" not in pushes[0]["env_keys"]
    assert "GITHUB_TOKEN" not in pushes[0]["env_keys"]
    assert "GH_TOKEN" not in pushes[0]["env_keys"]
    assert "ACT_SECRETS_FILE" not in pushes[0]["env_keys"]
    blob = json.dumps(pushes[0])
    assert _FAKE_GHP not in blob
    assert _FAKE_GHP not in json.dumps(result)
    run_entries = [entry for entry in _audit_entries(work) if entry["action"] == "local_pipeline_run"]
    assert run_entries
    assert run_entries[-1]["role"] == "ci"
    assert "error_code" not in run_entries[-1]["details"]
    assert run_entries[-1]["details"]["exit_code"] == 0
    assert set(run_entries[-1]["details"]) <= _AUDIT_DETAIL_KEYS
    infos = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.INFO]
    assert any(msg.startswith("backend=act ") and "exit_code=0" in msg for msg in infos)
    assert all("hello-out" not in msg and "log_tail" not in msg for msg in infos)
    docker_rows = _jsonl(paths["docker"])
    run_id = labels[0].rsplit("agentix.local_mcp.run=", 1)[1]
    assert any(
        row["argv"][:1] == ["ps"] and f"label=agentix.local_mcp.run={run_id}" in row["argv"]
        for row in docker_rows
    )
    assert any(row["argv"][:2] == ["rm", "-f"] and "c0ffee123" in row["argv"] for row in docker_rows)
    push_home = pushes[0]["HOME"]
    for row in docker_rows:
        argv = row["argv"]
        if argv[:2] in (["image", "inspect"], ["rm", "-f"]) or argv[:1] == ["ps"]:
            assert row["HOME"] == push_home
            assert row["XDG"] == push_home
            assert "agentix-act-" in row["HOME"]


def test_act_secret_file_matrix(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    outside = tmp_path / "outside-secret"
    outside.write_text("OUT=1\n", encoding="utf-8")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()

    def invoke(name: str, *, act: dict | None = None, env: dict | None = None, touch_secrets: bool = False):
        _reset_fakes(paths)
        work = _prepare_act(tmp_path, name, act)
        if touch_secrets:
            (work / ".secrets").write_text("FALLBACK=1\n", encoding="utf-8")
        extra = dict(env or {})
        result = _run_act(work, bin_dir, **extra)
        assert result["ok"] is True, name
        push = _pushes(paths["act"])
        assert len(push) == 1, name
        return work, push[0]

    work, row = invoke("present")
    (work / ".secrets").write_text("TOKEN=1\n", encoding="utf-8")
    # The file must exist before the run. Re-run this case in order.
    _reset_fakes(paths)
    work = _prepare_act(tmp_path, "present-file")
    secret_path = work / ".secrets"
    secret_path.write_text("TOKEN=1\n", encoding="utf-8")
    result = _run_act(work, bin_dir)
    assert result["ok"] is True
    argv = _pushes(paths["act"])[0]["argv"]
    files = _pushes(paths["act"])[0]["files"]
    assert files["--secret-file"]["path"] == str(secret_path.resolve())

    _reset_fakes(paths)
    work = _prepare_act(tmp_path, "missing-secret")
    result = _run_act(work, bin_dir)
    assert result["ok"] is True
    files = _pushes(paths["act"])[0]["files"]
    argv = _pushes(paths["act"])[0]["argv"]
    assert "--secret-file" in argv
    assert files["--secret-file"]["size"] == 0
    assert files["--secret-file"]["mode"] == 0o600
    assert Path(files["--secret-file"]["path"]).name == "empty.secrets"
    assert str((work / ".secrets").resolve()) not in argv

    _reset_fakes(paths)
    work = _prepare_act(tmp_path, "env-file")
    chosen = work / "custom.secrets"
    chosen.write_text("A=1\n", encoding="utf-8")
    (work / ".secrets").write_text("FALLBACK=1\n", encoding="utf-8")
    result = _run_act(work, bin_dir, ACT_SECRETS_FILE=str(chosen))
    assert result["ok"] is True
    files = _pushes(paths["act"])[0]["files"]
    assert files["--secret-file"]["path"] == str(chosen.resolve())

    def assert_scratch(name: str, **env: str) -> None:
        _reset_fakes(paths)
        work = _prepare_act(tmp_path, name)
        (work / ".secrets").write_text("FALLBACK=1\n", encoding="utf-8")
        result = _run_act(work, bin_dir, **env)
        assert result["ok"] is True, name
        row = _pushes(paths["act"])[0]
        info = row["files"]["--secret-file"]
        assert info["size"] == 0
        assert info["mode"] == 0o600
        assert "--secret-file" in row["argv"]
        assert str((work / ".secrets").resolve()) not in row["argv"]
        for value in env.values():
            assert value not in row["argv"]
        assert not any("AWS_SECRET_ACCESS_KEY" in part for part in row["argv"])
        assert row["files"]["--env-file"]["path"] != str((work / ".env").resolve())

    assert_scratch("env-missing", ACT_SECRETS_FILE=str(tmp_path / "no-such-secrets"))
    secret_dir = tmp_path / "secret-dir"
    secret_dir.mkdir()
    assert_scratch("env-dir", ACT_SECRETS_FILE=str(secret_dir))
    assert_scratch("env-outside", ACT_SECRETS_FILE=str(outside))

    _reset_fakes(paths)
    work = _prepare_act(tmp_path, "symlink-secret")
    link = work / ".secrets"
    link.symlink_to(outside)
    result = _run_act(work, bin_dir)
    assert result["ok"] is True
    row = _pushes(paths["act"])[0]
    assert row["files"]["--secret-file"]["size"] == 0
    assert str(outside.resolve()) not in row["argv"]
    assert str(link.resolve()) not in row["argv"]


def test_act_image_legacy_dind_and_pull(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")

    def once(name: str, act: dict | None = None, *, legacy: bool = False, missing: bool = False):
        _reset_fakes(paths)
        if legacy:
            paths["legacy"].write_text("1", encoding="utf-8")
        if missing:
            paths["missing"].write_text("1", encoding="utf-8")
        work = _prepare_act(tmp_path, name, act)
        return work, _run_act(work, bin_dir)

    _, missing_image = once("image", missing=True)
    assert missing_image["error"]["code"] == "act_image_missing"
    assert missing_image["exit_code"] == 2
    assert _DEFAULT_IMAGE in missing_image["error"]["message"]
    assert _pushes(paths["act"]) == []
    inspects = [row for row in _jsonl(paths["docker"]) if row["argv"][:2] == ["image", "inspect"]]
    assert inspects
    assert inspects[0]["argv"][:4] == ["image", "inspect", "--", _DEFAULT_IMAGE]
    assert "agentix-act-" in (inspects[0]["HOME"] or "")

    _, legacy = once("legacy", legacy=True)
    assert legacy["error"]["code"] == "act_legacy_mounts_socket"
    assert _pushes(paths["act"]) == []

    _, allowed = once("legacy-ok", {"allow_legacy_socket_mount": True}, legacy=True)
    assert allowed["ok"] is True
    argv = _pushes(paths["act"])[0]["argv"]
    assert "--container-daemon-socket" not in argv
    assert "--pull=false" in argv

    _, dind = once("dind", {"mount_docker_sock": True})
    assert dind["error"]["code"] == "dind_not_acknowledged"
    assert _pushes(paths["act"]) == []

    _, acked = once("dind-ok", {"mount_docker_sock": True, "acknowledge_dind": True})
    assert acked["ok"] is True
    assert "--container-daemon-socket" not in _pushes(paths["act"])[0]["argv"]

    _, pulled = once("pull", {"pull": True}, missing=True)
    assert pulled["ok"] is True
    argv = _pushes(paths["act"])[0]["argv"]
    assert "--pull=true" in argv
    assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))

    _, flags = once("flags", {"cache_server": True, "bind_workdir": True, "runner_image": "example.invalid/act:pin"})
    assert flags["ok"] is True
    assert flags["probe"]["runner_image"] == "example.invalid/act:pin"
    argv = _pushes(paths["act"])[0]["argv"]
    assert "--no-cache-server" not in argv
    assert "--bind" in argv
    assert argv[argv.index("-P") + 1] == "ubuntu-latest=example.invalid/act:pin"
    pin_inspects = [
        row for row in _jsonl(paths["docker"]) if row["argv"][:2] == ["image", "inspect"]
    ]
    assert pin_inspects[0]["argv"][:4] == ["image", "inspect", "--", "example.invalid/act:pin"]


def test_act_option_shaped_image_is_missing(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")

    def once(name: str, image: str) -> dict:
        _reset_fakes(paths)
        work = _prepare_act(tmp_path, name, {"runner_image": image, "pull": False})
        return _run_act(work, bin_dir)

    for name, image in (("help-flag", "--help"), ("short-help", "-h"), ("version-flag", "--version")):
        result = once(name, image)
        assert result_errors(result) == [], name
        assert result["ok"] is False
        assert result["exit_code"] == 2
        assert result["status"] == "refused"
        assert result["error"]["code"] == "act_image_missing"
        assert image in result["error"]["message"]
        assert result["probe"]["runner_image"] == image
        assert _pushes(paths["act"]) == []
        assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))


def test_act_dead_docker_socket_is_unreachable(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    paths["dead"].write_text("1", encoding="utf-8")
    work = _prepare_act(tmp_path, "dead-sock")
    host = "unix:///tmp/no-such-docker.sock"
    result = _run_act(work, bin_dir, DOCKER_HOST=host)
    assert result_errors(result) == []
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert result["status"] == "refused"
    assert result["error"]["code"] == "docker_unreachable"
    assert result["error"]["message"] == "Install and start the Docker daemon. act cannot run."
    assert "pull" not in result["error"]["message"]
    assert "not present" not in result["error"]["message"]
    assert _pushes(paths["act"]) == []
    inspects = [row for row in _jsonl(paths["docker"]) if row["argv"][:2] == ["image", "inspect"]]
    assert len(inspects) == 1
    assert inspects[0]["argv"][:4] == ["image", "inspect", "--", _DEFAULT_IMAGE]
    assert inspects[0]["DOCKER_HOST"] == host


def test_act_workflow_warning_and_strict(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    risky = _HARNESS_YML + "      - uses: ./local-action\n"
    work = _prepare_act(tmp_path, "warn", text=risky)
    warned = _run_act(work, bin_dir)
    assert warned["ok"] is True
    assert warned["warnings"] == [
        {"code": "reusable_or_composite", "message": "workflow uses a composite or reusable workflow"}
    ]
    assert _pushes(paths["act"])

    _reset_fakes(paths)
    strict_work = _prepare_act(tmp_path, "strict", {"strict_workflows": True}, text=risky)
    refused = _run_act(strict_work, bin_dir)
    assert refused["ok"] is False
    assert refused["exit_code"] == 2
    assert refused["error"]["code"] == "unsupported_workflow_feature"
    assert refused["warnings"][0]["code"] == "reusable_or_composite"
    assert _pushes(paths["act"]) == []
    assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))


def test_act_workflow_over_cap_refuses_before_docker(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    cap = 1024 * 1024
    prefix = _HARNESS_YML.encode("utf-8")
    assert b"uses: ./evil" not in prefix
    pad = b"#" * (cap - len(prefix) + 1)
    raw = prefix + pad + b"\n      - uses: ./evil\n"
    assert len(raw) > cap
    assert b"uses: ./evil" not in raw[:cap]
    text = raw.decode("utf-8")
    work = _prepare_act(tmp_path, "over-cap", {"strict_workflows": True}, text=text)
    refused = _run_act(work, bin_dir)
    assert result_errors(refused) == []
    assert refused["ok"] is False
    assert refused["exit_code"] == 2
    assert refused["status"] == "refused"
    assert refused["error"] == {
        "code": "unsupported_workflow_feature",
        "message": "Workflow exceeds 1 MiB. Split it so the whole file is scanned.",
    }
    assert refused["warnings"] == []
    assert _pushes(paths["act"]) == []
    assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))

    _reset_fakes(paths)
    open_work = _prepare_act(tmp_path, "over-cap-open", text=text)
    opened = _run_act(open_work, bin_dir)
    assert opened["error"]["code"] == "unsupported_workflow_feature"
    assert opened["exit_code"] == 2
    assert _pushes(paths["act"]) == []
    assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))


def test_act_timeout_value_and_job_failed(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    seen: dict = {}
    real = stream_cli

    def wrapped(argv, **kwargs):
        seen["timeout_s"] = kwargs["timeout_s"]
        return real(argv, **kwargs)

    monkeypatch.setattr("memory.local_mcp.stream.stream_cli", wrapped)
    cases = (
        ({}, 11),
        ({"timeout_s": 0}, 1800),
        ({"timeout_s": True}, 1800),
        ({"timeout_s": 9000}, 7200),
        ({"timeout_s": 4}, 4),
    )
    for index, (arguments, expected) in enumerate(cases):
        _reset_fakes(paths)
        work = _prepare_act(tmp_path, f"timeout-{index}", {"timeout_s": 11})
        result = _run_act(work, bin_dir, arguments)
        assert result["ok"] is True
        assert seen["timeout_s"] == expected

    _reset_fakes(paths)
    paths["mode"].write_text("fail", encoding="utf-8")
    work = _prepare_act(tmp_path, "failed")
    failed = _run_act(work, bin_dir, {"timeout_s": 5})
    assert result_errors(failed) == []
    assert failed["ok"] is False
    assert failed["exit_code"] == 1
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "job_failed"
    assert "stderr: only-err" in failed["log_tail"]
    assert failed["log_tail"].strip()


def test_act_path_escape_and_pipeline_busy(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _enable_act(outside)
    escaped = run_local_pipeline(
        {"backend": "act", "working_directory": str(outside)},
        allow_root=root,
    )
    assert result_errors(escaped) == []
    assert escaped["error"]["code"] == "path_escape"
    assert escaped["error"]["message"] == "Choose a path inside the project."
    assert escaped["exit_code"] == 2
    assert escaped["skipped"] is False
    assert _audit_entries(outside) == []

    link = root / "via"
    link.symlink_to(outside, target_is_directory=True)
    linked = run_local_pipeline(
        {"backend": "act", "working_directory": str(link)},
        allow_root=root,
    )
    assert linked["error"]["code"] == "path_escape"

    nulled = run_local_pipeline(
        {"backend": "act", "working_directory": "bad\x00path"},
        allow_root=root,
    )
    assert nulled["error"]["code"] == "path_escape"
    assert result_errors(nulled) == []

    work = tmp_path / "busy"
    work.mkdir()
    _enable_act(work)
    started = time.monotonic()
    with agent_lock(work / ".agent", name="local_pipeline", timeout=2):
        busy = run_local_pipeline(
            {"backend": "act", "working_directory": str(work)},
            allow_root=work,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 2
    assert result_errors(busy) == []
    assert busy["error"]["code"] == "pipeline_busy"
    assert busy["error"]["message"] == "A run is in progress in this workdir."
    assert busy["exit_code"] == 2
    assert busy["status"] == "refused"
    assert busy["ok"] is False
    assert busy["skipped"] is False
    assert _audit_entries(work) == []


def test_dispatch_confines_workdir_before_load(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    opened: list[Path] = []

    def spy(path: Path):
        opened.append(Path(path))
        return load_local_pipeline(path)

    monkeypatch.setattr("memory.local_mcp.pipeline.load_local_pipeline", spy)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_project_config(
        outside,
        {"local_pipeline": {"enabled": False, "backend": "jenkins"}},
    )
    link = root / "via"
    link.symlink_to(outside, target_is_directory=True)
    missing = root / "missing"
    not_dir = root / "not-a-dir"
    not_dir.write_text("x", encoding="utf-8")
    inside = root / "inside"
    inside.mkdir()
    _write_project_config(
        inside,
        {"local_pipeline": {"enabled": False, "backend": "jenkins"}},
    )
    calls = (
        ("act", {}),
        ("self_hosted", {"runner_labels": ["linux"]}),
        ("jenkins", {"job_path": "folder/name"}),
    )
    for backend, extra in calls:
        for raw in (outside, link, missing, not_dir):
            opened.clear()
            result = run_local_pipeline(
                {"backend": backend, "working_directory": str(raw), **extra},
                allow_root=root,
            )
            assert result_errors(result) == [], (backend, raw, result)
            assert result["error"]["code"] == "path_escape"
            assert result["error"]["message"] == "Choose a path inside the project."
            assert result["config_backend"] == "act"
            assert result["backend"] == backend
            assert result["skipped"] is False
            assert result["exit_code"] == 2
            assert opened == []
            assert _audit_entries(outside) == []
        opened.clear()
        disabled = run_local_pipeline(
            {"backend": backend, "working_directory": str(inside), **extra},
            allow_root=root,
        )
        assert result_errors(disabled) == []
        assert disabled["error"]["code"] == "local_pipeline_disabled"
        assert disabled["config_backend"] == "jenkins"
        assert disabled["skipped"] is True
        assert disabled["backend"] == backend
        assert [path.resolve() for path in opened] == [inside.resolve()]
        assert _audit_entries(inside) == []


def test_runs_symlink_does_not_delete_outside_file(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    runs_outside = outside / "local-pipeline-runs"
    runs_outside.mkdir(parents=True)
    kept = []
    for index in range(21):
        name = f"{index:04d}.json"
        (runs_outside / name).write_text("{}", encoding="utf-8")
        (runs_outside / f"{index:04d}.log").write_text("keep", encoding="utf-8")
        kept.append(name)
    (outside / "project_config.json").write_text(
        json.dumps({"local_pipeline": {"enabled": True, "backend": "jenkins"}}),
        encoding="utf-8",
    )
    (outside / "0000.json").write_text("{}", encoding="utf-8")
    (outside / "0000.log").write_text("keep", encoding="utf-8")

    work = root / "proj"
    work.mkdir()
    _write_project_config(
        work,
        {"local_pipeline": {"enabled": True, "backend": "jenkins"}},
    )
    runs = work / ".agent" / "local-pipeline-runs"
    runs.symlink_to(runs_outside, target_is_directory=True)
    before = sorted(path.name for path in runs_outside.iterdir())
    _prune(runs)
    assert (runs_outside / "0000.json").is_file()
    assert (runs_outside / "0000.log").is_file()
    assert sorted(path.name for path in runs_outside.iterdir()) == before
    calls = (
        ("act", {}),
        ("self_hosted", {"runner_labels": ["linux"]}),
        ("jenkins", {"job_path": "folder/name"}),
    )
    for backend, extra in calls:
        result = run_local_pipeline(
            {"backend": backend, "working_directory": str(work), **extra},
            allow_root=root,
        )
        assert result_errors(result) == [], (backend, result)
        assert result["error"]["code"] == "path_escape"
        assert result["error"]["message"] == "Choose a path inside the project."
        assert result["config_backend"] == "jenkins"
        assert result["backend"] == backend
        assert result["exit_code"] == 2
        assert result["skipped"] is False
        assert sorted(path.name for path in runs_outside.iterdir()) == before
        assert runs.is_symlink()
    assert _audit_entries(work) == []
    assert not (work / ".agent" / "LOOP_STATE.json").exists()
    assert not (outside / "AUDIT_LOG.json").exists()
    assert not (outside / "LOOP_STATE.json").exists()
    assert not (outside / "local_pipeline.lock").exists()

    linked = root / "linked"
    linked.mkdir()
    (linked / ".agent").symlink_to(outside, target_is_directory=True)
    _prune(linked / ".agent" / "local-pipeline-runs")
    assert (runs_outside / "0000.json").is_file()
    assert sorted(path.name for path in runs_outside.iterdir()) == before
    for backend, extra in calls:
        result = run_local_pipeline(
            {"backend": backend, "working_directory": str(linked), **extra},
            allow_root=root,
        )
        assert result_errors(result) == [], (backend, result)
        assert result["error"]["code"] == "path_escape"
        assert result["config_backend"] == "act"
        assert result["backend"] == backend
        assert result["skipped"] is False
        assert sorted(path.name for path in runs_outside.iterdir()) == before
    assert (outside / "0000.json").read_text(encoding="utf-8") == "{}"
    assert not (outside / "AUDIT_LOG.json").exists()
    assert not (outside / "LOOP_STATE.json").exists()
    assert not (outside / "local_pipeline.lock").exists()
    for name in kept:
        assert (runs_outside / name).is_file()


def test_act_environment_variables_refused(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    work = _prepare_act(tmp_path, "env")
    key_rejected = _run_act(
        work,
        bin_dir,
        {"environment_variables": {"GITHUB_TOKEN": "not-a-real-value"}},
    )
    assert key_rejected["error"]["code"] == "secret_key_rejected"
    assert key_rejected["exit_code"] == 2
    assert key_rejected["status"] == "refused"
    assert "not-a-real-value" not in json.dumps(key_rejected)
    assert _pushes(paths["act"]) == []
    assert _jsonl(paths["docker"]) == []

    value_rejected = _run_act(
        work,
        bin_dir,
        {"environment_variables": {"SAFE_VALUE": _FAKE_GHP}},
    )
    assert value_rejected["error"]["code"] == "secret_value_rejected"
    assert _FAKE_GHP not in json.dumps(value_rejected)
    assert _pushes(paths["act"]) == []
    blob = (work / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8")
    assert _FAKE_GHP not in blob
    assert "not-a-real-value" not in blob

    bad_key = _run_act(work, bin_dir, {"environment_variables": {"lower": "ok"}})
    assert bad_key["error"]["code"] == "secret_key_rejected"
    assert "lower" not in json.dumps(bad_key)


def test_act_workflow_symlink_escapes(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    work = tmp_path / "repo"
    work.mkdir()
    _enable_act(work)
    outside = tmp_path / "outside.yml"
    outside.write_text("uses: ./nope\n", encoding="utf-8")
    link = work / ".github" / "workflows" / "agentix-loop.yml"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    result = _run_act(work, bin_dir)
    assert result["error"]["code"] == "path_escape"
    assert result["exit_code"] == 2
    assert _pushes(paths["act"]) == []
    actions = [entry["action"] for entry in _audit_entries(work)]
    assert actions == ["local_pipeline_run"]


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_act_timeout_kills_after_grace(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    paths["mode"].write_text("ignore", encoding="utf-8")
    work = _prepare_act(tmp_path, "slow")
    holder: dict = {}

    def go() -> None:
        holder["result"] = _run_act(work, bin_dir, {"timeout_s": 1})

    thread = threading.Thread(target=go)
    thread.start()
    try:
        thread.join(12)
        if thread.is_alive():
            raise AssertionError("timeout run did not finish")
        result = holder["result"]
        assert result_errors(result) == []
        assert result["exit_code"] == 124
        assert result["status"] == "timed_out"
        assert result["timed_out"] is True
        assert result["ok"] is False
        assert result["error"]["code"] == "timed_out"
        ready = json.loads(paths["ready"].read_text(encoding="utf-8"))
        assert ready["pgid"] != os.getpgrp()
        _assert_dead(ready["pid"])
        assert time.time() - ready["t"] >= 1.8
        assert time.time() - ready["t"] < 10
    finally:
        if paths["ready"].is_file():
            try:
                info = json.loads(paths["ready"].read_text(encoding="utf-8"))
                os.killpg(int(info["pgid"]), signal.SIGKILL)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                pass
        thread.join(3)


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_act_cancel_kills_group_and_removes_container(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    paths["mode"].write_text("sleep", encoding="utf-8")
    work = _prepare_act(tmp_path, "cancel")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv("ACT_SECRETS_FILE", raising=False)
    with StdioSession(allow_root=work) as session:
        try:
            session.send(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "run_local_pipeline",
                        "arguments": {
                            "backend": "act",
                            "working_directory": str(work),
                            "timeout_s": 30,
                        },
                    },
                }
            )
            info = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if paths["ready"].is_file():
                    try:
                        info = json.loads(paths["ready"].read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        info = None
                    if isinstance(info, dict) and "pgid" in info:
                        break
                time.sleep(0.02)
            assert isinstance(info, dict)
            assert info["pgid"] != os.getpgrp()
            os.kill(info["pid"], 0)
            os.kill(info["child"], 0)
            session.notify("notifications/cancelled", {"requestId": 7})
            done = session.read(timeout=15)
            body = done["result"]["structuredContent"]
            assert result_errors(body) == []
            assert body["exit_code"] == 130
            assert body["status"] == "cancelled"
            assert body["cancelled"] is True
            assert body["ok"] is False
            assert body["error"]["code"] == "cancelled"
            assert done["result"]["isError"] is True
            _assert_group_dead(info["pgid"])
            _assert_dead(info["pid"])
            _assert_dead(info["child"])
            pushes = _pushes(paths["act"])
            assert pushes
            run_id = [
                part for part in pushes[0]["argv"] if "agentix.local_mcp.run=" in part
            ][0].rsplit("agentix.local_mcp.run=", 1)[1]
            docker_rows = _jsonl(paths["docker"])
            assert any(
                row["argv"][:1] == ["ps"] and f"label=agentix.local_mcp.run={run_id}" in row["argv"]
                for row in docker_rows
            )
            assert any(
                row["argv"][:2] == ["rm", "-f"] and "c0ffee123" in row["argv"] for row in docker_rows
            )
            actions = [entry["action"] for entry in _audit_entries(work)]
            assert "local_pipeline_cancelled" in actions
            assert "local_pipeline_run" not in actions
        finally:
            if paths["ready"].is_file():
                try:
                    killed = json.loads(paths["ready"].read_text(encoding="utf-8"))
                    os.killpg(int(killed["pgid"]), signal.SIGKILL)
                except (OSError, json.JSONDecodeError, KeyError, ValueError):
                    pass


def test_act_progress_and_quiet_without_token(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    paths["mode"].write_text("both", encoding="utf-8")
    work = _prepare_act(tmp_path, "progress")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv("ACT_SECRETS_FILE", raising=False)
    with StdioSession(allow_root=work) as session:
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "run_local_pipeline",
                    "arguments": {"backend": "act", "working_directory": str(work)},
                    "_meta": {"progressToken": "tok-1"},
                },
            }
        )
        notes = []
        done = None
        for _ in range(8):
            item = session.read(timeout=10)
            if item.get("method") == "notifications/progress":
                notes.append(item)
                continue
            done = item
            break
        assert notes
        assert done is not None and done["id"] == 7
        assert "id" not in notes[0]
        assert notes[0]["params"]["progressToken"] == "tok-1"
        assert "total" not in notes[0]["params"]
        assert isinstance(notes[0]["params"]["progress"], int)
        assert isinstance(notes[0]["params"]["message"], str)
        assert len(notes[0]["params"]["message"]) <= 500
        assert "\n" not in notes[0]["params"]["message"]
        body = done["result"]["structuredContent"]
        assert body["ok"] is True
        assert done["result"]["isError"] is False
        assert done["result"]["content"][0]["text"].startswith("act harness exit=0 duration_ms=")
        quiet = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {"backend": "act", "working_directory": str(work)},
            },
            req_id=8,
        )
        assert quiet["result"]["structuredContent"]["ok"] is True
        session.expect_quiet()

    paths["mode"].write_text("ok", encoding="utf-8")
    with StdioSession(allow_root=work) as session:
        started = session.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        )
        assert started["result"]["protocolVersion"] == "2025-06-18"
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "run_local_pipeline",
                    "arguments": {"backend": "act", "working_directory": str(work)},
                    "_meta": {"progressToken": "tok-old"},
                },
            }
        )
        old_notes = []
        finished = None
        for _ in range(8):
            item = session.read(timeout=10)
            if item.get("method") == "notifications/progress":
                old_notes.append(item)
                continue
            finished = item
            break
        assert old_notes
        assert finished is not None and finished["id"] == 3
        assert "message" not in old_notes[0]["params"]
        assert "total" not in old_notes[0]["params"]


def test_enabled_other_backends_do_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("JENKINS_USER", raising=False)
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    work = tmp_path / "proj"
    work.mkdir()
    _write_project_config(
        work,
        {"local_pipeline": {"enabled": True, "backend": "self_hosted"}},
    )
    with StdioSession(allow_root=work) as session:
        hosted = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {
                    "backend": "self_hosted",
                    "working_directory": str(work),
                    "runner_labels": ["linux"],
                },
            },
        )
        jenkins = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {
                    "backend": "jenkins",
                    "working_directory": str(work),
                    "job_path": "team/job",
                },
            },
            req_id=2,
        )
    assert hosted["result"]["structuredContent"]["error"]["code"] == "runner_label_unknown"
    assert hosted["result"]["structuredContent"]["skipped"] is False
    assert hosted["result"]["structuredContent"]["queue_risk"] is False
    assert result_errors(hosted["result"]["structuredContent"]) == []
    assert jenkins["result"]["structuredContent"]["error"]["code"] == "jenkins_unreachable"
    assert jenkins["result"]["structuredContent"]["skipped"] is False


def test_act_requires_linux_does_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr("memory.local_mcp.probe.sys.platform", "win32")
    work = tmp_path / "win"
    work.mkdir()
    _enable_act(work)
    result = _run_act(work, tmp_path / "bin")
    assert result["error"]["code"] == "act_requires_linux"
    assert result["exit_code"] == 2
    assert result["ok"] is False
    actions = [entry["action"] for entry in _audit_entries(work)]
    assert actions == ["local_pipeline_probe_failed"]


def test_act_retains_twenty_run_files(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    _install_backend_fakes(bin_dir, tmp_path / "rec")
    work = _prepare_act(tmp_path, "keep")
    runs = work / ".agent" / "local-pipeline-runs"
    runs.mkdir()
    names = []
    for index in range(22):
        name = f"2020{index:04d}T000000Z-act-{index:08x}.json"
        (runs / name).write_text("{}", encoding="utf-8")
        (runs / (Path(name).stem + ".log")).write_text("old", encoding="utf-8")
        names.append(name)
    result = _run_act(work, bin_dir)
    assert result["ok"] is True
    left = sorted(path.name for path in runs.glob("*.json"))
    assert len(left) == 20
    assert names[0] not in left
    assert names[1] not in left
    assert names[2] not in left
    assert names[3] in left
    assert not (runs / (Path(names[0]).stem + ".log")).exists()
    assert (runs / (Path(names[3]).stem + ".log")).is_file()
    created = [name for name in left if name not in names]
    assert len(created) == 1
    saved = json.loads((runs / created[0]).read_text(encoding="utf-8"))
    assert result_errors(saved) == []
    assert saved == result


def test_stream_merges_stderr_rate_limit_and_redaction(tmp_path: Path, monkeypatch):
    script = tmp_path / "talk.py"
    script.write_text(
        "import sys\n"
        "for index in range(40):\n"
        "    sys.stdout.write('line %d\\n' % index)\n"
        "sys.stderr.write('err-tail\\n')\n",
        encoding="utf-8",
    )
    notes: list = []

    def progress(amount, message):
        notes.append((amount, message))

    log_path = tmp_path / "run.log"
    started = time.monotonic()
    streamed = stream_cli(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=log_path,
        timeout_s=5,
        progress=progress,
        protocol="2025-11-25",
    )
    elapsed = time.monotonic() - started
    assert streamed.returncode == 0
    text = log_path.read_text(encoding="utf-8")
    assert "stdout: line 0" in text
    assert "stdout: line 39" in text
    assert "stderr: err-tail" in text
    assert "stderr: err-tail" in streamed.log_tail
    assert len(notes) + streamed.lines_dropped == 41
    if elapsed < 1:
        assert len(notes) <= 20
    assert all(message is None or (len(message) <= 500 and "\n" not in message) for _, message in notes)

    old_notes: list = []
    stream_cli(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        log_path=tmp_path / "old.log",
        timeout_s=5,
        progress=lambda amount, message: old_notes.append(message),
        protocol="2025-06-18",
    )
    assert old_notes
    assert all(message is None for message in old_notes)

    err_script = tmp_path / "err.py"
    err_script.write_text("import sys\nsys.stderr.write('only-err\\n')\nraise SystemExit(3)\n", encoding="utf-8")
    err = stream_cli(
        [sys.executable, str(err_script)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=tmp_path / "err.log",
        timeout_s=5,
    )
    assert err.returncode == 3
    assert "stderr: only-err" in err.log_tail
    assert err.log_tail.strip()

    monkeypatch.setattr("memory.local_mcp.stream.redact_text", lambda text: text)
    secret_script = tmp_path / "secret.py"
    secret_script.write_text(
        "import sys\nsys.stdout.write(%r)\n" % (_FAKE_GHP + "\n"),
        encoding="utf-8",
    )
    secret_notes: list = []
    stream_cli(
        [sys.executable, str(secret_script)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=tmp_path / "secret.log",
        timeout_s=5,
        progress=lambda amount, message: secret_notes.append(message),
    )
    assert secret_notes == ["[redacted]"]

    monkeypatch.setattr("memory.local_mcp.stream.LOG_CAP", 64)
    flood = tmp_path / "flood.py"
    flood.write_text(
        "import sys\nfor index in range(30):\n    sys.stdout.write('x' * 20 + '\\n')\n",
        encoding="utf-8",
    )
    capped = stream_cli(
        [sys.executable, str(flood)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=tmp_path / "capped.log",
        timeout_s=5,
    )
    assert capped.truncated is True
    assert (tmp_path / "capped.log").stat().st_size <= 64
    assert len(capped.log_tail) <= 8192


def test_stream_masks_multiline_pem(tmp_path: Path):
    body = "MIIEsecretbodyonlyonthemiddleline"
    script = tmp_path / "pem.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('-----BEGIN RSA PRIVATE KEY-----\\n')\n"
        "sys.stdout.write(%r)\n"
        "sys.stdout.write('-----END RSA PRIVATE KEY-----\\n')\n"
        "sys.stdout.write('after-pem\\n')\n" % (body + "\n"),
        encoding="utf-8",
    )
    notes: list = []
    log_path = tmp_path / "pem.log"
    streamed = stream_cli(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=log_path,
        timeout_s=5,
        progress=lambda amount, message: notes.append(message),
    )
    logged = log_path.read_text(encoding="utf-8")
    assert body not in logged
    assert "-----END" not in logged
    assert "-----BEGIN" not in logged
    assert body not in streamed.log_tail
    assert "-----END" not in streamed.log_tail
    assert "after-pem" in logged
    assert notes
    assert all(body not in (message or "") and "-----END" not in (message or "") for message in notes)


def test_stream_redacts_marker_past_clip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("memory.local_mcp.stream.redact_text", lambda text: text)
    script = tmp_path / "late.py"
    script.write_text(
        "import sys\nsys.stdout.write(%r)\n" % ("A" * 600 + _FAKE_GHP + "\n"),
        encoding="utf-8",
    )
    notes: list = []
    stream_cli(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        log_path=tmp_path / "late.log",
        timeout_s=5,
        progress=lambda amount, message: notes.append(message),
    )
    assert notes == ["[redacted]"]


def test_stream_caps_queue_and_flushes_final_progress(tmp_path: Path):
    capped = _LineQueue(1024)
    for _ in range(50):
        capped.put("x" * 100 + "\n")
    assert capped.dropped > 0
    kept = 0
    while True:
        try:
            kept += len(capped.get_nowait().encode("utf-8"))
        except queue.Empty:
            break
    assert 0 < kept <= 1024

    sentinel = tmp_path / "done"
    script = tmp_path / "flood.py"
    script.write_text(
        "import sys\n"
        "line = b'y' * 100 + b'\\n'\n"
        "for _ in range(20000):\n"
        "    sys.stdout.buffer.write(line)\n"
        "sys.stdout.buffer.flush()\n"
        "open(sys.argv[1], 'w').close()\n",
        encoding="utf-8",
    )
    release = threading.Event()
    started = threading.Event()
    notes: list = []

    def progress(amount, message):
        notes.append((time.monotonic(), amount, message))
        if not started.is_set():
            started.set()
            assert release.wait(timeout=5)

    log_path = tmp_path / "run.log"
    holder: dict = {}

    def run() -> None:
        holder["result"] = stream_cli(
            [sys.executable, str(script), str(sentinel)],
            cwd=tmp_path,
            env={"PATH": os.environ.get("PATH", "")},
            log_path=log_path,
            timeout_s=10,
            progress=progress,
            protocol="2025-11-25",
        )

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=5)
    # While progress is blocked the log stays the lines already written, not the flood.
    blocked = log_path.stat().st_size
    deadline = time.monotonic() + 5
    while not sentinel.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    time.sleep(0.2)
    assert thread.is_alive()
    assert log_path.stat().st_size == blocked
    assert blocked < 4096
    release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    streamed = holder["result"]
    assert streamed.returncode == 0
    size = log_path.stat().st_size
    full = 20000 * (len("stdout: ") + 101)
    # Dropped lines never reach the file. One in-flight read can still land after release.
    assert blocked < size < full
    assert streamed.lines_dropped > 0
    assert len(notes) + streamed.lines_dropped == 20000
    assert notes[-1][1] == size
    assert "\n" not in (notes[-1][2] or "")
    stamps = sorted(item[0] for item in notes)
    for index in range(20, len(stamps)):
        assert stamps[index] - stamps[index - 20] >= 0.95


def test_stream_valueerror_is_a_result(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise ValueError("embedded null byte")

    monkeypatch.setattr("memory.local_mcp.stream.subprocess.Popen", boom)
    streamed = stream_cli(
        ["act", "bad"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        log_path=tmp_path / "null.log",
        timeout_s=1,
    )
    assert streamed.missing is False
    assert streamed.returncode == 1


def test_stream_cli_win32_uses_process_group(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("memory.local_mcp.stream.sys.platform", "win32")
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        del argv
        seen["kwargs"] = kwargs
        raise FileNotFoundError("act")

    monkeypatch.setattr("memory.local_mcp.stream.subprocess.Popen", fake_popen)
    streamed = stream_cli(
        ["act"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        log_path=tmp_path / "win.log",
        timeout_s=1,
    )
    assert streamed.missing is True
    assert "start_new_session" not in seen["kwargs"]
    assert seen["kwargs"]["creationflags"] == 0x200


def test_capture_null_and_win32_group(monkeypatch):
    from memory.local_mcp.backends.act import _capture

    monkeypatch.setattr("memory.local_mcp.stream.sys.platform", "win32")
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        del argv
        seen["kwargs"] = kwargs
        raise ValueError("embedded null byte")

    monkeypatch.setattr("memory.local_mcp.backends.act.subprocess.Popen", fake_popen)
    code, text = _capture(["docker", "image", "inspect", "bad"], {"PATH": "/usr/bin"}, 1)
    assert code == 1
    assert text == ""
    assert "start_new_session" not in seen["kwargs"]
    assert seen["kwargs"]["creationflags"] == 0x200


def test_nul_names_and_env_do_not_spawn(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    work = _prepare_act(tmp_path, "nul")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    job = _run_act(work, bin_dir, {"job_name": "har\x00ness"})
    assert result_errors(job) == []
    assert job["error"]["code"] == "path_escape"
    assert job["error"]["message"] == "Choose a path inside the project."
    assert job["exit_code"] == 2
    flow = _run_act(work, bin_dir, {"workflow_file": ".github/workflows/a\x00.yml"})
    assert flow["error"]["code"] == "path_escape"
    env = _run_act(work, bin_dir, {"environment_variables": {"SAFE_KEY": "a\x00b"}})
    assert env["error"]["code"] == "secret_value_rejected"
    assert env["error"]["message"] == "environment variable value is not allowed"
    assert "\x00" not in env["error"]["message"]


def test_act_rejects_host_control_keys(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    work = _prepare_act(tmp_path, "host-env")
    keys = (
        "PATH",
        "DOCKER_HOST",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
    )
    for key in keys:
        _reset_fakes(paths)
        result = _run_act(work, bin_dir, {"environment_variables": {key: "/tmp/not-used"}})
        assert result["error"]["code"] == "secret_key_rejected", key
        assert key not in result["error"]["message"]
        assert "/tmp/not-used" not in json.dumps(result)
        assert _jsonl(paths["act"]) == []
        assert _jsonl(paths["docker"]) == []


def test_act_help_failure_cancel_and_inspect_timeout(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    paths = _install_backend_fakes(bin_dir, tmp_path / "rec")
    work = _prepare_act(tmp_path, "help")
    paths["help"].write_text("fail", encoding="utf-8")
    failed = _run_act(work, bin_dir)
    assert failed["error"]["code"] == "act_help_failed"
    assert failed["exit_code"] == 2
    assert "legacy" not in failed["error"]["message"].lower()
    assert "acknowledgement" not in failed["error"]["message"]
    assert _pushes(paths["act"]) == []
    assert not any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))

    _reset_fakes(paths)
    paths["help"].write_text("sleep", encoding="utf-8")
    monkeypatch.setattr("memory.local_mcp.backends.act._HELP_TIMEOUT_S", 0.3)
    timed = _run_act(work, bin_dir)
    assert timed["error"]["code"] == "act_help_failed"
    assert "legacy" not in timed["error"]["message"].lower()
    assert _pushes(paths["act"]) == []

    _reset_fakes(paths)
    paths["slow"].write_text("1", encoding="utf-8")
    slow = _run_act(work, bin_dir)
    assert slow["error"]["code"] == "docker_unreachable"
    assert "not present" not in slow["error"]["message"]
    assert "pull" not in slow["error"]["message"]
    assert _pushes(paths["act"]) == []

    _reset_fakes(paths)
    monkeypatch.setattr("memory.local_mcp.backends.act._HELP_TIMEOUT_S", 8.0)
    paths["slow"].write_text("1", encoding="utf-8")
    cancel = threading.Event()

    def fire() -> None:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            rows = _jsonl(paths["docker"])
            if any(row.get("argv", [])[:2] == ["image", "inspect"] for row in rows):
                break
            time.sleep(0.02)
        cancel.set()

    threading.Thread(target=fire, daemon=True).start()
    cancelled = run_local_pipeline(
        {"backend": "act", "working_directory": str(work)},
        allow_root=work,
        environ=_fake_env(bin_dir),
        cancel=cancel,
    )
    assert result_errors(cancelled) == []
    assert cancelled["exit_code"] == 130
    assert cancelled["error"]["code"] == "cancelled"
    assert cancelled["cancelled"] is True
    assert _pushes(paths["act"]) == []
    assert any(row["argv"][:2] == ["image", "inspect"] for row in _jsonl(paths["docker"]))

    _reset_fakes(paths)
    already = threading.Event()
    already.set()
    skipped = run_local_pipeline(
        {"backend": "act", "working_directory": str(work)},
        allow_root=work,
        environ=_fake_env(bin_dir),
        cancel=already,
    )
    assert skipped["exit_code"] == 130
    assert skipped["error"]["code"] == "cancelled"
    assert _pushes(paths["act"]) == []
    assert all("--help" not in row["argv"] for row in _jsonl(paths["act"]))


_RUN_SH_SRC = """\
import json, os, sys, time
from pathlib import Path
root = Path(__ROOT__)
mode_path = root / "mode"
mode = mode_path.read_text(encoding="utf-8").strip() if mode_path.is_file() else "exit0"
(root / "ran").write_text("1", encoding="utf-8")
(root / "argv.json").write_text(json.dumps(sys.argv), encoding="utf-8")
(root / "env.json").write_text(
    json.dumps({"keys": sorted(os.environ), "PATH": os.environ.get("PATH", "")}),
    encoding="utf-8",
)
if mode == "fail":
    raise SystemExit(3)
if mode == "sleep":
    child = os.fork()
    if child == 0:
        time.sleep(120)
        os._exit(0)
    (root / "ready.json").write_text(
        json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "child": child}),
        encoding="utf-8",
    )
    time.sleep(120)
    raise SystemExit(0)
if mode == "fast":
    diag = root / "_diag"
    diag.mkdir(parents=True, exist_ok=True)
    (diag / "Runner_20241010-213029-utc.log").write_text(
        "FAST_RUNNER_9f3a\\n",
        encoding="utf-8",
    )
    (diag / "Worker_20241010-213029-utc.log").write_text(
        "FAST_WORKER_9f3a\\n",
        encoding="utf-8",
    )
    for path in list(diag.glob("*.log")):
        path.unlink()
    raise SystemExit(0)
if mode == "ephemeral":
    diag = root / "_diag"
    diag.mkdir(parents=True, exist_ok=True)
    token = __TOKEN__
    (diag / "Runner_20241010-213029-utc.log").write_text(
        "2020-02-11 14:57:17Z: Listening for Jobs\\n" + token + "\\n",
        encoding="utf-8",
    )
    (diag / "Worker_20241010-213029-utc.log").write_text(
        "worker " + token + "\\n",
        encoding="utf-8",
    )
    time.sleep(0.8)
    for path in list(diag.glob("*.log")):
        path.unlink()
    raise SystemExit(0)
raise SystemExit(0)
"""


def _install_run_sh(runner: Path) -> None:
    body = _RUN_SH_SRC.replace("__ROOT__", json.dumps(str(runner))).replace(
        "__TOKEN__", json.dumps(_FAKE_GHP)
    )
    _write_exe(runner / "run.sh", body)


def _prepare_hosted(
    tmp_path: Path,
    name: str = "proj",
    *,
    backend: str = "self_hosted",
    labels: Any = ("linux", "self-hosted"),
    **flags: Any,
) -> tuple[Path, Path]:
    work = tmp_path / name
    work.mkdir()
    runner = work / "runner"
    now = time.time()
    _write_runner_log(
        runner / "_diag" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        now - 5,
    )
    (runner / ".runner").write_text(
        json.dumps(
            {
                "agentName": "box",
                "gitHubUrl": "https://github.com/example/repo",
                "token": "secret-runner-body",
            }
        ),
        encoding="utf-8",
    )
    (runner / "credentials").write_text("cred-secret-marker", encoding="utf-8")
    (runner / ".credentials").write_text("dot-cred-secret-marker", encoding="utf-8")
    (runner / "_work").mkdir()
    (runner / "_work" / "keep").write_text("keep", encoding="utf-8")
    hosted: dict[str, Any] = {"runner_dir": str(runner)}
    if labels is not _OMIT:
        hosted["labels"] = labels
    hosted.update(flags)
    _write_project_config(
        work,
        {
            "local_pipeline": {
                "enabled": True,
                "backend": backend,
                "self_hosted": hosted,
            }
        },
    )
    return work, runner


class _OmitType:
    pass


_OMIT = _OmitType()


def _assert_hosted(result: dict) -> None:
    assert result_errors(result) == []
    assert result["backend"] == "self_hosted"
    assert result["status_target"] == "github_actions_ui"
    assert result["status_posted_by"] is None
    assert result["scm"] == {"kind": None, "host": "github", "ref": None, "commit": None}
    assert result["skipped"] is False
    assert isinstance(result["job"].get("runner_labels"), list)
    code = None
    if isinstance(result.get("error"), dict):
        code = result["error"].get("code")
    assert result["queue_risk"] is (code == "runner_offline")
    blob = json.dumps(result)
    assert "secret-runner-body" not in blob
    assert "cred-secret-marker" not in blob
    assert "dot-cred-secret-marker" not in blob
    assert "example/repo" not in blob
    assert ".credentials" not in blob
    assert "--token" not in blob
    assert "--pat" not in blob
    assert _FAKE_GHP not in blob


def _assert_agent_clean(work: Path) -> None:
    root = work / ".agent"
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".log", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert _FAKE_GHP not in text
        assert "secret-runner-body" not in text
        assert "cred-secret-marker" not in text
        assert "dot-cred-secret-marker" not in text


def _run_hosted(
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
    processes: list,
    arguments: dict | None = None,
    environ: dict | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    monkeypatch.setattr(
        "memory.local_mcp.probe.list_linux_processes",
        lambda proc_root=Path("/proc"): list(processes),
    )
    args = {
        "backend": "self_hosted",
        "working_directory": str(work),
        "runner_labels": ["linux"],
    }
    if arguments:
        args.update(arguments)
    return run_local_pipeline(
        args,
        allow_root=work,
        environ=environ,
        cancel=cancel,
    )


def test_self_hosted_timeout_does_not_treat_bool_as_int():
    from memory.local_mcp.pipeline import _timeout_s

    section = {"self_hosted": {"timeout_s": 12}}
    assert _timeout_s({"timeout_s": 0}, section, "self_hosted") == 1800
    assert _timeout_s({"timeout_s": True}, section, "self_hosted") == 1800
    assert _timeout_s({"timeout_s": False}, section, "self_hosted") == 1800
    assert _timeout_s({}, section, "self_hosted") == 12
    assert _timeout_s({"timeout_s": 9000}, section, "self_hosted") == 7200
    assert _timeout_s({"timeout_s": 30}, section, "self_hosted") == 30
    assert _timeout_s({}, {"self_hosted": {"timeout_s": True}}, "self_hosted") == 1800


def test_self_hosted_label_unknown_and_mismatch(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(tmp_path, "unknown", labels=None)
    _write_runner_log(
        runner / "_diag" / "pages" / "Runner_20241010-213029-utc.log",
        _RUNNER_LOG,
        time.time() - 1,
    )
    _install_run_sh(runner)
    unknown = _run_hosted(
        monkeypatch,
        work,
        [_listener(runner)],
        arguments={"runner_labels": ["linux"], "timeout_s": 30},
    )
    _assert_hosted(unknown)
    assert unknown["error"]["code"] == "runner_label_unknown"
    assert unknown["exit_code"] == 2
    assert unknown["probe"] == {}
    assert not (runner / "ran").exists()
    assert (runner / "_work" / "keep").is_file()

    empty, runner_empty = _prepare_hosted(tmp_path, "empty", labels=[])
    _install_run_sh(runner_empty)
    empty_result = _run_hosted(monkeypatch, empty, [_listener(runner_empty)])
    _assert_hosted(empty_result)
    assert empty_result["error"]["code"] == "runner_label_unknown"

    mismatch_work, mismatch_runner = _prepare_hosted(
        tmp_path,
        "mismatch",
        backend="act",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(mismatch_runner)
    mismatch = _run_hosted(
        monkeypatch,
        mismatch_work,
        [],
        arguments={"runner_labels": ["Linux"]},
    )
    _assert_hosted(mismatch)
    assert mismatch["error"]["code"] == "runner_label_mismatch"
    assert mismatch["config_backend"] == "act"
    assert mismatch["job"] == {"runner_labels": ["Linux"]}
    assert mismatch["queue_risk"] is False
    assert not (mismatch_runner / "ran").exists()
    extra = _run_hosted(
        monkeypatch,
        mismatch_work,
        [_listener(mismatch_runner)],
        arguments={"runner_labels": ["linux", "gpu"]},
    )
    assert extra["error"]["code"] == "runner_label_mismatch"
    assert not (mismatch_runner / "ran").exists()


def test_self_hosted_not_registered_does_not_create_runner(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(tmp_path, "unreg")
    (runner / ".runner").unlink()
    result = _run_hosted(monkeypatch, work, [_listener(runner)])
    _assert_hosted(result)
    assert result["error"] == {
        "code": "runner_not_registered",
        "message": "Human runs config.sh with a token from the GitHub UI.",
    }
    assert not (runner / ".runner").exists()
    assert (runner / "credentials").is_file()
    assert (runner / ".credentials").is_file()
    _assert_agent_clean(work)


@pytest.mark.parametrize("newer", ["pages", "flat"])
def test_self_hosted_run_reads_both_runner_globs(
    tmp_path: Path, monkeypatch, newer: str
):
    work, runner = _prepare_hosted(
        tmp_path,
        newer,
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
        timeout_s=30,
    )
    _install_run_sh(runner)
    now = time.time()
    flat = runner / "_diag" / "Runner_20241010-213029-utc.log"
    pages = runner / "_diag" / "pages" / "Runner_20241010-213029-utc.log"
    quiet = "Current runner version: '2.319.1'\n"
    if newer == "pages":
        _write_runner_log(flat, quiet, now - 30)
        _write_runner_log(pages, _RUNNER_LOG, now - 5)
    else:
        _write_runner_log(pages, quiet, now - 30)
        _write_runner_log(flat, _RUNNER_LOG, now - 5)
    result = _run_hosted(
        monkeypatch,
        work,
        [_listener(runner)],
        arguments={"timeout_s": 1, "runner_labels": ["linux"]},
    )
    _assert_hosted(result)
    assert result["error"]["code"] == "listener_idle"
    assert result["exit_code"] == 2
    assert result["status"] == "idle"
    assert result["ok"] is False
    assert result["timed_out"] is False
    assert result["cancelled"] is False
    assert result["probe"] == {"runner_log_fresh": True}
    assert result["warnings"] == []
    assert not (runner / "ran").exists()
    assert (runner / "_work" / "keep").read_text(encoding="utf-8") == "keep"
    assert "labels:" not in _RUNNER_LOG


def test_self_hosted_offline_queue_risk_and_untrusted(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(tmp_path, "offline")
    _install_run_sh(runner)
    outside = tmp_path / "other"
    outside.mkdir()
    offline = _run_hosted(
        monkeypatch,
        work,
        [
            _listener(runner, exe=str(outside / "Runner.Listener"), cwd=str(outside)),
            {"comm": "python", "exe": str(outside / "python"), "cwd": str(runner)},
        ],
    )
    _assert_hosted(offline)
    assert offline["error"]["code"] == "runner_offline"
    assert offline["queue_risk"] is True
    assert "will not enroll" in offline["error"]["message"]
    assert not (runner / "ran").exists()
    assert (runner / "_work" / "keep").is_file()

    shared, shared_runner = _prepare_hosted(
        tmp_path,
        "shared",
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(shared_runner)
    refused = _run_hosted(monkeypatch, shared, [])
    _assert_hosted(refused)
    assert refused["error"]["code"] == "untrusted_pr_risk"
    assert refused["error"]["message"] == "Probe only. Do not run fork PR code here."
    assert refused["queue_risk"] is False
    assert not (shared_runner / "ran").exists()
    assert (shared_runner / "_work" / "keep").is_file()
    assert any(item["code"] == "workspace_pollution" for item in refused["warnings"])


@pytest.mark.parametrize(
    "flags",
    [
        {"supervise_listener": True},
        {"allow_start": True},
    ],
)
def test_self_hosted_shared_single_flag_stays_offline(
    tmp_path: Path, monkeypatch, flags: dict
):
    """A shared runner with only one start flag is down, not untrusted_pr_risk."""
    work, runner = _prepare_hosted(tmp_path, "one-flag", **flags)
    _install_run_sh(runner)
    result = _run_hosted(monkeypatch, work, [])
    _assert_hosted(result)
    assert result["error"]["code"] == "runner_offline"
    assert result["error"]["message"] == (
        "Runner is down. GitHub's queue can stall. Register and start it by hand. "
        "This tool will not enroll it."
    )
    assert result["queue_risk"] is True
    assert not (runner / "ran").exists()
    assert (runner / "_work" / "keep").is_file()
    assert any(item["code"] == "workspace_pollution" for item in result["warnings"])


@pytest.mark.parametrize(
    "flags",
    [
        {"allow_start": True, "ephemeral": True},
        {"supervise_listener": True, "ephemeral": True},
        {"ephemeral": True},
    ],
)
def test_self_hosted_does_not_start_unless_flags_allow(
    tmp_path: Path, monkeypatch, flags: dict
):
    work, runner = _prepare_hosted(tmp_path, "flags", **flags)
    _install_run_sh(runner)
    result = _run_hosted(monkeypatch, work, [])
    _assert_hosted(result)
    assert result["error"]["code"] == "runner_offline"
    assert result["queue_risk"] is True
    assert not (runner / "ran").exists()


def test_self_hosted_starts_run_sh_only_when_down(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "start",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    env = {
        "PATH": str(tmp_path / "bin"),
        "GITHUB_TOKEN": _FAKE_GHP,
        "LD_PRELOAD": "/tmp/libpreload.so",
        "HOME": "/tmp/hosted-home",
    }
    result = _run_hosted(
        monkeypatch,
        work,
        [],
        arguments={"environment_variables": {"SAFE_KEY": "plain"}},
        environ=env,
    )
    _assert_hosted(result)
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["status"] == "succeeded"
    assert result["timed_out"] is False
    assert result["cancelled"] is False
    assert result["error"] is None
    assert result["warnings"] == []
    argv = json.loads((runner / "argv.json").read_text(encoding="utf-8"))
    assert argv == [str(runner / "run.sh")]
    assert "--token" not in argv
    assert "--url" not in argv
    assert "--pat" not in argv
    assert not any("config.sh" in arg for arg in argv)
    child = json.loads((runner / "env.json").read_text(encoding="utf-8"))
    assert "SAFE_KEY" not in child["keys"]
    assert "GITHUB_TOKEN" not in child["keys"]
    assert "LD_PRELOAD" not in child["keys"]
    assert child["PATH"] == env["PATH"]
    _assert_agent_clean(work)

    isolated, isolated_runner = _prepare_hosted(
        tmp_path,
        "isolated",
        ephemeral=False,
        isolated_work_folder=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(isolated_runner)
    started = _run_hosted(monkeypatch, isolated, [], environ={"PATH": env["PATH"]})
    _assert_hosted(started)
    assert started["ok"] is True
    assert any(item["code"] == "workspace_pollution" for item in started["warnings"])
    assert (isolated_runner / "_work" / "keep").is_file()
    assert json.loads((isolated_runner / "argv.json").read_text(encoding="utf-8")) == [
        str(isolated_runner / "run.sh")
    ]


def test_self_hosted_registration_forbidden_does_not_exec(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "forbid",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    config = runner / "config.sh"
    _write_exe(
        config,
        "from pathlib import Path\n"
        "Path(%s).write_text('ran', encoding='utf-8')\n" % json.dumps(str(runner / "config-ran")),
    )
    run_sh = runner / "run.sh"
    run_sh.symlink_to(config)
    result = _run_hosted(monkeypatch, work, [])
    _assert_hosted(result)
    assert result["error"]["code"] == "registration_forbidden"
    assert result["error"]["message"] == "Refused."
    assert result["exit_code"] == 2
    assert not (runner / "config-ran").exists()
    assert not (runner / "argv.json").exists()


def test_self_hosted_stale_does_not_start_another(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "stale",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    log = runner / "_diag" / "Runner_20241010-213029-utc.log"
    old = time.time() - 5000
    os.utime(log, (old, old))
    result = _run_hosted(monkeypatch, work, [_listener(runner)], arguments={"timeout_s": 30})
    _assert_hosted(result)
    assert result["error"]["code"] == "runner_log_stale"
    assert result["probe"] == {"runner_log_fresh": False}
    assert result["queue_risk"] is False
    assert not (runner / "ran").exists()


def test_self_hosted_systemctl_user_offline_does_not_start(
    tmp_path: Path, monkeypatch
):
    work, runner = _prepare_hosted(
        tmp_path,
        "unit",
        allow_start=True,
        supervise_listener=True,
        ephemeral=True,
        systemd_unit="actions.runner.unit",
        systemd_scope="user",
    )
    _install_run_sh(runner)
    bin_dir = tmp_path / "bin"
    record = tmp_path / "systemctl-argv.json"
    _install_systemctl(bin_dir, record, active=False)
    started = time.monotonic()
    result = _run_hosted(
        monkeypatch,
        work,
        [_listener(runner)],
        arguments={"timeout_s": 2},
        environ={"PATH": str(bin_dir)},
    )
    elapsed = time.monotonic() - started
    _assert_hosted(result)
    assert result["error"]["code"] == "runner_offline"
    assert result["queue_risk"] is True
    assert elapsed < 1.5
    assert not (runner / "ran").exists()
    argv = json.loads(record.read_text(encoding="utf-8"))
    assert Path(argv[0]).name == "systemctl"
    assert argv[1:] == ["--user", "is-active", "--", "actions.runner.unit"]


def test_self_hosted_win32_start_unsupported(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "win",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "run.cmd").write_text("@echo off\n", encoding="utf-8")
    # Win32 has no process table. A log that is not listening is the offline signal.
    log = runner / "_diag" / "Runner_20241010-213029-utc.log"
    log.write_text("Current runner version: '2.319.1'\n", encoding="utf-8")
    fresh = time.time() - 5
    os.utime(log, (fresh, fresh))
    monkeypatch.setattr("memory.local_mcp.probe.sys.platform", "win32")
    result = _run_hosted(monkeypatch, work, [], arguments={"timeout_s": 30})
    _assert_hosted(result)
    assert result["error"]["code"] == "runner_start_unsupported"
    assert result["error"]["message"] == "Probe only. run.sh and run.cmd are not started."
    assert result["exit_code"] == 2
    assert result["queue_risk"] is False
    assert not (runner / "ran").exists()

    healthy, healthy_runner = _prepare_hosted(
        tmp_path,
        "win-up",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(healthy_runner)
    (healthy_runner / "run.cmd").write_text("@echo off\n", encoding="utf-8")
    idle = _run_hosted(
        monkeypatch,
        healthy,
        [],
        arguments={"timeout_s": 1, "runner_labels": ["linux"]},
    )
    _assert_hosted(idle)
    assert idle["error"]["code"] == "listener_idle"
    assert idle["exit_code"] == 2
    assert not (healthy_runner / "ran").exists()


def test_self_hosted_listener_idle_and_growing_worker(tmp_path: Path, monkeypatch):
    idle_work, idle_runner = _prepare_hosted(tmp_path, "idle", timeout_s=1)
    idle = _run_hosted(monkeypatch, idle_work, [_listener(idle_runner)])
    _assert_hosted(idle)
    assert idle["error"]["code"] == "listener_idle"
    assert idle["error"]["message"] == (
        "listener up; no Worker log appeared before timeout_s"
    )
    assert idle["exit_code"] == 2
    assert idle["status"] == "idle"
    assert idle["ok"] is False
    assert idle["timed_out"] is False
    assert idle["queue_risk"] is False

    work, runner = _prepare_hosted(tmp_path, "grow")
    flat = runner / "_diag" / "Worker_20241010-213029-utc.log"
    pages = runner / "_diag" / "pages" / "Worker_20241010-213029-utc.log"
    flat.parent.mkdir(parents=True, exist_ok=True)
    pages.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text("flat-worker-marker\n", encoding="utf-8")
    pages.write_text("pages-worker-marker\n", encoding="utf-8")
    stop = threading.Event()

    def grow() -> None:
        while not stop.is_set():
            with flat.open("a", encoding="utf-8") as handle:
                handle.write("flat-worker-marker " + _FAKE_GHP + "\n")
            with pages.open("a", encoding="utf-8") as handle:
                handle.write("pages-worker-marker " + _FAKE_GHP + "\n")
            time.sleep(0.02)

    thread = threading.Thread(target=grow, daemon=True)
    thread.start()
    try:
        result = _run_hosted(
            monkeypatch,
            work,
            [_listener(runner)],
            arguments={"timeout_s": 1},
        )
    finally:
        stop.set()
        thread.join(2)
    _assert_hosted(result)
    assert result["exit_code"] == 124
    assert result["status"] == "timed_out"
    assert result["timed_out"] is True
    assert result["ok"] is False
    assert result["cancelled"] is False
    assert result["error"]["code"] == "timed_out"
    assert result["queue_risk"] is False
    log_path = work / result["log_path"]
    logged = log_path.read_text(encoding="utf-8", errors="replace")
    assert "flat-worker-marker" in logged
    assert "pages-worker-marker" in logged
    assert _FAKE_GHP not in logged
    _assert_agent_clean(work)


def test_self_hosted_stopped_worker_log_is_not_listener_idle(tmp_path: Path, monkeypatch):
    """A Worker log that appears and then stops is not idle and not exit 124."""
    work, runner = _prepare_hosted(tmp_path, "plateau")
    flat = runner / "_diag" / "Worker_20241010-213029-utc.log"
    flat.parent.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()

    def write_then_stop() -> None:
        # Stop well before timeout_s. Do not keep the last 100ms growing.
        end = time.monotonic() + 0.25
        while time.monotonic() < end:
            with flat.open("a", encoding="utf-8") as handle:
                handle.write("JOBMARKER9f3a\n")
            time.sleep(0.02)
        stopped.set()

    thread = threading.Thread(target=write_then_stop, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        result = _run_hosted(
            monkeypatch,
            work,
            [_listener(runner)],
            arguments={"timeout_s": 1},
        )
    finally:
        thread.join(2)
    elapsed = time.monotonic() - started
    assert stopped.is_set()
    assert elapsed >= 0.75
    _assert_hosted(result)
    assert result["error"]["code"] == "worker_log_stopped"
    assert result["error"]["message"] == (
        "Worker log appeared and stopped before timeout_s"
    )
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["timed_out"] is False
    assert result["cancelled"] is False
    assert result["queue_risk"] is False
    assert result["error"]["code"] != "listener_idle"
    assert "JOBMARKER9f3a" in result["log_tail"]
    logged = (work / result["log_path"]).read_text(encoding="utf-8", errors="replace")
    assert "JOBMARKER9f3a" in logged
    assert not (runner / "ran").exists()
    _assert_agent_clean(work)


def test_self_hosted_worker_tail_keeps_end_marker(tmp_path: Path, monkeypatch):
    """A Worker log past 8 MiB is tailed from the end, and the diag append is capped."""
    work, runner = _prepare_hosted(tmp_path, "tail")
    flat = runner / "_diag" / "Worker_20241010-213029-utc.log"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_bytes(b"A" * (8 * 1024 * 1024) + b"\nENDMARKER9f3a\n")
    stop = threading.Event()

    def grow() -> None:
        while not stop.is_set():
            with flat.open("a", encoding="utf-8") as handle:
                handle.write("ENDMARKER9f3a\n")
            time.sleep(0.02)

    thread = threading.Thread(target=grow, daemon=True)
    thread.start()
    try:
        result = _run_hosted(
            monkeypatch,
            work,
            [_listener(runner)],
            arguments={"timeout_s": 1},
        )
    finally:
        stop.set()
        thread.join(2)
    _assert_hosted(result)
    assert result["exit_code"] == 124
    assert result["status"] == "timed_out"
    assert result["timed_out"] is True
    assert result["truncated"] is True
    assert "ENDMARKER9f3a" in result["log_tail"]
    logged_path = work / result["log_path"]
    logged = logged_path.read_bytes()
    assert b"ENDMARKER9f3a" in logged
    assert len(logged) <= 20 * 1024 * 1024
    assert not (runner / "ran").exists()
    _assert_agent_clean(work)


def test_self_hosted_ephemeral_copies_diag_before_exit(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "ephemeral",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "mode").write_text("ephemeral", encoding="utf-8")
    result = _run_hosted(
        monkeypatch,
        work,
        [],
        arguments={"timeout_s": 5},
        environ={"PATH": "/usr/bin", "GITHUB_TOKEN": _FAKE_GHP},
    )
    _assert_hosted(result)
    assert result["ok"] is True
    assert result["exit_code"] == 0
    runs = work / ".agent" / "local-pipeline-runs"
    runner_copies = list(runs.glob("*.runner.log"))
    worker_copies = list(runs.glob("*.worker.log"))
    assert len(runner_copies) == 1
    assert len(worker_copies) == 1
    copied = runner_copies[0].read_text(encoding="utf-8") + worker_copies[0].read_text(
        encoding="utf-8"
    )
    assert "Listening for Jobs" in copied
    assert "****" in copied
    assert _FAKE_GHP not in copied
    assert not (runner / "_diag" / "Runner_20241010-213029-utc.log").exists()
    assert not (runner / "_diag" / "Worker_20241010-213029-utc.log").exists()
    argv = json.loads((runner / "argv.json").read_text(encoding="utf-8"))
    assert argv == [str(runner / "run.sh")]
    _assert_agent_clean(work)


def _fanotify_permitted() -> bool:
    from memory.local_mcp.backends.self_hosted import _DiagSnap

    snap = _DiagSnap(Path("."), Path("."), "fan-probe")
    fd = snap._fan_init()
    if fd < 0:
        return False
    os.close(fd)
    return True


def test_self_hosted_ephemeral_snapshot_same_turn_as_unlink(
    tmp_path: Path, monkeypatch
):
    """Write and unlink of _diag in one turn still leaves the new logs."""
    if not _fanotify_permitted():
        pytest.skip("fanotify is required to read _diag after a same-turn unlink")
    work, runner = _prepare_hosted(
        tmp_path,
        "fast",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "mode").write_text("fast", encoding="utf-8")
    result = _run_hosted(
        monkeypatch,
        work,
        [],
        arguments={"timeout_s": 5},
        environ={"PATH": "/usr/bin"},
    )
    _assert_hosted(result)
    assert result["ok"] is True
    assert result["exit_code"] == 0
    runs = work / ".agent" / "local-pipeline-runs"
    runner_copies = list(runs.glob("*.runner.log"))
    worker_copies = list(runs.glob("*.worker.log"))
    assert len(runner_copies) == 1
    assert len(worker_copies) == 1
    runner_text = runner_copies[0].read_text(encoding="utf-8")
    worker_text = worker_copies[0].read_text(encoding="utf-8")
    assert "FAST_RUNNER_9f3a" in runner_text
    assert "FAST_WORKER_9f3a" in worker_text
    assert runner_text != _RUNNER_LOG
    assert "FAST_RUNNER_9f3a" in result["log_tail"]
    assert "FAST_WORKER_9f3a" in result["log_tail"]
    logged = (work / result["log_path"]).read_text(encoding="utf-8")
    assert "FAST_RUNNER_9f3a" in logged
    assert "FAST_WORKER_9f3a" in logged
    assert not (runner / "_diag" / "Runner_20241010-213029-utc.log").exists()
    assert not (runner / "_diag" / "Worker_20241010-213029-utc.log").exists()
    _assert_agent_clean(work)

    idle_work, idle_runner = _prepare_hosted(
        tmp_path,
        "fast-up",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
        timeout_s=2,
    )

    def wipe() -> None:
        diag = idle_runner / "_diag"
        time.sleep(0.25)
        for _ in range(8):
            (diag / "Runner_20241010-213029-utc.log").write_text(
                "FAST_RUNNER_9f3a\n",
                encoding="utf-8",
            )
            (diag / "Worker_20241010-213029-utc.log").write_text(
                "FAST_WORKER_9f3a\n",
                encoding="utf-8",
            )
            for path in list(diag.glob("*.log")):
                path.unlink()
            time.sleep(0.05)

    threading.Thread(target=wipe, daemon=True).start()
    idle = _run_hosted(
        monkeypatch,
        idle_work,
        [_listener(idle_runner)],
        arguments={"timeout_s": 2},
        environ={"PATH": "/usr/bin"},
    )
    _assert_hosted(idle)
    assert idle["ok"] is False
    assert idle["error"]["code"] in {"listener_idle", "worker_log_stopped"}
    assert not (idle_runner / "ran").exists()
    idle_runs = idle_work / ".agent" / "local-pipeline-runs"
    idle_runner_copies = list(idle_runs.glob("*.runner.log"))
    idle_worker_copies = list(idle_runs.glob("*.worker.log"))
    assert len(idle_runner_copies) == 1
    assert len(idle_worker_copies) == 1
    idle_runner_text = idle_runner_copies[0].read_text(encoding="utf-8")
    idle_worker_text = idle_worker_copies[0].read_text(encoding="utf-8")
    assert "FAST_RUNNER_9f3a" in idle_runner_text
    assert idle_runner_text != _RUNNER_LOG
    assert "FAST_WORKER_9f3a" in idle_worker_text
    assert "FAST_RUNNER_9f3a" in idle["log_tail"]
    assert "FAST_WORKER_9f3a" in idle["log_tail"]
    idle_logged = (idle_work / idle["log_path"]).read_text(encoding="utf-8")
    assert "FAST_RUNNER_9f3a" in idle_logged
    assert "FAST_WORKER_9f3a" in idle_logged
    _assert_agent_clean(idle_work)


def test_self_hosted_failed_run_sh_is_exit_1(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "fail",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "mode").write_text("fail", encoding="utf-8")
    result = _run_hosted(monkeypatch, work, [], environ={"PATH": "/usr/bin"})
    _assert_hosted(result)
    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert result["error"]["code"] == "job_failed"


def test_self_hosted_env_rejected_before_spawn(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "env",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    for key in ("PATH", "DOCKER_HOST", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "PYTHONSTARTUP"):
        result = _run_hosted(
            monkeypatch,
            work,
            [],
            arguments={"environment_variables": {key: "/tmp/not-used"}},
        )
        _assert_hosted(result)
        assert result["error"]["code"] == "secret_key_rejected", key
        assert key not in result["error"]["message"]
        assert not (runner / "ran").exists()
    nulled = _run_hosted(
        monkeypatch,
        work,
        [],
        arguments={"environment_variables": {"SAFE_KEY": "a\x00b"}},
    )
    _assert_hosted(nulled)
    assert nulled["error"]["code"] == "secret_value_rejected"
    assert nulled["error"]["message"] == "environment variable value is not allowed"
    assert "\x00" not in nulled["error"]["message"]
    assert not (runner / "ran").exists()


def test_self_hosted_popen_valueerror_is_a_result(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "value",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)

    def boom(*args, **kwargs):
        del args, kwargs
        raise ValueError("embedded nul")

    monkeypatch.setattr("memory.local_mcp.stream.subprocess.Popen", boom)
    result = _run_hosted(monkeypatch, work, [], environ={"PATH": "/usr/bin"})
    _assert_hosted(result)
    assert result["exit_code"] == 1
    assert result["error"]["code"] == "job_failed"
    assert result["ok"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_self_hosted_started_run_sh_timeout_is_not_listener_idle(
    tmp_path: Path, monkeypatch
):
    """Timeout that kills a run.sh this call started is exit 124, not listener_idle."""
    work, runner = _prepare_hosted(
        tmp_path,
        "killed",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "mode").write_text("sleep", encoding="utf-8")
    try:
        result = _run_hosted(
            monkeypatch,
            work,
            [],
            arguments={"timeout_s": 1},
            environ={"PATH": "/usr/bin"},
        )
        _assert_hosted(result)
        assert result["error"]["code"] == "timed_out"
        assert result["error"]["message"] == (
            "local tail stopped; the GitHub job was not cancelled"
        )
        assert result["exit_code"] == 124
        assert result["status"] == "timed_out"
        assert result["timed_out"] is True
        assert result["ok"] is False
        assert result["cancelled"] is False
        assert result["queue_risk"] is False
        assert result["error"]["code"] != "listener_idle"
        info = json.loads((runner / "ready.json").read_text(encoding="utf-8"))
        assert info["pgid"] != os.getpgrp()
        _assert_group_dead(int(info["pgid"]))
        _assert_dead(int(info["pid"]))
        _assert_dead(int(info["child"]))
        assert (runner / "ran").is_file()
    finally:
        ready = runner / "ready.json"
        if ready.is_file():
            try:
                info = json.loads(ready.read_text(encoding="utf-8"))
                os.killpg(int(info["pgid"]), signal.SIGKILL)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                pass

    idle_work, idle_runner = _prepare_hosted(
        tmp_path,
        "still-up",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
        timeout_s=1,
    )
    _install_run_sh(idle_runner)
    (idle_runner / "mode").write_text("sleep", encoding="utf-8")
    idle = _run_hosted(
        monkeypatch,
        idle_work,
        [_listener(idle_runner)],
        arguments={"timeout_s": 1},
        environ={"PATH": "/usr/bin"},
    )
    _assert_hosted(idle)
    assert idle["error"]["code"] == "listener_idle"
    assert idle["exit_code"] == 2
    assert idle["status"] == "idle"
    assert idle["timed_out"] is False
    assert idle["queue_risk"] is False
    assert not (idle_runner / "ran").exists()
    assert not (idle_runner / "ready.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX")
def test_self_hosted_cancel_kills_group_this_call_started(tmp_path: Path, monkeypatch):
    work, runner = _prepare_hosted(
        tmp_path,
        "cancel",
        ephemeral=True,
        allow_start=True,
        supervise_listener=True,
    )
    _install_run_sh(runner)
    (runner / "mode").write_text("sleep", encoding="utf-8")
    cancel = threading.Event()

    def fire() -> None:
        ready = runner / "ready.json"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.is_file():
            time.sleep(0.02)
        cancel.set()

    threading.Thread(target=fire, daemon=True).start()
    try:
        result = _run_hosted(
            monkeypatch,
            work,
            [],
            arguments={"timeout_s": 30},
            environ={"PATH": "/usr/bin"},
            cancel=cancel,
        )
        _assert_hosted(result)
        assert result["exit_code"] == 130
        assert result["status"] == "cancelled"
        assert result["cancelled"] is True
        assert result["ok"] is False
        assert result["error"]["code"] == "cancelled"
        info = json.loads((runner / "ready.json").read_text(encoding="utf-8"))
        assert info["pgid"] != os.getpgrp()
        _assert_group_dead(int(info["pgid"]))
        _assert_dead(int(info["pid"]))
        _assert_dead(int(info["child"]))
        actions = [entry["action"] for entry in _audit_entries(work)]
        assert "local_pipeline_cancelled" in actions
        assert "local_pipeline_run" not in actions
        _assert_agent_clean(work)
    finally:
        ready = runner / "ready.json"
        if ready.is_file():
            try:
                info = json.loads(ready.read_text(encoding="utf-8"))
                os.killpg(int(info["pgid"]), signal.SIGKILL)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                pass


def test_self_hosted_busy_path_and_jenkins_stay_refused(tmp_path: Path, monkeypatch):
    work, _runner = _prepare_hosted(tmp_path, "busy")
    started = time.monotonic()
    with agent_lock(work / ".agent", name="local_pipeline", timeout=2):
        busy = _run_hosted(monkeypatch, work, [])
    assert time.monotonic() - started < 2
    _assert_hosted(busy)
    assert busy["error"]["code"] == "pipeline_busy"
    assert busy["error"]["message"] == "A run is in progress in this workdir."
    assert _audit_entries(work) == []

    root = tmp_path / "root"
    root.mkdir()
    outside, _outside_runner = _prepare_hosted(tmp_path, "outside")
    escaped = run_local_pipeline(
        {
            "backend": "self_hosted",
            "working_directory": str(outside),
            "runner_labels": ["linux"],
        },
        allow_root=root,
    )
    _assert_hosted(escaped)
    assert escaped["error"]["code"] == "path_escape"
    nulled = run_local_pipeline(
        {
            "backend": "self_hosted",
            "working_directory": "bad\x00dir",
            "runner_labels": ["linux"],
        },
        allow_root=root,
    )
    _assert_hosted(nulled)
    assert nulled["error"]["code"] == "path_escape"
    jenkins = run_local_pipeline(
        {
            "backend": "jenkins",
            "working_directory": str(work),
            "job_path": "team/job",
        },
        allow_root=work,
        environ={},
    )
    assert jenkins["error"]["code"] == "jenkins_unreachable"
    assert jenkins["backend"] == "jenkins"
    assert jenkins["skipped"] is False


def _jenkins_spec() -> dict:
    return {
        "api_status": 200,
        "api_location": None,
        "api_body": b'{"mode":"NORMAL","secret":"probe-body-marker"}',
        "crumb_status": 200,
        "crumb_field": "X-Agentix-Crumb",
        "crumb": "crumb-value-77",
        "crumb_location": None,
        "crumb_body": None,
        "build_status": 201,
        "build_location": "same",
        "queue_delay": 0,
        "queue_over_cap": False,
        "build_number": 9,
        "executable_url": "same",
        "log": b"ok\n",
        "chunk": 4096,
        "log_hang": False,
        "result": "SUCCESS",
    }


def _fat_queue_body(number: int) -> bytes:
    """Queue JSON whose build number sits past the 1 MiB read cap."""
    suffix = f'","executable":{{"number":{int(number)}}}'.encode("ascii")
    prefix = b'{"pad":"'
    return prefix + (b"A" * (1024 * 1024)) + suffix


def _jenkins_controller():
    import traceback
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    redirects = (301, 302, 303, 307, 308)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self._serve()

        def do_POST(self):
            self._serve()

        def log_message(self, fmt, *args):
            return

        def _serve(self):
            try:
                self._dispatch()
            except Exception:
                self.server.errors.append(traceback.format_exc())
                try:
                    self._send(500, b"")
                except Exception:
                    return

        def _dispatch(self):
            length = 0
            raw_len = self.headers.get("Content-Length")
            if raw_len:
                try:
                    length = min(int(raw_len), 1024 * 1024)
                except ValueError:
                    length = 0
            payload = self.rfile.read(length) if length else b""
            path = urlsplit(self.path).path
            self.server.records.append(
                {
                    "method": self.command,
                    "path": path,
                    "target": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": payload,
                }
            )
            spec = self.server.spec
            if path == "/api/json":
                if spec["api_status"] in redirects:
                    self._send(
                        spec["api_status"],
                        extra={"Location": self._loc(spec["api_location"], "/api/json")},
                    )
                    return
                self._send(spec["api_status"], spec["api_body"], {"Content-Type": "application/json"})
                return
            if path == "/crumbIssuer/api/json":
                if spec["crumb_status"] in redirects:
                    self._send(
                        spec["crumb_status"],
                        extra={
                            "Location": self._loc(spec["crumb_location"], "/crumbIssuer/api/json")
                        },
                    )
                    return
                body = spec["crumb_body"]
                if body is None and spec["crumb_status"] == 200:
                    body = json.dumps(
                        {"crumbRequestField": spec["crumb_field"], "crumb": spec["crumb"]}
                    ).encode("utf-8")
                if body is None:
                    body = b""
                self._send(spec["crumb_status"], body, {"Content-Type": "application/json"})
                return
            if path.endswith("/buildWithParameters") or path.endswith("/build"):
                extra = {}
                status = spec["build_status"]
                if status in redirects or status == 201:
                    extra["Location"] = self._loc(spec["build_location"], "/queue/item/1/")
                self._send(status, b"", extra)
                return
            if path == "/queue/cancelItem":
                self.server.queue_cancels.append(self.path)
                self._send(200, b"")
                return
            if path.startswith("/queue/"):
                self.server.queue_polls += 1
                if spec.get("queue_over_cap"):
                    body = _fat_queue_body(int(spec["build_number"]))
                elif self.server.queue_polls <= spec["queue_delay"]:
                    body = b'{"executable":null}'
                else:
                    number = spec["build_number"]
                    executable: dict = {"number": number}
                    if spec["executable_url"] != "omit":
                        executable["url"] = self._loc(
                            spec["executable_url"],
                            f"/job/folder/job/name/{number}/",
                        )
                    body = json.dumps({"executable": executable}).encode("utf-8")
                self._send(200, body, {"Content-Type": "application/json"})
                return
            if path.endswith("/progressiveText"):
                query = parse_qs(urlsplit(self.path).query)
                try:
                    start = int(query.get("start", ["0"])[0])
                except ValueError:
                    start = 0
                if start < 0:
                    start = 0
                if spec["log_hang"]:
                    piece = b"tick\n"
                    more = True
                else:
                    data = spec["log"]
                    piece = data[start : start + int(spec["chunk"])]
                    more = start + len(piece) < len(data)
                gap = int(spec.get("text_size_gap") or 0)
                self._send(
                    200,
                    piece,
                    {
                        "Content-Type": "text/plain;charset=UTF-8",
                        "X-Text-Size": str(start + len(piece) + gap),
                        "X-More-Data": "true" if more else "false",
                    },
                )
                return
            if path.endswith("/stop"):
                self.server.stops.append(path)
                self._send(200, b"")
                return
            if path.endswith("/api/json"):
                name = spec["result"]
                body_obj: dict = {"result": name}
                if "actions" in spec:
                    body_obj["actions"] = spec["actions"]
                body = json.dumps(body_obj).encode("utf-8")
                self._send(200, body, {"Content-Type": "application/json"})
                return
            self._send(404, b"")

        def _loc(self, kind, fallback: str) -> str:
            host, port = self.server.server_address[:2]
            base = f"http://{host}:{port}"
            if kind == "off":
                return "http://example.test" + fallback
            if kind in (None, "same"):
                return base + fallback
            return str(kind)

        def _send(self, code: int, body: bytes = b"", extra: dict | None = None) -> None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            if extra:
                for key, value in extra.items():
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

    @contextmanager
    def _running():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.spec = _jenkins_spec()
        httpd.records = []
        httpd.stops = []
        httpd.queue_polls = 0
        httpd.queue_cancels = []
        httpd.errors = []
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    return _running()


def _jk_reset(server, **overrides) -> None:
    server.spec.clear()
    server.spec.update(_jenkins_spec())
    server.spec.update(overrides)
    server.records.clear()
    server.stops.clear()
    server.queue_polls = 0
    server.queue_cancels.clear()
    server.errors.clear()


def _jk_base(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def _jk_project(work: Path, base: str | None, **overrides) -> Path:
    enabled = overrides.pop("enabled", True)
    scm_kind = overrides.pop("scm_kind", "server")
    backend = overrides.pop("backend", "jenkins")
    work.mkdir()
    block = {
        "url": base,
        "job_path": "configured/job",
        "timeout_s": 30,
        "crumb_required": True,
        "report_status": False,
        "credential_ids": {
            "bitbucket_checkout": "cred-id-marker-9f3a",
            "bitbucket_status": "status-id-marker-9f3a",
        },
    }
    block.update(overrides)
    _write_project_config(
        work,
        {
            "local_pipeline": {
                "enabled": enabled,
                "backend": backend,
                "jenkins": block,
                "scm": {
                    "kind": scm_kind,
                    "bitbucket_url": "https://bitbucket.example.invalid",
                },
            }
        },
    )
    return work


def _jk_env(**extra: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "JENKINS_USER": "alice",
        "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
    }
    env.update(extra)
    return env


def _jk_run(
    work: Path,
    *,
    job_path: str | None = "folder/name",
    jenkinsfile_path: str | None = None,
    environ: dict | None = None,
    timeout_s: int | None = 20,
    extra_env: dict | None = None,
    cancel=None,
    allow_root: Path | None = None,
) -> dict:
    args: dict = {"backend": "jenkins", "working_directory": str(work)}
    if jenkinsfile_path is not None:
        args["jenkinsfile_path"] = jenkinsfile_path
    elif job_path is not None:
        args["job_path"] = job_path
    if timeout_s is not None:
        args["timeout_s"] = timeout_s
    if extra_env is not None:
        args["environment_variables"] = extra_env
    return run_local_pipeline(
        args,
        allow_root=work if allow_root is None else allow_root,
        environ=_jk_env() if environ is None else environ,
        cancel=cancel,
    )


def _assert_jk(result: dict) -> None:
    import base64

    assert result_errors(result) == []
    assert result["backend"] == "jenkins"
    assert result["status_target"] == "bitbucket_commit_status"
    posted = result["status_posted_by"]
    if result["status"] == "refused" or result["skipped"]:
        assert posted is None
    else:
        assert posted in (None, "jenkins", "harness")
    assert result["queue_risk"] is False
    assert result["scm"]["host"] == "bitbucket"
    ref = result["scm"]["ref"]
    commit = result["scm"]["commit"]
    assert ref is None or (isinstance(ref, str) and ref.startswith("refs/") and "\n" not in ref)
    assert commit is None or (
        isinstance(commit, str) and len(commit) >= 7 and len(commit) <= 40 and all(c in "0123456789abcdef" for c in commit)
    )
    blob = json.dumps(result)
    encoded = base64.b64encode(f"alice:{_FAKE_JENKINS_TOKEN}".encode()).decode("ascii")
    for banned in (
        _FAKE_JENKINS_TOKEN,
        _FAKE_GHP,
        "Basic ",
        encoded,
        "cred-id-marker-9f3a",
        "status-id-marker-9f3a",
        "probe-body-marker",
        "crumb-value-77",
        "standard-crumb",
        "bitbucket.example.invalid",
    ):
        assert banned not in blob


def _jk_saved(work: Path) -> list[Path]:
    runs = work / ".agent" / "local-pipeline-runs"
    if not runs.is_dir():
        return []
    return sorted(path for path in runs.glob("*.json") if path.is_file())


def _assert_jk_saved(work: Path, result: dict) -> Path:
    files = _jk_saved(work)
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == result
    assert files[0].stat().st_mode & 0o777 == 0o600
    log_path = files[0].with_suffix(".log")
    assert log_path.is_file()
    assert log_path.stat().st_mode & 0o777 == 0o600
    return log_path


def _assert_jk_uncommitted(work: Path) -> None:
    assert _jk_saved(work) == []
    runs = work / ".agent" / "local-pipeline-runs"
    if runs.is_dir():
        assert list(runs.iterdir()) == []


def _jk_paths(server) -> list[str]:
    return [rec["path"] for rec in server.records]


def _jk_posts(server) -> list[dict]:
    return [rec for rec in server.records if rec["method"] == "POST"]


_JENKINS_RUNNER = """\
import json
import os
import sys
from pathlib import Path

Path(sys.argv[0] + ".record").write_text(
    json.dumps({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}),
    encoding="utf-8",
)
sys.stdout.write("runner-out" + chr(10))
raise SystemExit(int(Path(sys.argv[0] + ".code").read_text(encoding="utf-8")))
"""


def test_jenkins_timeout_argument_does_not_fall_through():
    assert _timeout_s({}, {}, "jenkins") == 1800
    assert _timeout_s({}, {"jenkins": {}}, "jenkins") == 1800
    assert _timeout_s({}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 12
    assert _timeout_s({"timeout_s": 0}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 1800
    assert _timeout_s({"timeout_s": True}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 1800
    assert _timeout_s({"timeout_s": False}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 1800
    assert _timeout_s({}, {"jenkins": {"timeout_s": True}}, "jenkins") == 1800
    assert _timeout_s({}, {"jenkins": {"timeout_s": False}}, "jenkins") == 1800
    assert _timeout_s({}, {"jenkins": {"timeout_s": 9000}}, "jenkins") == 7200
    assert _timeout_s({"timeout_s": 30}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 30
    assert _timeout_s({"timeout_s": 1}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 1
    assert _timeout_s({"timeout_s": -5}, {"jenkins": {"timeout_s": 12}}, "jenkins") == 1800


def test_jenkins_build_streams_crumb_and_redacts(tmp_path: Path, caplog):
    import base64

    log_bytes = (
        b"alpha-line\n"
        b"JENKINS_API_TOKEN=" + _FAKE_JENKINS_TOKEN.encode() + b"\n"
        + _FAKE_GHP.encode()
        + b"\n"
        b"tail-marker\n"
    )
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "repo", base, report_status=True)
        _jk_reset(server, log=log_bytes, chunk=7, queue_delay=1, build_number=9)
        _capture_local_mcp(caplog)
        env = _jk_env(
            JENKINS_URL="http://127.0.0.1:1",
            HOME="/home/operator",
            GITHUB_TOKEN=_FAKE_GHP,
        )
        with caplog.at_level(logging.INFO, logger="memory.local_mcp"):
            result = _jk_run(
                work,
                job_path="folder/name",
                environ=env,
                extra_env={"SAFE_KEY": "plain-value", "EXTRA": "visible"},
            )
        _assert_jk(result)
        assert server.errors == []
        assert result["status_posted_by"] is None
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result["status"] == "succeeded"
        assert result["error"] is None
        assert result["skipped"] is False
        assert result["cancelled"] is False
        assert result["timed_out"] is False
        assert result["config_backend"] == "jenkins"
        assert result["probe"] == {"jenkins": True}
        assert result["job"] == {"job_path": "folder/name"}
        assert result["scm"]["kind"] == "server"
        assert result["warnings"] == []
        assert "alpha-line" in result["log_tail"]
        assert "tail-marker" in result["log_tail"]
        assert "JENKINS_API_TOKEN=****" in result["log_tail"]
        assert "ghp_" not in result["log_tail"]
        expected = "Basic " + base64.b64encode(
            f"alice:{_FAKE_JENKINS_TOKEN}".encode()
        ).decode("ascii")
        assert server.records
        assert all(rec["headers"].get("authorization") == expected for rec in server.records)
        assert server.records[0]["path"] == "/api/json"
        assert "x-agentix-crumb" not in server.records[0]["headers"]
        assert "jenkins-crumb" not in server.records[0]["headers"]
        posts = _jk_posts(server)
        assert [rec["path"] for rec in posts] == ["/job/folder/job/name/build"]
        build = posts[0]
        assert build["headers"].get("x-agentix-crumb") == "crumb-value-77"
        assert "jenkins-crumb" not in build["headers"]
        assert b"SAFE_KEY" not in build["body"]
        assert b"plain-value" not in build["body"]
        assert b"EXTRA" not in build["body"]
        assert b"GITHUB_TOKEN" not in build["body"]
        assert _FAKE_JENKINS_TOKEN.encode() not in build["body"]
        assert _FAKE_GHP.encode() not in build["body"]
        queue_paths = [path for path in _jk_paths(server) if path.startswith("/queue/")]
        assert len(queue_paths) == 2
        progressive = [rec["target"] for rec in server.records if rec["path"].endswith("/progressiveText")]
        assert len(progressive) > 1
        assert "start=0" in progressive[0]
        assert "/job/folder/job/name/9/logText/progressiveText" in progressive[0]
        assert server.stops == []
        log_path = _assert_jk_saved(work, result)
        logged = log_path.read_text(encoding="utf-8")
        assert logged == result["log_tail"]
        assert "alpha-line" in logged and "tail-marker" in logged
        assert _FAKE_JENKINS_TOKEN not in logged
        assert "ghp_" not in logged
        run_entries = [entry for entry in _audit_entries(work) if entry["action"] == "local_pipeline_run"]
        assert run_entries[-1]["role"] == "ci"
        assert run_entries[-1]["details"]["job_path"] == "folder/name"
        assert run_entries[-1]["details"]["exit_code"] == 0
        assert "job_name" not in run_entries[-1]["details"]
        assert set(run_entries[-1]["details"]) <= _AUDIT_DETAIL_KEYS
        audit_text = (work / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8")
        assert _FAKE_JENKINS_TOKEN not in audit_text
        assert _FAKE_GHP not in audit_text
        assert "crumb-value-77" not in audit_text
        assert _FAKE_JENKINS_TOKEN not in caplog.text
        assert "Basic " not in caplog.text
        assert "crumb-value-77" not in caplog.text


def test_jenkins_queue_item_over_read_cap_cancels_item(tmp_path: Path):
    import urllib.parse

    body = _fat_queue_body(9)
    assert len(body) > 1024 * 1024
    assert b'"number":9' in body
    assert b'"number":9' not in body[: 1024 * 1024]
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "repo", base)
        _jk_reset(server, queue_over_cap=True, build_number=9)
        result = _jk_run(work, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert server.errors == []
        assert result["ok"] is False
        assert result["exit_code"] == 2
        assert result["status"] == "refused"
        assert result["timed_out"] is False
        assert result["cancelled"] is False
        assert result["error"]["code"] == "jenkins_queue_truncated"
        assert "build was triggered" in result["error"]["message"].lower()
        assert "controller down" not in result["error"]["message"].lower()
        assert server.stops == []
        assert server.queue_cancels == ["/queue/cancelItem?id=1"]
        queue_gets = [
            rec
            for rec in server.records
            if rec["method"] == "GET" and rec["path"].startswith("/queue/item/")
        ]
        assert len(queue_gets) == 1
        target = urllib.parse.unquote(queue_gets[0]["target"])
        assert "tree=executable[number,url]" in target
        cancel = next(rec for rec in server.records if rec["path"] == "/queue/cancelItem")
        assert cancel["method"] == "POST"
        assert cancel["headers"].get("x-agentix-crumb") == "crumb-value-77"
        assert "id=1" in cancel["target"]
        posts = [rec["path"] for rec in _jk_posts(server)]
        assert posts[0] == "/job/folder/job/name/build"
        assert "/queue/cancelItem" in posts


def test_jenkins_finished_log_over_read_cap_keeps_end_marker(tmp_path: Path):
    marker = b"ENDMARKER9f3a"
    log_bytes = b"A" * (1024 * 1024 + 10) + b"\n" + marker + b"\n"
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "repo", base)
        _jk_reset(server, log=log_bytes, chunk=len(log_bytes), build_number=9)
        result = _jk_run(work, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result["truncated"] is False
        progressive = [rec for rec in server.records if rec["path"].endswith("/progressiveText")]
        assert len(progressive) == 2
        assert "start=0" in progressive[0]["target"]
        assert "start=1048576" in progressive[1]["target"]
        logged = _assert_jk_saved(work, result).read_bytes()
        assert marker in logged
        assert marker.decode("ascii") in result["log_tail"]


def test_jenkins_progressive_gap_sets_truncated(tmp_path: Path):
    marker = b"ENDMARKER9f3a"
    log_bytes = b"head\n" + marker + b"\n"
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "repo", base)
        _jk_reset(server, log=log_bytes, chunk=len(log_bytes), text_size_gap=40)
        result = _jk_run(work, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result["truncated"] is True
        progressive = [rec for rec in server.records if rec["path"].endswith("/progressiveText")]
        assert len(progressive) == 1
        logged = _assert_jk_saved(work, result).read_bytes()
        assert marker in logged


def test_jenkins_path_translation(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)
        rooted = _jk_project(tmp_path / "rooted", base)
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(rooted, job_path="/job/folder/job/name", environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        assert result["status_posted_by"] == "jenkins"
        assert result["job"] == {"job_path": "/job/folder/job/name"}
        assert _jk_posts(server)[0]["path"] == "/job/folder/job/name/build"

        spaced = _jk_project(tmp_path / "spaced", base)
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(spaced, job_path="folder/my job", environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        assert _jk_posts(server)[0]["path"] == "/job/folder/job/my%20job/build"


def test_jenkins_parameters_follow_allowlist(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)

        missing = _jk_project(tmp_path / "missing", base)
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(
            missing,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"BRANCH": "main", "EXTRA": "visible"},
        )
        _assert_jk(result)
        assert result["ok"] is True
        post = _jk_posts(server)[0]
        assert post["path"] == "/job/team/job/job/build"
        assert b"BRANCH" not in post["body"]
        assert b"EXTRA" not in post["body"]

        empty = _jk_project(tmp_path / "empty", base, parameters=[])
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(empty, job_path="team/job", environ=_jk_env(), extra_env={"BRANCH": "main"})
        _assert_jk(result)
        assert _jk_posts(server)[0]["path"] == "/job/team/job/job/build"

        allowed = _jk_project(tmp_path / "allowed", base, parameters=["BRANCH"])
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(
            allowed,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"BRANCH": "main", "EXTRA": "visible"},
        )
        _assert_jk(result)
        assert result["ok"] is True
        post = _jk_posts(server)[0]
        assert post["path"] == "/job/team/job/job/buildWithParameters"
        assert post["body"] == b"BRANCH=main"

        newline = _jk_project(tmp_path / "newline", base, parameters=["BRANCH"])
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(
            newline,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"BRANCH": "main\nnext"},
        )
        _assert_jk(result)
        assert result["ok"] is True
        post = _jk_posts(server)[0]
        assert post["path"].endswith("/buildWithParameters")
        assert post["body"] == b"BRANCH=main%0Anext"

        unmatched = _jk_project(tmp_path / "unmatched", base, parameters=["BRANCH"])
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(unmatched, job_path="team/job", environ=_jk_env(), extra_env={"OTHER": "x"})
        _assert_jk(result)
        post = _jk_posts(server)[0]
        assert post["path"] == "/job/team/job/job/buildWithParameters"
        assert post["body"] == b""

        proc_token = _jk_project(tmp_path / "proc", base, parameters=["GITHUB_TOKEN"])
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(
            proc_token,
            job_path="team/job",
            environ=_jk_env(GITHUB_TOKEN=_FAKE_GHP),
        )
        _assert_jk(result)
        post = _jk_posts(server)[0]
        assert post["path"].endswith("/buildWithParameters")
        assert b"GITHUB_TOKEN" not in post["body"]
        assert _FAKE_GHP.encode() not in post["body"]
        assert _FAKE_JENKINS_TOKEN.encode() not in post["body"]

        secret_key = _jk_project(tmp_path / "secret-key", base, parameters=["GITHUB_TOKEN"])
        _jk_reset(server)
        result = _jk_run(
            secret_key,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"GITHUB_TOKEN": "not-sent"},
        )
        _assert_jk(result)
        assert result["error"]["code"] == "secret_key_rejected"
        assert result["error"]["message"] == "environment variable key is not allowed"
        assert server.records == []
        assert _audit_entries(secret_key)[-1]["details"]["error_code"] == "secret_key_rejected"

        secret_value = _jk_project(tmp_path / "secret-value", base, parameters=["SAFE_KEY"])
        _jk_reset(server)
        result = _jk_run(
            secret_value,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"SAFE_KEY": _FAKE_GHP},
        )
        _assert_jk(result)
        assert result["error"]["code"] == "secret_value_rejected"
        assert server.records == []

        nul_value = _jk_project(tmp_path / "nul", base, parameters=["SAFE_KEY"])
        _jk_reset(server)
        result = _jk_run(
            nul_value,
            job_path="team/job",
            environ=_jk_env(),
            extra_env={"SAFE_KEY": "bad\x00value"},
        )
        _assert_jk(result)
        assert result["error"]["code"] == "secret_value_rejected"
        assert server.records == []


def test_jenkins_job_path_rejects_before_http(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with _jenkins_controller() as server:
        base = _jk_base(server)
        cases = [
            "folder/../name",
            "http://evil/job/a",
            "folder/name?x=1",
            "/not/under",
            "bad\x00name",
            "folder\\name",
            "//evil/job/a",
            "/job",
            "",
        ]
        for index, job_path in enumerate(cases):
            work = _jk_project(tmp_path / f"bad-{index}", base)
            _jk_reset(server)
            result = _jk_run(work, job_path=job_path, environ=_jk_env())
            _assert_jk(result)
            assert result["error"]["code"] == "path_escape"
            assert result["error"]["message"] == "Choose a path inside the project."
            assert result["exit_code"] == 2
            assert result["probe"] == {}
            assert server.records == []
            assert len(_jk_saved(work)) == 1
            assert _audit_entries(work)[-1]["details"]["error_code"] == "path_escape"

        both = _jk_project(tmp_path / "both", base)
        _jk_reset(server)
        result = run_local_pipeline(
            {
                "backend": "jenkins",
                "working_directory": str(both),
                "job_path": "folder/name",
                "jenkinsfile_path": "Jenkinsfile",
            },
            allow_root=both,
            environ=_jk_env(),
        )
        _assert_jk(result)
        assert result["error"]["code"] == "path_escape"
        assert result["job"] == {"job_path": ""}
        assert server.records == []

        numeric = _jk_project(tmp_path / "numeric", base)
        _jk_reset(server)
        result = run_local_pipeline(
            {
                "backend": "jenkins",
                "working_directory": str(numeric),
                "job_path": 12,
                "timeout_s": 5,
            },
            allow_root=numeric,
            environ=_jk_env(),
        )
        _assert_jk(result)
        assert result["error"]["code"] == "path_escape"
        assert result["job"] == {"job_path": ""}
        assert server.records == []
        assert "/job/invalid/" not in json.dumps(_jk_paths(server))


def test_jenkins_off_origin_is_rejected(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)

        build = _jk_project(tmp_path / "build", base)
        _jk_reset(server, build_location="off")
        result = _jk_run(build, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"] == {
            "code": "jenkins_redirect_rejected",
            "message": "Pin jenkins.url to the controller origin.",
        }
        assert result["probe"] == {"jenkins": True}
        assert not any(path.startswith("/queue/") for path in _jk_paths(server))
        assert server.stops == []
        assert len(_jk_saved(build)) == 1

        crumb = _jk_project(tmp_path / "crumb", base)
        _jk_reset(server, crumb_status=302, crumb_location="off")
        result = _jk_run(crumb, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_redirect_rejected"
        assert _jk_paths(server) == ["/api/json", "/crumbIssuer/api/json"]
        assert server.stops == []

        same = _jk_project(tmp_path / "same-build", base)
        _jk_reset(server, build_status=302, build_location="same")
        result = _jk_run(same, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_unreachable"
        assert result["error"]["message"] == "Controller down, or URL/DNS/firewall."
        assert not any(path.startswith("/queue/") for path in _jk_paths(server))
        assert server.stops == []

        executable = _jk_project(tmp_path / "exec", base)
        _jk_reset(server, executable_url="off")
        result = _jk_run(executable, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_redirect_rejected"
        assert any(path.startswith("/queue/") for path in _jk_paths(server))
        assert server.stops == []


def test_jenkins_probe_failure_does_not_post(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)
        for status in (401, 403):
            work = _jk_project(tmp_path / f"auth-{status}", base)
            _jk_reset(server, api_status=status, api_body=f"denied {_FAKE_JENKINS_TOKEN}".encode())
            result = _jk_run(work, job_path="folder/name", environ=_jk_env())
            _assert_jk(result)
            assert result["error"] == {
                "code": "jenkins_auth",
                "message": "Fix JENKINS_USER / JENKINS_API_TOKEN in the environment.",
            }
            assert result["job"] == {"job_path": "configured/job"}
            assert result["scm"]["kind"] is None
            assert result["skipped"] is False
            assert _jk_paths(server) == ["/api/json"]
            _assert_jk_uncommitted(work)
            actions = [entry["action"] for entry in _audit_entries(work)]
            assert actions == ["local_pipeline_probe_failed"]
            audit_text = (work / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8")
            assert _FAKE_JENKINS_TOKEN not in audit_text

        off = _jk_project(tmp_path / "off", base)
        _jk_reset(server, api_status=302, api_location="off")
        result = _jk_run(off, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_redirect_rejected"
        assert _jk_paths(server) == ["/api/json"]
        _assert_jk_uncommitted(off)
        assert [entry["action"] for entry in _audit_entries(off)] == ["local_pipeline_probe_failed"]

        same = _jk_project(tmp_path / "same", base)
        _jk_reset(server, api_status=302, api_location="same")
        result = _jk_run(same, job_path="folder/name", environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_unreachable"
        assert len(server.records) == 1
        _assert_jk_uncommitted(same)


def test_jenkins_stop_only_this_build(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)
        cancel_work = _jk_project(tmp_path / "cancel", base)
        _jk_reset(server, log_hang=True, build_number=9)
        cancel = threading.Event()
        box: list = []

        def invoke() -> None:
            try:
                box.append(("ok", _jk_run(cancel_work, environ=_jk_env(), timeout_s=8, cancel=cancel)))
            except Exception as exc:
                box.append(("err", exc))

        thread = threading.Thread(target=invoke)
        thread.start()
        found = False
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if any("progressiveText" in rec["path"] for rec in list(server.records)):
                    found = True
                    break
                time.sleep(0.02)
            cancel.set()
            thread.join(timeout=10)
        finally:
            cancel.set()
            thread.join(timeout=10)
        assert found and not thread.is_alive() and box
        assert box[0][0] == "ok"
        result = box[0][1]
        _assert_jk(result)
        assert result["exit_code"] == 130
        assert result["cancelled"] is True
        assert result["timed_out"] is False
        assert result["status"] == "cancelled"
        assert result["error"] == {"code": "cancelled", "message": "run cancelled"}
        assert server.stops == ["/job/folder/job/name/9/stop"]
        stop = next(rec for rec in server.records if rec["path"].endswith("/stop"))
        assert stop["method"] == "POST"
        assert stop["headers"].get("x-agentix-crumb") == "crumb-value-77"
        assert "jenkins-crumb" not in stop["headers"]
        actions = [entry["action"] for entry in _audit_entries(cancel_work)]
        assert "local_pipeline_cancelled" in actions
        assert "local_pipeline_run" not in actions

        timed = _jk_project(tmp_path / "timed", base)
        _jk_reset(server, log_hang=True, build_number=9)
        started = time.monotonic()
        result = _jk_run(timed, environ=_jk_env(), timeout_s=2)
        elapsed = time.monotonic() - started
        _assert_jk(result)
        assert elapsed < 20
        assert result["exit_code"] == 124
        assert result["timed_out"] is True
        assert result["cancelled"] is False
        assert result["error"] == {
            "code": "timed_out",
            "message": "Jenkins build exceeded timeout_s",
        }
        assert result["duration_ms"] < 20000
        assert server.stops == ["/job/folder/job/name/9/stop"]

        queued = _jk_project(tmp_path / "queued", base)
        _jk_reset(server, queue_delay=10**9, log_hang=True)
        started = time.monotonic()
        result = _jk_run(queued, environ=_jk_env(), timeout_s=1)
        elapsed = time.monotonic() - started
        _assert_jk(result)
        assert elapsed < 20
        assert result["exit_code"] == 124
        assert result["timed_out"] is True
        assert server.stops == []
        assert not any(path.endswith("/stop") for path in _jk_paths(server))

        preset = _jk_project(tmp_path / "preset", base)
        _jk_reset(server)
        cancel = threading.Event()
        cancel.set()
        result = _jk_run(preset, environ=_jk_env(), timeout_s=5, cancel=cancel)
        _assert_jk(result)
        assert result["exit_code"] == 130
        assert result["cancelled"] is True
        assert server.records == []
        assert server.stops == []
        assert [entry["action"] for entry in _audit_entries(preset)] == ["local_pipeline_cancelled"]


def test_jenkins_result_codes(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)
        cases = (
            ("UNSTABLE", "nope", None),
            ("FAILURE", "cloud", "cloud"),
            ("ABORTED", "server", "server"),
        )
        for name, kind, expected in cases:
            work = _jk_project(tmp_path / name, base, scm_kind=kind)
            _jk_reset(server, result=name, log=b"line\n")
            result = _jk_run(work, environ=_jk_env())
            _assert_jk(result)
            assert result["exit_code"] == 1
            assert result["ok"] is False
            assert result["status"] == "failed"
            assert result["cancelled"] is False
            assert result["timed_out"] is False
            assert result["error"] == {"code": "job_failed", "message": "Jenkins job failed"}
            assert result["scm"]["kind"] == expected
            assert server.stops == []


def test_jenkins_crumb_field_and_requirement(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)

        missing = _jk_project(tmp_path / "missing", base)
        _jk_reset(server, crumb_status=404)
        result = _jk_run(missing, environ=_jk_env())
        _assert_jk(result)
        assert result["error"] == {
            "code": "jenkins_crumb_unavailable",
            "message": "Fix CSRF settings. Do not turn the requirement off by default.",
        }
        assert result["probe"] == {"jenkins": True}
        assert not any(path.endswith("/build") or "buildWithParameters" in path for path in _jk_paths(server))

        optional = _jk_project(tmp_path / "optional", base, crumb_required=False)
        _jk_reset(server, crumb_status=404, log=b"ok\n")
        result = _jk_run(optional, environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        post = _jk_posts(server)[0]
        assert post["path"].endswith("/build")
        assert "x-agentix-crumb" not in post["headers"]
        assert "jenkins-crumb" not in post["headers"]

        blank = _jk_project(tmp_path / "blank", base)
        _jk_reset(server, crumb="")
        result = _jk_run(blank, environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_crumb_unavailable"
        assert not any(rec["method"] == "POST" for rec in server.records)

        omitted = _jk_project(tmp_path / "omitted", base)
        _jk_reset(server, crumb_body=b"{}")
        result = _jk_run(omitted, environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_crumb_unavailable"
        assert not any(rec["method"] == "POST" for rec in server.records)

        stolen = _jk_project(tmp_path / "stolen", base)
        _jk_reset(server, crumb_field="Authorization", crumb="evil")
        result = _jk_run(stolen, environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "jenkins_crumb_unavailable"
        assert not any(rec["method"] == "POST" for rec in server.records)

        standard = _jk_project(tmp_path / "standard", base)
        _jk_reset(server, crumb_field="Jenkins-Crumb", crumb="standard-crumb", log=b"ok\n")
        result = _jk_run(standard, environ=_jk_env())
        _assert_jk(result)
        assert result["ok"] is True
        post = _jk_posts(server)[0]
        assert post["headers"].get("jenkins-crumb") == "standard-crumb"
        assert "x-agentix-crumb" not in post["headers"]


def test_jenkins_controller_required(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with _jenkins_controller() as server:
        base = _jk_base(server)
        variants = (
            ("unset", {}),
            ("relative", {"local_runner": "jenkinsfile-runner"}),
            ("missing", {"local_runner": str(tmp_path / "no-such-runner-9f3a")}),
        )
        for name, jenkins in variants:
            work = _jk_project(tmp_path / name, base, **jenkins)
            (work / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")
            _jk_reset(server)
            result = _jk_run(work, jenkinsfile_path="Jenkinsfile", environ=_jk_env())
            _assert_jk(result)
            assert result["error"] == {
                "code": "jenkins_controller_required",
                "message": "Point at a job, or install a local runner binary yourself.",
            }
            assert result["exit_code"] == 2
            assert result["job"] == {"jenkinsfile_path": "Jenkinsfile"}
            assert server.records == []
            assert _jk_saved(work)


def test_jenkins_local_runner_has_no_home(tmp_path: Path):
    binary = tmp_path / "bin" / "jenkinsfile-runner"
    _write_exe(binary, _JENKINS_RUNNER)
    resolved = binary.resolve()
    Path(str(resolved) + ".code").write_text("0", encoding="utf-8")
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "repo", base, local_runner=str(resolved))
        (work / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/only-test",
            "HOME": "/home/should-not-leak",
            "DOCKER_HOST": "tcp://evil:2375",
            "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/py",
            "GITHUB_TOKEN": _FAKE_GHP,
            "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
            "JENKINS_USER": "alice",
            "LANG": "C",
            "USER": "op",
            "LC_CTYPE": "evil-locale",
        }
        result = _jk_run(
            work,
            jenkinsfile_path="Jenkinsfile",
            environ=env,
            extra_env={"SAFE_KEY": "plain", "HOME": "/home/from-the-call"},
        )
        _assert_jk(result)
        assert server.records == []
        assert result["ok"] is True
        assert result["status_posted_by"] is None
        assert result["exit_code"] == 0
        assert result["job"] == {"jenkinsfile_path": "Jenkinsfile"}
        assert "stdout: runner-out" in result["log_tail"]
        info = json.loads(Path(str(resolved) + ".record").read_text(encoding="utf-8"))
        jenkinsfile = str((work / "Jenkinsfile").resolve())
        assert info["argv"] == [str(resolved), jenkinsfile]
        assert Path(info["cwd"]).resolve() == work.resolve()
        assert info["env"].get("PATH") == "/usr/bin:/only-test"
        assert info["env"].get("LANG") == "C"
        assert info["env"].get("USER") == "op"
        # The interpreter may coerce LC_CTYPE after start. The caller value must not survive.
        assert info["env"].get("SAFE_KEY") == "plain"
        assert set(info["env"]) <= {"PATH", "LANG", "USER", "LOGNAME", "LC_CTYPE", "SAFE_KEY"}
        assert info["env"].get("LC_CTYPE") != "evil-locale"
        for banned in (
            "HOME",
            "DOCKER_HOST",
            "LD_PRELOAD",
            "PYTHONPATH",
            "GITHUB_TOKEN",
            "JENKINS_API_TOKEN",
            "JENKINS_USER",
        ):
            assert banned not in info["env"]
        assert _FAKE_GHP not in json.dumps(info)
        assert _FAKE_JENKINS_TOKEN not in json.dumps(info)

        Path(str(resolved) + ".code").write_text("3", encoding="utf-8")
        failed = _jk_project(tmp_path / "failed", base, local_runner=str(resolved))
        (failed / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")
        result = _jk_run(failed, jenkinsfile_path="Jenkinsfile", environ=env)
        _assert_jk(result)
        assert result["exit_code"] == 1
        assert result["ok"] is False
        assert result["error"]["code"] == "job_failed"
        assert "stdout: runner-out" in result["log_tail"]
        assert server.records == []


def test_jenkins_local_runner_refuses_escape_and_host_env(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    binary = tmp_path / "bin" / "jenkinsfile-runner"
    _write_exe(binary, _JENKINS_RUNNER)
    resolved = binary.resolve()
    record = Path(str(resolved) + ".record")

    outside = tmp_path / "outside-jenkinsfile"
    outside.write_text("pipeline {}\n", encoding="utf-8")
    linked = tmp_path / "linked"
    _jk_project(linked, None, local_runner=str(resolved))
    (linked / "Jenkinsfile").symlink_to(outside)
    result = _jk_run(linked, jenkinsfile_path="Jenkinsfile", environ=_jk_env())
    _assert_jk(result)
    assert result["error"]["code"] == "path_escape"
    assert not record.exists()

    parent = tmp_path / "parent"
    (parent / "out").mkdir(parents=True)
    (parent / "out" / "Jenkinsfile").write_text("x\n", encoding="utf-8")
    repo = parent / "repo"
    _jk_project(repo, None, local_runner=str(resolved))
    result = _jk_run(repo, jenkinsfile_path="../out/Jenkinsfile", environ=_jk_env())
    _assert_jk(result)
    assert result["error"]["code"] == "path_escape"
    assert result["error"]["message"] == "Choose a path inside the project."

    nulled = tmp_path / "nulled"
    _jk_project(nulled, None, local_runner=str(resolved))
    (nulled / "Jenkinsfile").write_text("x\n", encoding="utf-8")
    result = _jk_run(nulled, jenkinsfile_path="Jen\x00file", environ=_jk_env())
    _assert_jk(result)
    assert result["error"]["code"] == "path_escape"

    hosted = tmp_path / "hosted"
    _jk_project(hosted, None, local_runner=str(resolved))
    (hosted / "Jenkinsfile").write_text("x\n", encoding="utf-8")
    for key in ("PATH", "LD_PRELOAD", "PYTHONPATH", "DOCKER_HOST"):
        result = _jk_run(
            hosted,
            jenkinsfile_path="Jenkinsfile",
            environ=_jk_env(),
            extra_env={key: "/evil"},
        )
        _assert_jk(result)
        assert result["error"]["code"] == "secret_key_rejected"
    assert not record.exists()


def test_jenkins_busy_disabled_and_escape(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        del args, kwargs
        raise AssertionError("process spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with _jenkins_controller() as server:
        base = _jk_base(server)

        disabled = _jk_project(tmp_path / "disabled", base, enabled=False)
        result = _jk_run(disabled, environ=_jk_env())
        _assert_jk(result)
        assert result["skipped"] is True
        assert result["duration_ms"] == 0
        assert result["error"] == {
            "code": "local_pipeline_disabled",
            "message": "local pipeline is disabled",
        }
        assert server.records == []
        assert _audit_entries(disabled) == []
        _assert_jk_uncommitted(disabled)

        busy = _jk_project(tmp_path / "busy", base)
        started = time.monotonic()
        with agent_lock(busy / ".agent", name="local_pipeline", timeout=2):
            result = _jk_run(busy, environ=_jk_env())
        assert time.monotonic() - started < 2
        _assert_jk(result)
        assert result["error"] == {
            "code": "pipeline_busy",
            "message": "A run is in progress in this workdir.",
        }
        assert server.records == []
        assert _audit_entries(busy) == []
        _assert_jk_uncommitted(busy)

        root = tmp_path / "root"
        root.mkdir()
        outside = _jk_project(tmp_path / "outside", base)
        result = _jk_run(outside, allow_root=root, environ=_jk_env())
        _assert_jk(result)
        assert result["error"]["code"] == "path_escape"
        assert result["error"]["message"] == "Choose a path inside the project."
        assert result["config_backend"] == "act"
        assert server.records == []
        assert _audit_entries(outside) == []
        _assert_jk_uncommitted(outside)

        result = run_local_pipeline(
            {
                "backend": "jenkins",
                "working_directory": "bad\x00dir",
                "job_path": "folder/name",
            },
            allow_root=root,
            environ=_jk_env(),
        )
        _assert_jk(result)
        assert result["error"]["code"] == "path_escape"
        assert result["config_backend"] == "act"
        assert server.records == []


def test_jenkins_url_fallback_and_bad_port(tmp_path: Path):
    with _jenkins_controller() as server:
        base = _jk_base(server)
        work = _jk_project(tmp_path / "env-url", None)
        _jk_reset(server, log=b"ok\n")
        result = _jk_run(work, environ=_jk_env(JENKINS_URL=base))
        _assert_jk(result)
        assert result["ok"] is True
        assert server.records
        assert _jk_posts(server)[0]["path"] == "/job/folder/job/name/build"

    bad = _jk_project(tmp_path / "bad-port", "http://127.0.0.1:99999")
    result = _jk_run(bad, environ=_jk_env(), timeout_s=5)
    _assert_jk(result)
    assert result["error"] == {
        "code": "jenkins_unreachable",
        "message": "Controller down, or URL/DNS/firewall.",
    }
    _assert_jk_uncommitted(bad)
    assert [entry["action"] for entry in _audit_entries(bad)] == ["local_pipeline_probe_failed"]


def _slack_project(
    work: Path,
    *,
    notify: object = True,
    enabled: bool = False,
    backend: str = "act",
    slack: dict | None = None,
    governance: bool | None = True,
) -> None:
    body: dict = {
        "local_pipeline": {
            "enabled": enabled,
            "backend": backend,
            "notify": notify,
        }
    }
    if slack is not None:
        body["integrations"] = {"slack": slack}
    if governance is not None:
        body["tier"] = {"feature_flags": {"enterprise_governance": governance}}
    _write_project_config(work, body)


def _slack_body(req) -> dict:
    raw = req.data or b""
    return json.loads(raw.decode("utf-8"))


class _FakeSlackResp:
    """Ответ urlopen: webhook и chat.postMessage читают status и тело."""

    def __init__(self, raw: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(raw)
        self.status = status

    def read(self) -> bytes:
        return self._buf.read()

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeSlackResp":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeHttp:
    """Подмена urlopen только для Slack. Трекеры hub сюда не входят."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, req: Any, timeout: float | None = None) -> _FakeSlackResp:
        self.calls.append(req)
        url = str(getattr(req, "full_url", ""))
        if "hooks.slack.com" in url or "slack.com/api" in url:
            return _FakeSlackResp(b'{"ok": true}')
        raise AssertionError(f"unexpected url {url}")


def test_notify_text_posts_ci_audit_not_reviewer(tmp_path: Path, monkeypatch):
    import urllib.error

    from memory.integrations.slack import notify_text

    def _boom(*_args, **_kwargs):
        raise AssertionError("handoff cycle helper")

    monkeypatch.setattr("memory.integrations.slack._cycle_number", _boom)

    work = tmp_path / "hook"
    work.mkdir()
    _slack_project(
        work,
        slack={"enabled": True, "channel": "#ci"},
        governance=True,
    )
    (work / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": True}),
        encoding="utf-8",
    )
    fake = FakeHttp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/XXX")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    posted = notify_text(
        work,
        "act harness exit=1 duration_ms=4 .agent/local-pipeline-runs/x.log",
        {
            "source": "local_pipeline",
            "backend": "act",
            "exit_code": 1,
            "status": "DONE",
            "text": "do-not-audit",
            "url": "https://example.invalid/commit",
        },
    )
    assert posted["ok"] is True
    assert posted["skipped"] is False
    assert fake.calls
    body = _slack_body(fake.calls[-1])
    assert body["channel"] == "#ci"
    assert body["text"] == "act harness exit=1 duration_ms=4 .agent/local-pipeline-runs/x.log"
    assert "\n" not in body["text"]
    assert "Agentix Reviewer" not in body["text"]
    assert "://" not in body["text"]
    assert set(body) == {"text", "channel"}
    audit = _audit_entries(work)
    assert len(audit) == 1
    assert audit[0]["action"] == "slack_notify"
    assert audit[0]["role"] == "ci"
    assert audit[0]["cycle"] == 0
    assert audit[0]["details"] == {
        "source": "local_pipeline",
        "backend": "act",
        "exit_code": 1,
        "channel": "#ci",
        "ok": True,
    }

    numbered = tmp_path / "numbered"
    numbered.mkdir()
    _slack_project(numbered, slack={"enabled": True, "channel": "#ci"})
    (numbered / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": "9"}),
        encoding="utf-8",
    )
    fake.calls.clear()
    notify_text(
        numbered,
        "act harness exit=0 duration_ms=1 log",
        {"source": "local_pipeline", "backend": "act", "exit_code": 0},
    )
    assert _audit_entries(numbered)[-1]["cycle"] == 0

    real_int = tmp_path / "real-int"
    real_int.mkdir()
    _slack_project(real_int, slack={"enabled": True, "channel": "#ci"})
    (real_int / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 11}),
        encoding="utf-8",
    )
    notify_text(
        real_int,
        "act harness exit=0 duration_ms=1 log",
        {"source": "local_pipeline", "backend": "act", "exit_code": False},
    )
    entry = _audit_entries(real_int)[-1]
    assert entry["cycle"] == 11
    assert entry["role"] == "ci"
    assert "exit_code" not in entry["details"]
    assert "status" not in entry["details"]

    bot = tmp_path / "bot"
    bot.mkdir()
    _slack_project(bot, slack={"enabled": True, "channel": "#ci"})
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-a-real-token")
    before = len(fake.calls)
    bot_report = notify_text(
        bot,
        "jenkins folder/name exit=2 duration_ms=0 log",
        {"source": "local_pipeline", "backend": "jenkins", "exit_code": 2},
    )
    assert bot_report["ok"] is True
    assert len(fake.calls) == before + 1
    bot_req = fake.calls[-1]
    assert "chat.postMessage" in bot_req.full_url
    assert _slack_body(bot_req)["text"].startswith("jenkins folder/name")
    assert "Agentix Reviewer" not in _slack_body(bot_req)["text"]
    assert _audit_entries(bot)[-1]["role"] == "ci"

    closed = tmp_path / "closed"
    closed.mkdir()
    _slack_project(
        closed,
        slack={"enabled": True, "channel": "#ci"},
        governance=False,
    )
    before = len(fake.calls)
    skipped = notify_text(closed, "act harness exit=2 duration_ms=0 log", {})
    assert skipped["skipped"] is True
    assert len(fake.calls) == before
    assert _audit_entries(closed) == []

    dry = tmp_path / "dry"
    dry.mkdir()
    _slack_project(dry, slack={"enabled": True, "channel": "#ci"})
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/XXX")
    dry_report = notify_text(
        dry,
        "act harness exit=0 duration_ms=1 log",
        {"source": "local_pipeline", "backend": "act", "exit_code": 0},
        dry_run=True,
    )
    assert dry_report["ok"] is True
    assert dry_report["reason"] == "dry_run"
    assert len(fake.calls) == before
    assert _audit_entries(dry) == []
    assert not (dry / ".agent" / "LOOP_STATE.json").exists()

    broken = tmp_path / "broken"
    broken.mkdir()
    _slack_project(broken, slack={"enabled": True, "channel": "#ci"})

    def _offline(_req, timeout=None):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr("urllib.request.urlopen", _offline)
    failed = notify_text(
        broken,
        "act harness exit=1 duration_ms=2 log",
        {"source": "local_pipeline", "backend": "act", "exit_code": 1},
    )
    assert failed["ok"] is False
    assert failed["reason"] == "http"
    assert _audit_entries(broken)[-1]["details"]["ok"] is False
    assert "status" not in _audit_entries(broken)[-1]["details"]


def test_pipeline_notify_gate_message_and_fail_open(tmp_path: Path, monkeypatch, caplog):
    import urllib.error

    from memory.local_mcp.notify import notify_pipeline_result

    bridge = (REPO / "memory" / "local_mcp" / "notify.py").read_text(encoding="utf-8")
    assert "notify_slack" not in bridge
    assert "close_done_issues" not in bridge
    assert "build_message" not in bridge
    assert "_cycle_number" not in bridge
    supervisor = (REPO / "memory" / "supervisor.py").read_text(encoding="utf-8")
    assert "notify_text" not in supervisor
    assert "notify_pipeline_result" not in supervisor
    slack_src = (REPO / "memory" / "integrations" / "slack.py").read_text(encoding="utf-8")
    assert "memory.local_mcp" not in slack_src

    fake = FakeHttp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/XXX")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    def _no_spawn(*_args, **_kwargs):
        raise AssertionError("pipeline spawned")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)

    off = tmp_path / "off"
    off.mkdir()
    _slack_project(off, notify=False, slack={"enabled": True, "channel": "#agentix"})
    result = run_local_pipeline(
        {"backend": "act", "working_directory": str(off)},
        allow_root=off,
    )
    assert result["exit_code"] == 2
    assert result["error"]["code"] == "local_pipeline_disabled"
    assert fake.calls == []
    assert _audit_entries(off) == []

    for flag in ("true", 1, None):
        work = tmp_path / f"flag-{flag}"
        work.mkdir()
        _slack_project(work, notify=flag, slack={"enabled": True, "channel": "#agentix"})
        run_local_pipeline(
            {"backend": "act", "working_directory": str(work)},
            allow_root=work,
        )
    assert fake.calls == []

    shut = tmp_path / "shut"
    shut.mkdir()
    _slack_project(
        shut,
        slack={"enabled": True, "channel": "#agentix", "force": False},
        governance=False,
    )
    run_local_pipeline(
        {"backend": "act", "working_directory": str(shut)},
        allow_root=shut,
    )
    assert fake.calls == []
    assert _audit_entries(shut) == []

    forced = tmp_path / "forced"
    forced.mkdir()
    _slack_project(
        forced,
        slack={"enabled": True, "channel": "#agentix", "force": True},
        governance=False,
    )
    forced_result = run_local_pipeline(
        {"backend": "act", "working_directory": str(forced)},
        allow_root=forced,
    )
    assert forced_result["exit_code"] == 2
    assert len(fake.calls) == 1
    assert _slack_body(fake.calls[-1])["text"] == (
        "act harness exit=2 duration_ms=0 "
        ".agent/local-pipeline-runs/disabled-act.log"
    )

    fake.calls.clear()
    cases = (
        (
            "act",
            {"backend": "act", "working_directory": ""},
            "act harness exit=2 duration_ms=0 .agent/local-pipeline-runs/disabled-act.log",
        ),
        (
            "jenkins",
            {"backend": "jenkins", "job_path": "folder/name"},
            "jenkins folder/name exit=2 duration_ms=0 "
            ".agent/local-pipeline-runs/disabled-jenkins.log",
        ),
        (
            "jenkinsfile",
            {"backend": "jenkins", "jenkinsfile_path": "path/Jenkinsfile"},
            "jenkins path/Jenkinsfile exit=2 duration_ms=0 "
            ".agent/local-pipeline-runs/disabled-jenkins.log",
        ),
        (
            "hosted",
            {"backend": "self_hosted", "runner_labels": ["linux"]},
            "self_hosted exit=2 duration_ms=0 "
            ".agent/local-pipeline-runs/disabled-self_hosted.log",
        ),
    )
    for name, args, expected in cases:
        work = tmp_path / name
        work.mkdir()
        _slack_project(work, slack={"enabled": True, "channel": "#agentix"})
        args = dict(args)
        args["working_directory"] = str(work)
        result = run_local_pipeline(args, allow_root=work)
        assert result_errors(result) == []
        assert result["exit_code"] == 2
        assert result["skipped"] is True
        assert "SECRET_TAIL" not in json.dumps(result.get("log_tail"))
        assert _slack_body(fake.calls[-1])["text"] == expected
        assert "\n" not in _slack_body(fake.calls[-1])["text"]
        assert "://" not in _slack_body(fake.calls[-1])["text"]
        details = _audit_entries(work)[-1]["details"]
        assert details == {
            "source": "local_pipeline",
            "backend": args["backend"],
            "exit_code": 2,
            "channel": "#agentix",
            "ok": True,
        }
        assert _audit_entries(work)[-1]["role"] == "ci"
        blob = json.dumps(details)
        assert "hooks.slack.com" not in blob
        assert "SECRET" not in blob
        assert "text" not in details
    assert len(fake.calls) == len(cases)

    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    _slack_project(
        outside,
        enabled=True,
        slack={"enabled": True, "channel": "#agentix"},
    )
    before_escape = len(fake.calls)
    escaped = run_local_pipeline(
        {"backend": "act", "working_directory": str(outside)},
        allow_root=root,
    )
    assert escaped["error"]["code"] == "path_escape"
    assert escaped["exit_code"] == 2
    assert len(fake.calls) == before_escape
    assert _audit_entries(outside) == []
    assert not (outside / ".agent" / "LOOP_STATE.json").exists()

    linked = tmp_path / "linked-out"
    linked.mkdir()
    _slack_project(linked, enabled=True, slack={"enabled": True, "channel": "#agentix"})
    jail = tmp_path / "jail"
    jail.mkdir()
    escape_link = jail / "escape"
    escape_link.symlink_to(linked, target_is_directory=True)
    linked_escape = run_local_pipeline(
        {"backend": "act", "working_directory": str(escape_link)},
        allow_root=jail,
    )
    assert linked_escape["error"]["code"] == "path_escape"
    assert len(fake.calls) == before_escape
    assert _audit_entries(linked) == []
    assert not (linked / ".agent" / "LOOP_STATE.json").exists()

    busy = tmp_path / "busy"
    busy.mkdir()
    _slack_project(busy, enabled=True, slack={"enabled": True, "channel": "#agentix"})
    with agent_lock(busy / ".agent", name="local_pipeline", timeout=2):
        held = run_local_pipeline(
            {"backend": "act", "working_directory": str(busy)},
            allow_root=busy,
        )
    assert held["error"]["code"] == "pipeline_busy"
    assert held["exit_code"] == 2
    assert "pipeline-busy.log" in _slack_body(fake.calls[-1])["text"]

    crafted = tmp_path / "crafted"
    crafted.mkdir()
    _slack_project(crafted, slack={"enabled": True, "channel": "#agentix"})
    tail = "SECRET_TAIL_9f3a https://secret.example/a ghp_abcdefghijklmnopqrstuvwxyz"
    notify_pipeline_result(
        {"working_directory": str(crafted)},
        {
            "backend": "act",
            "exit_code": 1,
            "duration_ms": 15,
            "log_path": ".agent/local-pipeline-runs/x.log",
            "log_tail": tail,
            "job": {"job_name": "har\nness", "job_path": "https://evil.example/job"},
        },
        allow_root=crafted,
    )
    text = _slack_body(fake.calls[-1])["text"]
    assert text == "act har ness exit=1 duration_ms=15 .agent/local-pipeline-runs/x.log"
    assert "SECRET_TAIL" not in text
    assert "://" not in text
    assert "ghp_" not in text
    assert "\n" not in text
    assert _audit_entries(crafted)[-1]["details"]["exit_code"] == 1
    assert tail not in json.dumps(_audit_entries(crafted)[-1])

    nulled = run_local_pipeline(
        {"backend": "act", "working_directory": "bad\x00dir"},
        allow_root=root,
    )
    assert nulled["error"]["code"] == "path_escape"
    assert nulled["exit_code"] == 2

    def _offline(_req, timeout=None):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr("urllib.request.urlopen", _offline)
    _capture_local_mcp(caplog)
    down = tmp_path / "down"
    down.mkdir()
    _slack_project(down, slack={"enabled": True, "channel": "#agentix"})
    with caplog.at_level(logging.WARNING, logger="memory.integrations"):
        survived = run_local_pipeline(
            {"backend": "act", "working_directory": str(down)},
            allow_root=down,
        )
    assert survived["exit_code"] == 2
    assert survived["skipped"] is True
    assert survived["error"]["code"] == "local_pipeline_disabled"
    assert result_errors(survived) == []
    assert _audit_entries(down)[-1]["details"]["ok"] is False
    assert "hooks.slack.com" not in json.dumps(_audit_entries(down))

    def _explode(*_args, **_kwargs):
        raise RuntimeError("slack down")

    monkeypatch.setattr("memory.local_mcp.notify.notify_text", _explode)
    boom = tmp_path / "boom"
    boom.mkdir()
    _slack_project(boom, slack={"enabled": True, "channel": "#agentix"})
    with caplog.at_level(logging.WARNING, logger="memory.local_mcp"):
        still = run_local_pipeline(
            {"backend": "jenkins", "working_directory": str(boom), "job_path": "folder/name"},
            allow_root=boom,
        )
    assert still["exit_code"] == 2
    assert still["error"]["code"] == "local_pipeline_disabled"
    assert result_errors(still) == []
    assert "local pipeline slack notice failed" in caplog.text
    assert "hooks.slack.com" not in caplog.text


def test_notify_skips_agent_symlink_outside(tmp_path: Path, monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/XXX")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    def _no_spawn(*_args, **_kwargs):
        raise AssertionError("pipeline spawned")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)

    secret = "OUTSIDE_SECRET_9f3a"
    config = {
        "local_pipeline": {"enabled": False, "backend": "act", "notify": True},
        "integrations": {"slack": {"enabled": True, "channel": "#ci", "force": True}},
    }
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "LOOP_STATE.md").write_text(secret, encoding="utf-8")
    (outside / "project_config.json").write_text(json.dumps(config), encoding="utf-8")
    work = root / "proj"
    work.mkdir()
    (work / ".agent").symlink_to(outside, target_is_directory=True)
    before = sorted(path.name for path in outside.iterdir())
    result = run_local_pipeline(
        {"backend": "act", "working_directory": str(work)},
        allow_root=root,
    )
    assert result["exit_code"] == 2
    # Issue 19 locks a .agent symlink as path_escape before enabled is read.
    assert result["error"]["code"] == "path_escape"
    assert fake.calls == []
    assert (outside / "LOOP_STATE.md").read_text(encoding="utf-8") == secret
    assert not (outside / "AUDIT_LOG.json").exists()
    assert not (outside / "AUDIT_LOG.md").exists()
    assert not (outside / "LOOP_STATE.json").exists()
    assert sorted(path.name for path in outside.iterdir()) == before

    inside_root = tmp_path / "inside-root"
    inside_root.mkdir()
    inside = inside_root / "proj"
    inside.mkdir()
    nested = inside / "nested"
    nested.mkdir()
    (nested / "LOOP_STATE.md").write_text(secret, encoding="utf-8")
    (nested / "project_config.json").write_text(json.dumps(config), encoding="utf-8")
    (inside / ".agent").symlink_to(nested, target_is_directory=True)
    inside_result = run_local_pipeline(
        {"backend": "act", "working_directory": str(inside)},
        allow_root=inside_root,
    )
    assert inside_result["exit_code"] == 2
    assert inside_result["error"]["code"] == "path_escape"
    assert fake.calls == []
    assert (nested / "LOOP_STATE.md").read_text(encoding="utf-8") == secret
    assert not (nested / "AUDIT_LOG.json").exists()
    assert not (nested / "LOOP_STATE.json").exists()


def _relay_project(
    work: Path,
    *,
    enabled: bool = True,
    backend: str = "jenkins",
    webhook_relay: bool = True,
    url: str = "http://127.0.0.1:9",
    job_path: str = "configured/job",
    secret_file: str | None = None,
    report_status: bool = False,
) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    jenkins: dict[str, Any] = {
        "url": url,
        "job_path": job_path,
        "crumb_required": True,
        "report_status": report_status,
        "webhook_relay": webhook_relay,
    }
    if secret_file is not None:
        jenkins["webhook_secret_file"] = secret_file
    _write_project_config(
        work,
        {
            "local_pipeline": {
                "enabled": enabled,
                "backend": backend,
                "jenkins": jenkins,
                "scm": {"kind": "server", "bitbucket_url": None},
            }
        },
    )
    return work


def _hmac_headers(
    secret: str,
    body: bytes,
    request_id: str | None = "req-1",
    which: str = "X-Hub-Signature",
) -> dict[str, str]:
    import hashlib
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {which: "sha256=" + digest}
    if request_id is not None:
        headers["X-Request-Id"] = request_id
    return headers


def _scripted_http():
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            raw_len = self.headers.get("Content-Length")
            try:
                length = int(raw_len) if raw_len else 0
            except ValueError:
                length = 0
            payload = self.rfile.read(length) if length else b""
            self.server.hits.append(
                {
                    "path": urlsplit(self.path).path,
                    "body": payload,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                }
            )
            if self.server.script:
                item = self.server.script.pop(0)
            else:
                item = (500, b"")
            status = item[0]
            body = item[1] if len(item) > 1 else b""
            extra = item[2] if len(item) > 2 else {}
            self.send_response(status)
            for key, value in extra.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    @contextmanager
    def _running():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.hits = []
        httpd.script = []
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    return _running()


def _block_bitbucket_dns(monkeypatch) -> None:
    import socket

    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if isinstance(host, str) and host.lower().endswith("bitbucket.org"):
            raise AssertionError("live bitbucket " + host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


def _bb_section(base: str, **extra: Any) -> dict:
    scm = {
        "kind": "server",
        "bitbucket_url": base,
        "project_key": "PRJ",
        "repo_slug": "repo",
    }
    scm.update(extra)
    return {"scm": scm, "jenkins": {"credential_ids": {"bitbucket_status": "status-id-marker-9f3a"}}}


def _point_bitbucket(work: Path, base: str | None, **updates: Any) -> None:
    path = work / ".agent" / "project_config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    block = cfg["local_pipeline"]
    if "report_status" in updates:
        block["jenkins"]["report_status"] = updates.pop("report_status")
    scm = block.setdefault("scm", {})
    if base is not None:
        scm["bitbucket_url"] = base
    scm.update(updates)
    path.write_text(json.dumps(cfg), encoding="utf-8")


def test_webhook_relay_gates_do_not_bind(tmp_path: Path, monkeypatch, capsys):
    from memory.local_mcp.hmac_relay import bind_permitted, prepare, serve

    def _explode(*_args, **_kwargs):
        raise AssertionError("socket bind")

    monkeypatch.setattr("memory.local_mcp.hmac_relay._Server", _explode)

    def _expect(work: Path, env: dict, text: str, host: str = "127.0.0.1", port: int = 8114) -> None:
        capsys.readouterr()
        rc = serve(work, environ=env, host=host, port=port)
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert captured.err == text + "\n"

    bare = tmp_path / "bare"
    bare.mkdir()
    _expect(bare, {}, "local_pipeline_disabled")
    code, relay = prepare(bare, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"})
    assert code == "local_pipeline_disabled" and relay is None

    disabled = _relay_project(tmp_path / "disabled", enabled=False)
    _expect(disabled, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}, "local_pipeline_disabled")

    act = _relay_project(tmp_path / "act", backend="act")
    _expect(act, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}, "webhook_relay_off")

    off = _relay_project(tmp_path / "off", webhook_relay=False)
    _expect(off, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}, "webhook_relay_off")

    fuzzy = _relay_project(tmp_path / "fuzzy")
    cfg_path = fuzzy / ".agent" / "project_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["local_pipeline"]["jenkins"]["webhook_relay"] = "true"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    _expect(fuzzy, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}, "webhook_relay_off")

    missing = _relay_project(tmp_path / "missing")
    _expect(missing, {}, "webhook_secret_missing")
    _expect(missing, {"BITBUCKET_WEBHOOK_SECRET": ""}, "webhook_secret_missing")
    _expect(missing, {"BITBUCKET_WEBHOOK_SECRET": "has\nline"}, "webhook_secret_missing")

    for host, port in (
        ("0.0.0.0", 8114),
        ("localhost", 8114),
        ("::1", 8114),
        ("127.0.0.1", 8112),
        ("127.0.0.1", 8110),
        ("127.0.0.1", 8100),
        ("127.0.0.1", 8101),
        ("127.0.0.1", 8102),
        ("127.0.0.1", 8103),
        ("127.0.0.1", 80),
    ):
        assert bind_permitted(host, port) is False
        _expect(missing, {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}, "webhook_bind_rejected", host, port)
    assert bind_permitted("127.0.0.1", 8114) is True
    assert bind_permitted("127.0.0.1", True) is False
    assert "relay-secret" not in capsys.readouterr().err


def test_webhook_relay_parameters_without_values(tmp_path: Path, monkeypatch, capsys):
    from memory.local_mcp.backends.jenkins import trigger_job
    from memory.local_mcp.hmac_relay import prepare, serve

    def _explode(*_args, **_kwargs):
        raise AssertionError("socket bind")

    monkeypatch.setattr("memory.local_mcp.hmac_relay._Server", _explode)
    work = _relay_project(tmp_path / "params")
    cfg_path = work / ".agent" / "project_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    jenkins = cfg["local_pipeline"]["jenkins"]
    env = {"BITBUCKET_WEBHOOK_SECRET": "relay-secret"}

    jenkins["parameters"] = ["BRANCH"]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    code, relay = prepare(work, env)
    assert code == "webhook_parameters_required" and relay is None
    capsys.readouterr()
    assert serve(work, environ=env) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "webhook_parameters_required\n"
    assert "relay-secret" not in captured.err

    jenkins["parameters"] = []
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    code, relay = prepare(work, env)
    assert code is None and relay is not None

    jenkins["parameters"] = ["", 1]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    code, relay = prepare(work, env)
    assert code is None and relay is not None

    marker = b"webhook-body-marker"
    with _jenkins_controller() as server:
        base = _jk_base(server)
        section = {
            "jenkins": {
                "url": base,
                "job_path": "team/pipe",
                "parameters": ["BRANCH"],
                "crumb_required": True,
            }
        }
        assert trigger_job(section, environ=_jk_env()) is None
        posts = [rec for rec in server.records if rec["method"] == "POST"]
        assert [rec["path"] for rec in posts] == ["/job/team/job/pipe/build"]
        assert posts[0]["body"] == b""
        assert marker not in posts[0]["body"]
        assert not any("buildWithParameters" in rec["path"] for rec in server.records)


def test_webhook_secret_file_is_owner_read_under_root(tmp_path: Path, monkeypatch, capsys, caplog):
    from memory.local_mcp.hmac_relay import prepare, serve

    reads: list[tuple[str, Path]] = []
    secret_chunks: list[bytes] = []
    secret_flags: list[int] = []
    secret_holder: dict[str, Path] = {}
    fd_path: dict[int, Path] = {}
    real_bytes = Path.read_bytes
    real_text = Path.read_text
    real_open = os.open
    real_read = os.read
    real_close = os.close

    def spy_bytes(self):
        reads.append(("bytes", Path(self)))
        return real_bytes(self)

    def spy_text(self, *args, **kwargs):
        reads.append(("text", Path(self)))
        return real_text(self, *args, **kwargs)

    def _same_secret(opened: Path) -> bool:
        target = secret_holder.get("path")
        if target is None:
            return False
        try:
            return opened.resolve() == target.resolve()
        except OSError:
            return False

    def spy_open(file, flags, *args, **kwargs):
        fd = real_open(file, flags, *args, **kwargs)
        opened = Path(file)
        fd_path[fd] = opened
        if _same_secret(opened):
            secret_flags.append(flags)
        return fd

    def spy_read(fd, n, *args, **kwargs):
        data = real_read(fd, n, *args, **kwargs)
        opened = fd_path.get(fd)
        if opened is not None:
            reads.append(("os", opened))
            if _same_secret(opened):
                secret_chunks.append(data)
        return data

    def spy_close(fd, *args, **kwargs):
        fd_path.pop(fd, None)
        return real_close(fd, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy_bytes)
    monkeypatch.setattr(Path, "read_text", spy_text)
    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "read", spy_read)
    monkeypatch.setattr(os, "close", spy_close)
    _capture_local_mcp(caplog)

    work = _relay_project(tmp_path / "repo", secret_file="hook.secret")
    secret_path = work / "hook.secret"
    secret_holder["path"] = secret_path
    secret_path.write_text("file-secret\n", encoding="utf-8")

    def _saw_secret() -> bool:
        target = secret_path.resolve()
        return any(path.resolve() == target for _kind, path in reads)

    for mode in (0o644, 0o640, 0o700, 0o200):
        reads.clear()
        secret_path.chmod(mode)
        code, relay = prepare(work, {})
        assert code == "webhook_secret_missing" and relay is None
        assert _saw_secret() is False
        capsys.readouterr()
        assert serve(work, environ={}) == 2
        captured = capsys.readouterr()
        assert captured.err == "webhook_secret_missing\n"
        assert "file-secret" not in captured.err
        assert "file-secret" not in caplog.text

    reads.clear()
    secret_path.chmod(0o600)
    code, relay = prepare(work, {"BITBUCKET_WEBHOOK_SECRET": ""})
    assert code == "webhook_secret_missing" and relay is None
    assert _saw_secret() is False

    code, relay = prepare(work, {"BITBUCKET_WEBHOOK_SECRET": "env-secret"})
    assert code is None and relay is not None and relay.secret == "env-secret"
    assert _saw_secret() is False

    secret_path.write_bytes(b"file-secret\r\n")
    secret_path.chmod(0o400)
    code, relay = prepare(work, {})
    assert code is None and relay is not None and relay.secret == "file-secret"
    assert _saw_secret() is True

    # 0400 is owner-read. Root can still write it; the CI user cannot.
    secret_path.chmod(0o600)
    secret_path.write_bytes(b"file-secret\n\n")
    code, relay = prepare(work, {})
    assert code == "webhook_secret_missing" and relay is None

    canary = b"OVERSIZED_CANARY_9f3a"
    payload = b"a" * 4097 + canary + b"b" * 5000
    secret_path.write_bytes(payload)
    secret_path.chmod(0o600)
    secret_chunks.clear()
    secret_flags.clear()
    reads.clear()
    code, relay = prepare(work, {})
    assert code == "webhook_secret_missing" and relay is None
    blob = b"".join(secret_chunks)
    assert blob and len(blob) <= 4097
    assert len(blob) < secret_path.stat().st_size == len(payload)
    assert canary not in blob
    assert not any(
        kind == "bytes" and path.resolve() == secret_path.resolve() for kind, path in reads
    )
    if hasattr(os, "O_NOFOLLOW"):
        assert secret_flags and all(flag & os.O_NOFOLLOW for flag in secret_flags)

    outside = tmp_path / "outside.secret"
    outside.write_text("outside-secret\n", encoding="utf-8")
    outside.chmod(0o600)
    escaped = _relay_project(tmp_path / "escaped", secret_file=str(outside))
    reads.clear()
    code, relay = prepare(escaped, {})
    assert code == "webhook_secret_missing" and relay is None
    assert all(path.resolve() != outside.resolve() for _kind, path in reads)

    linked = _relay_project(tmp_path / "linked", secret_file="hook.secret")
    link = linked / "hook.secret"
    link.symlink_to(outside)
    reads.clear()
    code, relay = prepare(linked, {})
    assert code == "webhook_secret_missing" and relay is None
    assert all(path.resolve() != outside.resolve() for _kind, path in reads)

    relative = _relay_project(tmp_path / "relative", secret_file="../outside.secret")
    code, relay = prepare(relative, {})
    assert code == "webhook_secret_missing" and relay is None

    nulled = _relay_project(tmp_path / "nulled", secret_file="hook.secret")
    cfg_path = nulled / ".agent" / "project_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["local_pipeline"]["jenkins"]["webhook_secret_file"] = "bad\x00name"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    code, relay = prepare(nulled, {})
    assert code == "webhook_secret_missing" and relay is None
    assert "outside-secret" not in caplog.text
    assert "file-secret" not in caplog.text


def test_webhook_hmac_replay_and_body_cap(tmp_path: Path, monkeypatch, caplog):
    import hashlib
    import hmac

    from memory.local_mcp.hmac_relay import prepare

    secret = "relay-secret"
    work = _relay_project(tmp_path / "repo")
    (work / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 4}),
        encoding="utf-8",
    )
    code, relay = prepare(work, {"BITBUCKET_WEBHOOK_SECRET": secret})
    assert code is None and relay is not None
    seen: list[str] = []

    def fake_trigger(section, *, environ, timeout_s=15.0):
        del environ, timeout_s
        seen.append(section["jenkins"]["job_path"])
        if len(seen) == 1:
            return "jenkins_unreachable"
        return None

    monkeypatch.setattr("memory.local_mcp.hmac_relay.trigger_job", fake_trigger)
    compared: list[tuple[str, str]] = []
    real_compare = hmac.compare_digest

    def spy_compare(left, right):
        compared.append((str(left), str(right)))
        return real_compare(left, right)

    monkeypatch.setattr("memory.local_mcp.hmac_relay.hmac.compare_digest", spy_compare)
    _capture_local_mcp(caplog)
    body = b'{"marker":"webhook-body-marker","job_path":"evil/job"}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    with caplog.at_level(logging.DEBUG, logger="memory.local_mcp"):
        status, payload = relay.handle(body, {})
        assert (status, payload) == (401, b"")
        assert seen == []
        assert compared == []
        entry = _audit_entries(work)[-1]
        assert entry["action"] == "local_pipeline_webhook_reject"
        assert entry["role"] == "ci"
        assert entry["cycle"] == 4
        assert entry["details"] == {"reason": "bad_signature"}

        (work / ".agent" / "LOOP_STATE.json").write_text(
            json.dumps({"cycle_number": True}),
            encoding="utf-8",
        )
        status, payload = relay.handle(body, {"X-Hub-Signature": "sha1=" + "ab" * 20, "X-Request-Id": "sha1"})
        assert (status, payload) == (401, b"")
        assert _audit_entries(work)[-1]["cycle"] == 0
        assert _audit_entries(work)[-1]["details"] == {"reason": "bad_signature"}
        assert compared == []

        short = {"X-Hub-Signature": "sha256=abcd", "X-Request-Id": "short"}
        assert relay.handle(body, short) == (401, b"")
        assert compared == []

        wrong = {"X-Hub-Signature": "sha256=" + ("ab" * 32), "X-Request-Id": "wrong"}
        assert relay.handle(body, wrong) == (401, b"")
        assert len(compared) == 1
        assert seen == []

        missing = _hmac_headers(secret, body, request_id=None)
        assert relay.handle(body, missing) == (401, b"")
        assert relay.handle(body, missing) == (401, b"")
        assert _audit_entries(work)[-1]["details"] == {"reason": "missing_request_id"}
        assert relay.replay._seen == {}
        blank = _hmac_headers(secret, body, request_id="req-blank")
        blank["X-Request-Id"] = "  "
        assert relay.handle(body, blank) == (401, b"")
        assert _audit_entries(work)[-1]["details"] == {"reason": "missing_request_id"}

        first = _hmac_headers(secret, body, "again")
        assert relay.handle(body, first, now=10.0) == (502, b"")
        assert seen == ["configured/job"]
        assert relay.handle(body, first, now=10.0) == (200, b"ok")
        assert relay.handle(body, first, now=10.0) == (401, b"")
        assert _audit_entries(work)[-1]["details"] == {"reason": "replay"}
        assert seen == ["configured/job", "configured/job"]
        assert relay.handle(body, first, now=610.0) == (200, b"ok")
        assert len(seen) == 3

        upper = _hmac_headers(secret, body, "upper")
        upper["X-Hub-Signature"] = upper["X-Hub-Signature"].upper()
        assert relay.handle(body, upper, now=20.0) == (200, b"ok")

        either = _hmac_headers(secret, body, "either", "X-Hub-Signature-256")
        either["X-Hub-Signature"] = "sha1=" + "cd" * 20
        assert relay.handle(body, either, now=21.0) == (200, b"ok")
        flipped = {"X-Request-Id": "flipped", "X-Hub-Signature-256": "sha256=abcd"}
        flipped["X-Hub-Signature"] = "sha256=" + digest
        assert relay.handle(b"", flipped, now=22.0) == (401, b"")
        flipped_body = _hmac_headers(secret, body, "flipped", "X-Hub-Signature")
        flipped_body["X-Hub-Signature-256"] = "sha256=" + "ab" * 32
        assert relay.handle(body, flipped_body, now=23.0) == (200, b"ok")

        cap = b"x" * (1024 * 1024)
        before = len(seen)
        assert relay.handle(cap, _hmac_headers(secret, cap, "cap"), now=40.0) == (200, b"ok")
        assert len(seen) == before + 1
        audits = len(_audit_entries(work))
        assert relay.handle(cap + b"x", _hmac_headers(secret, cap + b"x", "over"), now=41.0) == (413, b"")
        assert len(seen) == before + 1
        assert len(_audit_entries(work)) == audits

        broken = fake_trigger
        calls = {"n": 0}

        def explode(section, *, environ, timeout_s=15.0):
            del section, environ, timeout_s
            calls["n"] += 1
            raise RuntimeError("token-in-exception " + secret)

        monkeypatch.setattr("memory.local_mcp.hmac_relay.trigger_job", explode)
        failed = _hmac_headers(secret, body, "explode")
        assert relay.handle(body, failed, now=50.0) == (502, b"")
        assert calls["n"] == 1
        monkeypatch.setattr("memory.local_mcp.hmac_relay.trigger_job", broken)
        assert relay.handle(body, failed, now=50.0)[0] == 200

        def _boom(left, right):
            raise RuntimeError("digest-leak-" + str(left))

        monkeypatch.setattr("memory.local_mcp.hmac_relay.hmac.compare_digest", _boom)
        boom_headers = {"X-Hub-Signature": "sha256=" + ("ab" * 32), "X-Request-Id": "boom"}
        assert relay.handle(body, boom_headers, now=60.0) == (401, b"")

    blob = (work / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8")
    for banned in (secret, "webhook-body-marker", "evil/job", digest, "digest-leak-", "token-in-exception"):
        assert banned not in blob
        assert banned not in caplog.text
    assert "jenkins webhook trigger refused" in caplog.text
    assert "jenkins webhook trigger failed" in caplog.text
    assert all(path == "configured/job" for path in seen)


def test_webhook_relay_loopback_triggers_configured_job(tmp_path: Path, caplog):
    import contextlib
    import hashlib
    import hmac
    import http.client
    import io
    import socket

    from memory.local_mcp.hmac_relay import serve

    secret = "relay-secret-marker"
    body = b'{"marker":"webhook-body-marker","job_path":"evil/job"}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    _capture_local_mcp(caplog)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 8114))
    sock.listen(1)
    try:
        held = _relay_project(tmp_path / "held")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = serve(held, environ={"BITBUCKET_WEBHOOK_SECRET": secret})
        assert rc == 2
        assert err.getvalue() == "webhook_bind_rejected\n"
        assert secret not in err.getvalue()
        assert secret not in caplog.text
    finally:
        sock.close()

    with _jenkins_controller() as jenkins:
        base = _jk_base(jenkins)
        work = _relay_project(tmp_path / "live", url=base, job_path="team/pipe")
        env = {
            "BITBUCKET_WEBHOOK_SECRET": secret,
            "JENKINS_USER": "alice",
            "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
            "PATH": "/usr/bin:/bin",
        }
        ready = threading.Event()
        bound: list = []
        box: dict = {}

        def _run() -> None:
            try:
                box["code"] = serve(work, environ=env, ready=ready, bound=bound)
            except BaseException as exc:
                box["exc"] = exc
                ready.set()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            assert ready.wait(5), box
            if "exc" in box:
                raise box["exc"]
            httpd = bound[0]

            def _post(request_id: str, payload: bytes = body, signature: str | None = None):
                mac = signature or hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
                conn = http.client.HTTPConnection("127.0.0.1", 8114, timeout=5)
                conn.request(
                    "POST",
                    "/webhook",
                    body=payload,
                    headers={
                        "X-Hub-Signature": "sha256=" + mac,
                        "X-Request-Id": request_id,
                        "Content-Type": "application/json",
                    },
                )
                response = conn.getresponse()
                data = response.read()
                status = response.status
                conn.close()
                return status, data

            status, data = _post("live-1")
            assert (status, data) == (200, b"ok")
            status, data = _post("live-1")
            assert (status, data) == (401, b"")
            posts = [rec for rec in jenkins.records if rec["method"] == "POST"]
            assert [rec["path"] for rec in posts] == ["/job/team/job/pipe/build"]
            assert posts[0]["headers"].get("x-agentix-crumb") == "crumb-value-77"
            assert b"evil/job" not in posts[0]["body"]
            assert b"webhook-body-marker" not in posts[0]["body"]
            conn = http.client.HTTPConnection("127.0.0.1", 8114, timeout=5)
            conn.request("GET", "/health")
            response = conn.getresponse()
            assert response.status == 404
            assert response.read() == b""
            conn.close()

            def _raw(header: bytes) -> bytes:
                client = socket.create_connection(("127.0.0.1", 8114), timeout=3)
                client.sendall(header)
                client.settimeout(3)
                chunks = b""
                while True:
                    try:
                        piece = client.recv(4096)
                    except socket.timeout:
                        break
                    if not piece:
                        break
                    chunks += piece
                client.close()
                return chunks

            audits = len(_audit_entries(work))
            over = _raw(
                b"POST /hook HTTP/1.1\r\n"
                b"Host: 127.0.0.1:8114\r\n"
                b"Content-Length: 1048577\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            assert over.startswith(b"HTTP/1.1 413")
            assert len([rec for rec in jenkins.records if rec["method"] == "POST"]) == 1
            assert len(_audit_entries(work)) == audits
            missing_len = _raw(
                b"POST /hook HTTP/1.1\r\nHost: 127.0.0.1:8114\r\nConnection: close\r\n\r\n"
            )
            assert missing_len.startswith(b"HTTP/1.1 401")
            assert _audit_entries(work)[-1]["details"] == {"reason": "bad_signature"}
            bad_len = _raw(
                b"POST /hook HTTP/1.1\r\n"
                b"Host: 127.0.0.1:8114\r\n"
                b"Content-Length: nope\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            assert bad_len.startswith(b"HTTP/1.1 400")
            assert _audit_entries(work)[-1]["details"] == {"reason": "bad_signature"}
            assert len([rec for rec in jenkins.records if rec["method"] == "POST"]) == 1
            blob = json.dumps(
                {
                    "jenkins": [{key: rec[key] for key in ("path", "body")} for rec in jenkins.records],
                    "audit": _audit_entries(work),
                },
                default=lambda value: value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value),
            )
            assert secret not in blob
            assert secret not in caplog.text
            assert "webhook-body-marker" not in blob
            assert digest not in blob
            httpd.shutdown()
        finally:
            if bound:
                bound[0].shutdown()
            thread.join(timeout=5)
            if box.get("exc") is not None:
                raise box["exc"]


def test_jenkins_relay_cli_calls_serve(monkeypatch):
    seen: dict = {}

    def fake_serve(workdir, **kwargs):
        seen["workdir"] = Path(workdir)
        seen["kwargs"] = kwargs
        return 7

    monkeypatch.setattr("memory.local_mcp.hmac_relay.serve", fake_serve)
    assert local_mcp_main(["jenkins-relay"]) == 7
    assert seen["workdir"] == Path.cwd()
    assert seen["kwargs"] == {}


def test_bitbucket_server_status_post(tmp_path: Path, monkeypatch, caplog):
    import base64

    from memory.local_mcp.backends.bitbucket_status import post_commit_status

    del tmp_path
    _block_bitbucket_dns(monkeypatch)
    _capture_local_mcp(caplog)
    sha = "ab" * 20
    with _scripted_http() as server, caplog.at_level(logging.DEBUG, logger="memory.local_mcp"):
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        build = base + "/job/folder/job/name/9/"
        primary = f"/rest/api/latest/projects/PRJ/repos/repo/commits/{sha}/builds"
        legacy = f"/rest/build-status/latest/commits/{sha}"

        def _post(state: str, *, ref: str | None = "refs/heads/main", commit: str = sha, environ=None, section=None):
            return post_commit_status(
                section or _bb_section(base),
                commit=commit,
                state=state,
                key="agentix",
                url=build,
                ref=ref,
                environ=environ
                or {
                    "BITBUCKET_API_TOKEN": "bb-token-marker",
                    "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
                    "BITBUCKET_USERNAME": "ignored-user",
                    "BITBUCKET_APP_PASSWORD": "ignored-pass",
                },
            )

        server.script[:] = [(204, b"")]
        posted = _post("SUCCESSFUL")
        assert posted.posted is True and posted.warnings == []
        hit = server.hits[0]
        assert hit["path"] == primary
        assert json.loads(hit["body"].decode("utf-8")) == {
            "key": "agentix",
            "state": "SUCCESSFUL",
            "url": build,
            "ref": "refs/heads/main",
        }
        assert hit["headers"]["authorization"] == "Bearer bb-token-marker"
        assert _FAKE_JENKINS_TOKEN not in hit["headers"]["authorization"]

        server.hits.clear()
        server.script[:] = [(200, b"not-json-marker")]
        assert _post("FAILED").posted is True
        assert "not-json-marker" not in caplog.text

        server.hits.clear()
        server.script[:] = [(302, b"", {"Location": "/stolen"})]
        assert _post("SUCCESSFUL").posted is False
        assert [item["path"] for item in server.hits] == [primary]

        server.hits.clear()
        server.script[:] = [(404, b""), (204, b"")]
        assert _post("SUCCESSFUL").posted is True
        assert [item["path"] for item in server.hits] == [primary, legacy]
        assert all("/rest/api/1.0/" not in item["path"] and "statuses/build" not in item["path"] for item in server.hits)

        server.hits.clear()
        server.script[:] = [(404, b""), (400, b"nope"), (204, b"")]
        cancelled = _post("CANCELLED")
        assert cancelled.posted is True
        assert cancelled.warnings == [
            {
                "code": "legacy_cancel_mapped_failed",
                "message": "Bitbucket build-status rejected CANCELLED; posted FAILED",
            }
        ]
        bodies = [json.loads(item["body"].decode("utf-8"))["state"] for item in server.hits]
        assert bodies == ["CANCELLED", "CANCELLED", "FAILED"]
        assert [item["path"] for item in server.hits] == [primary, legacy, legacy]

        not_posted = {
            "code": "legacy_cancel_retry_failed",
            "message": "Bitbucket build-status rejected CANCELLED; retry as FAILED was not posted",
        }
        for retry_status in (500, 201):
            server.hits.clear()
            server.script[:] = [(404, b""), (400, b"nope"), (retry_status, b"nope")]
            missed_retry = _post("CANCELLED")
            assert missed_retry.posted is False
            assert missed_retry.warnings == [not_posted]
            assert "posted FAILED" not in missed_retry.warnings[0]["message"]
            retry_bodies = [json.loads(item["body"].decode("utf-8"))["state"] for item in server.hits]
            assert retry_bodies == ["CANCELLED", "CANCELLED", "FAILED"]
            assert [item["path"] for item in server.hits] == [primary, legacy, legacy]

        class _OneStatus:
            def __init__(self, code: int) -> None:
                self.status = code

            def read(self, _n: int = -1) -> bytes:
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class _CancelOpener:
            def __init__(self, events: list) -> None:
                self.events = list(events)
                self.states: list[str] = []
                self.urls: list[str] = []

            def open(self, request, timeout=None):
                del timeout
                self.urls.append(request.full_url)
                self.states.append(json.loads(request.data.decode("utf-8"))["state"])
                event = self.events.pop(0)
                if isinstance(event, BaseException):
                    raise event
                return _OneStatus(event)

        timed = _CancelOpener([404, 400, TimeoutError("bb-status-timeout")])
        timed_out = post_commit_status(
            _bb_section(base),
            commit=sha,
            state="CANCELLED",
            key="agentix",
            url=build,
            ref="refs/heads/main",
            environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
            opener=timed,
        )
        assert timed_out.posted is False
        assert timed_out.warnings == [not_posted]
        assert "posted FAILED" not in timed_out.warnings[0]["message"]
        assert timed.states == ["CANCELLED", "CANCELLED", "FAILED"]
        assert timed.urls == [base + primary, base + legacy, base + legacy]
        assert all("bitbucket.org" not in item for item in timed.urls)

        server.hits.clear()
        server.script[:] = [(404, b""), (400, b"nope")]
        other = _post("FAILED")
        assert other.posted is False and other.warnings == []
        assert len(server.hits) == 2

        server.hits.clear()
        server.script[:] = [(400, b"nope")]
        primary_cancel = _post("CANCELLED")
        assert primary_cancel.posted is False and primary_cancel.warnings == []
        assert [item["path"] for item in server.hits] == [primary]

        server.hits.clear()
        server.script[:] = [(201, b"")]
        assert _post("SUCCESSFUL").posted is False

        server.hits.clear()
        server.script[:] = [(204, b"")]
        basic = _post(
            "SUCCESSFUL",
            environ={
                "BITBUCKET_USERNAME": "bb-user",
                "BITBUCKET_APP_PASSWORD": "bb-app-secret",
                "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
            },
        )
        assert basic.posted is True
        expected = "Basic " + base64.b64encode(b"bb-user:bb-app-secret").decode("ascii")
        assert server.hits[-1]["headers"]["authorization"] == expected

        server.hits.clear()
        server.script[:] = [(204, b"")]
        assert _post("SUCCESSFUL", environ={"JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN}).posted is False
        assert server.hits == []

        server.hits.clear()
        server.script[:] = [(204, b"")]
        null_url = _bb_section(base)
        null_url["scm"]["bitbucket_url"] = None
        assert _post("SUCCESSFUL", section=null_url, environ={"BITBUCKET_URL": base, "BITBUCKET_API_TOKEN": "bb-token-marker"}).posted is True
        assert server.hits[0]["path"] == primary

        server.hits.clear()
        server.script[:] = [(204, b"")]
        pinned = _bb_section("http://127.0.0.1:1")
        assert _post(
            "SUCCESSFUL",
            section=pinned,
            environ={"BITBUCKET_URL": base, "BITBUCKET_API_TOKEN": "bb-token-marker"},
        ).posted is False
        assert server.hits == []

        server.hits.clear()
        server.script[:] = [(204, b""), (204, b"")]
        bare = _post("SUCCESSFUL", ref="main")
        assert bare.posted is True
        assert "ref" not in json.loads(server.hits[-1]["body"].decode("utf-8"))
        pulled = _post("SUCCESSFUL", ref="refs/pull/1/head")
        assert pulled.posted is True
        assert "ref" not in json.loads(server.hits[-1]["body"].decode("utf-8"))

        def _forbid(request, timeout=None):
            del timeout
            raise AssertionError("opened " + getattr(request, "full_url", ""))

        opener = SimpleNamespace(open=_forbid)
        for commit in ("abc123", "a" * 41, "not-hex-aaaa"):
            missed = post_commit_status(
                _bb_section(base),
                commit=commit,
                state="SUCCESSFUL",
                key="agentix",
                url=build,
                ref="refs/heads/main",
                environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
                opener=opener,
            )
            assert missed.posted is False
        server.hits.clear()
        server.script[:] = [(204, b""), (204, b"")]
        short = "abc1234"
        long = "a" * 40
        opened: list[str] = []

        class _Done:
            def __init__(self) -> None:
                self.status = 204

            def read(self, _n: int = -1) -> bytes:
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class _Open:
            def open(self, request, timeout=None):
                del timeout
                opened.append(request.full_url)
                return _Done()

        for commit in (short, short.upper(), long):
            assert post_commit_status(
                _bb_section(base),
                commit=commit,
                state="SUCCESSFUL",
                key="agentix",
                url=build,
                ref=None,
                environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
                opener=_Open(),
            ).posted is True
        assert opened == [
            f"{base}/rest/api/latest/projects/PRJ/repos/repo/commits/{short}/builds",
            f"{base}/rest/api/latest/projects/PRJ/repos/repo/commits/{short}/builds",
            f"{base}/rest/api/latest/projects/PRJ/repos/repo/commits/{long}/builds",
        ]
        for bad in ("http://127.0.0.1:99999", "http://127.0.0.1\x00", "http://h/\x00"):
            assert post_commit_status(
                _bb_section(bad),
                commit=short,
                state="SUCCESSFUL",
                key="agentix",
                url=build,
                ref="refs/heads/main",
                environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
                opener=opener,
            ).posted is False
        assert post_commit_status(
            _bb_section(base),
            commit="bad\x00" + short,
            state="SUCCESSFUL",
            key="agentix",
            url=build,
            ref="refs/heads/main",
            environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
            opener=opener,
        ).posted is False


def test_bitbucket_cloud_status_uses_fake_opener(monkeypatch):
    from memory.local_mcp.backends.bitbucket_status import post_commit_status

    _block_bitbucket_dns(monkeypatch)
    sha = "abcdef1234567890"

    class _Resp:
        def __init__(self, status: int, body: bytes = b"") -> None:
            self.status = status
            self._body = body

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                data = self._body
                self._body = b""
                return data
            data = self._body[:n]
            self._body = self._body[n:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Opener:
        def __init__(self, statuses: list[int]) -> None:
            self._statuses = list(statuses)
            self.calls: list[dict] = []

        def open(self, request, timeout=None):
            self.calls.append(
                {
                    "url": request.full_url,
                    "data": request.data,
                    "auth": request.get_header("Authorization"),
                    "timeout": timeout,
                }
            )
            return _Resp(self._statuses.pop(0), b"not-json-marker")

    section = {
        "scm": {
            "kind": "cloud",
            "bitbucket_url": "http://127.0.0.1:9",
            "workspace": "acme",
            "repo_slug": "demo",
        }
    }
    url = f"https://api.bitbucket.org/2.0/repositories/acme/demo/commit/{sha}/statuses/build"
    opener = _Opener([204])
    posted = post_commit_status(
        section,
        commit=sha.upper(),
        state="CANCELLED",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref="refs/heads/main",
        environ={
            "BITBUCKET_API_TOKEN": "bb-token-marker",
            "JENKINS_API_TOKEN": _FAKE_JENKINS_TOKEN,
        },
        opener=opener,
    )
    assert posted.posted is True and posted.warnings == []
    assert opener.calls[0]["url"] == url
    assert json.loads(opener.calls[0]["data"].decode("utf-8")) == {
        "key": "agentix",
        "state": "STOPPED",
        "url": "http://127.0.0.1:9/job/1/",
        "refname": "refs/heads/main",
    }
    assert opener.calls[0]["auth"] == "Bearer bb-token-marker"
    refused = _Opener([400])
    missed = post_commit_status(
        section,
        commit=sha,
        state="CANCELLED",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref="refs/heads/main",
        environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
        opener=refused,
    )
    assert missed.posted is False and missed.warnings == []
    assert len(refused.calls) == 1
    fallen = _Opener([404])
    assert post_commit_status(
        section,
        commit=sha,
        state="FAILED",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref=None,
        environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
        opener=fallen,
    ).posted is False
    assert len(fallen.calls) == 1
    assert fallen.calls[0]["url"] == url
    assert json.loads(fallen.calls[0]["data"].decode("utf-8")) == {
        "key": "agentix",
        "state": "FAILED",
        "url": "http://127.0.0.1:9/job/1/",
    }
    created = _Opener([201])
    created_post = post_commit_status(
        section,
        commit=sha,
        state="SUCCESSFUL",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref="refs/heads/main",
        environ={"BITBUCKET_API_TOKEN": "bb-token-marker"},
        opener=created,
    )
    assert created_post.posted is True and created_post.warnings == []
    assert len(created.calls) == 1
    assert created.calls[0]["url"] == url
    assert json.loads(created.calls[0]["data"].decode("utf-8")) == {
        "key": "agentix",
        "state": "SUCCESSFUL",
        "url": "http://127.0.0.1:9/job/1/",
        "refname": "refs/heads/main",
    }


def test_jenkins_status_mapping_and_poster_owner():
    from memory.local_mcp.backends.jenkins import JenkinsOutcome, _bitbucket_ref, _normalize_commit, _status_state

    assert _normalize_commit("ABC1234") == "abc1234"
    assert _normalize_commit("a" * 40) == "a" * 40
    assert _normalize_commit("abc123") is None
    assert _normalize_commit("a" * 41) is None
    assert _bitbucket_ref("main") == "refs/heads/main"
    assert _bitbucket_ref("refs/heads/main") == "refs/heads/main"
    assert _bitbucket_ref("refs/tags/v1") == "refs/tags/v1"
    assert _bitbucket_ref("refs/remotes/origin/topic") == "refs/heads/topic"
    assert _bitbucket_ref("origin/feature") == "refs/heads/feature"
    assert _bitbucket_ref("refs/pull/1/head") is None
    assert _bitbucket_ref("refs/remotes/upstream/main") is None
    assert _bitbucket_ref("a/b") is None
    ok = JenkinsOutcome(exit_code=0, status="succeeded", ok=True)
    failed = JenkinsOutcome(exit_code=1, status="failed", ok=False)
    timed = JenkinsOutcome(exit_code=124, status="timed_out", ok=False, timed_out=True)
    assert _status_state(ok, "SUCCESS") == "SUCCESSFUL"
    assert _status_state(failed, "FAILURE") == "FAILED"
    assert _status_state(failed, "UNSTABLE") == "FAILED"
    assert _status_state(failed, "ABORTED") == "CANCELLED"
    assert _status_state(timed, None) == "CANCELLED"
    root = REPO / "memory" / "local_mcp"
    for rel in ("backends/act.py", "backends/self_hosted.py", "server.py", "pipeline.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "bitbucket_status" not in text
        assert "post_commit_status" not in text
        assert "hmac_relay" not in text
    callers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "bitbucket_status.py" and "post_commit_status" in path.read_text(encoding="utf-8")
    )
    assert callers == ["backends/jenkins.py"]
    assert "hmac_relay" not in (root / "server.py").read_text(encoding="utf-8")


def test_jenkins_report_status_posts_from_build_revision(tmp_path: Path, monkeypatch, caplog):
    _block_bitbucket_dns(monkeypatch)

    def _forbid_request_json(*_args, **_kwargs):
        raise AssertionError("request_json")

    monkeypatch.setattr("memory.integrations.http.request_json", _forbid_request_json)
    _capture_local_mcp(caplog)
    sha_gh = "1111111"
    sha_bb = "ABCDEF1234567"
    actions = [
        {
            "lastBuiltRevision": {"SHA1": sha_gh, "branch": [{"name": "main"}]},
            "remoteUrls": ["https://github.com/org/repo.git"],
        },
        {
            "lastBuiltRevision": {"SHA1": sha_bb, "branch": [{"name": "main"}]},
            "remoteUrls": ["https://bitbucket.local/scm/prj/repo.git"],
        },
    ]
    with _jenkins_controller() as jenkins, _scripted_http() as bitbucket:
        base = _jk_base(jenkins)
        host, port = bitbucket.server_address[:2]
        bb = f"http://{host}:{port}"
        token = "bb-token-marker"
        env = _jk_env(BITBUCKET_API_TOKEN=token)

        def _run(name: str, *, report_status: bool, spec: dict, environ=None, script=()):
            work = _jk_project(tmp_path / name, base)
            _point_bitbucket(
                work,
                bb,
                report_status=report_status,
                project_key="PRJ",
                repo_slug="repo",
                kind="server",
            )
            _jk_reset(jenkins, **spec)
            bitbucket.hits.clear()
            bitbucket.script[:] = list(script)
            return work, _jk_run(work, job_path="folder/name", environ=env if environ is None else environ)

        with caplog.at_level(logging.DEBUG, logger="memory.local_mcp"):
            work, result = _run("defer", report_status=False, spec={"actions": actions, "log": b"ok\n"})
            _assert_jk(result)
            assert result["status_posted_by"] == "jenkins"
            assert result["scm"]["commit"] == "abcdef1234567"
            assert result["scm"]["ref"] == "refs/heads/main"
            assert bitbucket.hits == []
            assert result["ok"] is True

            work, result = _run(
                "harness",
                report_status=True,
                spec={"actions": actions, "log": b"ok\n"},
                script=[(204, b"")],
            )
            _assert_jk(result)
            assert result["ok"] is True
            assert result["status_posted_by"] == "harness"
            assert result["scm"]["commit"] == "abcdef1234567"
            assert result["scm"]["ref"] == "refs/heads/main"
            assert bitbucket.hits[0]["path"].endswith("/commits/abcdef1234567/builds")
            body = json.loads(bitbucket.hits[0]["body"].decode("utf-8"))
            assert body["key"] == "agentix"
            assert body["state"] == "SUCCESSFUL"
            assert body["ref"] == "refs/heads/main"
            assert body["url"].endswith("/job/folder/job/name/9/")
            assert bitbucket.hits[0]["headers"]["authorization"] == "Bearer " + token
            targets = [rec["target"] for rec in jenkins.records if rec["path"].endswith("/9/api/json")]
            assert targets and all("lastBuiltRevision" in target for target in targets)
            blob = json.dumps(result)
            assert token not in blob
            assert token not in caplog.text
            assert token not in json.dumps(_audit_entries(work))
            assert token.encode() not in b"".join(rec["body"] for rec in jenkins.records)

            bitbucket.script.clear()
            _work, result = _run(
                "short",
                report_status=True,
                spec={"actions": [{"lastBuiltRevision": {"SHA1": "abc123", "branch": [{"name": "main"}]}}], "log": b"ok\n"},
            )
            _assert_jk(result)
            assert result["status_posted_by"] is None
            assert result["scm"]["commit"] is None
            assert bitbucket.hits == []

            _work, result = _run(
                "noauth",
                report_status=True,
                spec={"actions": actions, "log": b"ok\n"},
                environ=_jk_env(),
            )
            _assert_jk(result)
            assert result["ok"] is True
            assert result["status_posted_by"] is None
            assert result["scm"]["commit"] == "abcdef1234567"
            assert bitbucket.hits == []

            _work, result = _run(
                "aborted",
                report_status=True,
                spec={"actions": actions, "result": "ABORTED", "log": b"nope\n"},
                script=[(404, b""), (400, b"nope"), (204, b"")],
            )
            _assert_jk(result)
            assert result["exit_code"] == 1
            assert result["ok"] is False
            assert result["status_posted_by"] == "harness"
            assert [hit["path"].endswith("/builds") or hit["path"].endswith("/commits/abcdef1234567") for hit in bitbucket.hits] == [
                True,
                True,
                True,
            ]
            states = [json.loads(hit["body"].decode("utf-8"))["state"] for hit in bitbucket.hits]
            assert states == ["CANCELLED", "CANCELLED", "FAILED"]
            assert result["warnings"][0]["code"] == "legacy_cancel_mapped_failed"
            assert all("statuses/build" not in hit["path"] and "/rest/api/1.0/" not in hit["path"] for hit in bitbucket.hits)

            bitbucket.hits.clear()

            def _explode(*_args, **_kwargs):
                raise RuntimeError("leaked " + token)

            monkeypatch.setattr("memory.local_mcp.backends.jenkins.post_commit_status", _explode)
            _work, result = _run("explode", report_status=True, spec={"actions": actions, "log": b"ok\n"})
            _assert_jk(result)
            assert result["ok"] is True
            assert result["exit_code"] == 0
            assert result["status_posted_by"] is None
            assert bitbucket.hits == []
            assert "bitbucket status post failed" in caplog.text
            assert token not in caplog.text
            assert "leaked" not in caplog.text


def test_apply_status_warns_when_bitbucket_post_is_rejected(monkeypatch, caplog):
    """A fake opener only. No Jenkins controller and no Bitbucket host."""
    import urllib.error

    from memory.local_mcp.backends.bitbucket_status import post_commit_status
    from memory.local_mcp.backends.jenkins import JenkinsOutcome, _apply_status

    class _Resp:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                data = self._body
                self._body = b""
                return data
            data = self._body[:n]
            self._body = self._body[n:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Opener:
        def __init__(self, status: int, body: bytes = b"", down: bool = False) -> None:
            self.status = status
            self.body = body
            self.down = down
            self.calls = 0
            self.urls: list[str] = []

        def open(self, request, timeout=None):
            del timeout
            self.calls += 1
            self.urls.append(request.full_url)
            if self.down:
                raise urllib.error.URLError("down-host")
            return _Resp(self.status, self.body)

    sha = "abcdef1234567"
    token = "bb-token-marker"
    body = b"rejected-body-9f3a user:s3cret@bitbucket"
    warning = {
        "code": "bitbucket_status_not_posted",
        "message": "Bitbucket build-status was not posted",
    }
    section = {
        "jenkins": {"report_status": True},
        "scm": {
            "kind": "server",
            "bitbucket_url": "http://127.0.0.1:9",
            "project_key": "PRJ",
            "repo_slug": "repo",
        },
    }
    session = SimpleNamespace(
        number=9,
        commit=sha,
        ref="refs/heads/main",
        base="http://127.0.0.1:9",
        rest="/job/folder/job/name",
    )
    env = {"BITBUCKET_API_TOKEN": token}
    _capture_local_mcp(caplog)

    def _run(opener: _Opener, *, environ: dict, section: dict, exit_code: int) -> tuple[JenkinsOutcome, _Opener]:
        monkeypatch.setattr("memory.local_mcp.backends.bitbucket_status._opener", lambda: opener)
        outcome = JenkinsOutcome(
            exit_code=exit_code,
            status="succeeded" if exit_code == 0 else "failed",
            ok=exit_code == 0,
        )
        _apply_status(section, session, outcome, environ, "SUCCESS" if exit_code == 0 else "FAILURE")
        return outcome, opener

    with caplog.at_level(logging.DEBUG, logger="memory.local_mcp"):
        rejected, opener = _run(_Opener(401, body), environ=env, section=section, exit_code=0)
        assert rejected.exit_code == 0
        assert rejected.ok is True
        assert rejected.status == "succeeded"
        assert rejected.posted_by is None
        assert rejected.warnings == [warning]
        assert opener.calls == 1
        assert "user:s3cret" not in opener.urls[0]
        assert "bitbucket status post rejected" in caplog.text
        blob = json.dumps(rejected.warnings) + caplog.text
        assert token not in blob
        assert "rejected-body-9f3a" not in blob
        assert "user:s3cret" not in blob
        assert "http://" not in json.dumps(rejected.warnings)

        caplog.clear()
        down, opener = _run(_Opener(500, body, down=True), environ=env, section=section, exit_code=1)
        assert down.exit_code == 1
        assert down.ok is False
        assert down.posted_by is None
        assert down.warnings == [warning]
        assert opener.calls == 1
        assert "bitbucket status post rejected" in caplog.text
        assert token not in caplog.text
        assert "down-host" not in caplog.text

        caplog.clear()
        missing_key = dict(section)
        missing_key["scm"] = dict(section["scm"])
        missing_key["scm"].pop("project_key")
        skipped_key, opener = _run(_Opener(204), environ=env, section=missing_key, exit_code=0)
        assert skipped_key.exit_code == 0
        assert skipped_key.posted_by is None
        assert skipped_key.warnings == [warning]
        assert opener.calls == 0
        assert "bitbucket status post rejected" in caplog.text

        caplog.clear()
        noauth, opener = _run(_Opener(401, body), environ={}, section=section, exit_code=0)
        assert noauth.exit_code == 0
        assert noauth.ok is True
        assert noauth.posted_by is None
        assert noauth.warnings == []
        assert opener.calls == 0
        assert "bitbucket status post rejected" not in caplog.text
        assert "bitbucket status post failed" not in caplog.text

        caplog.clear()
        cloud = {
            "jenkins": {"report_status": True},
            "scm": {"kind": "cloud", "workspace": "acme", "repo_slug": "demo"},
        }
        created, opener = _run(_Opener(201, b'{"secret":"cloud-body-9f3a"}'), environ=env, section=cloud, exit_code=0)
        assert created.exit_code == 0
        assert created.ok is True
        assert created.posted_by == "harness"
        assert created.warnings == []
        assert opener.calls == 1
        assert "bitbucket status post rejected" not in caplog.text
        assert "cloud-body-9f3a" not in caplog.text
        assert token not in caplog.text

    missed = post_commit_status(
        section,
        commit=sha,
        state="SUCCESSFUL",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref="refs/heads/main",
        environ=env,
        opener=_Opener(401, body),
    )
    assert missed.posted is False
    assert missed.attempted is True
    assert missed.warnings == []
    quiet = post_commit_status(
        section,
        commit=sha,
        state="SUCCESSFUL",
        key="agentix",
        url="http://127.0.0.1:9/job/1/",
        ref="refs/heads/main",
        environ={},
        opener=_Opener(401, body),
    )
    assert quiet.posted is False
    assert quiet.attempted is False
    assert quiet.warnings == []


def test_peek_revision_uses_tree_when_sha_sits_past_uncapped_cap():
    """A fake opener only. No Jenkins controller and no Bitbucket post."""
    from memory.local_mcp.backends.jenkins import _READ_MAX, _Session

    sha = "abc1234"
    pad = "p" * (_READ_MAX + 64)
    uncapped = json.dumps(
        {
            "padding": pad,
            "result": "ABORTED",
            "actions": [
                {
                    "lastBuiltRevision": {
                        "SHA1": sha,
                        "branch": [{"name": "main"}],
                    },
                    "remoteUrls": ["https://bitbucket.local/scm/prj/repo.git"],
                }
            ],
        }
    ).encode("utf-8")
    assert uncapped.find(sha.encode("ascii")) > _READ_MAX
    slim = json.dumps(
        {
            "result": "ABORTED",
            "actions": [
                {
                    "lastBuiltRevision": {
                        "SHA1": sha,
                        "branch": [{"name": "main"}],
                    },
                    "remoteUrls": ["https://bitbucket.local/scm/prj/repo.git"],
                }
            ],
        }
    ).encode("utf-8")
    assert len(slim) < _READ_MAX
    tree = "tree=result,actions[lastBuiltRevision[SHA1,branch[name]],remoteUrls]"

    class _Body:
        def __init__(self, body: bytes) -> None:
            self.status = 200
            self.headers: dict[str, str] = {}
            self._body = body

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                chunk, self._body = self._body, b""
                return chunk
            chunk, self._body = self._body[:n], self._body[n:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Opener:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def open(self, request, timeout=None):
            del timeout
            url = request.full_url
            self.urls.append(url)
            if "tree=" not in url or "lastBuiltRevision" not in url:
                return _Body(uncapped)
            return _Body(slim)

    def _session(opener):
        session = _Session(
            "http://127.0.0.1:9",
            ("http", "127.0.0.1", 9),
            None,
            time.monotonic() + 30,
        )
        session.opener = opener
        session.number = 9
        session.rest = "/job/folder/job/name"
        return session

    opener = _Opener()
    session = _session(opener)
    session.peek_revision()
    assert opener.urls == [
        "http://127.0.0.1:9/job/folder/job/name/9/api/json?" + tree
    ]
    assert session.commit == sha
    assert session.ref == "refs/heads/main"
    assert session.revision_checked is True

    class _Cut:
        def open(self, request, timeout=None):
            del request, timeout
            return _Body(b"x" * (_READ_MAX + 8))

    cut = _session(_Cut())
    cut.peek_revision()
    assert cut.commit is None
    assert cut.revision_checked is False


def test_state_snapshot_does_not_rewrite_small_markdown(tmp_path: Path, monkeypatch):
    """A small LOOP_STATE.md and no JSON stays on disk. An existing JSON is only read."""
    import memory.state as state_mod

    def _refuse_write(*_args, **_kwargs):
        raise AssertionError("state_snapshot wrote durable state")

    monkeypatch.setattr(state_mod, "save_state", _refuse_write)
    monkeypatch.setattr(state_mod, "_migrate_from_md", _refuse_write)
    monkeypatch.setattr(state_mod, "_ensure_dirs", _refuse_write)
    monkeypatch.setattr(state_mod, "_write_md_projection", _refuse_write)

    work = _repo(tmp_path)
    agent = work / ".agent"
    agent.mkdir()
    marker = "SECRET_BODY_9f3a"
    md = agent / "LOOP_STATE.md"
    md.write_text(marker + "\n", encoding="utf-8")
    before = md.read_bytes()
    assert len(before) < 16 * 1024
    result = dispatch("state_snapshot", {"window": 3}, workdir=work)
    assert result.is_error is False
    assert result.structured["status"] == "READY"
    assert result.structured["cycle_number"] == 0
    assert marker not in json.dumps(result.structured)
    assert md.read_bytes() == before
    assert not (agent / "LOOP_STATE.json").exists()
    assert not (agent / "history").exists()

    (agent / "LOOP_STATE.json").write_text(
        json.dumps(
            {
                "cycle_number": 9,
                "status": "RUNNING",
                "recent_deltas": [
                    {"ts": "t1", "text": "one"},
                    {"ts": "t2", "text": "two"},
                ],
            }
        ),
        encoding="utf-8",
    )
    again = dispatch("state_snapshot", {"window": 1}, workdir=work)
    assert again.is_error is False
    assert again.structured["cycle_number"] == 9
    assert again.structured["status"] == "RUNNING"
    assert again.structured["recent_deltas"] == [{"ts": "t2", "text": "two"}]
    assert md.read_bytes() == before
    assert not (agent / "history").exists()


def _tree_bytes(path: Path) -> list[tuple[str, bytes | None]]:
    rows: list[tuple[str, bytes | None]] = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path).as_posix()
        if item.is_file() and not item.is_symlink():
            rows.append((rel, item.read_bytes()))
        else:
            rows.append((rel, None))
    return rows


def test_non_ci_working_directory_stays_under_allow_root(tmp_path: Path):
    """A non-CI tool may select a child checkout. It cannot widen the root."""
    wd = {"type": "string", "minLength": 1, "maxLength": 4096}
    for tool in catalog_tools():
        schema = tool["inputSchema"]
        assert schema["properties"]["working_directory"] == wd
        required = schema.get("required", [])
        if tool["name"] == "run_local_pipeline":
            assert "working_directory" in required
        else:
            assert "working_directory" not in required

    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".agent").mkdir()
    (root / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 1, "status": "ROOT"}),
        encoding="utf-8",
    )
    child = root / "child"
    child.mkdir()
    (child / ".git").mkdir()
    (child / ".agent").mkdir()
    (child / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 7, "status": "CHILD"}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".git").mkdir()
    (outside / ".agent").mkdir()
    (outside / ".agent" / "LOOP_STATE.json").write_text(
        json.dumps({"cycle_number": 99, "status": "OUT"}),
        encoding="utf-8",
    )
    (outside / "project_config.json").write_text("{}\n", encoding="utf-8")
    bare = root / "bare"
    bare.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    before_out = _tree_bytes(outside)
    before_root_state = (root / ".agent" / "LOOP_STATE.json").read_bytes()
    before_child_state = (child / ".agent" / "LOOP_STATE.json").read_bytes()

    def _snap(session: StdioSession, arguments: dict, req_id: int) -> dict:
        return session.request(
            "tools/call",
            {"name": "state_snapshot", "arguments": arguments},
            req_id=req_id,
        )

    with StdioSession(allow_root=root) as session:
        omitted = _snap(session, {}, 1)
        assert omitted["result"]["isError"] is False
        body = omitted["result"]["structuredContent"]
        assert body["cycle_number"] == 1
        assert body["status"] == "ROOT"
        assert body["history_dir"] == str((root / ".agent" / "history").resolve())

        selected = _snap(session, {"working_directory": str(child)}, 2)
        assert selected["result"]["isError"] is False
        child_body = selected["result"]["structuredContent"]
        assert child_body["cycle_number"] == 7
        assert child_body["status"] == "CHILD"
        assert child_body["history_dir"] == str((child / ".agent" / "history").resolve())

        dotted = _snap(
            session,
            {"working_directory": str(child / ".." / "child")},
            3,
        )
        assert dotted["result"]["structuredContent"]["cycle_number"] == 7

        for req_id, raw in (
            (4, outside),
            (5, link),
            (6, bare),
            (7, tmp_path),
        ):
            escaped = _snap(session, {"working_directory": str(raw)}, req_id)
            assert escaped["result"]["isError"] is True
            err = escaped["result"]["structuredContent"]
            assert err["error"]["code"] == "path_escape"
            assert err["exit_code"] == 2
            assert err["error"]["message"] in {
                "workdir escapes the allow-root",
                "workdir has no .agent or .git",
            }
            assert "99" not in json.dumps(err)

        relative = _snap(session, {"working_directory": "."}, 8)
        assert relative["result"]["isError"] is True
        relative_body = relative["result"]["structuredContent"]
        assert relative_body["error"]["code"] == "path_escape"
        assert relative_body.get("cycle_number") != 1

        empty = session.request(
            "tools/call",
            {"name": "state_snapshot", "arguments": {"working_directory": ""}},
            req_id=9,
        )
        assert empty["error"]["code"] == -32602
        huge = session.request(
            "tools/call",
            {
                "name": "memory_info",
                "arguments": {"working_directory": "x" * 4097},
            },
            req_id=10,
        )
        assert huge["error"]["code"] == -32602

        pipeline = session.request(
            "tools/call",
            {
                "name": "run_local_pipeline",
                "arguments": {
                    "backend": "act",
                    "working_directory": str(outside),
                },
            },
            req_id=11,
        )
        pipe_body = pipeline["result"]["structuredContent"]
        assert result_errors(pipe_body) == []
        assert pipeline["result"]["isError"] is True
        assert pipe_body["error"]["code"] == "path_escape"
        assert pipe_body["error"]["message"] == "Choose a path inside the project."
        assert pipe_body["config_backend"] == "act"
        assert pipe_body["exit_code"] == 2

    assert _tree_bytes(outside) == before_out
    assert (root / ".agent" / "LOOP_STATE.json").read_bytes() == before_root_state
    assert (child / ".agent" / "LOOP_STATE.json").read_bytes() == before_child_state
    assert not (root / ".agent" / "history").exists()
    assert not (child / ".agent" / "history").exists()
    assert not (bare / ".git").exists()
    assert not (bare / ".agent").exists()
    assert not (outside / ".agent" / "history").exists()
    assert not (outside / "AUDIT_LOG.json").exists()
