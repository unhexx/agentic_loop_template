# -*- coding: utf-8 -*-
"""Audit wrappers for local_pipeline. Details are an allowlist. Role is ci."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from memory.audit_log import append_entry
from memory.local_mcp.redact import contains_secret_marker
from memory.state import snapshot


def _cycle(workdir: Path) -> int:
    snap = snapshot(agent_dir=Path(workdir) / ".agent")
    cycle = snap.get("cycle_number") if isinstance(snap, dict) else None
    if isinstance(cycle, bool) or not isinstance(cycle, int):
        return 0
    return cycle


def _safe_str(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if contains_secret_marker(value):
        return None
    return value


def _run_details(
    *,
    backend: object,
    exit_code: object,
    duration_ms: object,
    error_code: object,
    job_name: object,
    job_path: object,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    backend_text = _safe_str(backend)
    if backend_text is not None:
        details["backend"] = backend_text
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        details["exit_code"] = exit_code
    if isinstance(duration_ms, int) and not isinstance(duration_ms, bool):
        details["duration_ms"] = duration_ms
    error_text = _safe_str(error_code)
    if error_text is not None:
        details["error_code"] = error_text
    job_text = _safe_str(job_name)
    if job_text is not None:
        details["job_name"] = job_text
    path_text = _safe_str(job_path)
    if path_text is not None:
        details["job_path"] = path_text
    return details


def _record(workdir: Path, action: str, details: dict[str, Any]) -> dict:
    return append_entry(
        action,
        role="ci",
        cycle=_cycle(workdir),
        details=details,
        agent_dir=Path(workdir) / ".agent",
    )


def _finish(
    workdir: Path,
    action: str,
    *,
    backend: object,
    exit_code: object,
    duration_ms: object,
    error_code: object,
    job_name: object = None,
    job_path: object = None,
    **extra: Any,
) -> dict:
    del extra  # not an audit field; may hold a secret
    return _record(
        workdir,
        action,
        _run_details(
            backend=backend,
            exit_code=exit_code,
            duration_ms=duration_ms,
            error_code=error_code,
            job_name=job_name,
            job_path=job_path,
        ),
    )


def local_pipeline_run(workdir: Path, **kwargs: Any) -> dict:
    return _finish(workdir, "local_pipeline_run", **kwargs)


def local_pipeline_probe_failed(workdir: Path, **kwargs: Any) -> dict:
    return _finish(workdir, "local_pipeline_probe_failed", **kwargs)


def local_pipeline_cancelled(workdir: Path, **kwargs: Any) -> dict:
    return _finish(workdir, "local_pipeline_cancelled", **kwargs)


def local_pipeline_webhook_reject(
    workdir: Path,
    *,
    reason: object,
    **extra: Any,
) -> dict:
    del extra
    details: dict[str, Any] = {}
    reason_text = _safe_str(reason)
    if reason_text is not None:
        details["reason"] = reason_text
    return _record(workdir, "local_pipeline_webhook_reject", details)


def local_mcp_memory_update(
    workdir: Path,
    *,
    category: object,
    description: object,
) -> dict:
    """Metadata only. A secret marker omits that field."""
    details: dict[str, Any] = {}
    category_text = _safe_str(category)
    if category_text is not None:
        details["category"] = category_text
    description_text = _safe_str(description)
    if description_text is not None:
        details["description"] = description_text
    return _record(workdir, "local_mcp_memory_update", details)
