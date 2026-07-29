# -*- coding: utf-8 -*-
"""
Cross-project experience extraction → structured memory patterns.

Scans sibling projects under a parent directory for LESSONS / SELF_IMPROVEMENT
artifacts, normalizes bullets, and feeds Common Failure Patterns + Effective Strategies.

Usage:
  python -m memory.experience_harvester scan --parent /path/to/_PROJECT --apply
  python -m memory.experience_harvester seed-defaults --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .schema import normalize
from .store import update_memory

# High-value seeds distilled from eegent/classifier/template production use
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
]


LESSON_GLOBS = (
    ".agent/LESSONS.md",
    "SELF_IMPROVEMENT_LOG.md",
    ".agent/SELF_IMPROVEMENT_LOG.md",
)


def _extract_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ", "• ")):
            body = s[2:].strip()
            # Skip pure metrics / empty / template scaffolding
            if len(body) < 20 or len(body) > 300:
                continue
            low = body.lower()
            if low.startswith(("context:", "observation:", "root cause:", "**lesson id**", "**context**", "**observation**", "**recommendation**", "**date**")):
                continue
            if "short memorable name" in low or "when/where observed" in low:
                continue
            bullets.append(body)
        # Recommendation lines
        m = re.match(r"^\*\*Recommendation\*\*:\s*(.+)$", s, re.I)
        if m:
            body = m.group(1).strip()
            if 20 <= len(body) <= 300:
                bullets.append(body)
    return bullets


def scan_parent(parent: Path, max_files: int = 40) -> List[Dict[str, str]]:
    patterns: List[Dict[str, str]] = []
    if not parent.is_dir():
        return patterns
    count_files = 0
    for child in sorted(parent.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for rel in LESSON_GLOBS:
            path = child / rel
            if not path.is_file():
                continue
            count_files += 1
            if count_files > max_files:
                return patterns
            try:
                # Cap read to avoid huge LESSONS
                text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
            except Exception:
                continue
            for b in _extract_bullets(text):
                cat = (
                    "Effective Loop Strategies"
                    if re.search(r"\b(always|prefer|use|strategy|narrow)\b", b, re.I)
                    else "Common Failure Patterns"
                )
                if re.search(r"\b(never|skip|forgot|error|fail|avoid)\b", b, re.I):
                    cat = "Common Failure Patterns"
                patterns.append({"category": cat, "description": b, "source": str(path)})
    return patterns


def dedupe(patterns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for p in patterns:
        key = (p.get("category", ""), normalize(p.get("description", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"category": p["category"], "description": p["description"]})
    return out


def apply_patterns(patterns: List[Dict[str, str]]) -> Dict[str, Any]:
    clean = dedupe(patterns)
    if not clean:
        return {"patterns_merged": 0, "unique": 0}
    result = update_memory(new_patterns=clean)
    result["unique_submitted"] = len(clean)
    return result


def cli(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-project experience harvester")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan parent directory for lessons")
    p_scan.add_argument("--parent", type=Path, required=True)
    p_scan.add_argument("--apply", action="store_true")
    p_scan.add_argument("--limit", type=int, default=100)

    p_seed = sub.add_parser("seed-defaults", help="Seed high-value template patterns")
    p_seed.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "seed-defaults":
        rows = DEFAULT_SEEDS
        if args.apply:
            print(json.dumps(apply_patterns(rows), ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"dry_run": True, "count": len(rows), "patterns": rows}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "scan":
        found = scan_parent(args.parent)
        found = dedupe(found)[: args.limit]
        if args.apply:
            print(json.dumps(apply_patterns(found), ensure_ascii=False, indent=2))
        else:
            print(
                json.dumps(
                    {"dry_run": True, "count": len(found), "sample": found[:15]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(cli())
