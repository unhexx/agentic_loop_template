# -*- coding: utf-8 -*-
"""Сканирование соседних репозиториев (без audit)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

from memory.experience.extract import (
    _classify,
    _extract_bullets,
    _extract_heading_rules,
    _extract_never_lines,
    _read_capped,
)

LESSON_GLOBS = (
    ".agent/LESSONS.md",
    "SELF_IMPROVEMENT_LOG.md",
    ".agent/SELF_IMPROVEMENT_LOG.md",
)

NAMED_SOURCES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "TIPS_AND_TRICKS.md",
    "PROJECT_CONTEXT.md",
    "SPRINTPLAN.md",
    "TASK_SPECIFICATION.md",
    "SYSTEM_PROMPT.md",
    "SELF_IMPROVEMENT_LOG.md",
    ".agent/LESSONS.md",
    ".agent/SELF_IMPROVEMENT_LOG.md",
    ".agent/PLAN.md",
    ".agent/TODO.md",
)

PLAYBOOK_BASENAMES = {
    "agent-playbook.md",
    "agent_playbook.md",
    "agentic_loop.md",
    "agents.md",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".turbo",
    "dist",
    "build",
    ".pytest_cache",
    "aq_classifier.egg-info",
}

WIN_PATH = re.compile(r"(?:C:\\Users\\|C:/_PROJECT|C:\\_PROJECT|\\\\Users\\\\)", re.I)
FOREIGN_LOOP = re.compile(
    r"localrepo-agentic-loop-template|agent-loop-template-upgrade-loop",
    re.I,
)

def _iter_playbooks(root: Path, max_depth: int = 4) -> Iterable[Path]:
    root = root.resolve()
    for path in root.rglob("*"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) > max_depth:
            continue
        if any(p in SKIP_DIRS for p in rel.parts):
            continue
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in PLAYBOOK_BASENAMES:
            yield path
        elif "playbook" in name and name.endswith(".md") and "agent" in name:
            yield path


def _source_files(project: Path) -> List[Path]:
    files: List[Path] = []
    seen = set()
    for rel in NAMED_SOURCES:
        p = project / rel
        if p.is_file() and p not in seen:
            files.append(p)
            seen.add(p)
    for p in _iter_playbooks(project):
        if p not in seen:
            files.append(p)
            seen.add(p)
    return files


def scan_parent(parent: Path, max_files: int = 80) -> List[Dict[str, str]]:
    patterns: List[Dict[str, str]] = []
    if not parent.is_dir():
        return patterns
    count_files = 0
    for child in sorted(parent.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for path in _source_files(child):
            count_files += 1
            if count_files > max_files:
                return patterns
            text = _read_capped(path)
            if not text.strip():
                continue
            chunks = (
                _extract_bullets(text)
                + _extract_heading_rules(text)
                + _extract_never_lines(text)
            )
            for b in chunks:
                patterns.append(
                    {
                        "category": _classify(b),
                        "description": b,
                        "source": str(path),
                    }
                )
            for issue in _loop_state_issues(child.name, path, text):
                patterns.append(
                    {
                        "category": "Common Failure Patterns",
                        "description": issue,
                        "source": str(path),
                    }
                )
    return patterns


def _loop_state_issues(project: str, path: Path, text: str) -> List[str]:
    if path.name not in {"LOOP_STATE.md", "LOOP_STATE.json"}:
        return []
    issues: List[str] = []
    if WIN_PATH.search(text):
        issues.append(
            f"Stale LOOP_STATE in {project} contains Windows host paths (copy-paste from another machine)"
        )
    if FOREIGN_LOOP.search(text) and project != "agentic_loop_template":
        issues.append(
            f"LOOP_STATE in {project} still points at agentic_loop_template worktree paths (foreign SSOT leak)"
        )
    return issues
