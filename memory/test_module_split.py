# -*- coding: utf-8 -*-
"""P8-12: тонкий загрузчик meta_harvester и пакет memory.meta."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _nl(path: Path) -> int:
    return path.read_text(encoding="utf-8").count("\n") + 1


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_meta_harvest_cli_does_not_import_curator(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir()
    handoff = tmp_path / "h.json"
    handoff.write_text(
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
    code = r"""
import json, sys
from pathlib import Path
import importlib
mh = importlib.import_module("memory.meta_harvester")
assert "memory.meta.curator" not in sys.modules
tid = mh.harvest_from_handoff(Path(r"%s"), cycle=42, outcome="DONE", agent_dir=Path(r"%s"))
assert tid is not None
assert "memory.meta.curator" not in sys.modules
mh.analyze_for_proposals(recent=1, min_confidence=0.8, agent_dir=Path(r"%s"))
assert "memory.meta.reflector" in sys.modules
assert "memory.meta.curator" not in sys.modules
mh.apply_safe_proposals(dry_run=True, agent_dir=Path(r"%s"))
assert "memory.meta.curator" in sys.modules
""" % (handoff, agent, agent, agent)
    subprocess.run([sys.executable, "-c", code], check=True, env=_env(), cwd=str(REPO))


def test_import_memory_does_not_load_meta_bodies() -> None:
    code = r"""
import sys
import memory
assert "memory.meta.generator" not in sys.modules
assert "memory.meta.reflector" not in sys.modules
assert "memory.meta.curator" not in sys.modules
from memory import harvest_from_handoff
assert "memory.meta.generator" in sys.modules
assert callable(harvest_from_handoff)
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=_env(), cwd=str(REPO))


def test_append_cycle_is_normal_import(tmp_path: Path) -> None:
    import memory
    import memory.meta.curator as curator
    import memory.meta.generator as generator
    import memory.meta.reflector as reflector

    src = inspect.getsource(curator.update_performance_ledger)
    assert "from memory.performance_ledger import append_cycle" in src
    assert "spec_from_file_location" not in src
    for mod in (generator, reflector, curator):
        body = inspect.getsource(mod)
        assert "from memory.agent_lock import" not in body
    agent = tmp_path / ".agent"
    agent.mkdir()
    curator.update_performance_ledger("P-1", agent_dir=agent)
    assert (agent / "PERFORMANCE_LEDGER.json").is_file()
    assert memory is not None


def test_save_index_gone() -> None:
    import memory.meta.store as store
    import memory.meta_harvester as mh

    assert not hasattr(store, "_save_index")
    assert not hasattr(mh, "_save_index")


def test_meta_line_caps() -> None:
    loader = REPO / "memory" / "meta_harvester.py"
    n = _nl(loader)
    assert n <= 200, f"{loader} {n} > 200"
    meta = REPO / "memory" / "meta"
    for path in sorted(meta.glob("*.py")):
        if path.name == "__init__.py":
            continue
        n = _nl(path)
        assert n <= 350, f"{path} {n} > 350"


def test_meta_cli_help() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "memory.meta_harvester", "--help"],
        cwd=str(REPO),
        env=_env(),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
