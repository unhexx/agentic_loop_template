# -*- coding: utf-8 -*-
"""Opt-in integrations for local MCP (config/state/Slack). Full tracker suite stays in agentix-hub until ported."""
from __future__ import annotations

from memory.integrations.config import slack_enabled, tracker_enabled

__all__ = [
    "tracker_enabled",
    "slack_enabled",
]
