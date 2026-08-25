# -*- coding: utf-8 -*-
"""Routing tests for tools/select.py skill intents (harvest vs reflect)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_select():
    path = REPO / "tools" / "select.py"
    spec = importlib.util.spec_from_file_location("agentix_select", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _relpaths(intent: str, os_name: str = "linux") -> list[str]:
    sel = _load_select()
    return [p.relative_to(REPO).as_posix() for p in sel.resolve_paths(intent, os_name)]


def test_harvest_intent_loads_experience_accumulation_not_reflect() -> None:
    paths = _relpaths("harvest")
    assert "tools/blocks/common/experience.md" in paths
    assert "skills/experience-accumulation/SKILL.md" in paths
    assert "skills/reflective-improvement/SKILL.md" not in paths
    assert "skills/loop-self-improve/SKILL.md" not in paths


def test_reflect_intent_loads_loop_self_improve_not_harvest() -> None:
    paths = _relpaths("reflect")
    assert "skills/loop-self-improve/SKILL.md" in paths
    assert "skills/experience-accumulation/SKILL.md" not in paths
    assert "skills/reflective-improvement/SKILL.md" not in paths


def test_git_intent_does_not_load_jira_or_harvest_skills() -> None:
    joined = " ".join(_relpaths("git"))
    assert "git-commit-to-jira-tasks" not in joined
    assert "experience-accumulation" not in joined
    assert "loop-self-improve" not in joined


def test_skill_files_exist_and_names_match() -> None:
    for rel, name in (
        ("skills/experience-accumulation/SKILL.md", "experience-accumulation"),
        ("skills/loop-self-improve/SKILL.md", "loop-self-improve"),
        ("skills/reflective-improvement/SKILL.md", "reflective-improvement"),
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"name: {name}" in text.split("---", 2)[1]


def test_reflective_improvement_does_not_own_harvest_cycle() -> None:
    text = (REPO / "skills/reflective-improvement/SKILL.md").read_text(encoding="utf-8")
    assert "experience_harvester cycle" not in text
