# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from memory.adapters.mock import MockAdapter
from memory.validate_handoff import validate_handoff


def test_mock_orchestrator_handoff_valid(tmp_path: Path):
    ad = MockAdapter()
    out = ad.run_role_turn(
        role="Orchestrator",
        prompt="plan",
        handoff_in_path=None,
        workdir=tmp_path,
        timeout_s=5,
    )
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    ok, errors = validate_handoff(data, strict_done=False)
    assert ok, errors
    assert data["role"] == "Orchestrator"
    assert data["handoff_to"] == "Coder"
