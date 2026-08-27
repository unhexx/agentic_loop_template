# -*- coding: utf-8 -*-
"""Проверки файловой блокировки каталога .agent."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from memory.agent_lock import agent_lock, lock_path
from memory.handoff_io import save_handoff
from memory.state import default_state, load_state, save_state


def test_lock_creates_and_releases(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    lp = lock_path(agent, "agent")
    assert not lp.exists()
    with agent_lock(agent):
        assert lp.is_file()
        body = lp.read_text(encoding="ascii")
        assert str(os.getpid()) in body
    assert not lp.exists()


def test_two_threads_serialized_json_replace(tmp_path: Path) -> None:
    """Один PID, два потока, общий tmp-файл — без thread-guard FileNotFoundError на replace."""
    agent = tmp_path / ".agent"
    agent.mkdir()
    target = agent / "shared.json"
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=5)
            with agent_lock(agent, name="shared", timeout=10.0):
                data = []
                if target.exists():
                    data = json.loads(target.read_text(encoding="utf-8"))
                data.append(n)
                tmp = target.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(target)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert errors == []
    body = json.loads(target.read_text(encoding="utf-8"))
    assert sorted(body) == list(range(8))
    assert not lock_path(agent, "shared").exists()


def test_two_threads_one_holder(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    hold = threading.Event()
    inside = threading.Event()

    def holder() -> None:
        with agent_lock(agent, timeout=5.0):
            inside.set()
            hold.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    assert inside.wait(timeout=3), "держатель не взял блокировку"
    with pytest.raises(TimeoutError) as excinfo:
        with agent_lock(agent, timeout=0.3):
            raise AssertionError("второй поток не должен войти в секцию")
    assert str(lock_path(agent)) in str(excinfo.value)
    hold.set()
    t.join(timeout=5)
    assert not t.is_alive()
    with agent_lock(agent, timeout=2.0):
        assert lock_path(agent).is_file()
    assert not lock_path(agent).exists()


def test_stale_pid_recovered(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir(parents=True)
    lp = lock_path(agent)
    lp.write_text("99999999\n", encoding="ascii")
    with agent_lock(agent, timeout=2.0):
        body = lp.read_text(encoding="ascii")
        assert str(os.getpid()) in body
        assert "99999999" not in body
    assert not lp.exists()


def test_save_state_under_lock_roundtrip(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    save_state(default_state(), agent_dir=agent)
    st = load_state(agent_dir=agent)
    assert isinstance(st, dict)
    raw = (agent / "LOOP_STATE.json").read_text(encoding="utf-8")
    obj = json.loads(raw)
    assert isinstance(obj, dict)
    assert (agent / "LOOP_STATE.md").is_file()
    assert not lock_path(agent, "state").exists()


def test_save_handoff_roundtrip(tmp_path: Path) -> None:
    data = {
        "role": "Coder",
        "handoff_to": "Tester",
        "status": "IN_PROGRESS",
        "summary": "implemented feature",
    }
    path = save_handoff(tmp_path, data)
    assert path == tmp_path / ".agent" / "last_handoff.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["role"] == "Coder"
    assert loaded["summary"] == "implemented feature"
    assert not lock_path(tmp_path / ".agent", "handoff").exists()
