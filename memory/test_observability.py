# -*- coding: utf-8 -*-
"""WARNING на критических swallow, редактура GROK_* в caplog, heartbeat молчит."""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from memory.supervisor import (
    HEARTBEAT_FILENAME,
    _knowledge_block,
    _maybe_compress_prompt,
    _state_snapshot_for_workdir,
    _stop_heartbeat_thread,
    _write_heartbeat,
    build_role_prompt,
    load_config,
    load_last_handoff,
)


def test_broken_project_config_logs_warning(tmp_path: Path, caplog):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory"):
        assert load_config(tmp_path) == {}
    assert "load_config failed" in caplog.text
    assert "project_config.json" in caplog.text


def test_load_config_unexpected_exception_raises(tmp_path: Path, monkeypatch):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text("{}", encoding="utf-8")

    def boom(_s, *a, **k):
        raise RuntimeError("unexpected")

    import memory.supervisor as s

    monkeypatch.setattr(s.json, "loads", boom)
    with pytest.raises(RuntimeError, match="unexpected"):
        load_config(tmp_path)


def test_load_last_handoff_corrupt_logs_warning(tmp_path: Path, caplog):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "last_handoff.json").write_text("{nope", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory"):
        assert load_last_handoff(tmp_path) is None
    assert "load_last_handoff failed" in caplog.text


def test_knowledge_inject_logs_warning(tmp_path: Path, caplog, monkeypatch):
    import memory.knowledge as kn

    db = tmp_path / "k.sqlite"
    db.write_bytes(b"x")
    monkeypatch.setattr(kn, "db_path", lambda cwd=None: db)

    def boom(*a, **k):
        raise RuntimeError("inject boom")

    monkeypatch.setattr(kn, "query", boom)
    with caplog.at_level(logging.WARNING, logger="memory"):
        assert _knowledge_block("Coder", {"summary": "x"}, tmp_path) == ""
    assert "knowledge inject failed" in caplog.text
    assert "inject boom" in caplog.text


def test_compress_skipped_logs_warning_without_prompt_body(
    tmp_path: Path, caplog, monkeypatch
):
    import memory.compressor as compressor
    import memory.context_budget as budget

    monkeypatch.setattr(budget, "estimate_tokens", lambda text, **_k: 99_999)

    def boom(*a, **k):
        raise RuntimeError("compress boom")

    monkeypatch.setattr(compressor, "compress_text", boom)
    text = "PROMPT_BODY_MUST_NOT_APPEAR " * 20
    with caplog.at_level(logging.WARNING, logger="memory"):
        out = _maybe_compress_prompt(text, tmp_path)
    assert out == text
    assert "compress skipped" in caplog.text
    assert "PROMPT_BODY_MUST_NOT_APPEAR" not in caplog.text


def test_state_snapshot_logs_warning(tmp_path: Path, caplog, monkeypatch):
    import memory.state as state_mod

    def boom(*a, **k):
        raise RuntimeError("snap boom")

    monkeypatch.setattr(state_mod, "snapshot", boom)
    with caplog.at_level(logging.WARNING, logger="memory"):
        assert _state_snapshot_for_workdir(tmp_path) == "{}"
    assert "state snapshot failed" in caplog.text


def test_role_prompt_read_logs_warning_without_body(
    tmp_path: Path, caplog, monkeypatch
):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "short_coder_prompt.md").write_text(
        "# Coder\nSECRET_PROMPT_BODY\n", encoding="utf-8"
    )
    (tmp_path / ".agent").mkdir()
    orig = Path.read_text

    def wrapped(self, *a, **k):
        if self.name.endswith("_prompt.md"):
            raise OSError("unreadable")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", wrapped)
    with caplog.at_level(logging.WARNING, logger="memory"):
        build_role_prompt("Coder", None, tmp_path)
    assert "role prompt read failed" in caplog.text
    assert "SECRET_PROMPT_BODY" not in caplog.text


def test_broken_project_config_proxy_logs_warning(tmp_path: Path, caplog):
    from memory.proxy.config import load_project_config

    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "project_config.json").write_text("{nope", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory"):
        assert load_project_config(tmp_path) == {}
    assert "load_project_config failed" in caplog.text


def test_playbooks_corrupt_index_logs_before_bak(tmp_path: Path, caplog, monkeypatch):
    from memory import playbooks as pb

    monkeypatch.chdir(tmp_path)
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "PLAYBOOKS.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory"):
        data = pb._load_index()
    assert data.get("playbooks") == {}
    assert (agent / "PLAYBOOKS.json.bak").is_file()
    assert "renaming to bak" in caplog.text


def test_heartbeat_unlink_stays_silent(tmp_path: Path, caplog):
    path = tmp_path / ".agent" / HEARTBEAT_FILENAME
    _write_heartbeat(path, "Coder", "IN_PROGRESS")
    stop = threading.Event()
    stop.set()
    with caplog.at_level(logging.DEBUG, logger="memory"):
        _stop_heartbeat_thread(stop, None, path)
    assert caplog.records == []


def test_gateway_middleware_exception_logs_and_continues(
    tmp_path: Path, caplog, monkeypatch
):
    from memory.proxy import gateway as gw_mod
    from memory.proxy.gateway import make_server

    class _Up(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            raw = b'{"upstream":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def boom(*a, **k):
        raise RuntimeError("distill exploded")

    monkeypatch.setattr(gw_mod, "process_request", boom)

    up = ThreadingHTTPServer(("127.0.0.1", 0), _Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        agent = tmp_path / ".agent"
        agent.mkdir()
        up_url = f"http://127.0.0.1:{up.server_address[1]}"
        (agent / "project_config.json").write_text(
            json.dumps({"proxy": {"mode": "required", "pxpipe_base": up_url}}),
            encoding="utf-8",
        )
        gw = make_server(
            "127.0.0.1",
            0,
            upstream=up_url,
            workdir=tmp_path,
            quiet=True,
        )
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        try:
            payload = json.dumps(
                {"model": "grok", "input": [{"role": "user", "content": "hi"}]}
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{gw.server_address[1]}/v1/responses",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with caplog.at_level(logging.WARNING, logger="memory"):
                with urllib.request.urlopen(req, timeout=3) as resp:
                    body = resp.read()
            assert b"upstream" in body
            assert "process_request failed path=" in caplog.text
            assert "/v1/responses" in caplog.text
            assert "distill exploded" in caplog.text
        finally:
            gw.shutdown()
    finally:
        up.shutdown()


def test_grok_shaped_secret_redacted_in_caplog(monkeypatch, caplog):
    from memory.dashboard.redact import RedactFilter
    from memory.logutil import configure_logging

    configure_logging()
    mem = logging.getLogger("memory")
    assert any(isinstance(f, RedactFilter) for f in mem.filters)
    assert all(
        not any(isinstance(f, RedactFilter) for f in h.filters) for h in mem.handlers
    )
    if caplog.handler not in mem.handlers:
        mem.addHandler(caplog.handler)
    secret = "GROK_live_abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    with caplog.at_level(logging.WARNING, logger="memory"):
        logging.getLogger("memory.supervisor").warning(
            "adapter failed GROK_API_KEY=%s", secret
        )
    assert secret not in caplog.text
    assert "****" in caplog.text


def test_maybe_cycle_on_done_logs_warning(tmp_path: Path, caplog, monkeypatch):
    import memory.experience_harvester as eh
    from memory.supervisor import Terminal, run_loop

    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    for name in ("orchestrator", "coder", "tester", "debugger", "reviewer"):
        (tmp_path / "prompts" / f"short_{name}_prompt.md").write_text(
            f"# {name}\n", encoding="utf-8"
        )
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "project_config.json").write_text(
        json.dumps(
            {
                "supervisor": {
                    "adapter": "mock",
                    "max_cycles": 1,
                    "max_role_retries": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    def boom(*a, **k):
        raise RuntimeError("harvest boom")

    monkeypatch.setattr(eh, "maybe_cycle_on_done", boom)
    with caplog.at_level(logging.WARNING, logger="memory"):
        result = run_loop(
            workdir=tmp_path, adapter_name="mock", max_cycles=1, create_pr=False
        )
    assert result["terminal"] in (Terminal.PR_READY, Terminal.PR_READY_LOCAL)
    assert "maybe_cycle_on_done failed" in caplog.text
