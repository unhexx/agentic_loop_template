# -*- coding: utf-8 -*-
"""Публичный загрузчик кросс-проектного сбора опыта. Тела — memory.experience.*."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "DEFAULT_SEEDS",
    "audit_parent",
    "dedupe",
    "looks_like_project_parent",
    "maybe_cycle_on_done",
    "scan_parent",
    "cli",
]

_SEEDS = {"DEFAULT_SEEDS"}
_EXTRACT = {"dedupe"}
_SCAN = {"scan_parent"}
_AUDIT = {
    "audit_parent",
    "looks_like_project_parent",
    "apply_patterns",
    "audit_project",
    "patterns_from_audit",
}


def __getattr__(name: str):
    """PEP 562: тело подгружается при первом обращении к имени."""
    if name in _SEEDS:
        from memory.experience import seeds as mod
    elif name in _EXTRACT:
        from memory.experience import extract as mod
    elif name in _SCAN:
        from memory.experience import scan as mod
    elif name in _AUDIT:
        from memory.experience import audit as mod
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(mod, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals()) + __all__)


def maybe_cycle_on_done(workdir: Path, apply: bool = False):
    """Обёртка для supervisor и monkeypatch тестов."""
    from memory.experience.audit import maybe_cycle_on_done as _impl
    return _impl(workdir, apply=apply)


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
        from memory.experience.seeds import DEFAULT_SEEDS
        rows = DEFAULT_SEEDS
        if args.apply:
            from memory.experience.audit import apply_patterns
            _print(apply_patterns(rows))
        else:
            _print({"dry_run": True, "count": len(rows), "patterns": rows})
        return 0

    if args.cmd == "scan":
        from memory.experience.extract import dedupe
        from memory.experience.scan import scan_parent
        found = dedupe(scan_parent(args.parent))[: args.limit]
        if args.apply:
            from memory.experience.audit import apply_patterns
            _print(apply_patterns(found))
        else:
            _print({"dry_run": True, "count": len(found), "sample": found[:20]})
        return 0

    if args.cmd == "audit":
        from memory.experience.audit import audit_parent
        report = audit_parent(args.parent)
        if args.apply:
            from memory.experience.audit import apply_patterns, patterns_from_audit
            merged = apply_patterns(patterns_from_audit(report))
            report["applied"] = merged
        _print(report)
        return 0

    if args.cmd == "cycle":
        from memory.experience.extract import dedupe
        from memory.experience.scan import scan_parent
        from memory.experience.audit import audit_parent, patterns_from_audit
        scanned = scan_parent(args.parent)
        report = audit_parent(args.parent)
        rows: List[Dict[str, str]] = []
        if not args.no_seeds:
            from memory.experience.seeds import DEFAULT_SEEDS
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
            from memory.experience.audit import apply_patterns
            payload["applied"] = apply_patterns(rows)
        else:
            payload["dry_run"] = True
            payload["sample"] = rows[:20]
        _print(payload)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
