# -*- coding: utf-8 -*-
"""Validate agentic handoff JSON against schemas/handoff.schema.json (stdlib-friendly)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROLES = {"Orchestrator", "Coder", "Tester", "Debugger", "Reviewer", "None"}
STATUSES = {"IN_PROGRESS", "BLOCKED", "DONE"}
PHASES = {
    "planning",
    "implementation",
    "testing",
    "debugging",
    "review",
    "finalization",
}


def _load_schema() -> Dict[str, Any]:
    # колесо / editable: data/ лежит в пакете memory, не в memory.data
    try:
        from importlib.resources import files as _pkg_files

        packaged = _pkg_files("memory").joinpath("data/handoff.schema.json")
        if packaged.is_file():
            return json.loads(packaged.read_text(encoding="utf-8"))
    except (ModuleNotFoundError, FileNotFoundError, OSError, AttributeError, ValueError, json.JSONDecodeError):
        pass

    for c in (
        Path(__file__).resolve().parents[1] / "schemas" / "handoff.schema.json",
        Path("schemas/handoff.schema.json"),
    ):
        if c.is_file():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    return {}


def validate_handoff(data: Dict[str, Any], strict_done: bool = True) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["handoff must be a JSON object"]

    required = [
        "handoff_to",
        "role",
        "current_phase",
        "cycle_number",
        "summary",
        "status",
        "confidence",
    ]
    for k in required:
        if k not in data:
            errors.append(f"missing required field: {k}")

    if "role" in data and data["role"] not in ROLES - {"None"}:
        errors.append(f"invalid role: {data.get('role')}")
    if "handoff_to" in data and data["handoff_to"] not in ROLES:
        errors.append(f"invalid handoff_to: {data.get('handoff_to')}")
    if "status" in data and data["status"] not in STATUSES:
        errors.append(f"invalid status: {data.get('status')}")
    if "current_phase" in data and data["current_phase"] not in PHASES:
        errors.append(f"invalid current_phase: {data.get('current_phase')}")

    conf = data.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            if c < 0.0 or c > 1.0:
                errors.append("confidence must be 0.0–1.0")
        except Exception:
            errors.append("confidence must be a number")

    if data.get("status") == "DONE":
        if data.get("handoff_to") not in (None, "None"):
            errors.append('status DONE requires handoff_to "None"')
        if strict_done:
            gss = data.get("git_sync_status")
            waived = data.get("sync_waived")
            if not waived:
                if not isinstance(gss, dict) or not gss.get("verified"):
                    errors.append(
                        "DONE requires git_sync_status.verified=true or sync_waived with reason"
                    )
            lessons = data.get("lessons_learned") or []
            if not lessons and not data.get("distillation_performed"):
                errors.append(
                    "DONE requires lessons_learned non-empty or distillation_performed=true"
                )
            metrics = data.get("metrics")
            if not isinstance(metrics, dict):
                errors.append("DONE requires metrics object")

    if "summary" in data and isinstance(data["summary"], str) and len(data["summary"]) > 800:
        errors.append("summary too long (>800 chars) — compress")

    return len(errors) == 0, errors


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate handoff JSON")
    parser.add_argument("path", type=Path, nargs="?", help="Path to handoff JSON")
    parser.add_argument("--json", default=None, help="Inline JSON string")
    parser.add_argument("--no-strict-done", action="store_true")
    args = parser.parse_args(argv)

    if args.json:
        data = json.loads(args.json)
    elif args.path:
        data = json.loads(args.path.read_text(encoding="utf-8"))
    else:
        print("Need path or --json", file=sys.stderr)
        return 2

    ok, errors = validate_handoff(data, strict_done=not args.no_strict_done)
    out = {"valid": ok, "errors": errors, "schema_present": bool(_load_schema())}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(cli())
