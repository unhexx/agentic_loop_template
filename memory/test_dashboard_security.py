# -*- coding: utf-8 -*-
"""Host/peer loopback и /health — без записи на диск."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.security import is_loopback_address, is_loopback_host


def test_health_ok(dashboard_client):
    r = dashboard_client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


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


def test_non_loopback_peer_403(tmp_path: Path):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

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


def _token_client(tmp_path: Path):
    try:
        import httpx2  # noqa: F401
    except ModuleNotFoundError:
        pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from memory.dashboard.server import create_app

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
    with _token_client(tmp_path) as client:
        r = client.get("/health")
        assert r.status_code == 401


def test_dashboard_token_wrong_401(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path) as client:
        r = client.get("/health", headers={"X-API-Token": "nope"})
        assert r.status_code == 401


def test_dashboard_token_query_sets_cookie(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    with _token_client(tmp_path) as client:
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
    with _token_client(tmp_path) as client:
        r = client.get("/health", headers={"X-API-Token": "s3cret"})
        assert r.status_code == 200
        r2 = client.get("/health", headers={"Authorization": "Bearer s3cret"})
        assert r2.status_code == 200


def test_empty_dashboard_token_disables_check(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    with _token_client(tmp_path) as client:
        assert client.get("/health").status_code == 200
