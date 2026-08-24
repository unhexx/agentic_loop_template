# -*- coding: utf-8 -*-
"""Контракт холодного старта: Init.sh и Init.ps1 вызывают один и тот же ритуал."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SH = REPO / "Agent-Init.sh"
PS1 = REPO / "Agent-Init.ps1"


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
