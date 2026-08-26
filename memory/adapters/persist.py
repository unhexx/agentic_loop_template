# -*- coding: utf-8 -*-
"""Общий persist handoff: validate + атомарная запись."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from memory.stream_context import owned_paths_csv, stream_name, worktree_path
from memory.validate_handoff import validate_handoff

log = logging.getLogger("memory.adapters")


def _stamp_stream_identity(data: Dict[str, Any]) -> None:
    """ContextVar важнее JSON модели — live CLI часто не заполняет поля потока."""
    name = stream_name()
    if name:
        prev = data.get("stream")
        if prev is not None and prev != name:
            log.warning("persist_role_handoff stream mismatch: %r -> %r", prev, name)
        data["stream"] = name
    owned = owned_paths_csv()
    if owned:
        paths = [p.strip() for p in owned.split(",") if p.strip()]
        prev_owned = data.get("owned_paths")
        if prev_owned is not None and prev_owned != paths:
            log.warning(
                "persist_role_handoff owned_paths mismatch: %r -> %r",
                prev_owned,
                paths,
            )
        data["owned_paths"] = paths
    wt = worktree_path()
    if wt:
        prev_wt = data.get("worktree")
        if prev_wt is not None and prev_wt != wt:
            log.warning(
                "persist_role_handoff worktree mismatch: %r -> %r", prev_wt, wt
            )
        data["worktree"] = wt


def persist_role_handoff(workdir: Path, data: Dict[str, Any]) -> Path:
    """Те же правила, что у extract_handoff: strict_done только при status==DONE."""
    _stamp_stream_identity(data)
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
