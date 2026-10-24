# -*- coding: utf-8 -*-
"""local_pipeline gate. One object from project config. No process is started."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from memory.integrations.config import load_project_cfg
from memory.local_mcp.validate import result_errors
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

_BACKENDS = frozenset({"act", "self_hosted", "jenkins"})
_DEFAULT_WORKFLOW = ".github/workflows/agentix-loop.yml"
_DEFAULT_JOB = "harness"

# Owned here so this module does not import the stdio server.
# refused_pipeline_result reads job and scm through disabled_result.
_STATUS_TARGET = {
    "act": "local",
    "self_hosted": "github_actions_ui",
    "jenkins": "bitbucket_commit_status",
}
_SCM_HOST = {
    "act": "local",
    "self_hosted": "github",
    "jenkins": "bitbucket",
}

# Fixed text: the raw backend value may be a token.
_UNKNOWN_BACKEND_MSG = (
    "local_pipeline.backend is not act, self_hosted, or jenkins; pipeline disabled"
)
_DISABLED_MESSAGE = "local pipeline is disabled"


@dataclass(frozen=True)
class LocalPipelineConfig:
    enabled: bool
    config_backend: str
    section: dict[str, Any]


def load_local_pipeline(workdir: Path) -> LocalPipelineConfig:
    """Read local_pipeline only. Missing section or unknown backend is off.

    config_backend is act, self_hosted, or jenkins. An unknown value is not kept.
    """
    raw_cfg = load_project_cfg(Path(workdir))
    section_raw = raw_cfg.get("local_pipeline")
    if not isinstance(section_raw, dict):
        return LocalPipelineConfig(enabled=False, config_backend="act", section={})
    section = dict(section_raw)
    backend = section.get("backend")
    known = isinstance(backend, str) and backend in _BACKENDS
    if known:
        config_backend = backend
    else:
        config_backend = "act"
        # Do not retain an out-of-enum value; it may be a token.
        section.pop("backend", None)
        if "backend" in section_raw:
            log.warning("%s", _UNKNOWN_BACKEND_MSG)
    enabled = section_raw.get("enabled") is True and known
    return LocalPipelineConfig(
        enabled=enabled,
        config_backend=config_backend,
        section=section,
    )


def _job(arguments: Mapping[str, Any]) -> dict[str, Any]:
    backend = arguments["backend"]
    if backend == "act":
        return {
            "workflow_file": arguments.get("workflow_file") or _DEFAULT_WORKFLOW,
            "job_name": arguments.get("job_name") or _DEFAULT_JOB,
        }
    if backend == "self_hosted":
        return {"runner_labels": list(arguments["runner_labels"])}
    if backend == "jenkins":
        has_job = "job_path" in arguments
        has_file = "jenkinsfile_path" in arguments
        if has_job and not has_file:
            return {"job_path": arguments["job_path"]}
        if has_file and not has_job:
            return {"jenkinsfile_path": arguments["jenkinsfile_path"]}
        raise ValueError("jenkins result needs exactly one path")
    raise ValueError("unknown backend")


def disabled_result(arguments: Mapping[str, Any], config_backend: str) -> dict:
    """Schema-valid refusal. skipped is true only for local_pipeline_disabled."""
    backend = arguments["backend"]
    result = {
        "ok": False,
        "skipped": True,
        "backend": backend,
        "config_backend": config_backend,
        "exit_code": 2,
        "duration_ms": 0,
        "timed_out": False,
        "cancelled": False,
        "truncated": False,
        "lines_dropped": 0,
        "queue_risk": False,
        "status": "refused",
        "job": _job(arguments),
        "log_tail": "",
        "log_path": f".agent/local-pipeline-runs/disabled-{backend}.log",
        "probe": {},
        "warnings": [],
        "status_target": _STATUS_TARGET[backend],
        "status_posted_by": None,
        "scm": {
            "kind": None,
            "host": _SCM_HOST[backend],
            "ref": None,
            "commit": None,
        },
        "error": {
            "code": "local_pipeline_disabled",
            "message": _DISABLED_MESSAGE,
        },
    }
    problems = result_errors(result)
    if problems:
        raise RuntimeError("disabled result failed schema: " + "; ".join(problems))
    return result
