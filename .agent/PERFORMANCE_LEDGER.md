# .agent/PERFORMANCE_LEDGER.md — Cycle Performance & ROI Tracking

Auto-updated by performance_ledger module (to be implemented in P1-METRICS).

## Schema (v1)
- cycle: int
- date: ISO
- outcome: DONE | PARTIAL | BLOCKED
- elapsed_minutes: float
- tool_calls: int
- tokens_est: int (or proxy)
- confidence: float (0-1)
- tests_total: int, tests_failed: int
- violations: int, process_tags: list
- meta_proposals_generated: int, auto_applied: int
- success_patterns: list[str]
- notes: str

## Trends (populated after cycles)

**Cycle 0 (bootstrap — manual seed)**

- outcome: IN_PROGRESS
- notes: "Initiated full business efficiency cycle. Created PLAN/TODO/spec artifacts."

(Real data will be appended by tools after first full role cycles.)

## Summary Stats

- Total cycles tracked: 0
- Avg efficiency gain from meta (TBD after 3+)
- Key wins: TBD

See memory/performance_ledger.py (to be added) and integration in meta_harvester.
