# -*- coding: utf-8 -*-
"""Validate agentic handoff JSON against schemas/handoff.schema.json (stdlib-friendly)."""

from __future__ import annotations

import argparse
import json
import logging
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

try:
    import jsonschema
except ImportError:
    jsonschema = None  # PYTHONPATH-only clone; Python extras/enums still run

log = logging.getLogger("memory.validate_handoff")

# "jsonschema" | "python" — тесты после pip install -e ".[dev]" требуют jsonschema-путь
_last_backend = "python"
_warned_jsonschema = False
_warned_schema = False


def _warn_missing_jsonschema() -> None:
    global _warned_jsonschema
    if _warned_jsonschema:
        return
    _warned_jsonschema = True
    log.warning("jsonschema недоступен — структурные проверки идут по Python-фолбэку")


def _warn_missing_schema() -> None:
    global _warned_schema
    if _warned_schema:
        return
    _warned_schema = True
    log.warning("схема handoff не найдена — структурные проверки идут по Python-фолбэку")


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


def _python_structural(data: Dict[str, Any], errors: List[str], schema: Dict[str, Any]) -> None:
    props = (schema.get("properties") or {}) if schema else {}

    required = schema.get("required") if schema and schema.get("required") else [
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

    def _enum(field: str, fallback: set) -> set:
        raw = (props.get(field) or {}).get("enum")
        return set(raw) if raw else fallback

    roles_sender = _enum("role", ROLES - {"None"})
    roles_to = _enum("handoff_to", ROLES)
    statuses = _enum("status", STATUSES)
    phases = _enum("current_phase", PHASES)

    if "role" in data and data["role"] not in roles_sender:
        errors.append(f"invalid role: {data.get('role')}")
    if "handoff_to" in data and data["handoff_to"] not in roles_to:
        errors.append(f"invalid handoff_to: {data.get('handoff_to')}")
    if "status" in data and data["status"] not in statuses:
        errors.append(f"invalid status: {data.get('status')}")
    if "current_phase" in data and data["current_phase"] not in phases:
        errors.append(f"invalid current_phase: {data.get('current_phase')}")

    conf = data.get("confidence")
    conf_schema = props.get("confidence") or {}
    cmin = conf_schema.get("minimum", 0.0)
    cmax = conf_schema.get("maximum", 1.0)
    if conf is not None:
        try:
            c = float(conf)
            if c < cmin or c > cmax:
                errors.append("confidence must be 0.0–1.0")
        except Exception:
            errors.append("confidence must be a number")

    # summary maxLength дублируется схемой; здесь только фолбэк без jsonschema
    max_len = (props.get("summary") or {}).get("maxLength", 800)
    if "summary" in data and isinstance(data["summary"], str) and len(data["summary"]) > max_len:
        errors.append("summary too long (>800 chars) — compress")


def _done_extras(data: Dict[str, Any], errors: List[str], strict_done: bool) -> None:
    if data.get("status") != "DONE":
        return
    if data.get("handoff_to") not in (None, "None"):
        errors.append('status DONE requires handoff_to "None"')
    if not strict_done:
        return
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


def validate_handoff(data: Dict[str, Any], strict_done: bool = True) -> Tuple[bool, List[str]]:
    global _last_backend
    errors: List[str] = []
    if not isinstance(data, dict):
        _last_backend = "python"
        return False, ["handoff must be a JSON object"]

    schema = _load_schema()
    if jsonschema is not None and schema:
        validator = jsonschema.Draft202012Validator(schema)
        for err in validator.iter_errors(data):
            errors.append(err.message)
        _last_backend = "jsonschema"
    else:
        _last_backend = "python"
        if jsonschema is None:
            _warn_missing_jsonschema()
        if not schema:
            _warn_missing_schema()
        _python_structural(data, errors, schema)

    _done_extras(data, errors, strict_done)
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
