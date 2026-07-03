# -*- coding: utf-8 -*-
"""
Partial resume / error recovery (P7).

Восстанавливает контекст цикла из last_handoff.json и LOOP_STATE.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

LAST_HANDOFF = Path(".agent/last_handoff.json")
LOOP_STATE = Path(".agent/LOOP_STATE.md")


def load_last_handoff() -> Optional[Dict[str, Any]]:
    if LAST_HANDOFF.exists():
        return json.loads(LAST_HANDOFF.read_text(encoding="utf-8"))
    agent_dir = LAST_HANDOFF.parent
    if agent_dir.exists():
        alt = list(agent_dir.glob("handoff_*.json"))
        if alt:
            candidates = sorted(alt, key=lambda p: p.stat().st_mtime, reverse=True)
            return json.loads(candidates[0].read_text(encoding="utf-8"))
    return None


def build_resume_context() -> Dict[str, Any]:
    """Собирает компактный контекст для возобновления цикла после сбоя."""
    handoff = load_last_handoff()
    loop_note = LOOP_STATE.read_text(encoding="utf-8") if LOOP_STATE.exists() else ""
    ctx: Dict[str, Any] = {
        "resumable": handoff is not None,
        "last_handoff_to": handoff.get("handoff_to") if handoff else None,
        "last_role": handoff.get("role") if handoff else None,
        "last_status": handoff.get("status") if handoff else None,
        "cycle_number": handoff.get("cycle_number") if handoff else None,
        "summary": handoff.get("summary") if handoff else None,
        "next_input_files": handoff.get("next_input_files", []) if handoff else [],
        "issues_found": handoff.get("issues_found", []) if handoff else [],
        "loop_state_excerpt": loop_note[:500] if loop_note else "",
        "recommended_next_role": _next_role(handoff),
    }
    return ctx


def _next_role(handoff: Optional[Dict[str, Any]]) -> str:
    if not handoff:
        return "Orchestrator"
    target = handoff.get("handoff_to", "Orchestrator")
    if target in ("None", None, ""):
        return "Orchestrator"
    return str(target)


def _cli() -> None:
    p = argparse.ArgumentParser(description="Resume context builder")
    p.add_argument("--json", action="store_true", help="Output JSON")
    args = p.parse_args()
    ctx = build_resume_context()
    if args.json:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(f"Resumable: {ctx['resumable']}")
        print(f"Next role: {ctx['recommended_next_role']}")
        print(f"Summary: {ctx.get('summary', 'N/A')}")


if __name__ == "__main__":
    _cli()