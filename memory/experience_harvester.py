# -*- coding: utf-8 -*-
"""
Кросс-проектный сбор опыта агентов → структурированные паттерны памяти.

Сканирует соседние репозитории не только по LESSONS.md (в текущем дереве
_PROJECT их нет), но и по AGENTS.md, playbook, CONTRIBUTING, живым планам,
LOOP_STATE (дрейф) и битым ссылкам из README.

Использование:
  python -m memory.experience_harvester scan --parent /path/to/_PROJECT --apply
  python -m memory.experience_harvester audit --parent /path/to/_PROJECT
  python -m memory.experience_harvester cycle --parent /path/to/_PROJECT --apply
  python -m memory.experience_harvester seed-defaults --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .schema import normalize
from .store import update_memory

# Высоценные семена: eegent/classifier (2026-07) + актуальное дерево _PROJECT (2026-08-20)
DEFAULT_SEEDS: List[Dict[str, str]] = [
    {
        "category": "Common Failure Patterns",
        "description": "Never read full .agent/LOOP_STATE archives into context — use `python -m memory state snapshot`",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Using bare python/python3 instead of project .venv interpreter",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Skipping Agent-Init after pull or on new worktree",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Forgetting machine-verifiable SYNC_DONE / git_sync_status.verified after merge",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Loading entire TOOLS_INSTRUCTIONS monologue — use tools/select.py progressive blocks",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Oversized multi-file refactors in one ACT wave (narrow 1-3 file slices win)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Appending free-form Sprint Eval text to LOOP_STATE instead of metrics.jsonl",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Stale copy-pasted LOOP_STATE from another project (paths/dates from foreign hosts)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Role collapse: skipping Tester/Reviewer gates when acting as multi-role alone",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Simulate/smoke paths writing durable .agent state on main clone without restore",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Narrow INVEST slice + explicit success criteria + machine-checkable markers",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Delta handoffs (summary + context_delta + links) instead of restating DEVELOPMENT_STANDARDS",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Orchestrator starts with state snapshot + memory query top-5 failures before PLAN",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Parallel workstreams only with owned_paths contracts and worktree isolation",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Git preflight via single script; full multi-repo gh ritual only when template files change",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "Cold-start: 1-2 paragraph compressed state + pointers to files; on-demand read only",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "TOOLS via selector by intent (git|test|memory|docker) not full registry paste",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "After DONE + high confidence: meta_harvester harvest then propose safe few-shots",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "Compact .agent bloat every Reviewer cycle when LESSONS/DONE/LOOP exceed thresholds",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Windows-only PowerShell blocks on Linux hosts — use tools/blocks/linux/*",
    },
    # --- 2026-08-20 harvest across current _PROJECT/* ---
    {
        "category": "Common Failure Patterns",
        "description": "Experience harvest that only reads LESSONS.md / SELF_IMPROVEMENT_LOG.md returns empty on product trees — also scan AGENTS.md, playbooks, CONTRIBUTING, living plans",
    },
    {
        "category": "Common Failure Patterns",
        "description": "README advertises docs/AGENT_PLAYBOOK.md or AGENTIC_LOOP.md that were never written (docs_gap: signet, nesttunnel)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Living plans filled without finishing Agent-Init: no .venv, no LOOP_STATE, incomplete .agent (telegrok-style incomplete adoption)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Consumer copied only Agent-Init.ps1 — Linux host has no Agent-Init.sh (classifier drift)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "SYSTEM_PROMPT still mandates Windows PowerShell / MiniMax while the active host is Linux + Grok",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Copying the entire agentic_loop_template tree into a product instead of a sibling symlink + PYTHONPATH",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Forcing full O→C→T→D→R on a product that only needs AGENTS.md + Definition of Done + exact commands",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Two-tier adoption: lite AGENTS.md/playbook for products; full loop only when autonomous multi-cycle work is in scope",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Consumer Agent-Init.sh should symlink ../agentic_loop_template and export PYTHONPATH to SSOT memory (do not vendor a stale copy)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Project-specific playbook: contracts-first, fixture tests, provenance, explicit MUST NOT list (contact-vault pattern)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Put exact install/lint/test/run commands in AGENTS.md so agents do not invent toolchains (telegrok uv/ruff/mypy/pytest)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "English product docs + natural-Russian commits/comments on loop artifacts; never mention models in git",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "Contracts first: change Zod/Prisma/tRPC (or equivalent schema) before UI or ad-hoc logic",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "Never invent a fact without provenance; never commit real PII or secrets; synthetic fixtures only",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "One logical change per PR — do not mix parser/domain changes with UI restyling",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "Once per parent-folder session: python -m memory.experience_harvester cycle --parent <_PROJECT> --apply",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "Cold SYSTEM_PROMPT is platform-adaptive (Linux bash Agent-Init.sh default; PowerShell only on Windows)",
    },
]


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

HEADING_HINTS = re.compile(
    r"boundar|must not|never do|definition of done|guiding principle|"
    r"permanent rule|coding standard|preferred development|what agents|"
    r"hard constraint|exact command|key decision|do not|never |"
    r"neural-network agent|getting unstuck|working on the parser|commit & pr",
    re.I,
)

NEVER_LINE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])\s+(?:never |do not |don't |must not |запрещ|нельзя |never\b)",
    re.I,
)

MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")
WIN_PATH = re.compile(r"(?:C:\\Users\\|C:/_PROJECT|C:\\_PROJECT|\\\\Users\\\\)", re.I)
FOREIGN_LOOP = re.compile(
    r"localrepo-agentic-loop-template|agent-loop-template-upgrade-loop",
    re.I,
)
AGENT_DOC_HINT = re.compile(
    r"agent(?:ic)?|playbook|AGENTS\.md|Agent-Init|agentic.loop",
    re.I,
)


def _extract_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ", "• ")):
            body = s[2:].strip()
            if len(body) < 20 or len(body) > 300:
                continue
            low = body.lower()
            if low.startswith(
                (
                    "context:",
                    "observation:",
                    "root cause:",
                    "**lesson id**",
                    "**context**",
                    "**observation**",
                    "**recommendation**",
                    "**date**",
                )
            ):
                continue
            if "short memorable name" in low or "when/where observed" in low:
                continue
            if PLACEHOLDER.search(body):
                continue
            bullets.append(body)
        m = re.match(r"^\*\*Recommendation\*\*:\s*(.+)$", s, re.I)
        if m:
            body = m.group(1).strip()
            if 20 <= len(body) <= 300:
                bullets.append(body)
    return bullets


def _strip_md(s: str) -> str:
    s = re.sub(r"[*`_]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_heading_rules(text: str) -> List[str]:
    """Правила из секций Boundaries / MUST NOT / DoD / Permanent Rules."""
    lines = text.splitlines()
    out: List[str] = []
    capture = False
    for raw in lines:
        s = raw.strip()
        if s.startswith("#"):
            title = re.sub(r"^#+\s*", "", s)
            capture = bool(HEADING_HINTS.search(title))
            continue
        if not capture:
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)]|\[[ xX]\])\s+(.*)$", s)
        if not m:
            continue
        body = _strip_md(m.group(1))
        if body.startswith("[ ]") or body.startswith("[x]") or body.startswith("[X]"):
            body = body[3:].strip()
        if 20 <= len(body) <= 300 and not PLACEHOLDER.search(body):
            out.append(body)
    return out


def _extract_never_lines(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        if not NEVER_LINE.search(raw):
            continue
        body = _strip_md(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw.strip()))
        if 20 <= len(body) <= 300:
            out.append(body)
    return out


def _classify(text: str) -> str:
    if re.search(r"\b(never|skip|forgot|error|fail|avoid|must not|do not)\b", text, re.I):
        return "Common Failure Patterns"
    if re.search(
        r"\b(always|prefer|use|strategy|narrow|exact command|contracts first)\b",
        text,
        re.I,
    ):
        return "Effective Loop Strategies"
    if re.search(r"\b(schema|provenance|fixture|zod|pii|secret|allowlist)\b", text, re.I):
        return "Project Playbook Patterns"
    return "Effective Loop Strategies"


def _read_capped(path: Path, cap: int = 200_000) -> str:
    try:
        raw = path.read_bytes()[:cap]
    except OSError:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16", errors="replace")
        except Exception:
            return ""
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return text


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


def dedupe(patterns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for p in patterns:
        key = (p.get("category", ""), normalize(p.get("description", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        item = {"category": p["category"], "description": p["description"]}
        if p.get("source"):
            item["source"] = str(p["source"])
        out.append(item)
    return out


def apply_patterns(patterns: List[Dict[str, str]]) -> Dict[str, Any]:
    clean = dedupe(patterns)
    if not clean:
        return {"patterns_merged": 0, "unique": 0}
    payload = [{"category": p["category"], "description": p["description"]} for p in clean]
    result = update_memory(new_patterns=payload)
    result["unique_submitted"] = len(clean)
    return result


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-project experience harvester")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan parent directory for lessons and playbooks")
    p_scan.add_argument("--parent", type=Path, required=True)
    p_scan.add_argument("--apply", action="store_true")
    p_scan.add_argument("--limit", type=int, default=100)

    p_seed = sub.add_parser("seed-defaults", help="Seed high-value template patterns")
    p_seed.add_argument("--apply", action="store_true")

    p_audit = sub.add_parser("audit", help="Adoption health report for sibling projects")
    p_audit.add_argument("--parent", type=Path, required=True)
    p_audit.add_argument("--apply", action="store_true", help="Merge audit issues into memory")

    p_cycle = sub.add_parser(
        "cycle",
        help="Self-improve cycle: seed + scan playbooks + audit, optionally apply",
    )
    p_cycle.add_argument("--parent", type=Path, required=True)
    p_cycle.add_argument("--apply", action="store_true")
    p_cycle.add_argument("--limit", type=int, default=100)
    p_cycle.add_argument("--no-seeds", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "seed-defaults":
        rows = DEFAULT_SEEDS
        if args.apply:
            _print(apply_patterns(rows))
        else:
            _print({"dry_run": True, "count": len(rows), "patterns": rows})
        return 0

    if args.cmd == "scan":
        found = dedupe(scan_parent(args.parent))[: args.limit]
        if args.apply:
            _print(apply_patterns(found))
        else:
            _print({"dry_run": True, "count": len(found), "sample": found[:20]})
        return 0

    if args.cmd == "audit":
        report = audit_parent(args.parent)
        if args.apply:
            merged = apply_patterns(patterns_from_audit(report))
            report["applied"] = merged
        _print(report)
        return 0

    if args.cmd == "cycle":
        scanned = scan_parent(args.parent)
        report = audit_parent(args.parent)
        rows: List[Dict[str, str]] = []
        if not args.no_seeds:
            rows.extend(DEFAULT_SEEDS)
        rows.extend(scanned)
        rows.extend(patterns_from_audit(report))
        rows = dedupe(rows)[: args.limit]
        payload: Dict[str, Any] = {
            "audit_summary": report.get("summary"),
            "issue_count": report.get("issue_count"),
            "pattern_count": len(rows),
            "projects": [
                {"project": p["project"], "tier": p["tier"], "issues": p["issues"]}
                for p in report.get("projects", [])
            ],
        }
        if args.apply:
            payload["applied"] = apply_patterns(rows)
        else:
            payload["dry_run"] = True
            payload["sample"] = rows[:20]
        _print(payload)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(cli())
