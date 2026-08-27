# -*- coding: utf-8 -*-
"""P8-12: тонкий загрузчик experience_harvester и пакет memory.experience."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import memory.experience.audit as audit_mod
import memory.experience.extract as extract_mod
import memory.experience_harvester as eh

REPO = Path(__file__).resolve().parents[1]


def _nl(path: Path) -> int:
    return path.read_text(encoding="utf-8").count("\n") + 1


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_experience_scan_cli_does_not_import_audit() -> None:
    src = inspect.getsource(eh.cli)
    assert "from memory.experience.scan import scan_parent" in src
    assert "from memory.experience.extract import dedupe" in src
    scan_idx = src.find('if args.cmd == "scan"')
    apply_idx = src.find("if args.apply:", scan_idx)
    assert scan_idx != -1 and apply_idx != -1
    scan_block = src[scan_idx:apply_idx]
    assert "apply_patterns" not in scan_block
    apply_block = src[apply_idx : src.find("return 0", apply_idx)]
    assert "apply_patterns" in apply_block


def test_scan_py_does_not_import_audit() -> None:
    src = (REPO / "memory" / "experience" / "scan.py").read_text(encoding="utf-8")
    assert "experience.audit" not in src
    assert "from .audit" not in src


def test_experience_absolute_imports() -> None:
    dedupe_src = inspect.getsource(extract_mod.dedupe)
    apply_src = inspect.getsource(audit_mod.apply_patterns)
    for src in (dedupe_src, apply_src):
        assert "from .store import" not in src
        assert "from .schema import" not in src
    combined = dedupe_src + apply_src + inspect.getsource(extract_mod) + inspect.getsource(audit_mod)
    assert "memory.schema" in combined or "..schema" in combined
    assert "memory.store" in combined or "..store" in combined


def test_experience_line_caps() -> None:
    loader = REPO / "memory" / "experience_harvester.py"
    n = _nl(loader)
    assert n <= 200, f"{loader} {n} > 200"
    pkg = REPO / "memory" / "experience"
    for path in sorted(pkg.glob("*.py")):
        if path.name == "__init__.py":
            continue
        n = _nl(path)
        assert n <= 350, f"{path} {n} > 350"


def test_experience_cli_help() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "memory.experience_harvester", "--help"],
        cwd=str(REPO),
        env=_env(),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
