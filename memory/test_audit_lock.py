# -*- coding: utf-8 -*-
"""Блокировка записи AUDIT_LOG: лок на родителе реального JSON, не cwd/.agent."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from memory import audit_log
from memory.agent_lock import lock_path
from memory.audit_log import append_entry, list_entries


def _hub_audit_lock() -> Path:
    return (Path.cwd() / ".agent" / "audit.lock").resolve()


def test_audit_write_under_lock(tmp_path: Path, monkeypatch) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir()
    hub_lock = _hub_audit_lock()
    tmp_lock = lock_path(agent, "audit").resolve()
    assert tmp_lock != hub_lock

    lock_roots: list[Path] = []
    lock_names: list[str] = []
    lock_seen = threading.Event()
    orig = audit_log.agent_lock

    @contextmanager
    def spy_lock(agent_dir, *, name="agent", timeout=30.0):
        lock_roots.append(Path(agent_dir).resolve())
        lock_names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            lock_seen.set()
            assert lock_path(Path(agent_dir), name).is_file()
            yield

    monkeypatch.setattr(audit_log, "agent_lock", spy_lock)

    entry = append_entry(
        "lock.check",
        "tester",
        3,
        {"ok": True},
        agent_dir=agent,
    )
    assert entry["action"] == "lock.check"
    assert entry["id"] == "A-0001"

    written = json.loads((agent / "AUDIT_LOG.json").read_text(encoding="utf-8"))
    assert written["entries"][-1]["action"] == "lock.check"
    assert (agent / "AUDIT_LOG.md").is_file()
    assert "lock.check" in (agent / "AUDIT_LOG.md").read_text(encoding="utf-8")
    assert not (agent / "AUDIT_LOG.json.tmp").exists()

    assert lock_seen.is_set()
    assert lock_names == ["audit"]
    assert lock_roots == [agent.resolve()]
    assert not tmp_lock.exists()
    assert not hub_lock.exists()

    held = 0
    max_held = 0
    counter = threading.Lock()
    barrier = threading.Barrier(2)

    @contextmanager
    def counting_lock(agent_dir, *, name="agent", timeout=30.0):
        nonlocal held, max_held
        lock_roots.append(Path(agent_dir).resolve())
        lock_names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            with counter:
                held += 1
                if held > max_held:
                    max_held = held
            try:
                time.sleep(0.08)
                yield
            finally:
                with counter:
                    held -= 1

    monkeypatch.setattr(audit_log, "agent_lock", counting_lock)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=5)
            append_entry(f"act-{n}", "tester", n, {"n": n}, agent_dir=agent)
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
    entries = list_entries(limit=20, agent_dir=agent)
    assert len(entries) == 3
    actions = {e["action"] for e in entries}
    assert actions == {"lock.check", "act-1", "act-2"}
    assert not tmp_lock.exists()
    assert not hub_lock.exists()
    assert all(root == agent.resolve() for root in lock_roots)
    assert all(name == "audit" for name in lock_names)
    assert "audit" in lock_names
