"""
Agentic Loop Memory System

Workspace-scoped, structured, deduplicating memory for the self-improving
agentic development loop.

Inspired by:
- memory.py from the /implement skill
- Generative Agents (memory stream + reflection)
- Reflexion (verbal self-reflection)

Usage from the agent (via powershell tool):

    # Get workspace info
    python -m agentic_loop_template.memory info

    # Record distilled patterns (usually called by Reviewer)
    python -m agentic_loop_template.memory update --patterns '[{"category": "Testing", "description": "Missing edge case for empty input"}]'

    # Get current memory snapshot (JSON)
    python -m agentic_loop_template.memory snapshot

    # Simple query (future)
    python -m agentic_loop_template.memory query --category "Common Failure Patterns"

    # Clarification Questions Pool (collect user clarifications without blocking the loop)
    python -m agentic_loop_template.memory.questions_collector append --question "Need exact demo platforms?" --cycle 7
    python -m agentic_loop_template.memory.questions_collector resolve --ids Q-003 --notes "PO decision: Win10 + Arch"
"""

from .workspace import get_workspace_id, memory_paths
from .store import read_memory, update_memory, snapshot, query_memory
from .schema import MemoryState, Pattern
from .questions_collector import (
    append_question,
    get_open_questions,
    get_all_questions,
    should_escalate,
    mark_reviewed,
    sync_from_handoff,
    escalate_if_needed,
    load_config,
)

__all__ = [
    "get_workspace_id",
    "memory_paths",
    "read_memory",
    "update_memory",
    "snapshot",
    "query_memory",
    "MemoryState",
    "Pattern",
    # Clarification Questions Pool (non-blocking user clarification collection)
    "append_question",
    "get_open_questions",
    "get_all_questions",
    "should_escalate",
    "mark_reviewed",
    "sync_from_handoff",
    "escalate_if_needed",
    "load_config",
]
