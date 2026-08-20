# -*- coding: utf-8 -*-
"""Тесты кросс-проектного harvester: playbook scan, audit, cycle."""

from __future__ import annotations

import json
from pathlib import Path

from memory.experience_harvester import (
    DEFAULT_SEEDS,
    audit_parent,
    dedupe,
    scan_parent,
    cli,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_reads_agents_and_playbook(tmp_path: Path) -> None:
    parent = tmp_path / "_PROJECT"
    vault = parent / "contact-vault"
    _write(
        vault / "docs" / "06-ENGINEERING" / "Agent-Playbook.md",
        """# Agent Playbook

## What agents must NOT do
- Never invent a fact without attaching Provenance
- Do not commit real personal data from live OSINT sources

## Guiding Principles
- Contracts first. Prefer changing Zod schemas before writing UI.
""",
    )
    grok = parent / "telegrok"
    _write(
        grok / "AGENTS.md",
        """# AGENTS.md
## Boundaries — NEVER do the following
- Never hard-code secrets, tokens or hostnames
- Do not expose the agent host on a public IP without Tailscale
""",
    )
    found = scan_parent(parent)
    descs = " ".join(p["description"] for p in found)
    assert "Provenance" in descs
    assert "hard-code secrets" in descs
    assert any(p["category"] == "Common Failure Patterns" for p in found)


def test_audit_docs_gap_and_stale_loop(tmp_path: Path) -> None:
    parent = tmp_path / "_PROJECT"
    signet = parent / "signet"
    _write(
        signet / "README.md",
        "See [playbook](docs/AGENT_PLAYBOOK.md) for agentic loops.\n",
    )
    clf = parent / "classifier"
    _write(clf / "Agent-Init.ps1", "# windows only\n")
    _write(
        clf / ".agent" / "LOOP_STATE.md",
        "current_worktree: C:\\Users\\ROOT\\.grok\\worktrees\\localrepo-agentic-loop-template\n",
    )
    empty = parent / "localhost"
    empty.mkdir()

    report = audit_parent(parent)
    by_name = {p["project"]: p for p in report["projects"]}
    assert by_name["signet"]["tier"] == "docs_gap"
    assert any("missing agent doc" in i for i in by_name["signet"]["issues"])
    assert by_name["classifier"]["tier"] == "stale"
    assert any("Windows host paths" in i for i in by_name["classifier"]["issues"])
    assert any("Agent-Init.sh" in i for i in by_name["classifier"]["issues"])
    assert by_name["localhost"]["tier"] == "empty"


def test_cycle_cli_dry_run(tmp_path: Path, capsys) -> None:
    parent = tmp_path / "_PROJECT"
    proj = parent / "demo"
    _write(
        proj / "AGENTS.md",
        "## Boundaries\n- Never commit .env, tokens, private keys or real user IDs\n",
    )
    rc = cli(["cycle", "--parent", str(parent), "--no-seeds"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["pattern_count"] >= 1
    assert out["projects"][0]["project"] == "demo"


def test_seeds_dedupe_grows() -> None:
    rows = dedupe(DEFAULT_SEEDS + DEFAULT_SEEDS)
    assert len(rows) == len(DEFAULT_SEEDS)
    assert len(rows) >= 30
    cats = {p["category"] for p in rows}
    assert "Project Playbook Patterns" in cats
    assert "Meta Improvement Patterns" in cats
