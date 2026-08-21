# -*- coding: utf-8 -*-
"""
Тесты политики прокси и health. Живой pxpipe/сеть не нужны.

Запуск:
  python -m memory.test_proxy
  pytest memory/test_proxy.py -q
"""

from __future__ import annotations

import io
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterator, Optional

from memory.proxy.config import (
    DEFAULT_INSTALL_CHAT_PROXY,
    DEFAULT_MODE,
    effective_mode,
    load_proxy_config,
)
from memory.proxy.health import health_report, tcp_ok
from memory.proxy.install import MARKER, MARKER_END, install_venv
from memory.proxy.policy import (
    ProxyNotReady,
    adapter_requires_proxy,
    assert_ready,
    init_should_fail,
)
from memory.proxy.__main__ import cli


@contextmanager
def _env(**kwargs: Optional[str]) -> Iterator[None]:
    """Временно подставляем/снимаем переменные окружения."""
    keys = list(kwargs.keys())
    saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in keys}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _write_cfg(root: Path, payload: dict) -> Path:
    agent = root / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "project_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _listen() -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    return srv


def test_tcp_ok_open_and_closed_port() -> None:
    srv = _listen()
    host, port = srv.getsockname()[:2]
    try:
        assert tcp_ok(host, port) is True
    finally:
        srv.close()
    assert tcp_ok(host, port) is False


def test_mode_matrix_env_beats_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(root, {"proxy": {"mode": "required"}, "supervisor": {"adapter": "grok"}})
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL=None,
            GROK_CLI_CHAT_PROXY_BASE_URL=None,
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert cfg["mode"] == "required"
            assert effective_mode(cfg) == "required"
        with _env(AGENTIX_PROXY="0", AGENTIX_PROJECT_ROOT=str(root)):
            cfg = load_proxy_config(root)
            assert effective_mode(cfg) == "off"
            assert_ready(root, adapter_name="grok")  # off — не бросаем
        with _env(
            AGENTIX_PROXY="1",
            AGENTIX_PROXY_MODE="preferred",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert effective_mode(cfg) == "preferred"
        with _env(AGENTIX_PROXY="off", AGENTIX_PROJECT_ROOT=str(root)):
            assert load_proxy_config(root)["mode"] == "off"


def test_missing_proxy_section_defaults_to_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(root, {"supervisor": {"adapter": "mock"}})
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert cfg["mode"] == DEFAULT_MODE == "required"


def test_mock_skips_assert_ready_even_if_port_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {
                    "mode": "required",
                    "pxpipe_base": "http://127.0.0.1:1",
                },
                "supervisor": {"adapter": "mock"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            assert adapter_requires_proxy("mock") is False
            assert_ready(root, adapter_name="mock")
            assert init_should_fail(root, frontend="mock") is False


def test_required_grok_raises_when_pxpipe_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "required", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "grok"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            raised = False
            try:
                assert_ready(root, adapter_name="grok")
            except ProxyNotReady as exc:
                raised = True
                assert "pxpipe" in str(exc).lower() or "required" in str(exc).lower()
            assert raised
            assert init_should_fail(root, frontend="grok") is True


def test_required_grok_ok_with_fake_listener() -> None:
    srv = _listen()
    host, port = srv.getsockname()[:2]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = f"http://{host}:{port}"
            _write_cfg(
                root,
                {
                    "proxy": {"mode": "required", "pxpipe_base": base},
                    "supervisor": {"adapter": "grok"},
                },
            )
            with _env(
                AGENTIX_PROXY=None,
                AGENTIX_PROXY_MODE=None,
                AGENTIX_PXPIPE_URL=base,
                AGENTIX_PROJECT_ROOT=str(root),
            ):
                assert_ready(root, adapter_name="grok")
                report = health_report(root, frontend="grok")
                assert report["pxpipe_ok"] is True
                assert report["ok"] is True
                assert init_should_fail(root, frontend="grok") is False
    finally:
        srv.close()


def test_init_foreign_frontends_skip_pxpipe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "required", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "mock"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            assert adapter_requires_proxy("blackbox") is False
            assert adapter_requires_proxy("cursor") is False
            assert adapter_requires_proxy("claude") is False
            assert adapter_requires_proxy("claude-code") is False
            assert adapter_requires_proxy("grok") is True
            assert init_should_fail(root, frontend="blackbox") is False
            assert init_should_fail(root, frontend="cursor") is False
            assert init_should_fail(root, frontend="claude") is False
            assert init_should_fail(root, frontend="3") is False
            assert init_should_fail(root, frontend="4") is False
            assert init_should_fail(root, frontend="grok") is True


def test_preferred_does_not_raise_when_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "preferred", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "grok"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE="preferred",
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            assert_ready(root, adapter_name="grok")
            assert init_should_fail(root, frontend="grok") is False


def test_install_venv_writes_marker_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        act = root / ".venv" / "bin" / "activate"
        act.parent.mkdir(parents=True)
        act.write_text("# fake activate\n", encoding="utf-8")
        first = install_venv(root=root)
        assert first["ok"]
        text = act.read_text(encoding="utf-8")
        assert MARKER in text
        assert MARKER_END in text
        assert DEFAULT_INSTALL_CHAT_PROXY in text
        assert "GROK_CLI_CHAT_PROXY_BASE_URL" in text
        second = install_venv(root=root)
        text2 = act.read_text(encoding="utf-8")
        assert text.count(MARKER_END) == 1
        assert text2.count(MARKER_END) == 1
        assert second["written"] == [] or MARKER in text2


def test_cli_health_json_and_init_mock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "required", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "mock"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli(
                    [
                        "health",
                        "--json",
                        "--init",
                        "--workdir",
                        str(root),
                        "--frontend",
                        "mock",
                    ]
                )
            assert rc == 0
            payload = json.loads(buf.getvalue())
            assert payload["adapter_exempt"] is True
            assert payload["pxpipe_ok"] is False


class _FakeUpstream(BaseHTTPRequestHandler):
    last_headers: Dict[str, str] = {}
    last_body = b""
    posts = 0

    def log_message(self, *_a: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        type(self).last_body = self.rfile.read(n)
        type(self).last_headers = {k: v for k, v in self.headers.items()}
        type(self).posts += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"id":"sse-1"}\n\n')
        self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"upstream":true}')


def _start_httpd(httpd) -> threading.Thread:
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


def test_bind_rejects_non_loopback() -> None:
    from memory.proxy.gateway import BindError, make_server

    try:
        make_server("0.0.0.0", 0)
        raise AssertionError("expected BindError")
    except BindError:
        pass


def test_gateway_healthz_sse_roundtrip_and_redaction() -> None:
    from memory.proxy.gateway import make_server

    _FakeUpstream.posts = 0
    up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    _start_httpd(up)
    up_port = up.server_address[1]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            _write_cfg(
                root,
                {
                    "proxy": {
                        "mode": "required",
                        "pxpipe_base": f"http://127.0.0.1:{up_port}",
                        "exact_cache": True,
                        "compress_body": True,
                    },
                    "supervisor": {"adapter": "mock"},
                },
            )
            with _env(AGENTIX_PROJECT_ROOT=str(root), AGENTIX_PROXY=None):
                gw = make_server(
                    "127.0.0.1",
                    0,
                    upstream=f"http://127.0.0.1:{up_port}",
                    workdir=root,
                    quiet=True,
                )
                _start_httpd(gw)
                port = gw.server_address[1]
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/healthz", timeout=2
                    ) as resp:
                        health = json.loads(resp.read().decode("utf-8"))
                    assert health["ok"] is True

                    payload = json.dumps(
                        {
                            "model": "grok",
                            "input": [{"role": "user", "content": "hello gateway"}],
                        }
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/responses",
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer SUPERSECRET_TOKEN",
                            "X-Agentix-Root": str(root),
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        body = resp.read()
                    assert b"data:" in body
                    assert b"sse-1" in body
                    events = (root / ".agent" / "proxy_events.jsonl").read_text(
                        encoding="utf-8"
                    )
                    assert "SUPERSECRET_TOKEN" not in events
                    assert "Bearer " not in events
                    assert "/v1/responses" in events
                    assert _FakeUpstream.last_headers.get("Authorization") == (
                        "Bearer SUPERSECRET_TOKEN"
                    )
                finally:
                    gw.shutdown()
    finally:
        up.shutdown()


def test_gateway_required_no_public_fallback() -> None:
    from memory.proxy.gateway import make_server

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".agent").mkdir()
        _write_cfg(
            root,
            {
                "proxy": {
                    "mode": "required",
                    "pxpipe_base": "http://127.0.0.1:1",
                    "upstream_fallback": "https://cli-chat-proxy.grok.com",
                }
            },
        )
        with _env(AGENTIX_PROJECT_ROOT=str(root), AGENTIX_PROXY=None):
            gw = make_server(
                "127.0.0.1",
                0,
                upstream="http://127.0.0.1:1",
                workdir=root,
                quiet=True,
            )
            _start_httpd(gw)
            port = gw.server_address[1]
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/responses",
                    data=b'{"model":"x","input":[]}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urllib.request.urlopen(req, timeout=3)
                    raise AssertionError("expected 502")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 502
                    err = exc.read().decode("utf-8")
                    assert "pxpipe" in err.lower() or "unavailable" in err.lower()
            finally:
                gw.shutdown()


def test_stats_unprobed_savings_stay_null() -> None:
    import memory.proxy.stats as stats_mod
    from memory.proxy.stats import collect_stats, summarize_pxpipe

    fake = {
        "total": 10,
        "compressed": 6,
        "ok2xx": 8,
        "baselineMeasuredEvents": 0,
        "savedTokensTotal": 0,
        "baselineTokensTotal": 0,
        "cacheReadTokensTotal": 0,
        "inputTokensTotal": 100,
    }
    s = summarize_pxpipe(fake)
    assert s["requests"] == 10
    assert s["compressed_pct"] == 60.0
    assert s["measured_saved_pct"] is None
    orig = stats_mod._pxpipe_raw
    stats_mod._pxpipe_raw = lambda: None  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agent").mkdir()
            with _env(AGENTIX_PROJECT_ROOT=str(root), AGENTIX_PROXY="0"):
                report = collect_stats(root)
            assert report["proxy_mode"] == "off"
            assert report["pxpipe"]["measured_saved_pct"] is None
            assert "measured_raw_token_saved_pct" in report["slo"]
            assert "unslod" in report["slo"]["measured_raw_token_saved_pct"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli(["stats", "--json", "--workdir", str(root)])
            assert rc == 0
            payload = json.loads(buf.getvalue())
            assert payload["pxpipe"]["measured_saved_pct"] is None
    finally:
        stats_mod._pxpipe_raw = orig


def test_cache_tools_do_not_collide_with_plain() -> None:
    from memory.proxy.cache import canonical_key
    from memory.proxy.middleware import maybe_store_cache, process_request

    messages = [{"role": "user", "content": "hello cache"}]
    plain = {"model": "grok", "input": messages}
    tooled = {
        "model": "grok",
        "input": messages,
        "tools": [{"type": "function", "function": {"name": "x"}}],
    }
    hot = {"model": "grok", "input": messages, "temperature": 0.2}
    cold = {"model": "grok", "input": messages, "temperature": 0.9}
    assert canonical_key(plain) != canonical_key(tooled)
    assert canonical_key(hot) != canonical_key(cold)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".agent").mkdir()
        cfg = {
            "compress_body": True,
            "body_budget_tokens": 24000,
            "keep_recent_turns": 2,
            "exact_cache": True,
            "fidelity": False,
        }
        _, meta_t = process_request(
            json.dumps(tooled).encode("utf-8"),
            path="/v1/responses",
            headers={},
            cfg=cfg,
            project_root=root,
        )
        maybe_store_cache(
            project_root=root,
            cfg=cfg,
            meta=meta_t,
            status=200,
            content_type="application/json",
            response_body=b'{"tool":true}',
        )
        _, meta_p = process_request(
            json.dumps(plain).encode("utf-8"),
            path="/v1/responses",
            headers={},
            cfg=cfg,
            project_root=root,
        )
        assert meta_p.get("cache_hit") is False


def test_fidelity_golden_ids_survive_distill() -> None:
    from memory.proxy.middleware import process_request

    sha = "c5ca061"
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    digest = "0123456789abcdef" * 4  # 64 hex
    filler = ("lorem ipsum dolor sit amet " * 220) + f" commit {sha} id {uuid} hash {digest}"
    obj = {
        "model": "grok",
        "input": [
            {"role": "user", "content": filler},
            {"role": "assistant", "content": filler},
            {"role": "user", "content": "latest turn only"},
        ],
    }
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    cfg = {
        "compress_body": True,
        "body_budget_tokens": 80,
        "keep_recent_turns": 1,
        "exact_cache": False,
        "fidelity": True,
    }
    out, meta = process_request(
        body,
        path="/v1/responses",
        headers={},
        cfg=cfg,
        project_root=None,
    )
    text = out.decode("utf-8")
    assert "--- FIDELITY ---" in text
    assert sha in text
    assert uuid in text
    assert digest in text
    # sidecar держит оригиналы даже если старые ходы ужаты
    sidecar = text.split("--- FIDELITY ---", 1)[1].split("--- END FIDELITY ---", 1)[0]
    assert sha in sidecar
    assert uuid in sidecar
    assert digest in sidecar
    assert meta.get("fidelity") is True
    parsed = json.loads(text)
    first = parsed["input"][0]["content"]
    rest = first.split("--- END FIDELITY ---", 1)[-1]
    assert len(rest) < len(filler)


def test_fidelity_keeps_short_sha_without_git_log() -> None:
    from memory.proxy.fidelity import extract_ids

    sha = "b107f1e"
    blob = f"see commit {sha} in the other clone"
    repo = Path(__file__).resolve().parents[1]
    ids = extract_ids(blob, project_root=repo)
    assert sha in ids


def _selfsigned_cert(dir_path: Path) -> tuple[Path, Path]:
    cert = dir_path / "cert.pem"
    key = dir_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return cert, key


def test_preferred_https_fallback_local_tls() -> None:
    from memory.proxy.config import split_host_port
    from memory.proxy.gateway import make_server

    host, port = split_host_port("https://cli-chat-proxy.grok.com", 8100)
    assert host == "cli-chat-proxy.grok.com"
    assert port == 443

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".agent").mkdir()
        cert, key = _selfsigned_cert(root)
        up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
        ctx_srv = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx_srv.load_cert_chain(str(cert), str(key))
        up.socket = ctx_srv.wrap_socket(up.socket, server_side=True)
        _start_httpd(up)
        tls_port = up.server_address[1]
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE
        try:
            _write_cfg(
                root,
                {
                    "proxy": {
                        "mode": "preferred",
                        "pxpipe_base": "http://127.0.0.1:1",
                        "upstream_fallback": f"https://127.0.0.1:{tls_port}",
                    }
                },
            )
            with _env(AGENTIX_PROJECT_ROOT=str(root), AGENTIX_PROXY_MODE="preferred"):
                gw = make_server(
                    "127.0.0.1",
                    0,
                    upstream="http://127.0.0.1:1",
                    workdir=root,
                    quiet=True,
                    ssl_context=client_ctx,
                )
                _start_httpd(gw)
                gport = gw.server_address[1]
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{gport}/v1/responses",
                        data=json.dumps(
                            {"model": "grok", "input": [{"role": "user", "content": "hi"}]}
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        body = resp.read()
                    assert resp.status == 200
                    assert b"data:" in body
                finally:
                    gw.shutdown()
        finally:
            up.shutdown()


def test_grok_adapter_calls_assert_ready(monkeypatch=None) -> None:
    """GrokAdapter.run_role_turn должен упасть на политике до subprocess."""
    import memory.adapters.grok as grok_mod
    from memory.adapters.grok import GrokAdapter

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise ProxyNotReady("blocked in test")

    orig = grok_mod.assert_ready
    grok_mod.assert_ready = _boom  # type: ignore[assignment]
    try:
        ad = GrokAdapter({"command": "grok"})
        with tempfile.TemporaryDirectory() as tmp:
            raised = False
            try:
                ad.run_role_turn(
                    role="Coder",
                    prompt="x",
                    handoff_in_path=None,
                    workdir=Path(tmp),
                    timeout_s=5,
                )
            except ProxyNotReady:
                raised = True
            assert raised
            assert called["n"] == 1
    finally:
        grok_mod.assert_ready = orig


def _run_all() -> None:
    tests = [
        test_tcp_ok_open_and_closed_port,
        test_mode_matrix_env_beats_file,
        test_missing_proxy_section_defaults_to_required,
        test_mock_skips_assert_ready_even_if_port_closed,
        test_init_foreign_frontends_skip_pxpipe,
        test_required_grok_raises_when_pxpipe_down,
        test_required_grok_ok_with_fake_listener,
        test_preferred_does_not_raise_when_down,
        test_install_venv_writes_marker_idempotent,
        test_cli_health_json_and_init_mock,
        test_bind_rejects_non_loopback,
        test_gateway_healthz_sse_roundtrip_and_redaction,
        test_gateway_required_no_public_fallback,
        test_stats_unprobed_savings_stay_null,
        test_cache_tools_do_not_collide_with_plain,
        test_fidelity_golden_ids_survive_distill,
        test_fidelity_keeps_short_sha_without_git_log,
        test_preferred_https_fallback_local_tls,
        test_grok_adapter_calls_assert_ready,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"all {len(tests)} proxy tests passed")


if __name__ == "__main__":
    _run_all()
