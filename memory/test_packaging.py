# -*- coding: utf-8 -*-
"""Контракт упаковки: копия схемы и extra dashboard совпадают с SSOT."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SSOT = REPO / "schemas" / "handoff.schema.json"
PACKAGED = REPO / "memory" / "data" / "handoff.schema.json"
REQ_DASH = REPO / "requirements-dashboard.txt"
PYPROJECT = REPO / "pyproject.toml"


def test_packaged_schema_matches_ssot():
    assert PACKAGED.is_file(), (
        "memory/data/handoff.schema.json отсутствует — копия SSOT обязательна"
    )
    assert SSOT.is_file()
    assert PACKAGED.read_bytes() == SSOT.read_bytes()


def test_load_schema_reads_packaged_copy(monkeypatch):
    from importlib.resources import files as pkg_files
    from memory.validate_handoff import _load_schema

    packaged = pkg_files("memory").joinpath("data/handoff.schema.json")
    assert packaged.is_file(), "packaged schema missing under files('memory')/data"

    src = (REPO / "memory" / "validate_handoff.py").read_text(encoding="utf-8")
    assert 'files("memory.data")' not in src
    assert '_pkg_files("memory")' in src

    orig_is_file = Path.is_file

    def hide_ssot(self: Path) -> bool:
        if self.name == "handoff.schema.json" and self.parent.name == "schemas":
            return False
        return orig_is_file(self)

    monkeypatch.setattr(Path, "is_file", hide_ssot)
    loaded = _load_schema()
    assert loaded, "без SSOT fallback схема должна читаться из package data"
    assert loaded == json.loads(SSOT.read_text(encoding="utf-8"))


def _dashboard_extra_items(text: str) -> list[str]:
    try:
        import tomllib

        data = tomllib.loads(text)
        return list(data["project"]["optional-dependencies"]["dashboard"])
    except ModuleNotFoundError:
        pass
    match = re.search(r"(?ms)^dashboard\s*=\s*\[(.*?)\]", text)
    assert match, "dashboard extra не найден в pyproject.toml"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_dashboard_extra_matches_requirements_dashboard():
    extra = _dashboard_extra_items(PYPROJECT.read_text(encoding="utf-8"))
    req = [
        line.strip()
        for line in REQ_DASH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert extra == req
    assert "httpx>=0.27" in extra


def test_windows_extras_syntax_has_no_dot():
    ps1 = (REPO / "Agent-Init.ps1").read_text(encoding="utf-8")
    assert '"$ProjectRoot.[dev]"' not in ps1
    assert '"$TemplateRoot.[dev]"' not in ps1
    assert '"$TemplateRoot[dev]"' in ps1
    assert '"$ProjectRoot[dev]"' not in ps1
    assert "install','-e'" in ps1 or "install', '-e'" in ps1
    assert "agentic_loop_template" in ps1
    assert "$TemplateRoot" in ps1
