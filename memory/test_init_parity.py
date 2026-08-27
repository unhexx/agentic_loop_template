# -*- coding: utf-8 -*-
"""Контракт холодного старта: Init.sh и Init.ps1 вызывают один и тот же ритуал."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SH = REPO / "Agent-Init.sh"
PS1 = REPO / "Agent-Init.ps1"
WIN_PY = REPO / "scripts" / "windows" / "Init-Python.ps1"
WIN_PR = REPO / "scripts" / "windows" / "Init-Prompt.ps1"


def _scripts() -> tuple[str, str]:
    sh = SH.read_text(encoding="utf-8")
    ps1 = PS1.read_text(encoding="utf-8")
    return sh, ps1


def test_shared_cold_start_ritual():
    sh, ps1 = _scripts()
    for token in (
        "memory state init",
        "knowledge ingest-if-empty",
        "playbooks seed",
        "proxy install-venv",
    ):
        assert token in sh, f"нет {token!r} в Agent-Init.sh"
        assert token in ps1, f"нет {token!r} в Agent-Init.ps1"


def test_unix_editable_install_and_jsonschema_fallback():
    sh = SH.read_text(encoding="utf-8")
    assert 'pip install -e ".[dev]"' in sh
    assert "|| python -m pip install" in sh
    assert "jsonschema" in sh


def test_windows_editable_install_extras_syntax():
    # не ищем 'pip install -e' — эта фраза уже есть в справке хелперов
    ps1 = PS1.read_text(encoding="utf-8")
    assert '"$ProjectRoot[dev]"' in ps1 or '"$TemplateRoot[dev]"' in ps1
    assert "install','-e'" in ps1 or "install', '-e'" in ps1
    assert '"$ProjectRoot.[dev]"' not in ps1
    assert '"$TemplateRoot.[dev]"' not in ps1


def test_wizard_flags_and_playbooks_from_standards():
    sh, ps1 = _scripts()
    assert "--wizard" in sh
    assert "[switch]$Wizard" in ps1
    assert "playbooks seed --from-standards" in sh
    assert "playbooks seed --from-standards" in ps1
    assert 'INIT_FE="grok"' in sh
    assert '$initFe = "grok"' in ps1


def test_windows_dotsource_helpers_and_bom():
    assert WIN_PY.is_file()
    assert WIN_PR.is_file()
    raw = PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    ps1 = PS1.read_text(encoding="utf-8-sig")
    assert "Init-Python.ps1" in ps1
    assert "Init-Prompt.ps1" in ps1
    assert "install','-e'" in ps1 or "install', '-e'" in ps1
    py = WIN_PY.read_text(encoding="utf-8")
    assert "memory state init" not in py
    assert "playbooks seed" not in py


def test_init_line_caps():
    def _nl(path: Path) -> int:
        return path.read_text(encoding="utf-8-sig").count("\n") + 1

    n_ps1 = _nl(PS1)
    assert n_ps1 <= 400, f"Agent-Init.ps1 {n_ps1} > 400"
    for path in (WIN_PY, WIN_PR):
        n = _nl(path)
        assert n <= 450, f"{path.name} {n} > 450"
