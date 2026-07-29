# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union


class Terminal(str, Enum):
    PR_READY = "PR_READY"
    PR_READY_LOCAL = "PR_READY_LOCAL"
    BLOCKED = "BLOCKED"
    STOPPED_LIMIT = "STOPPED_LIMIT"
    STOPPED = "STOPPED"


SupervisorStatus = Terminal
Next = Union[str, Terminal]

ROLE_PROMPT_FILES = {
    "Orchestrator": "prompts/short_orchestrator_prompt.md",
    "Coder": "prompts/short_coder_prompt.md",
    "Tester": "prompts/short_tester_prompt.md",
    "Debugger": "prompts/short_debugger_prompt.md",
    "Reviewer": "prompts/short_reviewer_prompt.md",
}

_PROMPT_BODY_CAP = 8000
_SNAP_JSON_CAP = 4000


def next_role(current_role: str, handoff: Dict[str, Any]) -> Next:
    status = (handoff.get("status") or "").upper()
    if status == "BLOCKED":
        return Terminal.BLOCKED
    if current_role == "Reviewer" and status == "DONE":
        return Terminal.PR_READY
    if current_role == "Tester":
        metrics = handoff.get("metrics") or {}
        failed = int(metrics.get("tests_failed") or 0)
        if failed > 0:
            return "Debugger"
        to = handoff.get("handoff_to") or "Reviewer"
        if to == "Debugger":
            return "Debugger"
        return "Reviewer"
    to = handoff.get("handoff_to")
    if to and to != "None":
        return str(to)
    chain = {
        "Orchestrator": "Coder",
        "Coder": "Tester",
        "Debugger": "Tester",
        "Reviewer": "Orchestrator",
    }
    return chain.get(current_role, Terminal.BLOCKED)


def load_config(workdir: Path) -> Dict[str, Any]:
    """Load .agent/project_config.json, falling back to example, else {}."""
    workdir = Path(workdir)
    for name in ("project_config.json", "project_config.example.json"):
        p = workdir / ".agent" / name
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def load_last_handoff(workdir: Path) -> Optional[Dict[str, Any]]:
    """Read workdir/.agent/last_handoff.json if present."""
    p = Path(workdir) / ".agent" / "last_handoff.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_handoff(workdir: Path, data: Dict[str, Any]) -> Path:
    """Persist handoff dict to workdir/.agent/last_handoff.json."""
    agent = Path(workdir) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    p = agent / "last_handoff.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _state_snapshot_for_workdir(workdir: Path) -> str:
    """Best-effort bounded LOOP_STATE snapshot with AGENT_DIR rebound to workdir/.agent."""
    try:
        from memory import state as state_mod

        agent_dir = Path(workdir) / ".agent"
        orig = {
            "AGENT_DIR": state_mod.AGENT_DIR,
            "STATE_JSON": state_mod.STATE_JSON,
            "STATE_MD": state_mod.STATE_MD,
            "HISTORY_DIR": state_mod.HISTORY_DIR,
            "METRICS_JSONL": state_mod.METRICS_JSONL,
        }
        try:
            state_mod.AGENT_DIR = agent_dir
            state_mod.STATE_JSON = agent_dir / "LOOP_STATE.json"
            state_mod.STATE_MD = agent_dir / "LOOP_STATE.md"
            state_mod.HISTORY_DIR = agent_dir / "history"
            state_mod.METRICS_JSONL = agent_dir / "metrics.jsonl"
            snap_obj = state_mod.snapshot(window=3)
            return json.dumps(snap_obj, ensure_ascii=False)[:_SNAP_JSON_CAP]
        finally:
            for key, value in orig.items():
                setattr(state_mod, key, value)
    except Exception:
        return "{}"


def build_role_prompt(
    role: str,
    handoff_in: Optional[Dict[str, Any]],
    workdir: Path,
) -> str:
    """
    Assemble cold prompt for one role turn:
    short role prompt + previous handoff delta + optional state snapshot.
    Instructs supervisor-driven JSON handoff; never dump .agent/history/*.
    """
    workdir = Path(workdir)
    rel = ROLE_PROMPT_FILES.get(role, "prompts/short_orchestrator_prompt.md")
    body = ""
    path = workdir / rel
    if path.is_file():
        try:
            body = path.read_text(encoding="utf-8")[:_PROMPT_BODY_CAP]
        except Exception:
            body = ""

    prev = ""
    if handoff_in:
        prev = (
            "\n\n## Previous handoff (delta only)\n"
            f"- summary: {handoff_in.get('summary', '')}\n"
            f"- context_delta: {handoff_in.get('context_delta', '')}\n"
            f"- status: {handoff_in.get('status', '')}\n"
            f"- role: {handoff_in.get('role', '')}\n"
            f"- handoff_to: {handoff_in.get('handoff_to', '')}\n"
        )

    snap = _state_snapshot_for_workdir(workdir)

    return (
        f"You are the **{role}** in the Agentix loop. "
        "Driven by supervisor — do not wait for human «продолжай».\n"
        "End with exactly one JSON handoff object "
        "(HANDOFF_SCHEMA / schemas/handoff.schema.json / last_handoff).\n"
        "Do NOT read .agent/history/* archives. "
        "Use tools/select.py for tools (do not inline full tool docs).\n\n"
        f"{body}\n{prev}\n## State snapshot\n{snap}\n"
    )
