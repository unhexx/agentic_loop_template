# -*- coding: utf-8 -*-
"""Тесты performance_ledger: явный agent_dir, лок на родителе JSON, модульные дефолты."""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List

import pytest

from memory.agent_lock import lock_path
from memory import performance_ledger as pl


def _agent(tmp_path: Path) -> Path:
    agent = tmp_path / ".agent"
    agent.mkdir()
    return agent


def _hub_ledger_locks() -> List[Path]:
    return [
        Path.cwd() / ".agent" / "ledger.lock",
        Path(__file__).resolve().parent.parent / ".agent" / "ledger.lock",
        Path(__file__).resolve().parent / ".agent" / "ledger.lock",
    ]


def test_append_and_report(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    rec = pl.append_cycle(
        agent_dir=agent,
        cycle=42,
        outcome="DONE",
        elapsed_minutes=5.5,
        tool_calls=10,
        confidence=0.95,
        meta_applied=2,
        notes="test run",
    )
    assert rec["cycle"] == 42
    assert rec["confidence"] == 0.95
    written = json.loads((agent / "PERFORMANCE_LEDGER.json").read_text(encoding="utf-8"))
    assert written["cycles"][-1]["cycle"] == 42
    assert written["cycles"][-1]["notes"] == "test run"
    assert (agent / "PERFORMANCE_LEDGER.md").is_file()
    assert "Cycle 42" in (agent / "PERFORMANCE_LEDGER.md").read_text(encoding="utf-8")

    recents = pl.get_recent(5, agent_dir=agent)
    assert len(recents) >= 1
    assert recents[0]["cycle"] == 42

    rep = pl.generate_report(5, agent_dir=agent)
    assert isinstance(rep, dict)
    assert rep["recent_cycles"] >= 1
    assert "avg_elapsed_min" in rep
    assert rep["avg_elapsed_min"] == 5.5

    for i in range(5):
        pl.append_cycle(agent_dir=agent, cycle=50 + i, elapsed_minutes=float(i))
    recents = pl.get_recent(10, agent_dir=agent)
    assert len(recents) == 6
    assert {c["cycle"] for c in recents} == {42, 50, 51, 52, 53, 54}


def test_ledger_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp1 = tmp_path / "one"
    tmp2 = tmp_path / "two"
    agent = tmp1 / ".agent"
    agent.mkdir(parents=True)
    tmp2.mkdir()
    monkeypatch.chdir(tmp2)

    rec = pl.append_cycle(
        agent_dir=agent,
        cycle=42,
        outcome="DONE",
        elapsed_minutes=5.5,
        notes="isolated",
    )
    assert rec["cycle"] == 42
    ledger = agent / "PERFORMANCE_LEDGER.json"
    assert ledger.is_file()
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["cycles"][-1]["notes"] == "isolated"
    assert not (tmp2 / ".agent" / "PERFORMANCE_LEDGER.json").exists()

    # без kwargs — модульные глобали (cwd), как meta_harvester.update_performance_ledger
    rec2 = pl.append_cycle()
    assert rec2["outcome"] == "DONE"
    cwd_json = tmp2 / ".agent" / "PERFORMANCE_LEDGER.json"
    assert cwd_json.is_file()
    cwd_data = json.loads(cwd_json.read_text(encoding="utf-8"))
    assert cwd_data["cycles"][-1]["outcome"] == "DONE"
    assert cwd_data["cycles"][-1]["cycle"] is None
    isolated = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(isolated["cycles"]) == 1
    assert isolated["cycles"][0]["cycle"] == 42


def test_append_cycle_none_uses_module_globals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    js = tmp_path / "PERFORMANCE_LEDGER.json"
    md = tmp_path / "PERFORMANCE_LEDGER.md"
    monkeypatch.setattr(pl, "LEDGER_JSON", js)
    monkeypatch.setattr(pl, "LEDGER_MD", md)
    rec = pl.append_cycle(cycle=7, notes="через глобали модуля")
    assert rec["cycle"] == 7
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["cycles"][0]["notes"] == "через глобали модуля"
    assert md.is_file()
    assert "Cycle 7" in md.read_text(encoding="utf-8")
    assert not (tmp_path / "ledger.lock").exists()


def test_ledger_write_releases_lock(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    pl.append_cycle(agent_dir=agent, cycle=1, elapsed_minutes=1.0)
    lp = lock_path(agent, "ledger")
    assert (agent / "PERFORMANCE_LEDGER.json").is_file()
    body = json.loads((agent / "PERFORMANCE_LEDGER.json").read_text(encoding="utf-8"))
    assert body["cycles"][0]["cycle"] == 1
    assert not lp.exists()
    assert not (agent / "PERFORMANCE_LEDGER.json.tmp").exists()


def test_ledger_lock_held_during_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(tmp_path)
    lp = lock_path(agent, "ledger")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "PERFORMANCE_LEDGER.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    pl.append_cycle(agent_dir=agent, cycle=3, notes="under lock")
    assert seen
    assert any(seen)
    assert not lp.exists()
    data = json.loads((agent / "PERFORMANCE_LEDGER.json").read_text(encoding="utf-8"))
    assert data["cycles"][0]["notes"] == "under lock"


def test_explicit_agent_dir_does_not_create_hub_lock(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    before = {p: p.exists() for p in _hub_ledger_locks()}
    rec = pl.append_cycle(agent_dir=agent, cycle=9, notes="не трогать хаб")
    assert rec["cycle"] == 9
    written = json.loads((agent / "PERFORMANCE_LEDGER.json").read_text(encoding="utf-8"))
    assert written["cycles"][0]["notes"] == "не трогать хаб"
    assert not lock_path(agent, "ledger").exists()
    for path, existed in before.items():
        if not existed:
            assert not path.exists(), path


def test_ledger_two_threads_max_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(tmp_path)
    held = 0
    max_held = 0
    counter = threading.Lock()
    names: List[str] = []
    roots: List[Path] = []
    orig = pl.agent_lock
    hub_before = {p: p.exists() for p in _hub_ledger_locks()}

    @contextmanager
    def wrapping_lock(agent_dir, *, name="agent", timeout=30.0) -> Iterator[None]:
        nonlocal held, max_held
        names.append(name)
        roots.append(Path(agent_dir).resolve())
        with orig(agent_dir, name=name, timeout=timeout):
            with counter:
                held += 1
                if held > max_held:
                    max_held = held
            try:
                time.sleep(0.05)
                yield
            finally:
                with counter:
                    held -= 1

    monkeypatch.setattr(pl, "agent_lock", wrapping_lock)
    barrier = threading.Barrier(2)
    errors: List[BaseException] = []

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=5)
            pl.append_cycle(agent_dir=agent, cycle=n, notes=f"поток-{n}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert errors == []
    assert max_held == 1
    assert names == ["ledger", "ledger"]
    assert all(root == agent.resolve() for root in roots)
    data = json.loads((agent / "PERFORMANCE_LEDGER.json").read_text(encoding="utf-8"))
    cycles = data.get("cycles")
    assert isinstance(cycles, list)
    assert {c["cycle"] for c in cycles} == {1, 2}
    notes = {c.get("notes") for c in cycles}
    assert notes == {"поток-1", "поток-2"}
    assert not lock_path(agent, "ledger").exists()
    for path, existed in hub_before.items():
        if not existed:
            assert not path.exists(), path


def test_edge_cases(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    rep = pl.generate_report(5, agent_dir=agent)
    assert rep == "No cycles recorded yet."

    js = agent / "PERFORMANCE_LEDGER.json"
    js.write_text("not valid json {", encoding="utf-8")
    ledger = pl._load_ledger(agent_dir=agent)
    assert ledger["cycles"] == []

    rec = pl.append_cycle(agent_dir=agent, cycle=1, elapsed_minutes=1.0)
    assert rec["cycle"] == 1
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["cycles"][0]["cycle"] == 1
    assert data["cycles"][0]["elapsed_minutes"] == 1.0


def test_cli_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    pl.append_cycle(cycle=3, elapsed_minutes=2.0, confidence=0.8)
    old_argv = sys.argv
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    try:
        sys.argv = ["prog", "report", "--recent", "2"]
        try:
            pl.main()
        except SystemExit:
            pass
    finally:
        sys.argv = old_argv
    output = buf.getvalue()
    assert "recent_cycles" in output
    assert "avg_elapsed_min" in output
    payload = json.loads(output)
    assert payload["recent_cycles"] == 1
    assert payload["avg_elapsed_min"] == 2.0
