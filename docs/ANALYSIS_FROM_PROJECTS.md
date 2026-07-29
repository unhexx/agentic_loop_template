# Analysis: agentic-loop usage across `_PROJECT/*` (2026-07-29)

## Projects scored (agentic signals)

| Score | Project | Signals |
|------:|---------|---------|
| 5 | eegent | `.agent/` (heavy), nested `agentic_loop_template`, SELF_IMPROVEMENT, EXECUTION_LOG, self-dev UI |
| 5 | tunex / tunex-tcp-support | `agentic_loop_tunex`, `.agent/LOOP_STATE.json` |
| 3 | classifier | template files at root, `.agent/`, multi-agent handoffs under `.agents/` |
| 3 | agentic_loop_template | SSOT |
| 1 | agent-box | `scripts/agentic_loop.sh`, multi-agent `AGENTS.md` (parallel worktrees) |

Other `_PROJECT` trees (hashtager, max-gate, InstantLegalBot, …) are product code without full harness adoption.

## Critical measurements

| Artifact | Size / observation | Impact |
|----------|-------------------|--------|
| `eegent/.agent/LOOP_STATE.md` | **~12.5 MB**, repeated Sprint Eval YELLOW blocks | Context death if read |
| `eegent/.agent/DONE.md` | **~1.6 MB** | Same |
| `eegent/.agent/LESSONS.md` | ~62 KB useful + noisy simulate entries | Needs top-N compaction |
| `eegent/.agent/worktrees` | ~271 MB | Isolation OK; cleanup discipline |
| `eegent/.../TOOLS_INSTRUCTIONS.md` | **~115 KB** | Token bloat vs template stub ~1 KB |
| Template cold prompt stack | SYSTEM+ROLES+STANDARDS+SCHEMA+TOOLS ~100 KB if all loaded | Must be progressive |
| `classifier/.agent/LOOP_STATE.md` | Byte-identical stale template (Windows paths 2026-06-10) | Drift / false sync |
| Memory package split | template: meta_harvester only; eegent: store/schema/workspace only | Broken SSOT before this work |
| EXECUTION_LOG | many `returncode: -1` / 255 on real dogfood | PATH/blackbox/worktree fragility |

## Tool patterns that dominated cycles (eegent)

1. **gh pre verbatim** (5 blocks, multi-repo) — every push/PR  
2. **sync-worktree -VerifyOnly** + SYNC_DONE marker  
3. **blackbox_wrapper** simulate/real + worktree spawn  
4. **memory / questions_collector** (underused relative to LESSONS append)  
5. **docker compose / .venv** for product tests  

## Process lessons (high value)

- Narrow INVEST slices beat large refactors (LESSONS).  
- Machine-checkable markers (SYNC_DONE) beat human log reading.  
- English model-facing prompts + Russian code/commits is the working split.  
- Parallel streams need ownership contracts (ANALYSIS_AND_OPTIMIZATION_RECOMMENDATIONS).  
- Simulate paths must not pollute main-clone `.agent` without restore.  
- Template hygiene: remove foreign project examples (leak-data-importer history).

## Platform mismatch

Harness docs/scripts centered on **Windows + PowerShell + Blackbox + MiniMax**, while active host and agent-box direction are **Linux + bash + Grok**. This produced invalid tool blocks and bootstrap friction.

## Ranking method

`Impact = 0.35*ContextSaved + 0.30*ErrorReduction + 0.20*CycleSpeed + 0.15*QualityLift`  
See `docs/TOP10_IMPROVEMENTS.md`.
