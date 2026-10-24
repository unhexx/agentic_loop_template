# -*- coding: utf-8 -*-
"""Одна команда ставит потребительский проект: файлы, симлинк, без вендоринга дерева."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from memory.init_project import InitError, init_project, ritual_argv, venv_python

REPO = Path(__file__).resolve().parents[1]


def _init(target: Path, **kwargs):
    kwargs.setdefault("template", REPO)
    kwargs.setdefault("venv", False)
    kwargs.setdefault("ritual", False)
    kwargs.setdefault("quiet", True)
    return init_project(target, **kwargs)


def test_full_scaffold_fills_files_and_symlink(tmp_path: Path):
    target = tmp_path / "billing"
    result = _init(target, name="billing", goal="Ship invoices", frontend="grok", stack="Python")
    assert result["ok"] is True
    assert (target / "agentic_loop_template").is_symlink()
    assert (target / "agentic_loop_template").resolve() == REPO.resolve()
    agents = (target / "AGENTS.md").read_text(encoding="utf-8")
    spec = (target / "TASK_SPECIFICATION.md").read_text(encoding="utf-8")
    assert "{{" not in agents
    assert "{{" not in spec
    assert "Ship invoices" in agents
    assert "Ship invoices" in spec
    assert "Python" in (target / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")
    cfg = json.loads((target / ".agent" / "project_config.json").read_text(encoding="utf-8"))
    assert cfg["project"]["name"] == "billing"
    assert cfg["project"]["frontend"] == "grok"
    assert cfg["project"]["tier"] == "full"
    assert cfg["supervisor"]["adapter"] == "grok"
    assert "question_pool" in cfg
    ignore = (target / ".gitignore").read_text(encoding="utf-8")
    # A trailing slash ignores a directory, not the SSOT symlink.
    assert [ln for ln in ignore.splitlines() if ln.strip() == "agentic_loop_template"] == [
        "agentic_loop_template"
    ]
    assert "agentic_loop_template/" not in ignore
    assert ".agent/stream_leases.json" in ignore
    assert "billing" in (target / ".env.agentic").read_text(encoding="utf-8")
    assert (target / "Agent-Init.sh").stat().st_mode & 0o111
    assert (target / ".agent" / "PLAN.md").is_file()
    assert (target / ".agent" / "starter_prompt_grok.txt").is_file()
    assert "agentic_loop_template" not in [
        p.name for p in target.iterdir() if p.is_dir() and not p.is_symlink()
    ]


def test_second_run_skips_and_gitignore_once(tmp_path: Path):
    target = tmp_path / "app"
    _init(target, name="app")
    again = _init(target, name="app", goal="Changed")
    assert "AGENTS.md" in again["skipped"]
    assert "TASK_SPECIFICATION.md" in again["skipped"]
    assert ".gitignore" in again["skipped"]
    assert "Changed" not in (target / "AGENTS.md").read_text(encoding="utf-8")
    ignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert [ln for ln in ignore.splitlines() if ln.strip() == "agentic_loop_template"] == [
        "agentic_loop_template"
    ]


def test_force_rewrites_goal_and_claude_stays_mock(tmp_path: Path):
    target = tmp_path / "app"
    _init(target, name="app", goal="First")
    _init(target, name="app", goal="Second", frontend="claude", force=True)
    assert "Second" in (target / "TASK_SPECIFICATION.md").read_text(encoding="utf-8")
    cfg = json.loads((target / ".agent" / "project_config.json").read_text(encoding="utf-8"))
    assert cfg["supervisor"]["adapter"] == "mock"
    assert cfg["project"]["frontend"] == "claude"
    assert (target / ".env.agentic").is_file()


def test_lite_skips_loop_docs(tmp_path: Path):
    target = tmp_path / "lite"
    _init(target, name="lite", tier="lite")
    assert (target / "AGENTS.md").is_file()
    assert not (target / "TASK_SPECIFICATION.md").exists()
    assert not (target / "Agent-Init.sh").exists()
    assert not (target / ".agent" / "PLAN.md").exists()
    cfg = json.loads((target / ".agent" / "project_config.json").read_text(encoding="utf-8"))
    assert cfg["project"]["tier"] == "lite"
    assert cfg["supervisor"]["adapter"] == "grok"


def test_refuses_template_itself_and_foreign_tree(tmp_path: Path):
    with pytest.raises(InitError):
        _init(REPO, name="nope")
    foreign = tmp_path / "proj"
    foreign.mkdir()
    (foreign / "agentic_loop_template").mkdir()
    with pytest.raises(InitError):
        _init(foreign, name="proj")


def test_refuses_when_template_is_inside_target(tmp_path: Path):
    with pytest.raises(InitError):
        _init(REPO.parent, name="parent")


def test_preserves_existing_gitignore_prefix(tmp_path: Path):
    target = tmp_path / "app"
    target.mkdir()
    (target / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _init(target, name="app")
    text = (target / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n")
    assert "\nagentic_loop_template\n" in text
    assert "agentic_loop_template/" not in text


def test_ritual_argv_matches_cold_start():
    commands = ritual_argv(Path("python"), "grok")
    flat = [" ".join(cmd) for cmd in commands]
    for token in (
        "memory state init",
        "knowledge ingest-if-empty",
        "playbooks seed --from-standards",
        "proxy install-venv",
        "memory.proxy health --init --frontend grok",
    ):
        assert any(token in line for line in flat), token


def test_docs_advertise_one_command():
    for rel in (
        "README.md",
        "README.ru.md",
        "examples/consumer-starter/README.md",
        "docs/getting-started.md",
        "docs/ru/getting-started.md",
        "docs/onboarding-wizard.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "scripts/agentix-init.sh" in text, rel


def test_venv_installs_editable_then_ritual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "proj"
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, **kwargs):
        argv = [str(part) for part in cmd]
        calls.append((argv, kwargs.get("cwd")))
        if "-m" in argv and "venv" in argv:
            py = venv_python(target)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/bin/sh\n", encoding="utf-8")
            py.chmod(0o755)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = init_project(
        target,
        template=REPO,
        name="proj",
        venv=True,
        ritual=True,
        quiet=True,
        python="python3",
    )
    assert result["venv"] == str(venv_python(target))
    joined = [" ".join(argv) for argv, _cwd in calls]
    assert any(line.endswith("-m venv " + str(target / ".venv")) or " -m venv " in line for line in joined)
    assert any("-e" in line and f"{REPO}[dev]" in line for line in joined)
    ritual = [cwd for argv, cwd in calls if "memory" in argv and "state" in argv]
    assert ritual == [target.resolve()]


def test_cli_ok_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from memory.init_project import cli

    code = cli(
        [
            "--template",
            str(REPO),
            "--target",
            str(tmp_path / "cli-app"),
            "--name",
            "cli-app",
            "--no-venv",
            "--no-ritual",
            "--quiet",
            "--frontend",
            "blackbox",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "AGENT_INIT_OK" in out
    assert "frontend=blackbox" in out
    cfg = json.loads((tmp_path / "cli-app" / ".agent" / "project_config.json").read_text(encoding="utf-8"))
    assert cfg["supervisor"]["adapter"] == "blackbox"
