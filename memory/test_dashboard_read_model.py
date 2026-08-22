# -*- coding: utf-8 -*-
"""DashboardStore: явные пути, порванный JSON, snapshot не надмножество CLI."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from memory.dashboard import read_model
from memory.dashboard.read_model import (
    HISTORY_TAIL_MAX_BYTES,
    DashboardStore,
)


# Ключи memory.state.snapshot(), которых нет в проекции полосы Loop.
_CLI_ONLY = {
    "workspace_hint",
    "template_version",
    "working_bytes",
    "history_dir",
    "rule",
}


@pytest.fixture
def cwd_guard():
    prev = os.getcwd()
    try:
        yield Path(prev)
    finally:
        os.chdir(prev)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _loop_payload(**overrides):
    data = {
        "cycle_number": 12,
        "active_role": "Coder",
        "status": "IN_PROGRESS",
        "branch": "feat-x",
        "last_commit": "abc123",
        "git_sync": {"verified": True, "feature_pushed": True},
        "open_invest": ["T-12 parser"],
        "recent_deltas": [
            {"ts": "2026-08-21T12:00:00Z", "role": "Coder", "text": "parser ok"}
        ],
        "updated_at": "2026-08-21T12:00:01Z",
        "notes": "all good",
        "template_version": "3.7.0",
        "workspace_hint": "should-not-leak",
    }
    data.update(overrides)
    return data


def _handoff_payload(**overrides):
    data = {
        "role": "Coder",
        "handoff_to": "Tester",
        "current_phase": "implementation",
        "cycle_number": 12,
        "summary": "Implemented parser. Tests pending.",
        "status": "IN_PROGRESS",
        "confidence": 0.86,
        "git_sync_status": {"verified": True},
        "metrics": {"tests_total": 12, "tests_failed": 0},
    }
    data.update(overrides)
    return data


def test_snapshot_loop_strip_fields_and_handoff_keys(tmp_path: Path, cwd_guard):
    agent = tmp_path / ".agent"
    _write_json(agent / "LOOP_STATE.json", _loop_payload())
    _write_json(agent / "last_handoff.json", _handoff_payload())
    (agent / "STOP").write_text("1", encoding="utf-8")

    store = DashboardStore(tmp_path)
    snap = store.snapshot()
    st = snap["state"]

    assert st["cycle_number"] == 12
    assert st["active_role"] == "Coder"
    assert st["status"] == "IN_PROGRESS"
    assert st["branch"] == "feat-x"
    assert st["last_commit"] == "abc123"
    assert st["git_sync"]["verified"] is True
    assert st["notes"] == "all good"
    assert st["updated_at"] == "2026-08-21T12:00:01Z"
    assert snap["last_handoff_summary"] == "Implemented parser. Tests pending."
    assert snap["last_handoff_status"] == "IN_PROGRESS"
    assert snap["last_handoff_role"] == "Coder"
    assert snap["last_handoff_to"] == "Tester"
    assert snap["last_handoff_confidence"] == 0.86
    assert snap["last_handoff_metrics"]["tests_total"] == 12
    assert snap["stop"] is True
    assert "heartbeat" in snap
    assert Path.cwd() == cwd_guard


def test_snapshot_is_not_cli_key_superset(tmp_path: Path, cwd_guard):
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    snap = DashboardStore(tmp_path).snapshot()
    # Не проверяем snap.keys() >= state.snapshot().keys() — это другой контракт.
    assert _CLI_ONLY.isdisjoint(snap.keys())
    assert _CLI_ONLY.isdisjoint(snap["state"].keys())
    assert "notes" in snap["state"]
    assert "workspace_hint" not in snap["state"]
    assert "last_handoff_summary" not in snap["state"]


def test_open_invest_cap_20_deltas_last_5(tmp_path: Path, cwd_guard):
    invest = [f"T-{i}" for i in range(25)]
    deltas = [{"ts": str(i), "role": "Coder", "text": f"d{i}"} for i in range(8)]
    _write_json(
        tmp_path / ".agent" / "LOOP_STATE.json",
        _loop_payload(open_invest=invest, recent_deltas=deltas),
    )
    st = DashboardStore(tmp_path).snapshot()["state"]
    assert st["open_invest"] == invest[:20]
    assert st["recent_deltas"] == deltas[-5:]


def test_explicit_paths_ignore_cwd_agent(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    _write_json(
        cwd_wd / ".agent" / "LOOP_STATE.json",
        _loop_payload(status="BLOCKED", active_role="Tester"),
    )
    _write_json(
        real_wd / ".agent" / "LOOP_STATE.json",
        _loop_payload(status="IN_PROGRESS", active_role="Coder"),
    )
    monkeypatch.chdir(cwd_wd)
    snap = DashboardStore(real_wd).snapshot()
    assert snap["state"]["status"] == "IN_PROGRESS"
    assert snap["state"]["active_role"] == "Coder"
    assert Path.cwd() == cwd_wd


def test_store_does_not_chdir(tmp_path: Path, cwd_guard):
    prev = Path.cwd()
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    DashboardStore(tmp_path).snapshot()
    assert Path.cwd() == prev == cwd_guard


def test_missing_loop_state_status_missing(tmp_path: Path, cwd_guard):
    snap = DashboardStore(tmp_path).snapshot()
    assert snap["state"]["status"] == "missing"
    assert snap["last_handoff_summary"] is None
    assert snap["last_handoff_status"] is None
    assert snap["last_handoff_role"] is None
    assert snap["stop"] is False
    assert snap["stale"] is False


def test_torn_handoff_uses_last_good_and_stale(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    agent = tmp_path / ".agent"
    _write_json(agent / "LOOP_STATE.json", _loop_payload())
    _write_json(agent / "last_handoff.json", _handoff_payload(summary="good summary"))
    store = DashboardStore(tmp_path)
    first = store.snapshot()
    assert first["last_handoff_summary"] == "good summary"
    assert first["stale"] is False

    sleeps = []
    monkeypatch.setattr(read_model.time, "sleep", lambda s: sleeps.append(s))
    (agent / "last_handoff.json").write_text("{", encoding="utf-8")
    second = store.snapshot()
    assert second["last_handoff_summary"] == "good summary"
    assert second["stale"] is True
    assert second["state"]["status"] == "IN_PROGRESS"
    assert sleeps == []


def test_torn_loop_state_uses_last_good(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    p = tmp_path / ".agent" / "LOOP_STATE.json"
    _write_json(p, _loop_payload(status="IN_PROGRESS", notes="keep me"))
    store = DashboardStore(tmp_path)
    store.snapshot()
    p.write_text("", encoding="utf-8")
    snap = store.snapshot()
    assert snap["state"]["status"] == "IN_PROGRESS"
    assert snap["state"]["notes"] == "keep me"
    assert snap["stale"] is True


def test_torn_without_cache_does_not_raise(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    sleeps = []
    monkeypatch.setattr(read_model.time, "sleep", lambda s: sleeps.append(s))
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "last_handoff.json").write_text("{not-json", encoding="utf-8")
    store = DashboardStore(tmp_path)
    snap = store.snapshot()
    assert snap["last_handoff_summary"] is None
    assert snap["stale"] is True
    assert sleeps == [0, 0, 0]


def test_stop_absent_and_present(tmp_path: Path, cwd_guard):
    store = DashboardStore(tmp_path)
    assert store.stop_present() is False
    stop = tmp_path / ".agent" / "STOP"
    stop.parent.mkdir(parents=True)
    stop.write_text("1", encoding="utf-8")
    assert store.stop_present() is True
    assert store.snapshot()["stop"] is True


def test_heartbeat_missing_is_unknown(tmp_path: Path, cwd_guard):
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "unknown"
    assert hb["label"] == "liveness unknown"


def test_heartbeat_fresh_running(tmp_path: Path, cwd_guard):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 4242, "role": "Coder", "status": "IN_PROGRESS", "ts": ts},
    )
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "running"
    assert "4242" in hb["label"]
    assert "Coder" in hb["label"]


def test_torn_heartbeat_does_not_flip_strip_stale(
    tmp_path: Path, monkeypatch, cwd_guard
):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload())
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 4242, "role": "Coder", "status": "IN_PROGRESS", "ts": ts},
    )
    store = DashboardStore(tmp_path)
    first = store.snapshot()
    assert first["stale"] is False
    assert first["heartbeat"]["status"] == "running"

    (tmp_path / ".agent" / "supervisor.heartbeat").write_text("{", encoding="utf-8")
    snap = store.snapshot()
    assert snap["stale"] is False
    hb = store.heartbeat()
    assert hb["status"] in ("running", "unknown")
    if hb["status"] == "running":
        assert hb.get("pid") == 4242

    cold = DashboardStore(tmp_path).snapshot()
    assert cold["stale"] is False
    assert cold["heartbeat"]["status"] == "unknown"

    (tmp_path / ".agent" / "last_handoff.json").write_text("{", encoding="utf-8")
    torn_ho = store.snapshot()
    assert torn_ho["stale"] is True


def test_heartbeat_stale_file(tmp_path: Path, cwd_guard):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace(
        "+00:00", "Z"
    )
    _write_json(
        tmp_path / ".agent" / "supervisor.heartbeat",
        {"pid": 7, "role": "Tester", "status": "IN_PROGRESS", "ts": ts},
    )
    hb = DashboardStore(tmp_path).heartbeat()
    assert hb["status"] == "stale"
    assert hb["label"] == "not running / stale"


def _ym_pair():
    now = datetime.now(timezone.utc)
    cur = f"{now.year:04d}{now.month:02d}"
    if now.month == 1:
        prev = f"{now.year - 1:04d}12"
        older_y, older_m = now.year - 1, 11
    else:
        prev = f"{now.year:04d}{now.month - 1:02d}"
        if now.month == 2:
            older_y, older_m = now.year - 1, 12
        else:
            older_y, older_m = now.year, now.month - 2
    older = f"{older_y:04d}{older_m:02d}"
    return cur, prev, older


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_history_tail_last_20_current_month(tmp_path: Path, cwd_guard):
    cur, _, _ = _ym_pair()
    rows = [{"n": i, "ts": f"t{i}", "text": f"line-{i}"} for i in range(25)]
    _write_jsonl(tmp_path / ".agent" / "history" / f"loop_state-{cur}.jsonl", rows)
    tail = DashboardStore(tmp_path).history_tail()
    assert len(tail) == 20
    assert [r["n"] for r in tail] == list(range(5, 25))
    assert Path.cwd() == cwd_guard


def test_history_tail_fills_from_previous_month(tmp_path: Path, cwd_guard):
    cur, prev, older = _ym_pair()
    hist = tmp_path / ".agent" / "history"
    _write_jsonl(
        hist / f"loop_state-{cur}.jsonl",
        [{"n": i, "src": "cur", "text": f"c{i}"} for i in range(5)],
    )
    _write_jsonl(
        hist / f"loop_state-{prev}.jsonl",
        [{"n": i, "src": "prev", "text": f"p{i}"} for i in range(20)],
    )
    _write_jsonl(
        hist / f"loop_state-{older}.jsonl",
        [{"n": i, "src": "old", "text": "MUST-NOT-APPEAR"} for i in range(30)],
    )
    tail = DashboardStore(tmp_path).history_tail()
    assert len(tail) == 20
    assert [r["src"] for r in tail] == ["prev"] * 15 + ["cur"] * 5
    assert [r["n"] for r in tail] == list(range(5, 20)) + list(range(5))
    assert all(r.get("src") != "old" for r in tail)


def test_history_tail_64kib_from_eof_drops_head(tmp_path: Path, monkeypatch, cwd_guard):
    cur, _, _ = _ym_pair()
    path = tmp_path / ".agent" / "history" / f"loop_state-{cur}.jsonl"
    path.parent.mkdir(parents=True)
    early = json.dumps({"marker": "HEAD", "text": "early"}) + "\n"
    # одна огромная строка без \n, чтобы last-20 по всему файлу захватил HEAD
    padding = "x" * (HISTORY_TAIL_MAX_BYTES + 4096)
    late = "\n".join(
        json.dumps({"marker": "TAIL", "n": i, "text": f"late-{i}"}) for i in range(3)
    ) + "\n"
    path.write_text(early + padding + "\n" + late, encoding="utf-8")
    assert path.stat().st_size > HISTORY_TAIL_MAX_BYTES

    real_open = Path.open

    class _GuardedJsonl:
        """Обёртка: слоты read/seek у BufferedReader только для чтения."""

        def __init__(self, fh, src: Path):
            self._fh = fh
            self._src = src
            self._pos = fh.tell()

        def seek(self, offset, whence=0):
            r = self._fh.seek(offset, whence)
            self._pos = self._fh.tell()
            return r

        def read(self, n=-1):
            if n is None or n < 0 or n > HISTORY_TAIL_MAX_BYTES:
                raise AssertionError(f"jsonl must not slurp n={n}")
            if self._pos == 0 and self._src.stat().st_size > HISTORY_TAIL_MAX_BYTES:
                raise AssertionError("jsonl must seek from EOF, not read from start")
            data = self._fh.read(n)
            self._pos = self._fh.tell()
            return data

        def tell(self):
            return self._fh.tell()

        def close(self):
            return self._fh.close()

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    def guarded_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        if self.suffix != ".jsonl":
            return fh
        return _GuardedJsonl(fh, self)

    monkeypatch.setattr(Path, "open", guarded_open)
    tail = DashboardStore(tmp_path).history_tail()
    assert all(r.get("marker") != "HEAD" for r in tail)
    assert [r.get("n") for r in tail] == [0, 1, 2]
    assert all(r.get("marker") == "TAIL" for r in tail)


def test_history_tail_never_read_text_whole_jsonl(tmp_path: Path, monkeypatch, cwd_guard):
    cur, _, _ = _ym_pair()
    path = tmp_path / ".agent" / "history" / f"loop_state-{cur}.jsonl"
    _write_jsonl(path, [{"n": 1, "text": "ok"}])
    real = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise AssertionError("jsonl must not be read via read_text")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    real_rb = Path.read_bytes

    def guarded_bytes(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise AssertionError("jsonl must not be read via read_bytes")
        return real_rb(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_bytes)
    tail = DashboardStore(tmp_path).history_tail()
    assert len(tail) == 1
    assert tail[0]["n"] == 1


def test_history_tail_malformed_line_becomes_raw(tmp_path: Path, cwd_guard):
    cur, _, _ = _ym_pair()
    path = tmp_path / ".agent" / "history" / f"loop_state-{cur}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"n": 1, "text": "good"})
        + "\n{not-json\n"
        + json.dumps({"n": 2, "text": "also-good"})
        + "\n",
        encoding="utf-8",
    )
    tail = DashboardStore(tmp_path).history_tail()
    assert len(tail) == 3
    assert tail[0]["n"] == 1
    assert tail[1].get("raw") == "{not-json"
    assert tail[2]["n"] == 2
    assert Path.cwd() == cwd_guard


def test_history_tail_explicit_paths_and_no_mkdir(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    cur, _, _ = _ym_pair()
    _write_jsonl(
        cwd_wd / ".agent" / "history" / f"loop_state-{cur}.jsonl",
        [{"n": 1, "text": "cwd-hist"}],
    )
    _write_jsonl(
        real_wd / ".agent" / "history" / f"loop_state-{cur}.jsonl",
        [{"n": 2, "text": "real-hist"}],
    )
    monkeypatch.chdir(cwd_wd)
    store = DashboardStore(real_wd)
    tail = store.history_tail()
    assert [r["text"] for r in tail] == ["real-hist"]
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = DashboardStore(empty)
    assert missing.history_tail() == []
    assert not (empty / ".agent").exists()
    assert Path.cwd() == cwd_wd


def test_history_tail_does_not_write(tmp_path: Path, cwd_guard):
    cur, _, _ = _ym_pair()
    path = tmp_path / ".agent" / "history" / f"loop_state-{cur}.jsonl"
    _write_jsonl(path, [{"n": 1, "text": "keep"}])
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    DashboardStore(tmp_path).history_tail()
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
    assert not (tmp_path / ".agent" / "STOP").exists()


def test_ledger_cycles_last_50_and_summary(tmp_path: Path, cwd_guard):
    cycles = []
    for i in range(55):
        cycles.append(
            {
                "cycle": i,
                "timestamp": f"2026-08-21T12:00:{i:02d}Z",
                "outcome": "DONE",
                "elapsed_minutes": 2.0,
                "tool_calls": 1,
                "tokens_est": 10,
                "confidence": 0.5,
                "tests_total": 4,
                "tests_failed": 0,
                "violations": 0,
                "meta_applied": 2,
            }
        )
    _write_json(
        tmp_path / ".agent" / "PERFORMANCE_LEDGER.json",
        {"cycles": cycles, "summary": {"total_cycles": 55}},
    )
    store = DashboardStore(tmp_path)
    got = store.ledger_cycles()
    assert len(got) == 50
    assert got[0]["cycle"] == 5
    assert got[-1]["cycle"] == 54
    summ = store.ledger_summary()
    assert summ["count"] == 50
    assert summ["avg_elapsed_min"] == 2.0
    assert summ["avg_confidence"] == 0.5
    assert summ["total_meta_applied"] == 100
    assert Path.cwd() == cwd_guard


def test_ledger_summary_empty_and_missing_file(tmp_path: Path, cwd_guard):
    store = DashboardStore(tmp_path)
    assert store.ledger_cycles() == []
    summ = store.ledger_summary()
    assert summ == {
        "count": 0,
        "avg_elapsed_min": 0.0,
        "avg_confidence": 0.0,
        "total_meta_applied": 0,
    }
    assert not (tmp_path / ".agent").exists()


def test_ledger_explicit_paths_ignore_cwd(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    _write_json(
        cwd_wd / ".agent" / "PERFORMANCE_LEDGER.json",
        {
            "cycles": [
                {"cycle": 1, "elapsed_minutes": 9, "confidence": 0.1, "meta_applied": 0}
            ]
        },
    )
    _write_json(
        real_wd / ".agent" / "PERFORMANCE_LEDGER.json",
        {
            "cycles": [
                {"cycle": 7, "elapsed_minutes": 3, "confidence": 0.8, "meta_applied": 4}
            ]
        },
    )
    monkeypatch.chdir(cwd_wd)
    store = DashboardStore(real_wd)
    got = store.ledger_cycles()
    assert len(got) == 1
    assert got[0]["cycle"] == 7
    summ = store.ledger_summary()
    assert summ["avg_elapsed_min"] == 3.0
    assert summ["avg_confidence"] == 0.8
    assert summ["total_meta_applied"] == 4
    assert Path.cwd() == cwd_wd


def test_ledger_torn_uses_last_good(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    p = tmp_path / ".agent" / "PERFORMANCE_LEDGER.json"
    _write_json(
        p,
        {
            "cycles": [
                {"cycle": 3, "elapsed_minutes": 1.5, "confidence": 0.9, "meta_applied": 1}
            ]
        },
    )
    store = DashboardStore(tmp_path)
    assert store.ledger_cycles()[0]["cycle"] == 3
    p.write_text("{", encoding="utf-8")
    got = store.ledger_cycles()
    assert got[0]["cycle"] == 3
    summ = store.ledger_summary()
    assert summ["count"] == 1


def test_ledger_does_not_write(tmp_path: Path, cwd_guard):
    p = tmp_path / ".agent" / "PERFORMANCE_LEDGER.json"
    _write_json(p, {"cycles": [{"cycle": 1, "elapsed_minutes": 1, "confidence": 1}]})
    before = p.read_bytes()
    mtime = p.stat().st_mtime_ns
    store = DashboardStore(tmp_path)
    store.ledger_cycles()
    store.ledger_summary()
    assert p.read_bytes() == before
    assert p.stat().st_mtime_ns == mtime
    assert not (tmp_path / ".agent" / "STOP").exists()


def _seed_playbooks(tmp_path: Path, extra_bullet: str = "Always start with git.") -> None:
    _write_json(
        tmp_path / ".agent" / "PLAYBOOKS.json",
        {
            "playbooks": {
                "global-dev": {
                    "scope": "global",
                    "name": "Global Dev",
                    "bullets": [
                        {
                            "id": "b-0001",
                            "content": extra_bullet,
                            "effectiveness": 0.95,
                        },
                        {
                            "id": "b-0002",
                            "content": "see {{title}}",
                            "effectiveness": 0.5,
                        },
                    ],
                    "last_curated": "2026-08-21T12:00:00Z",
                }
            }
        },
    )


def test_playbooks_shape_and_no_list_call(tmp_path: Path, monkeypatch, cwd_guard):
    import memory.playbooks as pb

    monkeypatch.setattr(
        pb,
        "list_playbooks",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("list_playbooks")),
    )
    monkeypatch.setattr(
        pb,
        "export_hub_index",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("export")),
    )
    _seed_playbooks(tmp_path)
    items = DashboardStore(tmp_path).playbooks()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "global-dev"
    assert item["scope"] == "global"
    assert item["name"] == "Global Dev"
    assert item["bullet_count"] == 2
    assert item["avg_effectiveness"] == 0.725
    assert item["last_curated"] == "2026-08-21T12:00:00Z"
    assert item["install_path"] == ".agent/PLAYBOOKS/global-dev.md"
    assert Path.cwd() == cwd_guard


def test_playbooks_explicit_paths_and_no_mkdir(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    _seed_playbooks(cwd_wd)
    _write_json(
        real_wd / ".agent" / "PLAYBOOKS.json",
        {
            "playbooks": {
                "tool-git": {
                    "scope": "tool:git",
                    "name": "Git",
                    "bullets": [{"id": "b-1", "effectiveness": 1.0}],
                    "last_curated": "t",
                }
            }
        },
    )
    monkeypatch.chdir(cwd_wd)
    items = DashboardStore(real_wd).playbooks()
    assert [i["id"] for i in items] == ["tool-git"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert DashboardStore(empty).playbooks() == []
    assert DashboardStore(empty).hub_index_header() is None
    assert not (empty / ".agent").exists()
    assert Path.cwd() == cwd_wd


def test_hub_index_header_reads_file_does_not_export(tmp_path: Path, cwd_guard):
    _seed_playbooks(tmp_path)
    store = DashboardStore(tmp_path)
    assert store.hub_index_header() is None
    hub = tmp_path / ".agent" / "HUB_INDEX.json"
    _write_json(
        hub,
        {
            "version": "1.0",
            "generated_at": "2026-08-21T12:00:00Z",
            "item_count": 4,
        },
    )
    before = hub.read_bytes()
    mtime = hub.stat().st_mtime_ns
    header = store.hub_index_header()
    assert header == {
        "version": "1.0",
        "generated_at": "2026-08-21T12:00:00Z",
        "item_count": 4,
    }
    assert hub.read_bytes() == before
    assert hub.stat().st_mtime_ns == mtime


def test_playbook_detail_allowlist(tmp_path: Path, cwd_guard):
    _seed_playbooks(tmp_path)
    store = DashboardStore(tmp_path)
    ok = store.playbook_detail("global-dev")
    assert ok is not None
    assert ok["id"] == "global-dev"
    assert len(ok["bullets"]) == 2
    assert ok["bullets"][1]["content"] == "see {{title}}"
    assert store.playbook_detail("missing-id") is None
    assert store.playbook_detail("../etc/passwd") is None
    assert store.playbook_detail("foo/bar") is None
    assert store.playbook_detail("") is None
    assert store.playbook_detail("global-dev.md") is None


def test_playbooks_torn_uses_last_good(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _seed_playbooks(tmp_path)
    store = DashboardStore(tmp_path)
    assert store.playbooks()[0]["id"] == "global-dev"
    (tmp_path / ".agent" / "PLAYBOOKS.json").write_text("{", encoding="utf-8")
    assert store.playbooks()[0]["id"] == "global-dev"
    assert store.playbook_detail("global-dev")["id"] == "global-dev"


def test_audit_entries_last_50_signature_cut(tmp_path: Path, cwd_guard):
    entries = []
    for i in range(55):
        entries.append(
            {
                "id": f"A-{i:04d}",
                "ts": f"2026-08-21T12:00:{i:02d}Z",
                "action": "git.sync",
                "role": "Coder",
                "cycle": i,
                "approval_required": False,
                "approved": True,
                "signature": "abcdef0123456789ffff",
            }
        )
    _write_json(tmp_path / ".agent" / "AUDIT_LOG.json", {"entries": entries})
    got = DashboardStore(tmp_path).audit_entries()
    assert len(got) == 50
    assert got[0]["id"] == "A-0005"
    assert got[-1]["id"] == "A-0054"
    assert got[0]["signature"] == "abcdef012345"
    assert len(got[0]["signature"]) == 12
    assert Path.cwd() == cwd_guard


def test_audit_explicit_paths_empty_no_mkdir(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    _write_json(
        cwd_wd / ".agent" / "AUDIT_LOG.json",
        {"entries": [{"id": "A-cwd", "signature": "x" * 20}]},
    )
    _write_json(
        real_wd / ".agent" / "AUDIT_LOG.json",
        {"entries": [{"id": "A-real", "signature": "y" * 20}]},
    )
    monkeypatch.chdir(cwd_wd)
    got = DashboardStore(real_wd).audit_entries()
    assert [e["id"] for e in got] == ["A-real"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert DashboardStore(empty).audit_entries() == []
    assert not (empty / ".agent").exists()
    assert Path.cwd() == cwd_wd


def test_audit_torn_uses_last_good(tmp_path: Path, monkeypatch, cwd_guard):
    monkeypatch.setattr(read_model, "TORN_RETRY_S", 0)
    _write_json(
        tmp_path / ".agent" / "AUDIT_LOG.json",
        {"entries": [{"id": "A-0001", "action": "stop", "signature": "sigsigsigsigxx"}]},
    )
    store = DashboardStore(tmp_path)
    assert store.audit_entries()[0]["id"] == "A-0001"
    (tmp_path / ".agent" / "AUDIT_LOG.json").write_text("{", encoding="utf-8")
    got = store.audit_entries()
    assert got[0]["id"] == "A-0001"
    assert got[0]["signature"] == "sigsigsigsig"


def test_plan_and_todo_read_or_missing(tmp_path: Path, cwd_guard):
    store = DashboardStore(tmp_path)
    assert store.plan_text() is None
    assert store.todo_text() is None
    assert not (tmp_path / ".agent").exists()
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "PLAN.md").write_text("Do {{title}}\n", encoding="utf-8")
    (agent / "TODO.md").write_text("- task\n", encoding="utf-8")
    plan = store.plan_text()
    todo = store.todo_text()
    assert plan is not None and "Do {{title}}" in plan["text"]
    assert plan["truncated"] is False
    assert todo is not None and todo["text"] == "- task\n"
    assert Path.cwd() == cwd_guard


def test_plan_explicit_paths(tmp_path: Path, monkeypatch, cwd_guard):
    cwd_wd = tmp_path / "cwd"
    real_wd = tmp_path / "real"
    (cwd_wd / ".agent").mkdir(parents=True)
    (real_wd / ".agent").mkdir(parents=True)
    (cwd_wd / ".agent" / "PLAN.md").write_text("cwd-plan", encoding="utf-8")
    (real_wd / ".agent" / "PLAN.md").write_text("real-plan", encoding="utf-8")
    monkeypatch.chdir(cwd_wd)
    got = DashboardStore(real_wd).plan_text()
    assert got is not None and got["text"] == "real-plan"
    assert Path.cwd() == cwd_wd


def test_plan_todo_byte_cap(tmp_path: Path, cwd_guard):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "PLAN.md").write_text("P" * (read_model.PLAN_MAX_BYTES + 50), encoding="utf-8")
    (agent / "TODO.md").write_text("T" * 10, encoding="utf-8")
    store = DashboardStore(tmp_path)
    plan = store.plan_text()
    todo = store.todo_text()
    assert plan is not None
    assert plan["truncated"] is True
    assert plan["text"] == "P" * read_model.PLAN_MAX_BYTES
    assert todo is not None
    assert todo["truncated"] is False
    assert todo["text"] == "T" * 10


def test_memory_excerpt_missing_does_not_mkdir(tmp_path: Path, monkeypatch, cwd_guard):
    import memory.workspace as ws

    root = tmp_path / "memroot"
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    monkeypatch.setattr(
        ws,
        "memory_paths",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("memory_paths")),
    )
    info = DashboardStore(tmp_path).memory_excerpt()
    assert info["present"] is False
    assert info["excerpt"] == ""
    assert info["workspace_id"]
    assert not root.exists()
    assert Path.cwd() == cwd_guard


def test_memory_excerpt_caps_and_skips_siblings(tmp_path: Path, monkeypatch, cwd_guard):
    from memory.workspace import get_workspace_id

    root = tmp_path / "memroot"
    root.mkdir()
    wid = get_workspace_id(cwd=tmp_path)
    lines = [f"line-{i} see {{{{title}}}}" for i in range(100)]
    (root / f"{wid}.md").write_text("\n".join(lines), encoding="utf-8")
    (root / "other-project.md").write_text("SECRET-OTHER", encoding="utf-8")
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    info = DashboardStore(tmp_path).memory_excerpt()
    assert info["present"] is True
    assert info["truncated"] is True
    got_lines = info["excerpt"].splitlines()
    assert len(got_lines) == 80
    assert got_lines[0].startswith("line-0")
    assert got_lines[-1].startswith("line-79")
    assert "see {{title}}" in info["excerpt"]
    assert "SECRET-OTHER" not in info["excerpt"]
    assert "line-80" not in info["excerpt"]


def test_memory_excerpt_8kib_cap(tmp_path: Path, monkeypatch, cwd_guard):
    from memory.workspace import get_workspace_id

    root = tmp_path / "memroot"
    root.mkdir()
    wid = get_workspace_id(cwd=tmp_path)
    payload = "H" * 9000
    (root / f"{wid}.md").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    info = DashboardStore(tmp_path).memory_excerpt()
    assert info["present"] is True
    assert info["truncated"] is True
    assert len(info["excerpt"].encode("utf-8")) <= read_model.MEMORY_EXCERPT_BYTES
    assert info["excerpt"] == "H" * read_model.MEMORY_EXCERPT_BYTES


def test_memory_does_not_list_parent(tmp_path: Path, monkeypatch, cwd_guard):
    from memory.workspace import get_workspace_id

    root = tmp_path / "memroot"
    root.mkdir()
    wid = get_workspace_id(cwd=tmp_path)
    (root / f"{wid}.md").write_text("mine", encoding="utf-8")
    monkeypatch.setattr(read_model, "_memory_root", lambda: root)
    real_iterdir = Path.iterdir

    def guarded(self):
        if Path(self).resolve() == root.resolve():
            raise AssertionError("must not list memory parent")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", guarded)
    info = DashboardStore(tmp_path).memory_excerpt()
    assert info["excerpt"] == "mine"
    assert Path.cwd() == cwd_guard


def test_workspace_id_cached(tmp_path: Path, monkeypatch, cwd_guard):
    calls = []

    def fake(cwd=None):
        calls.append(cwd)
        return "wid-cached"

    monkeypatch.setattr(read_model, "get_workspace_id", fake)
    monkeypatch.setattr(read_model, "_memory_root", lambda: tmp_path / "memroot")
    store = DashboardStore(tmp_path)
    store.memory_excerpt()
    store.memory_excerpt()
    assert store.workspace_id() == "wid-cached"
    assert len(calls) == 1
    assert Path.cwd() == cwd_guard


def test_write_and_clear_stop_explicit_path(tmp_path: Path, cwd_guard):
    store = DashboardStore(tmp_path)
    path = store.write_stop()
    assert path == tmp_path / ".agent" / "STOP"
    assert path.read_text(encoding="utf-8") == "1"
    assert store.stop_present() is True
    assert store.clear_stop() is True
    assert store.stop_present() is False
    assert store.clear_stop() is False
    assert Path.cwd() == cwd_guard


def test_open_questions_and_cadence(tmp_path: Path, cwd_guard):
    _write_json(
        tmp_path / ".agent" / "QUESTIONS_POOL.json",
        {
            "questions": [
                {"id": "Q-001", "question": "open one", "status": "open"},
                {"id": "Q-002", "question": "done", "status": "resolved"},
            ],
            "last_escalated_cycle": 0,
        },
    )
    _write_json(tmp_path / ".agent" / "LOOP_STATE.json", _loop_payload(cycle_number=12))
    store = DashboardStore(tmp_path)
    open_qs = store.open_questions()
    assert [q["id"] for q in open_qs] == ["Q-001"]
    cad = store.questions_cadence()
    assert cad["open_count"] == 1
    assert "frequency" in cad
    assert "escalate" in cad
    assert cad["reason"] == "every_N_cycles"
    assert "нет" not in cad["reason"]
    assert Path.cwd() == cwd_guard
