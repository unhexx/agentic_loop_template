# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from memory.doctor import _delegate_env, doctor_report, cli


def test_delegate_env_flags_base_url(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GEMINI_BASE_URL", raising=False)
    assert _delegate_env()["p10_safe"] is True
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://127.0.0.1:8101")
    env = _delegate_env()
    assert env["p10_safe"] is False
    assert env["google_gemini_base_url_set"] is True


def test_doctor_report_structure(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GOOGLE_GEMINI_BASE_URL", raising=False)
    (tmp_path / "deploy").mkdir()
    # stack may fail contract without files; still returns structured report
    report = doctor_report(tmp_path, include_stack=False)
    assert "proxy" in report
    assert "ports" in report
    assert isinstance(report["ports"], list)
    assert any(p["name"] == "pxpipe" and p["port"] == 8100 for p in report["ports"])
    assert "findings" in report
    assert "guidance" in report


def test_doctor_cli_json(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_GEMINI_BASE_URL", raising=False)
    code = cli(["--workdir", str(tmp_path), "--no-stack"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "ok" in data
    assert code in (0, 1)
