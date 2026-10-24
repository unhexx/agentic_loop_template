# -*- coding: utf-8 -*-
"""Stdlib MCP server for the local tool catalog."""

from memory.local_mcp.framing import FramingError
from memory.local_mcp.registry import catalog_tools
from memory.local_mcp.server import (
    CallContext,
    ToolResult,
    kill_process_group,
    refused_pipeline_result,
    serve,
    server_version,
)

__all__ = [
    "CallContext",
    "FramingError",
    "ToolResult",
    "catalog_tools",
    "kill_process_group",
    "refused_pipeline_result",
    "serve",
    "server_version",
]
