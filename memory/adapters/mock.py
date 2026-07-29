# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _base(role: str, handoff_to: str, phase: str, status: str = "IN_PROGRESS") -> Dict[str, Any]:
    return {
        "handoff_to": handoff_to,
        "role": role,
        "current_phase": phase,
        "cycle_number": 1,
        "summary": f"mock {role} step",
        "context_delta": "mock",
        "status": status,
        "confidence": 0.95,
        "lessons_learned": ["mock cycle"],
        "metrics": {"tests_total": 1, "tests_failed": 0, "tool_calls": 1},
        "artifacts": [],
        "next_input_files": [],
        "process_tags": [],
    }


class MockAdapter:
    name = "mock"

    def __init__(self) -> None:
        self._step = 0

    def run_role_turn(
        self,
        role: str,
        prompt: str,
        handoff_in_path: Optional[Path],
        workdir: Path,
        timeout_s: int,
    ) -> Path:
        agent = workdir / ".agent"
        agent.mkdir(parents=True, exist_ok=True)
        # Fixed sequence ignores role mismatches for simplicity of full-cycle tests
        seq = [
            ("Orchestrator", "Coder", "planning", "IN_PROGRESS"),
            ("Coder", "Tester", "implementation", "IN_PROGRESS"),
            ("Tester", "Reviewer", "testing", "IN_PROGRESS"),  # green tests → skip Debugger
            ("Reviewer", "None", "finalization", "DONE"),
        ]
        if self._step >= len(seq):
            self._step = 0
        r, to, phase, status = seq[self._step]
        self._step += 1
        data = _base(r, to, phase, status)
        if status == "DONE":
            data["sync_waived"] = "mock adapter CI cycle"
            data["git_sync_status"] = {"verified": False}
        out = agent / "last_handoff.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
