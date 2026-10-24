# -*- coding: utf-8 -*-
"""Static MCP catalog. Annotations match the local-pipeline design."""

from __future__ import annotations

from typing import Any

from memory.local_mcp.validate import load_schema

# destructiveHint is true only for tools that run repo code or rewrite state.
_OPEN_DESTRUCTIVE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
_CLOSED_DESTRUCTIVE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}
_CLOSED_READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_GIT_PREFLIGHT = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# Order is the design catalog. Do not add a free-form shell tool.
_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "run_local_pipeline",
        "description": (
            "Supervise one local CI backend (act, an already-registered "
            "self-hosted runner, or Jenkins) and return a structured result."
        ),
        "annotations": _OPEN_DESTRUCTIVE,
    },
    {
        "name": "git_preflight",
        "description": (
            "Read-only git and gh preflight. Does not push and does not run "
            "gh workflow."
        ),
        "annotations": _GIT_PREFLIGHT,
    },
    {
        "name": "validate_handoff",
        "description": "Validate a handoff object or a JSON file under the workdir.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "state_snapshot",
        "description": "Bounded LOOP_STATE snapshot. window is an integer from 1 to 20.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "state_append_delta",
        "description": "Append one LOOP_STATE delta. text is at most 500 characters.",
        "annotations": _CLOSED_DESTRUCTIVE,
    },
    {
        "name": "state_metrics_log",
        "description": "Record one metrics object on LOOP_STATE.",
        "annotations": _CLOSED_DESTRUCTIVE,
    },
    {
        "name": "memory_info",
        "description": "Workspace memory paths for the server workdir.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "memory_query",
        "description": "Query workspace memory. top is 1..20. category and contains are bounded.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "memory_snapshot",
        "description": "Workspace memory snapshot, truncated by dropping whole keys.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "memory_update",
        "description": "Record one memory pattern (category and description).",
        "annotations": _CLOSED_DESTRUCTIVE,
    },
    {
        "name": "knowledge_query",
        "description": "Query the workdir knowledge index. The caller cannot pass a db path.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "knowledge_stats",
        "description": "Summarize the workdir knowledge index. The caller cannot pass a db path.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "playbooks_select",
        "description": "Select playbook bullets. query is at most 200 characters. k is 1..20.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "playbooks_list",
        "description": "List playbook names and counts.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "proxy_health",
        "description": "Report loopback proxy and gateway health. Does not start either.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "proxy_stats",
        "description": "Collect proxy stats for the server workdir.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "integrations_status",
        "description": "Report tracker and Slack gates. No tokens.",
        "annotations": _CLOSED_READONLY,
    },
    {
        "name": "pytest_run",
        "description": "Run pytest with an allowlisted argv under the workdir.",
        "annotations": _OPEN_DESTRUCTIVE,
    },
    {
        "name": "docker_compose",
        "description": "Read-only docker compose ps, logs, or config. Not up, down, or build.",
        "annotations": _CLOSED_READONLY,
    },
)

_BY_NAME = {spec["name"]: spec for spec in _TOOLS}


def has_tool(name: str) -> bool:
    return name in _BY_NAME


def catalog_tools() -> list[dict[str, Any]]:
    """The tools/list array. One page, no cursor."""
    tools: list[dict[str, Any]] = []
    for spec in _TOOLS:
        tools.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "inputSchema": load_schema(spec["name"]),
                "annotations": dict(spec["annotations"]),
            }
        )
    return tools
