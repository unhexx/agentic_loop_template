# -*- coding: utf-8 -*-
"""Высоценные семена паттернов (данные, не логика)."""
from __future__ import annotations

from typing import Dict, List

DEFAULT_SEEDS: List[Dict[str, str]] = [
    {
        "category": "Common Failure Patterns",
        "description": "Never read full .agent/LOOP_STATE archives into context — use `python -m memory state snapshot`",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Using bare python/python3 instead of project .venv interpreter",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Skipping Agent-Init after pull or on new worktree",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Forgetting machine-verifiable SYNC_DONE / git_sync_status.verified after merge",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Loading entire TOOLS_INSTRUCTIONS monologue — use tools/select.py progressive blocks",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Oversized multi-file refactors in one ACT wave (narrow 1-3 file slices win)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Appending free-form Sprint Eval text to LOOP_STATE instead of metrics.jsonl",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Stale copy-pasted LOOP_STATE from another project (paths/dates from foreign hosts)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Role collapse: skipping Tester/Reviewer gates when acting as multi-role alone",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Simulate/smoke paths writing durable .agent state on main clone without restore",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Narrow INVEST slice + explicit success criteria + machine-checkable markers",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Delta handoffs (summary + context_delta + links) instead of restating DEVELOPMENT_STANDARDS",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Orchestrator starts with state snapshot + memory query top-5 failures before PLAN",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Parallel workstreams only with owned_paths contracts and worktree isolation",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Git preflight via single script; full multi-repo gh ritual only when template files change",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "Cold-start: 1-2 paragraph compressed state + pointers to files; on-demand read only",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "TOOLS via selector by intent (git|test|memory|docker) not full registry paste",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "After DONE + high confidence: meta_harvester harvest then propose safe few-shots",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "Compact .agent bloat every Reviewer cycle when LESSONS/DONE/LOOP exceed thresholds",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Windows-only PowerShell blocks on Linux hosts — use tools/blocks/linux/*",
    },
    # --- 2026-08-20 harvest across current _PROJECT/* ---
    {
        "category": "Common Failure Patterns",
        "description": "Experience harvest that only reads LESSONS.md / SELF_IMPROVEMENT_LOG.md returns empty on product trees — also scan AGENTS.md, playbooks, CONTRIBUTING, living plans",
    },
    {
        "category": "Common Failure Patterns",
        "description": "README advertises docs/AGENT_PLAYBOOK.md or AGENTIC_LOOP.md that were never written (docs_gap: signet, nesttunnel)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Living plans filled without finishing Agent-Init: no .venv, no LOOP_STATE, incomplete .agent (telegrok-style incomplete adoption)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Consumer copied only Agent-Init.ps1 — Linux host has no Agent-Init.sh (classifier drift)",
    },
    {
        "category": "Common Failure Patterns",
        "description": "SYSTEM_PROMPT still mandates Windows PowerShell / MiniMax while the active host is Linux + Grok",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Copying the entire agentic_loop_template tree into a product instead of a sibling symlink + PYTHONPATH",
    },
    {
        "category": "Common Failure Patterns",
        "description": "Forcing full O→C→T→D→R on a product that only needs AGENTS.md + Definition of Done + exact commands",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Two-tier adoption: lite AGENTS.md/playbook for products; full loop only when autonomous multi-cycle work is in scope",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Consumer Agent-Init.sh should symlink ../agentic_loop_template and export PYTHONPATH to SSOT memory (do not vendor a stale copy)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Project-specific playbook: contracts-first, fixture tests, provenance, explicit MUST NOT list (contact-vault pattern)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "Put exact install/lint/test/run commands in AGENTS.md so agents do not invent toolchains (telegrok uv/ruff/mypy/pytest)",
    },
    {
        "category": "Effective Loop Strategies",
        "description": "English product docs + natural-Russian commits/comments on loop artifacts; never mention models in git",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "Contracts first: change Zod/Prisma/tRPC (or equivalent schema) before UI or ad-hoc logic",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "Never invent a fact without provenance; never commit real PII or secrets; synthetic fixtures only",
    },
    {
        "category": "Project Playbook Patterns",
        "description": "One logical change per PR — do not mix parser/domain changes with UI restyling",
    },
    {
        "category": "Meta Improvement Patterns",
        "description": "Once per parent-folder session: python -m memory.experience_harvester cycle --parent <_PROJECT> --apply",
    },
    {
        "category": "High-Value Compression Patterns",
        "description": "Cold SYSTEM_PROMPT is platform-adaptive (Linux bash Agent-Init.sh default; PowerShell only on Windows)",
    },
]
