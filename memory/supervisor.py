# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Union


class Terminal(str, Enum):
    PR_READY = "PR_READY"
    PR_READY_LOCAL = "PR_READY_LOCAL"
    BLOCKED = "BLOCKED"
    STOPPED_LIMIT = "STOPPED_LIMIT"
    STOPPED = "STOPPED"


SupervisorStatus = Terminal
Next = Union[str, Terminal]


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
