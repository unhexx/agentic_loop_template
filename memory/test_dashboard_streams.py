# -*- coding: utf-8 -*-
"""Страница Streams, allowlist пульса worktree и fan-out STOP с дашборда."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Sequence

import pytest

pytest.importorskip("fastapi")

from memory.dashboard.read_model import DashboardStore
from memory.dashboard.watcher import WATCHED_FILES, Watcher

_FORBIDDEN_ROOT_IO = frozenset(
    {
        "/supervisor.heartbeat",
        "/.agent/supervisor.heartbeat",
        "/etc",
        "/etc/passwd",
    }
)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _heartbeat(agent: Path, **fields) -> None:
    payload = {"pid": 4242, "role": "Coder", "status": "IN_PROGRESS", "ts": _fresh_ts()}
    payload.update(fields)
    _write_json(agent / "supervisor.heartbeat", payload)


def _csrf_header(client) -> dict:
    r = client.get("/")
    assert r.status_code == 200
    token = client.cookies.get("agentix_csrf")
    assert token
    return {"X-CSRF-Token": token}


def _as_path_str(arg: object) -> str:
    try:
        raw = os.fspath(arg)  # type: ignore[arg-type]
    except TypeError:
        return str(arg)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "surrogateescape")
    return str(raw)


def _is_forbidden_root_io(s: str, extra: Sequence[str] = ()) -> bool:
    n = os.path.normpath(s)
    if n in _FORBIDDEN_ROOT_IO or n.startswith("/etc/"):
        return True
    extras = {os.path.normpath(p) for p in extra}
    return n in extras


def _install_fs_spy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra_forbidden: Sequence[str] = (),
) -> List[str]:
    """Ловим реальные вызовы pathlib 3.14: os.stat / isfile / isdir / realpath."""
    seen: List[str] = []

    def wrap(orig: Callable, label: str):
        def inner(path, *args, **kwargs):
            s = _as_path_str(path)
            seen.append(s)
            if _is_forbidden_root_io(s, extra_forbidden):
                raise AssertionError(f"{label} outside allowlist: {s}")
            return orig(path, *args, **kwargs)

        return inner

    monkeypatch.setattr(os, "stat", wrap(os.stat, "os.stat"))
    monkeypatch.setattr(os, "lstat", wrap(os.lstat, "os.lstat"))
    monkeypatch.setattr(os.path, "isfile", wrap(os.path.isfile, "os.path.isfile"))
    monkeypatch.setattr(os.path, "isdir", wrap(os.path.isdir, "os.path.isdir"))
    monkeypatch.setattr(os.path, "realpath", wrap(os.path.realpath, "os.path.realpath"))
    return seen


def test_watcher_includes_streams_state():
    assert "streams_state.json" in WATCHED_FILES


def test_watcher_streams_state_event(tmp_path: Path):
    import asyncio

    agent = tmp_path / ".agent"
    agent.mkdir()
    events: list = []

    class _Sink:
        async def broadcast(self, msg):
            events.append(msg)

    w = Watcher(tmp_path, _Sink(), poll_s=0, debounce_s=0)  # type: ignore[arg-type]
    w.prime()
    _write_json(
        agent / "streams_state.json",
        {"streams": {"harness": {"status": "RUNNING"}}, "terminal": "IN_PROGRESS"},
    )
    asyncio.run(w.tick())
    types = [e["type"] for e in events]
    assert "streams:changed" in types
    ev = next(e for e in events if e["type"] == "streams:changed")
    assert ev["path"] == "streams_state.json"


def test_dashboard_streams_page(dashboard_client, tmp_path: Path):
    wt = tmp_path / "wt-harness"
    (wt / ".agent").mkdir(parents=True)
    _heartbeat(wt / ".agent")
    _write_json(
        tmp_path / ".agent" / "streams_state.json",
        {
            "streams": {
                "harness": {
                    "status": "RUNNING",
                    "worktree": str(wt),
                    "branch": "feature/c1-harness",
                }
            },
            "terminal": "IN_PROGRESS",
        },
    )
    r = dashboard_client.get("/streams")
    assert r.status_code == 200
    assert "harness" in r.text
    assert "RUNNING" in r.text
    assert "feature/c1-harness" in r.text
    assert 'href="/streams"' in r.text
    assert 'hx-get="/partials/streams"' in r.text
    partial = dashboard_client.get("/partials/streams")
    assert partial.status_code == 200
    assert "harness" in partial.text
    assert 'data-stream="harness"' in partial.text


def test_dashboard_nav_streams_href(dashboard_client):
    home = dashboard_client.get("/")
    streams = dashboard_client.get("/streams")
    assert home.status_code == 200
    assert streams.status_code == 200
    assert 'href="/streams"' in home.text
    assert 'href="/streams"' in streams.text
    assert 'nav-active">Loop</a>' in home.text
    assert 'nav-active">Streams</a>' in streams.text
    assert 'nav-active">Streams</a>' not in home.text
    assert 'nav-active">Loop</a>' not in streams.text


def test_dashboard_heartbeat_rejects_root(tmp_path: Path, monkeypatch, caplog):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "poison": {"status": "RUNNING", "worktree": "/"},
            }
        },
    )
    store = DashboardStore(hub)
    seen = _install_fs_spy(monkeypatch)
    orig_read = Path.read_text

    def spy_read(self, *args, **kwargs):
        s = str(self)
        if _is_forbidden_root_io(s):
            raise AssertionError(f"read outside tmp: {s}")
        return orig_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read)
    with caplog.at_level(logging.WARNING, logger="memory.dashboard.read_model"):
        rows = store.stream_heartbeats()
    assert len(rows) == 1
    assert rows[0]["name"] == "poison"
    assert rows[0]["heartbeat"]["status"] == "unknown"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "ожидали WARNING на worktree вне allowlist"
    assert any("outside allowlist" in w for w in warnings)
    hub_state = str((hub / ".agent" / "streams_state.json").resolve())
    assert seen, "ожидали реальный FS-IO к хабу"
    assert any(
        os.path.normpath(p) == hub_state or p.endswith("streams_state.json")
        for p in seen
    ), f"ожидали IO к hub streams_state, seen={seen!r}"
    assert not any(
        os.path.normpath(p).endswith("/supervisor.heartbeat")
        and "/hub/" not in os.path.normpath(p)
        for p in seen
    )
    assert not any(
        os.path.normpath(p) == "/etc" or os.path.normpath(p).startswith("/etc/")
        for p in seen
    )


def test_dashboard_heartbeat_requires_agent_dir(tmp_path: Path, monkeypatch, caplog):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    wt = tmp_path / "agentic-loop-worktrees" / "wt-no-agent"
    wt.mkdir(parents=True)
    decoy = wt / "supervisor.heartbeat"
    _write_json(decoy, {"pid": 7, "role": "Coder", "ts": _fresh_ts()})
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "ghost": {"status": "RUNNING", "worktree": str(wt)},
            }
        },
    )
    extra_forbidden = (
        str(decoy.resolve()),
        str((wt / ".agent" / "supervisor.heartbeat").resolve()),
    )
    seen = _install_fs_spy(monkeypatch, extra_forbidden=extra_forbidden)
    store = DashboardStore(hub)
    with caplog.at_level(logging.WARNING, logger="memory.dashboard.read_model"):
        rows = store.stream_heartbeats()
    assert len(rows) == 1
    assert rows[0]["name"] == "ghost"
    assert rows[0]["heartbeat"]["status"] == "unknown"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "ожидали WARNING на отсутствие .agent"
    assert any("no .agent dir" in w for w in warnings)
    assert seen, "ожидали реальный FS-IO к хабу / worktree"
    assert not any(
        os.path.normpath(p).endswith("supervisor.heartbeat") for p in seen
    ), f"heartbeat не должны трогать, seen={seen!r}"


def test_dashboard_heartbeat_null_wt_base_uses_default(tmp_path: Path, monkeypatch, caplog):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    _write_json(
        hub / ".agent" / "project_config.json",
        {
            "supervisor": {
                "parallel": {
                    "wt_base": None,
                    "base": "main",
                    "concurrent": False,
                }
            }
        },
    )
    wt = tmp_path / "agentic-loop-worktrees" / "wt-harness"
    (wt / ".agent").mkdir(parents=True)
    _heartbeat(wt / ".agent", pid=99, role="Tester")
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "harness": {
                    "status": "RUNNING",
                    "worktree": str(wt),
                    "branch": "feature/c1-harness",
                },
                "poison": {"status": "RUNNING", "worktree": "/"},
            }
        },
    )
    src = Path(__file__).resolve().parents[0] / "dashboard" / "read_model.py"
    text = src.read_text(encoding="utf-8")
    assert 'import_module("memory.supervisor")' in text
    assert "load_config" in text
    assert "memory.dashboard.config" not in text

    import memory.dashboard.config as dash_cfg

    def boom(*_a, **_k):
        raise AssertionError("dashboard.config.load_config used for allowlist")

    monkeypatch.setattr(dash_cfg, "load_config", boom)

    load_calls: list[Path] = []
    import memory.supervisor as sup_mod

    real_load = sup_mod.load_config

    def spy_load(workdir):
        load_calls.append(Path(workdir).resolve())
        return real_load(workdir)

    monkeypatch.setattr(sup_mod, "load_config", spy_load)

    store = DashboardStore(hub)
    with caplog.at_level(logging.WARNING, logger="memory.dashboard.read_model"):
        rows = store.stream_heartbeats()
    by_name = {r["name"]: r for r in rows}
    assert "harness" in by_name
    assert by_name["harness"]["heartbeat"]["status"] == "running"
    assert "99" in str(by_name["harness"]["heartbeat"].get("label") or "")
    assert by_name["poison"]["heartbeat"]["status"] == "unknown"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "ожидали WARNING на worktree=/"
    assert any("outside allowlist" in w for w in warnings)
    assert load_calls, "ожидали вызов memory.supervisor.load_config"
    assert all(p == hub.resolve() for p in load_calls)


def test_dashboard_stop_fanout(dashboard_client, tmp_path: Path):
    wt = tmp_path / "wt-harness"
    (wt / ".agent").mkdir(parents=True)
    _write_json(
        tmp_path / ".agent" / "streams_state.json",
        {
            "streams": {
                "harness": {"status": "RUNNING", "worktree": str(wt)},
            }
        },
    )
    headers = _csrf_header(dashboard_client)
    r = dashboard_client.post("/actions/stop", headers=headers)
    assert r.status_code == 204
    assert (tmp_path / ".agent" / "STOP").read_text(encoding="utf-8") == "1"
    assert (wt / ".agent" / "STOP").is_file()
    assert (wt / ".agent" / "STOP").read_text(encoding="utf-8") == "1"
    r2 = dashboard_client.post("/actions/clear-stop", headers=headers)
    assert r2.status_code == 204
    assert not (tmp_path / ".agent" / "STOP").exists()
    assert not (wt / ".agent" / "STOP").exists()


def test_write_stop_hub_written_if_wt_base_load_fails(
    tmp_path: Path, monkeypatch, caplog
):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    import memory.supervisor as sup_mod

    def boom(_workdir):
        raise RuntimeError("runner boom")

    monkeypatch.setattr(sup_mod, "load_config", boom)
    store = DashboardStore(hub)
    with caplog.at_level(logging.WARNING, logger="memory.dashboard.read_model"):
        path = store.write_stop()
    assert path == hub / ".agent" / "STOP"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "1"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "ожидали WARNING на сбой загрузки wt_base"
    assert any("wt_base config load failed" in w for w in warnings)


def test_write_stop_passes_extra_roots_for_custom_wt_base(tmp_path: Path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    custom = tmp_path / "custom-wts"
    wt = custom / "stream-a"
    (wt / ".agent").mkdir(parents=True)
    _write_json(
        hub / ".agent" / "project_config.json",
        {"supervisor": {"parallel": {"wt_base": str(custom)}}},
    )
    _write_json(
        hub / ".agent" / "streams_state.json",
        {"streams": {"harness": {"status": "RUNNING", "worktree": str(wt)}}},
    )
    captured: dict = {}
    import memory.dashboard.read_model as rm

    real = rm.fanout_stop

    def spy(hub_path, extra_roots=()):
        captured["extra"] = [Path(p).resolve() for p in extra_roots]
        return real(hub_path, extra_roots=extra_roots)

    monkeypatch.setattr(rm, "fanout_stop", spy)
    store = DashboardStore(hub)
    path = store.write_stop()
    assert path == hub / ".agent" / "STOP"
    assert path.read_text(encoding="utf-8") == "1"
    assert (wt / ".agent" / "STOP").read_text(encoding="utf-8") == "1"
    assert captured.get("extra"), "ожидали extra_roots с custom wt_base"
    assert custom.resolve() in captured["extra"]
    assert store.clear_stop() is True
    assert not (wt / ".agent" / "STOP").exists()


def test_streams_page_empty_is_200(dashboard_client):
    r = dashboard_client.get("/streams")
    assert r.status_code == 200
    assert "No streams." in r.text
    assert 'href="/streams"' in r.text
