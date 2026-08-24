# -*- coding: utf-8 -*-
"""Общий persist handoff: validate + атомарная запись."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from memory.validate_handoff import validate_handoff

log = logging.getLogger("memory.adapters")


def persist_role_handoff(workdir: Path, data: Dict[str, Any]) -> Path:
    """Те же правила, что у extract_handoff: strict_done только при status==DONE."""
    strict = (data.get("status") or "").upper() == "DONE"
    ok, errors = validate_handoff(data, strict_done=strict)
    if not ok:
        log.warning("persist_role_handoff rejected: %s", "; ".join(errors))
        from memory.state import log_metrics

        log_metrics(
            {"event": "handoff_invalid", "errors": len(errors)},
            agent_dir=Path(workdir) / ".agent",
        )
        from memory.adapters.grok import HandoffExtractError

        raise HandoffExtractError("; ".join(errors))
    from memory.handoff_io import save_handoff

    return save_handoff(Path(workdir), data)
