# -*- coding: utf-8 -*-
"""Host/peer loopback, матрица токена, редактура логов и /health."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.security import is_loopback_address, is_loopback_host


def test_health_ok(dashboard_client):
    r = dashboard_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["ws_clients"], int)
    assert body["ws_clients"] >= 0
    assert body["watcher"] == "poll-1s"
    assert "workdir" in body
    assert "loop_status" in body
    assert "stop" in body
    assert body["bind"].endswith(":8112")
    assert ":8110" not in body["bind"]
    assert r.headers.get("referrer-policy") == "same-origin"
    assert "DASHBOARD_TOKEN" not in r.text
    assert "Authorization" not in r.text


def test_host_evil_dot_com_403(dashboard_client):
    r = dashboard_client.get("/health", headers={"Host": "evil.com"})
    assert r.status_code == 403
    assert "dashboard is loopback-only" in r.text


def test_host_nip_io_rebinding_403(dashboard_client):
    r = dashboard_client.get("/health", headers={"Host": "127.0.0.1.nip.io"})
    assert r.status_code == 403
    assert "dashboard is loopback-only" in r.text


def test_root_renders_loop(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Agentix Control" in r.text
    assert "Loop" in r.text


def test_is_loopback_host_rejects_rebinding():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("127.0.0.1:8112")
    assert is_loopback_host("localhost")
    assert is_loopback_host("localhost:8112")
    assert is_loopback_host("::1")
    assert is_loopback_host("[::1]:8112")
    assert is_loopback_address("127.0.0.8")
    assert not is_loopback_host("127.0.0.1.nip.io")
    assert not is_loopback_host("evil.com")
    assert not is_loopback_host("10.0.0.2")
    assert not is_loopback_address("10.0.0.2")
    assert not is_loopback_host("127.evil.com")


def test_non_loopback_peer_403(tmp_path: Path, monkeypatch):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    monkeypatch.setenv("AGENTIX_DASHBOARD_PORT", "8112")
    app = create_app(workdir=tmp_path)

    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = ("10.0.0.2", 9)
        await app(scope, receive, send)

    with TestClient(asgi, base_url="http://127.0.0.1:8112") as client:
        r = client.get("/health")
        assert r.status_code == 403
        assert "dashboard is loopback-only" in r.text


def test_serve_rejects_non_loopback_bind(tmp_path: Path):
    from memory.dashboard.server import BindError, serve

    try:
        serve(workdir=tmp_path, host="0.0.0.0", port=8112)
    except BindError as exc:
        msg = str(exc)
        assert "SR-04" in msg
        assert "0.0.0.0" in msg
        return
    raise AssertionError("expected BindError")


def test_playbook_partial_rejects_traversal(dashboard_client):
    for path in (
        "/partials/playbook/%2e%2e%2fetc%2fpasswd",
        "/partials/playbook/%2e%2e%2fPLAYBOOKS",
        "/partials/playbook/..%2fLOOP_STATE.json",
        "/partials/playbook/%3Cscript%3E",
    ):
        r = dashboard_client.get(path)
        assert r.status_code == 404
        assert "<script>" not in r.text


def _csrf_header(client) -> dict:
    r = client.get("/")
    assert r.status_code == 200
    token = client.cookies.get("agentix_csrf")
    assert token
    return {"X-CSRF-Token": token}


def _asgi_loopback(app):
    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = ("127.0.0.1", 9)
        await app(scope, receive, send)

    return asgi


def _token_client(tmp_path: Path, monkeypatch):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

    monkeypatch.setenv("AGENTIX_DASHBOARD_PORT", "8112")
    app = create_app(workdir=tmp_path)
    return TestClient(_asgi_loopback(app), base_url="http://127.0.0.1:8112")


def test_get_sets_csrf_cookie_and_hx_headers(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    token = dashboard_client.cookies.get("agentix_csrf")
    assert token
    assert len(token) >= 32
    raw = r.headers.get("set-cookie") or ""
    cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [raw]
    blob = "\n".join(cookies).lower()
    assert "agentix_csrf=" in blob
    assert "httponly" in blob
    assert "samesite=strict" in blob
    assert "path=/" in blob
    assert '"X-CSRF-Token": "' in r.text
    assert token in r.text


def test_post_stop_requires_csrf(dashboard_client, tmp_path: Path):
    r = dashboard_client.post("/actions/stop")
    assert r.status_code == 403
    assert "csrf" in r.text.lower()
    assert not (tmp_path / ".agent" / "STOP").exists()

    bad = dashboard_client.get("/")
    assert bad.status_code == 200
    r2 = dashboard_client.post("/actions/stop", headers={"X-CSRF-Token": "nope"})
    assert r2.status_code == 403
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_post_stop_with_csrf_writes_flag(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    r = dashboard_client.post("/actions/stop", headers=headers)
    assert r.status_code == 204
    stop = tmp_path / ".agent" / "STOP"
    assert stop.is_file()
    assert stop.read_text(encoding="utf-8") == "1"
    audit = json.loads((tmp_path / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8"))
    last = audit["entries"][-1]
    assert last["action"] == "dashboard.stop"
    assert last["role"] == "operator"
    assert last["approval_required"] is True
    assert last["approved"] is True


def test_clear_stop_csrf_and_idempotent(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    assert dashboard_client.post("/actions/stop", headers=headers).status_code == 204
    r = dashboard_client.post("/actions/clear-stop", headers=headers)
    assert r.status_code == 204
    assert not (tmp_path / ".agent" / "STOP").exists()
    r2 = dashboard_client.post("/actions/clear-stop", headers=headers)
    assert r2.status_code == 204
    audit = json.loads((tmp_path / ".agent" / "AUDIT_LOG.json").read_text(encoding="utf-8"))
    actions = [e["action"] for e in audit["entries"]]
    assert "dashboard.clear_stop" in actions
    assert all(e["role"] == "operator" for e in audit["entries"])


def test_same_origin_origin_mismatch_403(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    headers["Origin"] = "http://evil.com"
    r = dashboard_client.post("/actions/stop", headers=headers)
    assert r.status_code == 403
    assert "cross-origin" in r.text
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_same_origin_sec_fetch_site_cross_site_403(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    headers["Sec-Fetch-Site"] = "cross-site"
    r = dashboard_client.post("/actions/stop", headers=headers)
    assert r.status_code == 403
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_same_origin_matching_origin_ok(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    headers["Origin"] = "http://127.0.0.1:8112"
    r = dashboard_client.post("/actions/stop", headers=headers)
    assert r.status_code == 204
    assert (tmp_path / ".agent" / "STOP").read_text(encoding="utf-8") == "1"


def test_body_cap_413(dashboard_client, tmp_path: Path):
    headers = _csrf_header(dashboard_client)
    r = dashboard_client.post(
        "/actions/stop",
        content=b"x" * (64 * 1024 + 1),
        headers=headers,
    )
    assert r.status_code == 413
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_body_cap_chunked_when_content_length_understated(
    dashboard_client, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "memory.dashboard.server.content_length_too_large",
        lambda headers, limit=64 * 1024: False,
    )
    headers = _csrf_header(dashboard_client)
    r = dashboard_client.post(
        "/actions/stop",
        content=b"x" * (64 * 1024 + 1),
        headers=headers,
    )
    assert r.status_code == 413
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_consume_capped_missing_content_length():
    from memory.dashboard.security import MAX_BODY_BYTES, consume_capped

    async def over():
        async def stream():
            yield b"x" * (MAX_BODY_BYTES // 2)
            yield b"y" * (MAX_BODY_BYTES // 2 + 2)

        return await consume_capped(stream(), MAX_BODY_BYTES)

    async def under():
        async def stream():
            yield b"ok"
            yield b"-body"

        return await consume_capped(stream(), MAX_BODY_BYTES)

    assert asyncio.run(over()) is None
    assert asyncio.run(under()) == b"ok-body"


def test_pr_link_get_skips_csrf(dashboard_client, monkeypatch):
    monkeypatch.setattr(
        "memory.dashboard.actions._gh_pr_url",
        lambda workdir: (None, "no PR / gh missing"),
    )
    r = dashboard_client.get("/actions/pr-link")
    assert r.status_code == 200
    assert "no PR / gh missing" in r.text
    assert "text-amber-400" in r.text


def test_dashboard_token_missing_401(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/health")
        assert r.status_code == 401


def test_dashboard_token_wrong_401(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/health", headers={"X-API-Token": "nope"})
        assert r.status_code == 401


def test_dashboard_token_query_sets_cookie(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/health?token=s3cret")
        assert r.status_code == 200
        raw = r.headers.get("set-cookie") or ""
        cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [raw]
        blob = "\n".join(cookies).lower()
        assert "agentix_token=" in blob
        assert "httponly" in blob
        assert "samesite=strict" in blob
        r2 = client.get("/health")
        assert r2.status_code == 200


def test_dashboard_token_header_ok(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/health", headers={"X-API-Token": "s3cret"})
        assert r.status_code == 200
        r2 = client.get("/health", headers={"Authorization": "Bearer s3cret"})
        assert r2.status_code == 200


def test_query_token_overrides_stale_cookie(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "new")
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/health?token=new", headers={"Cookie": "agentix_token=old"})
        assert r.status_code == 200
        raw = r.headers.get("set-cookie") or ""
        cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [raw]
        if hasattr(r.headers, "getlist"):
            cookies = r.headers.getlist("set-cookie")
        blob = "\n".join(cookies)
        assert "agentix_token=new" in blob


def test_extract_token_query_beats_cookie():
    from memory.dashboard.security import extract_token

    assert (
        extract_token(
            {},
            cookies={"agentix_token": "old"},
            query_params={"token": "new"},
        )
        == "new"
    )
    assert (
        extract_token(
            {"x-api-token": "hdr"},
            cookies={"agentix_token": "old"},
            query_params={"token": "new"},
        )
        == "hdr"
    )
    assert (
        extract_token(
            {"authorization": "Bearer brr"},
            cookies={"agentix_token": "old"},
            query_params={"token": "new"},
        )
        == "brr"
    )
    assert extract_token({}, cookies={"agentix_token": "cook"}) == "cook"


def test_empty_dashboard_token_disables_check(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    with _token_client(tmp_path, monkeypatch) as client:
        assert client.get("/health").status_code == 200


def test_header_preferred_over_query(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "good-token")
    with _token_client(tmp_path, monkeypatch) as client:
        bad = client.get("/health?token=good-token", headers={"X-API-Token": "nope"})
        assert bad.status_code == 401
        ok = client.get("/health?token=nope", headers={"X-API-Token": "good-token"})
        assert ok.status_code == 200


def test_ws_query_token_works(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path, monkeypatch) as client:
        with client.websocket_connect("ws://127.0.0.1:8112/ws/ui?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "connected"


def test_health_ws_clients_and_loop_fields(dashboard_client, tmp_path: Path):
    agent = tmp_path / ".agent"
    agent.mkdir(exist_ok=True)
    (agent / "LOOP_STATE.json").write_text(
        json.dumps(
            {"status": "IN_PROGRESS", "active_role": "Coder", "cycle_number": 12}
        ),
        encoding="utf-8",
    )
    (agent / "STOP").write_text("1", encoding="utf-8")
    before = dashboard_client.get("/health").json()
    assert before["loop_status"] == "IN_PROGRESS"
    assert before["role"] == "Coder"
    assert before["stop"] is True
    assert before["watcher"] == "poll-1s"
    n0 = before["ws_clients"]
    with dashboard_client.websocket_connect("ws://127.0.0.1:8112/ws/ui") as ws:
        assert ws.receive_json()["type"] == "connected"
        during = dashboard_client.get("/health").json()
        assert during["ws_clients"] == n0 + 1
    after = dashboard_client.get("/health").json()
    assert after["ws_clients"] == n0


def test_redact_tokens_hides_dashboard_token_and_authorization(monkeypatch):
    from memory.dashboard.redact import redact_headers, redact_tokens

    secret = "s3cret-value-99"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    raw = (
        f"auth {secret} Authorization: Bearer {secret} "
        f"GET /health?token={secret} Cookie: agentix_token={secret}"
    )
    out = redact_tokens(raw)
    assert secret not in out
    assert "****" in out
    headers = redact_headers(
        {
            "Authorization": f"Bearer {secret}",
            "X-API-Token": secret,
            "Cookie": f"agentix_token={secret}",
            "Host": "127.0.0.1:8112",
        }
    )
    assert secret not in headers["Authorization"]
    assert secret not in headers["X-API-Token"]
    assert secret not in headers["Cookie"]
    assert headers["Host"] == "127.0.0.1:8112"


def test_redact_leaves_ws_js_token_placeholder():
    from memory.dashboard.redact import redact_tokens

    js = "url += '?token=' + encodeURIComponent(q);"
    assert redact_tokens(js) == js


def test_log_filter_redacts_token_and_authorization(monkeypatch, caplog):
    from memory.dashboard.redact import RedactFilter, install_log_redaction

    secret = "s3cret-value-99"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    install_log_redaction()
    log = logging.getLogger("memory.dashboard.redact_test")
    log.addFilter(RedactFilter())
    log.setLevel(logging.INFO)
    with caplog.at_level(logging.INFO, logger="memory.dashboard.redact_test"):
        log.info("GET /health?token=%s Authorization: Bearer %s", secret, secret)
    assert secret not in caplog.text
    assert "****" in caplog.text


def test_request_log_does_not_echo_query_token(tmp_path: Path, monkeypatch, caplog):
    secret = "s3cret-value-99"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    with _token_client(tmp_path, monkeypatch) as client:
        with caplog.at_level(logging.INFO, logger="memory.dashboard"):
            r = client.get("/health?token=" + secret)
            assert r.status_code == 200
    assert secret not in caplog.text


def test_html_query_token_redirects_stripping_query(tmp_path: Path, monkeypatch):
    secret = "s3cret-value-99"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/?token=" + secret, follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers.get("location") or ""
        assert "token=" not in loc
        assert loc == "/" or loc.endswith("/")
        assert r.headers.get("referrer-policy") == "same-origin"
        raw = r.headers.get("set-cookie") or ""
        cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [raw]
        if hasattr(r.headers, "getlist"):
            cookies = r.headers.getlist("set-cookie")
        blob = "\n".join(cookies)
        assert "agentix_token=" in blob.lower()
        health = client.get("/health?token=" + secret, follow_redirects=False)
        assert health.status_code == 200
        assert health.json()["ok"] is True
        r2 = client.get("/", follow_redirects=False)
        assert r2.status_code == 200
        assert "Agentix Control" in r2.text


def test_html_does_not_echo_dashboard_token(tmp_path: Path, monkeypatch):
    secret = "super-secret-token-99"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    agent = tmp_path / ".agent"
    agent.mkdir(exist_ok=True)
    (agent / "LOOP_STATE.json").write_text(
        json.dumps(
            {
                "status": "IN_PROGRESS",
                "active_role": "Coder",
                "cycle_number": 1,
                "notes": f"do not leak {secret}",
            }
        ),
        encoding="utf-8",
    )
    with _token_client(tmp_path, monkeypatch) as client:
        r = client.get("/", headers={"X-API-Token": secret})
        assert r.status_code == 200
        assert secret not in r.text
        assert "supe****n-99" not in r.text
        assert "do not leak " in r.text
        assert "****" in r.text
        assert "?token=' + encodeURIComponent(q)" in r.text
        assert 'name="referrer"' in r.text
        assert "same-origin" in r.text


def test_env_example_dashboard_token_empty():
    root = Path(__file__).resolve().parents[1]
    for rel in (".env.example", "examples/consumer-starter/agentic.env.example"):
        text = (root / rel).read_text(encoding="utf-8")
        found = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("DASHBOARD_TOKEN="):
                assert stripped == "DASHBOARD_TOKEN=", rel
                found = True
        assert found, rel
    assert "AGENTIX_DASHBOARD_PORT=8112" in (root / ".env.example").read_text(
        encoding="utf-8"
    )


def test_serve_keeps_uvicorn_default_log_config():
    root = Path(__file__).resolve().parents[1]
    text = (root / "memory/dashboard/server.py").read_text(encoding="utf-8")
    assert "log_config=None" not in text
    assert "access_log=False" in text


def test_redact_filter_not_installed_on_root():
    from memory.dashboard.redact import RedactFilter, install_log_redaction

    install_log_redaction()
    assert not any(isinstance(f, RedactFilter) for f in logging.getLogger().filters)
    dash = logging.getLogger("memory.dashboard")
    assert any(isinstance(f, RedactFilter) for f in dash.filters)


def test_redact_sources_do_not_import_runner():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "memory/dashboard/redact.py",
        "memory/dashboard/security.py",
        "memory/dashboard/server.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "run_loop" not in text
        assert "get_adapter" not in text
