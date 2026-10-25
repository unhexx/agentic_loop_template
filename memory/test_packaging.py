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


def _dashboard_extra_from_text(text: str) -> list[str]:
    """Разбор extra dashboard без tomllib (его нет в stdlib до Python 3.11).

    Закрывающая скобка массива ищется вне кавычек: в ``uvicorn[standard]``
    символ ``]`` стоит внутри строки и массив не заканчивает.
    """
    match = re.search(r"(?m)^dashboard\s*=\s*\[", text)
    assert match, "dashboard extra не найден в pyproject.toml"
    items: list[str] = []
    i = match.end()
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "]":
            return items
        if ch != '"':
            i += 1
            continue
        i += 1
        buf: list[str] = []
        while i < n:
            c = text[i]
            if c == "\\":
                i += 1
                if i >= n:
                    break
                esc = text[i]
                buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(esc, esc))
            elif c == '"':
                items.append("".join(buf))
                break
            else:
                buf.append(c)
            i += 1
        else:
            raise AssertionError("dashboard extra: незакрытая строка в pyproject.toml")
        i += 1
    raise AssertionError("dashboard extra не закрыт в pyproject.toml")


def _dashboard_extra_items(text: str) -> list[str]:
    try:
        import tomllib

        data = tomllib.loads(text)
        return list(data["project"]["optional-dependencies"]["dashboard"])
    except ModuleNotFoundError:
        return _dashboard_extra_from_text(text)


def test_dashboard_extra_matches_requirements_dashboard():
    text = PYPROJECT.read_text(encoding="utf-8")
    req = [
        line.strip()
        for line in REQ_DASH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # tomllib на 3.11+ скрывает ошибку фолбэка; сверяем оба пути.
    assert _dashboard_extra_items(text) == req
    assert _dashboard_extra_from_text(text) == req
    assert "httpx>=0.27" in req


def test_windows_extras_syntax_has_no_dot():
    ps1 = (REPO / "Agent-Init.ps1").read_text(encoding="utf-8")
    assert '"$ProjectRoot.[dev]"' not in ps1
    assert '"$TemplateRoot.[dev]"' not in ps1
    assert '"$TemplateRoot[dev]"' in ps1
    assert '"$ProjectRoot[dev]"' not in ps1
    assert "install','-e'" in ps1 or "install', '-e'" in ps1
    assert "agentic_loop_template" in ps1
    assert "$TemplateRoot" in ps1


def test_console_scripts_include_doctor_and_local_mcp():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'agentix-doctor = "memory.doctor:main"' in text
    assert 'agentix-local-mcp = "memory.local_mcp.__main__:main"' in text
    assert '"local_mcp/schemas/*.json"' in text
    assert (REPO / "memory" / "local_mcp" / "schemas" / "proxy_health.schema.json").is_file()
    assert (REPO / "scripts" / "parallel_scaffold.sh").is_file()
    assert (REPO / "docs" / "live-verify.md").is_file()
