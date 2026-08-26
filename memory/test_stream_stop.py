# -*- coding: utf-8 -*-
"""Юнит-тесты раздачи кооперативного STOP на worktree потоков."""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.stream_stop import (
    STOP_BODY,
    clear_fanout,
    fanout_stop,
    stream_worktrees_from_hub,
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _wt_root(tmp_path: Path) -> Path:
    return tmp_path / "agentic-loop-worktrees"


def _make_wt(root: Path, name: str) -> Path:
    wt = root / name
    (wt / ".agent").mkdir(parents=True)
    return wt.resolve()


def _layout(
    tmp_path: Path,
    *,
    streams: Optional[Dict[str, str]] = None,
    leases: Optional[Dict[str, str]] = None,
    n_fake: int = 2,
):
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    wts: List[Path] = [
        _make_wt(_wt_root(tmp_path), n) for n in ("wt-a", "wt-b", "wt-c")[:n_fake]
    ]
    if streams is None and n_fake:
        names = ("harness", "docs", "ops")
        streams = {names[i]: str(wts[i]) for i in range(n_fake)}
    if streams:
        payload = {
            "streams": {
                name: {"worktree": path, "status": "RUNNING"}
                for name, path in streams.items()
            },
            "terminal": "IN_PROGRESS",
        }
        _write_json(hub / ".agent" / "streams_state.json", payload)
    if leases:
        payload = {
            "leases": {
                name: {
                    "owned_paths": [f"{name}/"],
                    "worktree": path,
                    "pid": 1,
                }
                for name, path in leases.items()
            }
        }
        _write_json(hub / ".agent" / "stream_leases.json", payload)
    return hub.resolve(), wts


def _stop(root: Path) -> Path:
    return root / ".agent" / "STOP"


def test_fanout_writes_all_stop_files(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    written = fanout_stop(hub)
    expected = [_stop(hub), _stop(wts[0]), _stop(wts[1])]
    assert [_p.resolve() for _p in written] == [p.resolve() for p in expected]
    for path in expected:
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == STOP_BODY
        assert path.read_text(encoding="utf-8") == "1"


def test_missing_json_hub_only(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    for name in ("streams_state.json", "stream_leases.json"):
        p = hub / ".agent" / name
        if p.exists():
            p.unlink()
    assert stream_worktrees_from_hub(hub) == []
    written = fanout_stop(hub)
    assert [p.resolve() for p in written] == [_stop(hub).resolve()]
    assert _stop(hub).read_text(encoding="utf-8") == "1"
    assert not _stop(wts[0]).exists()
    assert not _stop(wts[1]).exists()


def test_stream_worktrees_from_hub_missing_files_empty(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    assert stream_worktrees_from_hub(hub) == []


def test_fanout_union_state_and_leases(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    wt_c = _make_wt(_wt_root(tmp_path), "wt-c")
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "harness": {"worktree": str(wts[0]), "status": "RUNNING"},
            }
        },
    )
    _write_json(
        hub / ".agent" / "stream_leases.json",
        {
            "leases": {
                "docs": {"worktree": str(wts[1]), "owned_paths": ["docs/"], "pid": 2},
                "ops": {"worktree": str(wt_c), "owned_paths": ["ops/"], "pid": 3},
            }
        },
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wts[0], wts[1], wt_c]
    written = fanout_stop(hub)
    assert len(written) == 4
    for root in (hub, wts[0], wts[1], wt_c):
        assert _stop(root).read_text(encoding="utf-8") == "1"


def test_dedup_same_worktree_in_state_and_leases(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=1)
    _write_json(
        hub / ".agent" / "stream_leases.json",
        {"leases": {"harness": {"worktree": str(wts[0]), "pid": 9}}},
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wts[0].resolve()]
    written = fanout_stop(hub)
    assert len(written) == 2
    assert _stop(wts[0]).read_text(encoding="utf-8") == "1"


def test_clear_fanout_removes_hub_and_worktrees(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    fanout_stop(hub)
    n = clear_fanout(hub)
    assert n == 3
    assert not _stop(hub).exists()
    assert not _stop(wts[0]).exists()
    assert not _stop(wts[1]).exists()


def test_clear_when_nothing_returns_zero(tmp_path: Path) -> None:
    hub, _wts = _layout(tmp_path, n_fake=2)
    assert clear_fanout(hub) == 0
    assert clear_fanout(hub) == 0


def test_fanout_logs_count_and_paths(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    with caplog.at_level(logging.INFO, logger="memory.stream_stop"):
        written = fanout_stop(hub)
    assert len(written) == 3
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert infos, "ожидали INFO о числе записанных STOP"
    msg = infos[-1].getMessage()
    assert "3" in msg
    assert "STOP fan-out" in msg
    for path in written:
        assert str(path) in msg


def test_malformed_json_hub_only(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    (hub / ".agent" / "streams_state.json").write_text("{nope", encoding="utf-8")
    (hub / ".agent" / "stream_leases.json").write_text("[]", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="memory.stream_stop"):
        found = stream_worktrees_from_hub(hub)
        written = fanout_stop(hub)
    assert found == []
    assert [p.resolve() for p in written] == [_stop(hub).resolve()]
    assert not _stop(wts[0]).exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "ожидали WARNING на битый JSON"
    assert any("streams_state.json" in w for w in warnings)


def test_non_utf8_json_hub_only(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    (hub / ".agent" / "streams_state.json").write_bytes(b"\xff\xfe\x00garbage")
    with caplog.at_level(logging.WARNING, logger="memory.stream_stop"):
        found = stream_worktrees_from_hub(hub)
        written = fanout_stop(hub)
    assert found == []
    assert [p.resolve() for p in written] == [_stop(hub).resolve()]
    assert _stop(hub).read_text(encoding="utf-8") == "1"
    assert not _stop(wts[0]).exists()
    assert not _stop(wts[1]).exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "ожидали WARNING на не-UTF-8 JSON"
    assert any("streams_state.json" in w for w in warnings)


def test_binary_state_still_reads_leases(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    (hub / ".agent" / "streams_state.json").write_bytes(b"\xff")
    _write_json(
        hub / ".agent" / "stream_leases.json",
        {"leases": {"docs": {"worktree": str(wts[1]), "pid": 4}}},
    )
    with caplog.at_level(logging.WARNING, logger="memory.stream_stop"):
        found = stream_worktrees_from_hub(hub)
        written = fanout_stop(hub)
    assert [p.resolve() for p in found] == [wts[1].resolve()]
    assert _stop(hub).is_file()
    assert _stop(wts[1]).is_file()
    assert not _stop(wts[0]).exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("streams_state.json" in w for w in warnings)


def test_leases_only(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    wt = _make_wt(_wt_root(tmp_path), "leased")
    _write_json(
        hub / ".agent" / "stream_leases.json",
        {"leases": {"ops": {"worktree": str(wt), "pid": 4}}},
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wt]
    written = fanout_stop(hub)
    assert len(written) == 2
    assert _stop(hub).read_text(encoding="utf-8") == "1"
    assert _stop(wt).read_text(encoding="utf-8") == "1"


def test_streams_list_shape(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    wt = _make_wt(_wt_root(tmp_path), "listed")
    _write_json(
        hub / ".agent" / "streams_state.json",
        {"streams": [{"name": "docs", "worktree": str(wt)}]},
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wt]


def test_skips_empty_and_root_worktree(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=1)
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "empty": {"worktree": ""},
                "none": {"worktree": None},
                "root": {"worktree": "/"},
                "ok": {"worktree": str(wts[0]), "status": "RUNNING"},
            }
        },
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wts[0].resolve()]
    written = fanout_stop(hub)
    assert len(written) == 2
    assert _stop(wts[0]).is_file()
    assert not Path("/.agent/STOP").exists()


def test_escaped_parent_and_tmp_not_written(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=1)
    parent_stop = tmp_path / ".agent" / "STOP"
    tmp_stop = Path("/tmp/.agent/STOP")
    had_tmp_stop = tmp_stop.exists()
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "up": {"worktree": ".."},
                "tmp": {"worktree": "/tmp"},
                "ok": {"worktree": str(wts[0])},
            }
        },
    )
    with caplog.at_level(logging.WARNING, logger="memory.stream_stop"):
        found = stream_worktrees_from_hub(hub)
        written = fanout_stop(hub)
    assert [p.resolve() for p in found] == [wts[0].resolve()]
    assert _stop(hub).is_file()
    assert _stop(hub).read_text(encoding="utf-8") == "1"
    assert _stop(wts[0]).is_file()
    assert not parent_stop.exists()
    assert tmp_stop.exists() is had_tmp_stop
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "ожидали WARNING на пути вне allowlist"
    assert any(w.endswith(str(tmp_path.resolve())) for w in warnings)
    assert any(w.endswith(str(Path("/tmp").resolve())) for w in warnings)


def test_extra_roots_allows_custom_wt_base(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    custom = tmp_path / "custom-wts"
    wt = _make_wt(custom, "stream-a")
    _write_json(
        hub / ".agent" / "streams_state.json",
        {"streams": {"harness": {"worktree": str(wt)}}},
    )
    assert stream_worktrees_from_hub(hub) == []
    found = stream_worktrees_from_hub(hub, extra_roots=[custom])
    assert [p.resolve() for p in found] == [wt.resolve()]
    written = fanout_stop(hub, extra_roots=[custom])
    assert _stop(hub).is_file()
    assert _stop(wt).is_file()
    assert len(written) == 2
    n = clear_fanout(hub, extra_roots=[custom])
    assert n == 2
    assert not _stop(wt).exists()


def test_relative_worktree_resolved_against_hub(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agent").mkdir(parents=True)
    wt = _make_wt(hub, "nested-wt")
    _write_json(
        hub / ".agent" / "streams_state.json",
        {"streams": {"harness": {"worktree": "nested-wt"}}},
    )
    found = stream_worktrees_from_hub(hub)
    assert [p.resolve() for p in found] == [wt.resolve()]
    fanout_stop(hub)
    assert _stop(wt).read_text(encoding="utf-8") == "1"


def test_missing_worktree_dir_skipped(tmp_path: Path, caplog) -> None:
    hub, wts = _layout(tmp_path, n_fake=1)
    ghost = _wt_root(tmp_path) / "ghost-wt"
    _write_json(
        hub / ".agent" / "streams_state.json",
        {
            "streams": {
                "live": {"worktree": str(wts[0])},
                "ghost": {"worktree": str(ghost)},
            }
        },
    )
    with caplog.at_level(logging.WARNING, logger="memory.stream_stop"):
        written = fanout_stop(hub)
    assert len(written) == 2
    assert _stop(hub).is_file()
    assert _stop(wts[0]).is_file()
    assert not ghost.exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ghost-wt" in w for w in warnings)


def test_unrelated_dir_untouched(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    other = _make_wt(tmp_path, "other")
    fanout_stop(hub)
    assert not _stop(other).exists()
    assert _stop(wts[0]).is_file()
    clear_fanout(hub)
    # чужой STOP не создаём и не трогаем, даже если его подложили
    _stop(other).write_text("1", encoding="utf-8")
    assert clear_fanout(hub) == 0
    assert _stop(other).read_text(encoding="utf-8") == "1"


def test_fanout_idempotent_rewrite(tmp_path: Path) -> None:
    hub, wts = _layout(tmp_path, n_fake=2)
    first = fanout_stop(hub)
    second = fanout_stop(hub)
    assert [p.resolve() for p in first] == [p.resolve() for p in second]
    assert _stop(wts[0]).read_text(encoding="utf-8") == "1"


def test_source_does_not_import_supervisor() -> None:
    src = Path(__file__).resolve().parent / "stream_stop.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "supervisor" not in alias.name.split(".")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "supervisor" not in mod.split(".")
            for alias in node.names:
                assert "supervisor" not in alias.name.split(".")
