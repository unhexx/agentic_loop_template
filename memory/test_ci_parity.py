# -*- coding: utf-8 -*-
"""Jenkinsfile at repo root must stay in lockstep with GitHub Actions."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GHA = REPO / ".github" / "workflows" / "agentix-loop.yml"
JENKINS = REPO / "Jenkinsfile"

# Commands both CIs must run. Keep in sync with agentix-loop.yml.
SHARED = (
    'pip install -e ".[dev,dashboard]"',
    "cd /tmp",
    "env -u PYTHONPATH",
    "import memory, memory.supervisor, memory.validate_handoff",
    "python -m pytest -q memory/",
    "memory/test_supervisor_mock_cycle.py",
    "import httpx, fastapi",
    "memory/test_dashboard_security.py",
    "memory/test_dashboard_ws.py",
    "bash Agent-Init.sh",
    "playbooks seed --from-standards",
    'pip install -e ".[dev]"',
    "--collect-only",
    "memory/test_supervisor_fsm.py",
)


def test_jenkinsfile_exists_at_repo_root():
    assert JENKINS.is_file(), "Jenkinsfile must live at repo root (Bitbucket Test configuration)"
    assert JENKINS.name == "Jenkinsfile"


def test_jenkinsfile_mirrors_github_actions():
    gha = GHA.read_text(encoding="utf-8")
    jf = JENKINS.read_text(encoding="utf-8")
    missing_gha = [t for t in SHARED if t not in gha]
    missing_jf = [t for t in SHARED if t not in jf]
    assert not missing_gha, f"agentix-loop.yml missing {missing_gha}"
    assert not missing_jf, f"Jenkinsfile missing {missing_jf}"
