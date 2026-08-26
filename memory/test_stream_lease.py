# -*- coding: utf-8 -*-
"""Проверки эксклюзивных lease на owned_paths."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memory.agent_lock import lock_path
from memory.stream_lease import (
    DEFAULT_TTL_S,
    claim,
    main,
    release,
    renew,
    status,
)
from memory.streams import _owned_covers, owned_covers


def _leases_file(hub: Path) -> Path:
    return hub / ".agent" / "stream_leases.json"


def _read(hub: Path) -> dict:
    return json.loads(_leases_file(hub).read_text(encoding="utf-8"))


def _write_registry(hub: Path, leases: dict) -> None:
    agent = hub / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    payload = {"leases": leases}
    _leases_file(hub).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_owned_covers_public_alias() -> None:
    assert owned_covers is _owned_covers
    assert owned_covers("memory/", "memory/streams.py")
    assert not owned_covers("docs/", "memory/state.py")


def test_claim_writes_registry(tmp_path: Path) -> None:
    rec = claim(tmp_path, "harness", ["memory/", "tools/"], worktree="/abs/wt")
    assert rec["name"] == "harness"
    assert rec["owned_paths"] == ["memory/", "tools/"]
    assert rec["pid"] == os.getpid()
    assert rec["worktree"] == "/abs/wt"
    stored = _read(tmp_path)["leases"]["harness"]
    assert "name" not in stored
    assert stored["pid"] == os.getpid()
    claimed = datetime.strptime(stored["claimed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    expires = datetime.strptime(stored["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert (expires - claimed).total_seconds() == DEFAULT_TTL_S
    assert DEFAULT_TTL_S == 7200
    assert not lock_path(tmp_path / ".agent", "leases").exists()


def test_claim_disjoint_ok(tmp_path: Path) -> None:
    claim(tmp_path, "harness", ["memory/"])
    claim(tmp_path, "docs", ["docs/"])
    leases = _read(tmp_path)["leases"]
    assert set(leases) == {"harness", "docs"}


def test_claim_overlap_live_raises(tmp_path: Path) -> None:
    claim(tmp_path, "harness", ["memory/"])
    with pytest.raises(ValueError, match="overlap between streams"):
        claim(tmp_path, "other", ["memory/state.py"])
    assert "other" not in _read(tmp_path)["leases"]


def test_live_pid_past_expires_at_is_not_stolen(tmp_path: Path) -> None:
    rec = claim(tmp_path, "harness", ["memory/"], ttl_s=7200)
    data = _read(tmp_path)
    data["leases"]["harness"]["expires_at"] = "2000-01-01T00:00:00Z"
    _leases_file(tmp_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="overlap between streams"):
        claim(tmp_path, "other", ["memory/streams.py"])
    after = _read(tmp_path)["leases"]
    assert "harness" in after
    assert "other" not in after
    assert after["harness"]["pid"] == rec["pid"] == os.getpid()
    assert after["harness"]["expires_at"] == "2000-01-01T00:00:00Z"


def test_same_name_live_pid_past_ttl_renews_in_place(tmp_path: Path) -> None:
    first = claim(tmp_path, "harness", ["memory/"], ttl_s=60)
    data = _read(tmp_path)
    data["leases"]["harness"]["expires_at"] = "2000-01-01T00:00:00Z"
    _leases_file(tmp_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    second = claim(tmp_path, "harness", ["memory/"], ttl_s=500)
    assert second["pid"] == os.getpid()
    assert second["claimed_at"] == first["claimed_at"]
    assert second["expires_at"] != "2000-01-01T00:00:00Z"
    assert list(_read(tmp_path)["leases"]) == ["harness"]


def test_dead_pid_may_steal_overlap(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "worktree": None,
                "pid": 99999999,
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "branch": None,
            }
        },
    )
    rec = claim(tmp_path, "docs", ["memory/state.py"])
    leases = _read(tmp_path)["leases"]
    assert "harness" not in leases
    assert leases["docs"]["pid"] == rec["pid"] == os.getpid()


def test_dead_pid_same_name_stolen(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "worktree": "/old",
                "pid": 99999999,
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "branch": "old",
            }
        },
    )
    rec = claim(tmp_path, "harness", ["tools/"], worktree="/new")
    stored = _read(tmp_path)["leases"]["harness"]
    assert stored["pid"] == os.getpid()
    assert stored["owned_paths"] == ["tools/"]
    assert stored["worktree"] == "/new"
    assert stored["claimed_at"] == rec["claimed_at"]
    assert stored["claimed_at"] != "2000-01-01T00:00:00Z"


def test_unreadable_pid_may_steal(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "pid": "not-a-pid",
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        },
    )
    claim(tmp_path, "other", ["memory/"])
    leases = _read(tmp_path)["leases"]
    assert "harness" not in leases
    assert leases["other"]["pid"] == os.getpid()


def test_missing_pid_may_steal(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        },
    )
    claim(tmp_path, "harness", ["memory/", "tools/"])
    assert _read(tmp_path)["leases"]["harness"]["pid"] == os.getpid()


def test_foreign_live_pid_not_stolen_even_if_expired(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_registry(
            tmp_path,
            {
                "harness": {
                    "owned_paths": ["memory/"],
                    "pid": proc.pid,
                    "claimed_at": "2000-01-01T00:00:00Z",
                    "expires_at": "2000-01-01T00:00:01Z",
                    "worktree": None,
                    "branch": None,
                }
            },
        )
        with pytest.raises(ValueError, match="overlap"):
            claim(tmp_path, "harness", ["memory/"])
        with pytest.raises(ValueError, match="overlap between streams"):
            claim(tmp_path, "other", ["memory/"])
        stored = _read(tmp_path)["leases"]["harness"]
        assert stored["pid"] == proc.pid
        assert stored["expires_at"] == "2000-01-01T00:00:01Z"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_renew_extends_expires_at(tmp_path: Path) -> None:
    first = claim(tmp_path, "harness", ["memory/"], ttl_s=100)
    second = renew(tmp_path, "harness", ttl_s=500)
    assert second["claimed_at"] == first["claimed_at"]
    assert second["expires_at"] != first["expires_at"]
    claimed = datetime.strptime(second["claimed_at"], "%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.strptime(second["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (expires - claimed).total_seconds() == 500


def test_renew_dead_pid_raises(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "pid": 99999999,
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        },
    )
    with pytest.raises(ValueError, match="no live lease"):
        renew(tmp_path, "harness")


def test_release_own_pid(tmp_path: Path) -> None:
    claim(tmp_path, "harness", ["memory/"])
    assert release(tmp_path, "harness") is True
    assert _read(tmp_path)["leases"] == {}
    assert release(tmp_path, "harness") is False


def test_release_dead_pid(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "harness": {
                "owned_paths": ["memory/"],
                "pid": 99999999,
                "claimed_at": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        },
    )
    assert release(tmp_path, "harness") is True
    assert _read(tmp_path)["leases"] == {}


def test_release_live_foreign_pid_refused(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_registry(
            tmp_path,
            {
                "harness": {
                    "owned_paths": ["memory/"],
                    "pid": proc.pid,
                    "claimed_at": "2000-01-01T00:00:00Z",
                    "expires_at": "2000-01-01T00:00:01Z",
                }
            },
        )
        with pytest.raises(ValueError, match="held by live pid"):
            release(tmp_path, "harness")
        assert _read(tmp_path)["leases"]["harness"]["pid"] == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_status_empty_and_populated(tmp_path: Path) -> None:
    empty = status(tmp_path)
    assert empty == {"leases": {}}
    claim(tmp_path, "docs", ["docs/"])
    snap = status(tmp_path)
    assert "docs" in snap["leases"]
    assert snap["leases"]["docs"]["pid"] == os.getpid()


def test_cli_claim_status_renew_release(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hub = str(tmp_path)
    rc = main(
        [
            "claim",
            "--stream",
            "harness:memory/,tools/",
            "--workdir",
            hub,
            "--worktree",
            "/tmp/wt",
            "--branch",
            "feature/c1-harness",
        ]
    )
    assert rc == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["name"] == "harness"
    assert claimed["owned_paths"] == ["memory/", "tools/"]
    assert claimed["branch"] == "feature/c1-harness"

    rc = main(["status", "--workdir", hub])
    assert rc == 0
    snap = json.loads(capsys.readouterr().out)
    assert "harness" in snap["leases"]
    assert snap["leases"]["harness"]["pid"] == os.getpid()

    rc = main(["renew", "--stream", "harness", "--workdir", hub, "--ttl", "100"])
    assert rc == 0
    renewed = json.loads(capsys.readouterr().out)
    assert renewed["name"] == "harness"

    rc = main(["release", "--stream", "harness", "--workdir", hub])
    assert rc == 0
    released = json.loads(capsys.readouterr().out)
    assert released["released"] is True
    assert _read(tmp_path)["leases"] == {}


def test_cli_overlap_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hub = str(tmp_path)
    assert main(["claim", "--stream", "harness:memory/", "--workdir", hub]) == 0
    capsys.readouterr()
    rc = main(["claim", "--stream", "docs:memory/state.py", "--workdir", hub])
    assert rc == 1
    err = capsys.readouterr().err
    assert "overlap between streams" in err
    assert "docs" in err
    assert "harness" in err
