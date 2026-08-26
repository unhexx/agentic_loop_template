# -*- coding: utf-8 -*-
"""Проверки явного каталога .agent и файловой блокировки индекса playbooks."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from memory.agent_lock import agent_lock as real_lock, lock_path
from memory.playbooks import (
    curate_from_reflection,
    export_hub_index,
    list_playbooks,
    load_config,
    seed_initial_playbooks,
    select_bullets,
)


def test_playbooks_agent_dir_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp1 = tmp_path / "one"
    tmp2 = tmp_path / "two"
    agent = tmp1 / ".agent"
    agent.mkdir(parents=True)
    tmp2.mkdir()
    monkeypatch.chdir(tmp2)

    n = seed_initial_playbooks(agent_dir=agent)
    assert n > 0
    index = agent / "PLAYBOOKS.json"
    assert index.is_file()
    data = json.loads(index.read_text(encoding="utf-8"))
    assert "global-dev" in data.get("playbooks", {})
    assert (agent / "PLAYBOOKS" / "overview.md").is_file()

    cwd_index = tmp2 / ".agent" / "PLAYBOOKS.json"
    assert not cwd_index.exists()

    mut = curate_from_reflection(
        {
            "lessons_learned": ["Always pin the git remote before syncing two clones"],
            "cycle": 7,
        },
        "global-dev",
        agent_dir=agent,
    )
    assert mut["playbook"] == "global-dev"
    assert mut["added"] + mut["updated"] >= 1
    assert index.is_file()
    assert not cwd_index.exists()

    bullets = select_bullets("git", agent_dir=agent)
    assert len(bullets) >= 1
    assert all("_score" in b for b in bullets)
    assert not cwd_index.exists()

    items = list_playbooks(agent_dir=agent)
    assert any(it["id"] == "global-dev" for it in items)


def test_export_hub_index_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir()
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    seed_initial_playbooks(agent_dir=agent)
    data = export_hub_index(fmt="hub", agent_dir=agent)
    assert data["version"] == "1.0"
    assert data["item_count"] > 0
    assert "items" in data
    hub = agent / "HUB_INDEX.json"
    assert hub.is_file()
    written = json.loads(hub.read_text(encoding="utf-8"))
    assert written["item_count"] == data["item_count"]
    assert written["version"] == "1.0"
    assert not (cwd / ".agent" / "HUB_INDEX.json").exists()
    assert not lock_path(agent, "playbooks").exists()


def test_playbooks_write_releases_lock(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    seed_initial_playbooks(agent_dir=agent)
    lp = lock_path(agent, "playbooks")
    assert (agent / "PLAYBOOKS.json").is_file()
    assert not lp.exists()
    body = json.loads((agent / "PLAYBOOKS.json").read_text(encoding="utf-8"))
    assert "playbooks" in body


def test_playbooks_lock_held_during_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = tmp_path / ".agent"
    lp = lock_path(agent, "playbooks")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "PLAYBOOKS.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    seed_initial_playbooks(agent_dir=agent)
    assert seen
    assert any(seen)
    assert not lp.exists()


def test_playbooks_two_threads_max_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.playbooks as pb

    agent = tmp_path / ".agent"
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

    monkeypatch.setattr(pb, "agent_lock", counting_lock)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            pb.seed_initial_playbooks(agent_dir=agent)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert errors == []
    assert max_held == 1
    raw = (agent / "PLAYBOOKS.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "playbooks" in payload
    assert not lock_path(agent, "playbooks").exists()


def test_load_config_reads_agent_dir_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "iso" / ".agent"
    agent.mkdir(parents=True)
    (agent / "project_config.json").write_text(
        json.dumps({"playbooks": {"enabled": False, "default_k": 1}}),
        encoding="utf-8",
    )
    other = tmp_path / "cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    cfg = load_config(agent_dir=agent)
    assert cfg["enabled"] is False
    assert cfg["default_k"] == 1
    cfg_cwd = load_config()
    assert cfg_cwd["enabled"] is True
    assert select_bullets("git", agent_dir=agent) == []
