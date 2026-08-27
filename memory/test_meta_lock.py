# -*- coding: utf-8 -*-
"""Явный каталог .agent и именные локи meta_harvester."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import memory.meta.store as mstore
from memory.agent_lock import agent_lock as real_lock, lock_path


def _handoff(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "role": "Reviewer",
                "summary": "Успешно завершил задачу по sync",
                "confidence": 0.91,
                "metrics": {
                    "tests_total": 12,
                    "tests_failed": 0,
                    "coverage": 88.0,
                    "tool_calls": 7,
                    "elapsed_minutes": 9.5,
                },
                "process_tags": [],
                "lessons_learned": ["Всегда использовать явный маркер завершения"],
                "git_branch": "feature/sync-verify",
                "last_commit": "Улучшил верификацию с маркером SYNC_DONE",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_harvest_agent_dir_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / "one" / ".agent"
    agent.mkdir(parents=True)
    cwd = tmp_path / "two"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    handoff = _handoff(tmp_path / "h.json")

    tid = mh.harvest_from_handoff(handoff, cycle=42, outcome="DONE", agent_dir=agent)
    assert tid is not None
    assert (agent / "TRAJECTORIES.json").is_file()
    assert (agent / "META_PROPOSALS.md").is_file()
    assert not (cwd / ".agent").exists()
    body = json.loads((agent / "TRAJECTORIES.json").read_text(encoding="utf-8"))
    assert body["trajectories"][0]["cycle"] == 42
    assert not list(agent.glob("*.tmp"))


def test_harvest_lock_name_and_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    lock_roots: list[Path] = []
    lock_names: list[str] = []
    orig = mstore.agent_lock

    @contextmanager
    def spy_lock(agent_dir, *, name="agent", timeout=30.0):
        lock_roots.append(Path(agent_dir).resolve())
        lock_names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mstore, "agent_lock", spy_lock)
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=7, outcome="DONE", agent_dir=agent)
    assert "trajectories" in lock_names
    assert all(r == agent.resolve() for r in lock_roots)
    hub = Path.cwd() / ".agent" / "trajectories.lock"
    assert hub.resolve() != lock_path(agent, "trajectories").resolve() or not hub.exists()


def test_harvest_lock_held_during_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    lp = lock_path(agent, "trajectories")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "TRAJECTORIES.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=8, outcome="DONE", agent_dir=agent)
    assert seen
    assert any(seen)
    assert not lp.exists()


def test_harvest_releases_lock(tmp_path: Path) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=9, outcome="DONE", agent_dir=agent)
    assert not lock_path(agent, "trajectories").exists()
    assert not list(agent.glob("*.tmp"))


def test_two_harvest_threads_max_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    current = 0
    max_held = 0
    gate = threading.Lock()

    @contextmanager
    def counting_lock(agent_dir, *, name="agent", timeout=30.0):
        nonlocal current, max_held
        with real_lock(agent_dir, name=name, timeout=timeout):
            with gate:
                current += 1
                max_held = max(max_held, current)
            try:
                time.sleep(0.05)
                yield
            finally:
                with gate:
                    current -= 1

    monkeypatch.setattr(mstore, "agent_lock", counting_lock)
    errors: list[BaseException] = []
    handoffs = []
    for i, cycle in enumerate((11, 12, 13, 14)):
        p = _handoff(tmp_path / f"h{i}.json")
        handoffs.append((p, cycle))

    def worker(item: tuple[Path, int]) -> None:
        try:
            mh.harvest_from_handoff(item[0], cycle=item[1], outcome="DONE", agent_dir=agent)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(item,)) for item in handoffs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert errors == []
    assert max_held == 1
    body = json.loads((agent / "TRAJECTORIES.json").read_text(encoding="utf-8"))
    cycles = {t["cycle"] for t in body["trajectories"]}
    assert cycles == {11, 12, 13, 14}
    assert not lock_path(agent, "trajectories").exists()


def test_export_sft_default_uses_sft_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    names: list[str] = []
    orig = mstore.agent_lock

    @contextmanager
    def spy(agent_dir, *, name="agent", timeout=30.0):
        names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mstore, "agent_lock", spy)
    mh.seed_example_trajectory(agent_dir=agent)
    report = mh.export_sft(agent_dir=agent)
    assert report["written"] >= 1
    assert "sft" in names
    assert (agent / "sft" / "train.jsonl").is_file()
    assert not (cwd / ".agent").exists()


def test_export_sft_out_skips_sft_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    names: list[str] = []
    orig = mstore.agent_lock

    @contextmanager
    def spy(agent_dir, *, name="agent", timeout=30.0):
        names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mstore, "agent_lock", spy)
    mh.seed_example_trajectory(agent_dir=agent)
    dest = tmp_path / "other.jsonl"
    names.clear()
    report = mh.export_sft(out=dest, agent_dir=agent)
    assert report["written"] >= 1
    assert dest.is_file()
    assert "sft" not in names


def test_update_performance_ledger_passes_agent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    mh.update_performance_ledger("P-1", "impact", agent_dir=agent)
    assert (agent / "LOOP_PERFORMANCE.md").is_file()
    text = (agent / "LOOP_PERFORMANCE.md").read_text(encoding="utf-8")
    assert "P-1" in text
    assert (agent / "PERFORMANCE_LEDGER.json").is_file()
    assert not (cwd / ".agent" / "LOOP_PERFORMANCE.md").exists()
    assert not (cwd / ".agent" / "PERFORMANCE_LEDGER.json").exists()


def test_ledger_lock_not_nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.agent_lock as al
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    depth = 0
    max_depth = 0
    orig = al.agent_lock

    @contextmanager
    def counting(agent_dir, *, name="agent", timeout=30.0):
        nonlocal depth, max_depth
        if name == "ledger":
            depth += 1
            max_depth = max(max_depth, depth)
            try:
                with orig(agent_dir, name=name, timeout=timeout):
                    yield
            finally:
                depth -= 1
        else:
            with orig(agent_dir, name=name, timeout=timeout):
                yield

    monkeypatch.setattr(al, "agent_lock", counting)
    monkeypatch.setattr(mstore, "agent_lock", counting)
    mh.update_performance_ledger("P-2", agent_dir=agent)
    assert max_depth == 1
    assert depth == 0
