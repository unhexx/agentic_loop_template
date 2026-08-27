# -*- coding: utf-8 -*-
"""Аудит внедрения и apply паттернов в память."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.experience.extract import _read_capped, dedupe
from memory.experience.scan import _iter_playbooks, _loop_state_issues, scan_parent
from memory.store import update_memory

MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
AGENT_DOC_HINT = re.compile(
    r"agent(?:ic)?|playbook|AGENTS\.md|Agent-Init|agentic.loop",
    re.I,
)

def _broken_agent_links(project: Path) -> List[str]:
    issues: List[str] = []
    readme = project / "README.md"
    if not readme.is_file():
        return issues
    text = _read_capped(readme, cap=80_000)
    for _label, href in MD_LINK.findall(text):
        href = href.split("#", 1)[0].strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        if not AGENT_DOC_HINT.search(href) and not AGENT_DOC_HINT.search(_label):
            continue
        target = (project / href).resolve()
        try:
            target.relative_to(project.resolve())
        except ValueError:
            continue
        if not target.exists():
            issues.append(
                f"README in {project.name} links to missing agent doc {href}"
            )
    return issues


def _signals(project: Path) -> Dict[str, Any]:
    return {
        "agents_md": (project / "AGENTS.md").is_file(),
        "playbook": any(True for _ in _iter_playbooks(project)),
        "agent_dir": (project / ".agent").is_dir(),
        "agent_init_sh": (project / "Agent-Init.sh").is_file(),
        "agent_init_ps1": (project / "Agent-Init.ps1").is_file(),
        "loop_state_md": (project / ".agent" / "LOOP_STATE.md").is_file(),
        "loop_state_json": (project / ".agent" / "LOOP_STATE.json").is_file(),
        "plan": (project / ".agent" / "PLAN.md").is_file(),
        "todo": (project / ".agent" / "TODO.md").is_file(),
        "living_plans": {
            name: (project / name).is_file()
            for name in ("TASK_SPECIFICATION.md", "PROJECT_CONTEXT.md", "SPRINTPLAN.md")
        },
        "venv": (project / ".venv").is_dir(),
        "nested_template": (project / "agentic_loop_template").exists(),
        "system_prompt": (project / "SYSTEM_PROMPT.md").is_file(),
        "file_count": sum(
            1 for p in project.iterdir() if p.name not in {".", "..", ".git"}
        ),
    }


def _tier(sig: Dict[str, Any], issues: List[str]) -> str:
    lite = sig["agents_md"] or sig["playbook"]
    docs_gap = any(
        i.startswith("README") and "missing agent doc" in i for i in issues
    ) and not lite
    if docs_gap:
        return "docs_gap"
    if (
        sig["file_count"] <= 2
        and not sig["agents_md"]
        and not sig["agent_dir"]
        and not any(sig["living_plans"].values())
    ):
        return "empty"
    stale = any(
        "Stale LOOP_STATE" in i or "foreign SSOT" in i or "Windows-only" in i
        for i in issues
    )
    fullish = (
        sig["agent_init_sh"] or sig["nested_template"] or sig["agent_init_ps1"]
    ) and sig["agent_dir"]
    if stale and fullish:
        return "stale"
    if fullish and (sig["plan"] or sig["loop_state_json"] or sig["loop_state_md"]):
        return "full"
    if fullish:
        return "partial"
    if lite:
        return "lite"
    if any(sig["living_plans"].values()):
        return "partial"
    return "none"


def audit_project(project: Path) -> Dict[str, Any]:
    sig = _signals(project)
    issues: List[str] = list(_broken_agent_links(project))
    for path in (
        project / ".agent" / "LOOP_STATE.md",
        project / ".agent" / "LOOP_STATE.json",
    ):
        if path.is_file():
            issues.extend(_loop_state_issues(project.name, path, _read_capped(path)))
    if sig["agent_init_ps1"] and not sig["agent_init_sh"]:
        issues.append(
            f"{project.name} has Agent-Init.ps1 but no Agent-Init.sh (Linux host cannot bootstrap)"
        )
    living = sig["living_plans"]
    if any(living.values()) and not sig["agent_dir"] and not sig["agent_init_sh"]:
        issues.append(
            f"{project.name} has living plans but Agent-Init never produced .agent/ state"
        )
    if sig["agent_dir"] and not sig["venv"] and (sig["agent_init_sh"] or sig["nested_template"]):
        issues.append(
            f"{project.name} has .agent/ but no .venv — Agent-Init was not finished on this host"
        )
    if sig["system_prompt"]:
        sp = _read_capped(project / "SYSTEM_PROMPT.md", cap=20_000)
        if re.search(r"Strictly Windows PowerShell only", sp, re.I):
            issues.append(
                f"{project.name} SYSTEM_PROMPT mandates Windows PowerShell only (breaks Linux/Grok cycles)"
            )
    return {
        "project": project.name,
        "tier": _tier(sig, issues),
        "signals": sig,
        "issues": issues,
    }


def audit_parent(parent: Path) -> Dict[str, Any]:
    projects: List[Dict[str, Any]] = []
    if not parent.is_dir():
        return {"parent": str(parent), "projects": [], "summary": {}}
    for child in sorted(parent.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        projects.append(audit_project(child))
    summary: Dict[str, int] = {}
    for row in projects:
        summary[row["tier"]] = summary.get(row["tier"], 0) + 1
    return {
        "parent": str(parent),
        "projects": projects,
        "summary": summary,
        "issue_count": sum(len(p["issues"]) for p in projects),
    }


def patterns_from_audit(report: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in report.get("projects", []):
        for issue in row.get("issues", []):
            out.append(
                {
                    "category": "Common Failure Patterns",
                    "description": issue,
                    "source": f"audit:{row.get('project')}",
                }
            )
    return out

def looks_like_project_parent(parent: Path) -> bool:
    """Родитель похож на _PROJECT: шаблон + хотя бы один соседний продукт."""
    parent = Path(parent)
    if not parent.is_dir():
        return False
    if parent.name == "_PROJECT":
        return True
    try:
        children = [
            p for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")
        ]
    except OSError:
        return False
    if len(children) < 2:
        return False
    names = {p.name for p in children}
    has_template = "agentic_loop_template" in names or any(
        (p / "memory" / "supervisor.py").is_file() for p in children
    )
    has_product = any(
        (p / "AGENTS.md").is_file() or (p / "TASK_SPECIFICATION.md").is_file()
        for p in children
        if p.name != "agentic_loop_template"
    )
    return bool(has_template and has_product)


def maybe_cycle_on_done(workdir: Path, apply: bool = False) -> Optional[Dict[str, Any]]:
    """
    После DONE ревьюера: dry-run cycle по родителю, если рядом sibling-layout.
    apply=False по умолчанию — не пишем память из авто-хука.
    """
    workdir = Path(workdir).resolve()
    parent = workdir.parent
    if not looks_like_project_parent(parent):
        return None
    scanned = scan_parent(parent)
    report = audit_parent(parent)
    rows = dedupe(scanned + patterns_from_audit(report))
    payload: Dict[str, Any] = {
        "parent": str(parent),
        "dry_run": not apply,
        "pattern_count": len(rows),
        "issue_count": report.get("issue_count"),
        "projects": [
            {"project": p["project"], "tier": p["tier"]}
            for p in report.get("projects", [])
        ],
    }
    if apply:
        payload["applied"] = apply_patterns(rows)
    else:
        payload["sample"] = rows[:10]
    return payload


def apply_patterns(patterns: List[Dict[str, str]]) -> Dict[str, Any]:
    clean = dedupe(patterns)
    if not clean:
        return {"patterns_merged": 0, "unique": 0}
    payload = [{"category": p["category"], "description": p["description"]} for p in clean]
    result = update_memory(new_patterns=payload)
    result["unique_submitted"] = len(clean)
    return result
