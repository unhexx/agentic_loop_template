# -*- coding: utf-8 -*-
"""Optional Slack notice for a finished local pipeline result. Default off."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from memory.integrations.config import load_project_cfg, slack_enabled
from memory.integrations.slack import notify_text
from memory.local_mcp.redact import clip_message, redact_text
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

# Act job name, Jenkins job, or a Jenkinsfile. Not runner labels, not a URL.
_JOB_KEYS = ("job_name", "job_path", "jenkinsfile_path")


def notify_pipeline_result(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    allow_root: Path,
) -> None:
    """Offer one result to Slack. A failure here does not change the result."""
    try:
        _notify(arguments, result, allow_root)
    except Exception as exc:
        log.warning("local pipeline slack notice failed: %s", exc)


def _resolved_workdir(raw: str, allow_root: Path) -> Path | None:
    # resolve follows a symlink out of the allow-root. Do not read or audit that path.
    try:
        root = Path(allow_root).resolve()
        resolved = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _agent_contained(workdir: Path) -> bool:
    # resolve() matches tools.local._contained_agent. A symlink is not contained,
    # even when the target stays inside, so snapshot and append_entry are not called through it.
    agent = workdir / ".agent"
    try:
        if agent.is_symlink():
            return False
        resolved = agent.resolve()
    except (OSError, ValueError):
        return False
    return resolved == workdir or workdir in resolved.parents


def _notify(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    allow_root: Path,
) -> None:
    raw = arguments.get("working_directory")
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return
    workdir = _resolved_workdir(raw, allow_root)
    if workdir is None or not _agent_contained(workdir):
        return
    project = load_project_cfg(workdir)
    section = project.get("local_pipeline")
    if not isinstance(section, dict) or section.get("notify") is not True:
        return
    # dry_run=False only after the same gate hooks use. A closed gate is a no-op.
    if not slack_enabled(project):
        return
    backend = result.get("backend")
    if not isinstance(backend, str):
        backend = ""
    exit_code = result.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = 0
    notify_text(
        workdir,
        _notice_line(result),
        {
            "source": "local_pipeline",
            "backend": backend,
            "exit_code": exit_code,
        },
        dry_run=False,
    )


def _notice_line(result: Mapping[str, Any]) -> str:
    # log_tail can hold a secret redaction missed. Do not read it.
    backend = result.get("backend")
    parts: list[str] = []
    if isinstance(backend, str) and backend:
        parts.append(backend)
    job = result.get("job")
    if isinstance(job, dict):
        for key in _JOB_KEYS:
            value = job.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
    exit_code = result.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = 0
    duration = result.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        duration = 0
    parts.append(f"exit={exit_code}")
    parts.append(f"duration_ms={duration}")
    log_path = result.get("log_path")
    if isinstance(log_path, str) and log_path:
        parts.append(log_path)
    line = clip_message(" ".join(parts), limit=8192)
    line = redact_text(line)
    return " ".join(part for part in line.split() if "://" not in part)
