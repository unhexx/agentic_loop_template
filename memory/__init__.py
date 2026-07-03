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

    # Meta-Optimizer / Trajectory Harvesting (v3.x self-improvement of the harness itself)
    python -m agentic_loop_template.memory.meta_harvester harvest --handoff .agent/last_handoff.json --cycle 17 --outcome DONE
    python -m agentic_loop_template.memory.meta_harvester analyze --recent 5
    python -m agentic_loop_template.memory.meta_harvester propose --limit 2
    python -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run
"""

# Guarded imports — the full workspace/store/schema may be in separate files or installed package.
# Core working modules (questions, meta, new performance_ledger) are self-contained.
try:
    from .workspace import get_workspace_id, memory_paths
    from .store import read_memory, update_memory, snapshot, query_memory
    from .schema import MemoryState, Pattern
except Exception:
    # Provide minimal fallbacks so submodule imports (performance_ledger, meta_harvester) still work
    def get_workspace_id(): return "agentix-local"
    def memory_paths(): return {"file": "~/.grok/agentic-loop-memory/agentix.md"}
    def read_memory(*a, **k): return {}
    def update_memory(*a, **k): return {}
    def snapshot(*a, **k): return {"patterns": {}, "recent_distillations": []}
    def query_memory(*a, **k): return []
    MemoryState = dict
    Pattern = dict

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

from .meta_harvester import (
    harvest_from_handoff,
    get_recent_trajectories,
    analyze_for_proposals,
    generate_proposals,
    apply_safe_proposals,
    seed_example_trajectory,
    update_performance_ledger,
    load_config as load_meta_config,
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
    # Meta-Optimizer & Trajectory Harvesting (self-improvement of the loop harness)
    "harvest_from_handoff",
    "get_recent_trajectories",
    "analyze_for_proposals",
    "generate_proposals",
    "apply_safe_proposals",
    "seed_example_trajectory",
    "update_performance_ledger",
    "load_meta_config",
    # Performance Ledger (P1 metrics/ROI — new in business efficiency initiative)
    "performance_ledger",
]

# Lazy exposure so we don't break when full workspace/store modules are not present in all envs
def __getattr__(name):
    if name == "performance_ledger":
        from . import performance_ledger as pl
        return pl
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
