# Meta-Optimizer & Trajectory Harvesting — Specification (v3.x)

**Status:** Draft for implementation in agentic_loop_template  
**Version:** 3.2 (extension of self-learning)  
**Goal:** Turn the loop's own execution traces into a systematic source of improvements for the *harness itself* (prompts, standards, compression examples, strategies).  
**Inspired by:** STaR / Reflexion trace filtering, self-evolving agent loops, harness telemetry + eval-driven improvement (Arize, AddyOsmani continuous coding loops), context engineering write/select/compress cycles.

## 1. Problem & Opportunity
Current self-improvement (v3.1) is excellent at:
- Recording lessons (SELF_IMPROVEMENT_LOG)
- Distilling high-density summaries
- Persisting countable patterns in workspace memory
- Non-blocking clarification via questions_collector

It is **mostly manual crystallization** by the Reviewer. There is no automated, repeatable harvesting of *what actually worked well in successful end-to-end cycles* to evolve the artifacts that control future cycles (AGENT_ROLES micro-prompts, few-shots in PROMPT_COMPRESSION_GUIDE, permanent rules, memory categories, tool usage patterns).

**Resulting gap:** long-running loops improve the *target product* very well, but the loop's own efficiency (cycles-to-DONE, violation rate, token budget adherence, compression quality) improves only as fast as a human/Reviewer manually extracts patterns.

**Solution:** Add a parallel, config-driven "meta" collector + analyzer (modeled exactly on the proven questions_collector pattern) that:
- Captures compact "golden" (and diagnostic) trajectories on high-quality DONE cycles.
- Extracts actionable signals (successful strategies, high-value deltas, compression wins, process hygiene that correlated with success).
- Produces *concrete, minimal, reviewable proposals* for template files.
- Feeds back into memory (new "Meta / Effective Strategies" categories) and the few-shot example corpus.
- Optionally auto-applies the safest class of improvements (new verified few-shot examples).

This directly implements "the agentic loop exists to improve both the product **and** the development process itself" at the meta level.

## 2. High-Level Architecture & Integration
- **Trigger points** (same philosophy as clarification pool):
  - Primarily Reviewer at end of a cycle where `status == "DONE"` and quality gates passed (confidence >= 0.85, tests_failed == 0, no blocking process_tags, etc.).
  - Orchestrator can request "consider recent meta proposals" at start of planning (like memory snapshot).
- **Storage locations** (outside repo, like memory; or .agent/ for project-specific):
  - Machine: `.agent/TRAJECTORIES.json` (index) + `.agent/TRAJECTORIES/<cycle>-<shortid>.json` (full compact trace) or single append-only log.
  - Human: `.agent/TRAJECTORIES_SUMMARY.md` (auto-updated, similar to QUESTIONS_POOL.md).
  - Cross-project learning: selected high-value patterns flow into workspace memory (same `~/.grok/agentic-loop-memory/<wid>.md`).
- **Config** (extends existing `.agent/project_config.json`):
  ```json
  {
    "meta_optimizer": {
      "enabled": true,
      "frequency": "after_every_done_cycle",   // "after_every_done_cycle" | "every_2_done" | "end_of_sprint" | "manual"
      "min_quality_for_harvest": { "confidence": 0.85, "tests_failed": 0, "process_violations": 0 },
      "auto_apply_safe": true,                 // only for "new_few_shot_example", "add_compression_tip" etc.
      "max_proposals_per_cycle": 3,
      "last_harvested_cycle": 12
    }
  }
  ```
- **Handoff integration** (additive, backward compatible):
  New optional top-level fields in every handoff (populated by Reviewer):
  ```json
  "meta_harvest": {
    "performed": true,
    "trajectories_captured": 1,
    "proposals_generated": 2,
    "proposals_auto_applied": 1,
    "notes": "Harvested golden trajectory for sync-worktree hardening; added 1 compression example"
  }
  ```
- **Memory categories** (recommended addition to §9):
  - "Effective Loop Strategies"
  - "High-Value Compression Patterns"
  - "Meta Improvement Patterns" (proposals that measurably helped)
- **Flow (one successful DONE cycle)**:
  1. Reviewer finishes normal duties + distillation.
  2. If quality gate passed + config says harvest: `python -m agentic_loop_template.memory.meta_harvester harvest --handoff last_handoff.json --cycle 17 --outcome DONE`
  3. Harvester writes compact trajectory (see format below).
  4. (Same or subsequent step) `... analyze --recent 5` or automatic.
  5. Produces proposals (structured list of {target_file, rationale, proposed_patch_or_text, safe_to_auto, confidence}).
  6. Safe ones auto-applied (search_replace style, or append to GUIDE) + committed with natural Russian dev message.
  7. All proposals + harvested patterns recorded in memory + handoff.
  8. Next Orchestrator sees updated memory + "recent meta wins" in context.
- **Safety & reviewability**:
  - All generated patches are small and human-readable.
  - Unsafe / high-impact (core STANDARDS changes) always go through clarification pool or explicit Reviewer sign-off.
  - Every meta action is logged with before/after evidence.
  - Reversible: proposals carry "revert_hint".

## 3. Harvest Format (the core prototype artifact)

### 3.1 Trajectory Record (captured on harvest)
Compact, self-contained, token-friendly. Stored as JSON (machine) + summarized in MD.

```json
{
  "id": "T-017-3f8a",
  "cycle": 17,
  "timestamp": "2026-06-10T14:22:00Z",
  "outcome": "DONE",
  "task_ref": "sync-worktree-VerifyOnly-hardening",
  "spec_ref": "TODO.md#P1-04 or SPRINTPLAN item",
  "quality_signals": {
    "confidence": 0.94,
    "tests_total": 18,
    "tests_failed": 0,
    "coverage": 87.2,
    "process_tags": [],
    "violations_enforced_by_reviewer": 0,
    "elapsed_minutes": 11.5,
    "tool_calls_total": 14
  },
  "compressed_handoff_chain": [
    {
      "role": "Orchestrator",
      "summary": "Git sync verified, memory snapshot taken, 2 patterns applied, INVEST P1-04 selected",
      "context_delta": "Added -VerifyOnly and SYNC_DONE marker; main clone path now configurable",
      "key_decisions": ["Use git -C for cross-clone", "Expose marker for tests"],
      "artifacts": ["scripts/sync-worktree.ps1"]
    },
    {
      "role": "Coder",
      ...
    }
    // ... only  the last 3-4 roles or top deltas; full history is in git + .agent/LOOP_STATE
  ],
  "final_distillation_excerpt": "### Cycle 17 Distillation...\n**Distilled Guidance**:\n- Always surface SYNC_DONE marker for verification...",
  "lessons_learned": [
    "Verification step must be machine-checkable (grep for marker) not just human log reading"
  ],
  "compression_metrics": {
    "handoff_avg_chars": 1240,
    "before_meta_example": "long verbose...",
    "win": "Used delta + link to previous wave"
  },
  "git_evidence": {
    "branch": "feature/sync-verify",
    "last_commit": "Улучшил верификацию sync-worktree с маркером SYNC_DONE",
    "sync_verified": true
  },
  "success_patterns": [
    "Explicit machine-verifiable completion marker in script output",
    "Cross-clone commands always via git -C + full path from config/LOOP_STATE"
  ]
}
```

**Design notes for format:**
- Keep under ~8-12k chars per trajectory (aggressive compression).
- Prefer references (git commit, file paths, wave ids) over full diffs.
- `success_patterns` are the raw material for memory + proposals.
- `compressed_handoff_chain` uses the same `summary` + `context_delta` discipline already enforced.

### 3.2 Proposal Record (output of analyze)
```json
{
  "id": "P-017-01",
  "from_trajectories": ["T-017-3f8a"],
  "target_file": "agentic_loop_template/PROMPT_COMPRESSION_GUIDE.md",
  "change_type": "add_few_shot_example",
  "title": "Add verified 'marker + VerifyOnly' compression example",
  "rationale": "Cycle 17 succeeded with unusually low token use on Orchestrator step because of explicit marker + link strategy. This pattern appeared in 3 prior successful sync-related cycles.",
  "proposed_text": "```... good handoff excerpt ...```",
  "insertion_anchor": "After the 'Good compressed handoff' example",
  "safe_to_auto": true,
  "confidence": 0.82,
  "expected_impact": "Faster planning on infra/sync tasks; lower handoff size for similar work",
  "revert_hint": "Remove the new subsection under 'Few-shot examples'"
}
```

Proposals are collected in `.agent/meta_proposals.json` (or appended to TRAJECTORIES.json) and rendered into `.agent/META_PROPOSALS.md` for human review.

## 4. Python Module Design (memory/meta_harvester.py)
- 100% stdlib + same UTF-8 discipline as questions_collector.py.
- Storage: `.agent/TRAJECTORIES.json` (list of trajectories + proposals index) + per-trajectory files for large ones (optional).
- Public API (exported via `__init__.py`):
  - `harvest_from_handoff(handoff_path, cycle, outcome, quality_signals=None)`
  - `analyze_recent(n=5, min_confidence=0.8) -> list[Proposal]`
  - `generate_proposals(trajectories) -> list[Proposal]`
  - `apply_safe_proposals(proposals, dry_run=False)`
  - `load_config()`, `should_harvest(...)` (mirrors should_escalate)
  - CLI entry: `python -m agentic_loop_template.memory.meta_harvester ...`
- Commands (modeled on collector):
  - `harvest --handoff <path> --cycle N [--outcome DONE]`
  - `list --recent 5`
  - `analyze --recent 5 --min-confidence 0.85`
  - `propose --limit 3`
  - `apply-safe --ids P-017-01,P-017-02 --dry-run`
- Integration points for Reviewer (exact commands to put in role instructions):
  ```powershell
  & ".\.venv\Scripts\python.exe" -m agentic_loop_template.memory.meta_harvester harvest `
      --handoff .agent/last_handoff.json --cycle 17 --outcome DONE
  ```
- Simple deterministic analysis first (keyword/pattern match on success_patterns + quality filters). Later: optional LLM-assisted with the same model (temperature 0.0) using a tiny dedicated prompt that outputs only JSON proposals.
- All writes UTF-8, atomic where possible, auto-update human .md view.

## 5. Updates Required to Core Files (see implementation PR)
- `DEVELOPMENT_STANDARDS.md`: new §12 "Meta-Optimizer and Trajectory Harvesting", extend §3 and §9.
- `AGENT_ROLES.md`: add "Meta Trajectory Harvest duty" and "Review recent meta proposals" to Reviewer block + micro-prompt.
- `HANDOFF_SCHEMA.md`: document new `meta_harvest` object + example updates.
- `PROJECT_CONTEXT_TEMPLATE.md`: "Meta Optimizer Settings" table (parallel to questions pool), example performance metrics section, seed permanent rules.
- `PROMPT_COMPRESSION_GUIDE.md`: new subsection "Meta-harvested examples (auto-contributed)" + guidance for Reviewer/Optimizer on what makes a good candidate for harvesting.
- `memory/README.md` + `__init__.py`: document + re-export the new module (exact same style as questions_collector).
- `Agent-Init.md` / AGENTIC_LOOP_README.md (light mentions).
- Optional: add example `project_config.example.json` snippet.

## 6. Success Metrics & Evaluation
Objective (harvested automatically):
- Reduction in avg `elapsed_minutes` and `tool_calls` for recurring task classes after 2-3 meta cycles.
- Drop in "process_tags" frequency for categories that had meta proposals applied.
- Number of high-confidence proposals that were auto-applied or accepted and later referenced in distillations as "helpful".
- Qualitative: Reviewer notes "meta example from cycle 17 directly improved planning speed in cycle 19".

Guardrails:
- Never more than N proposals per cycle.
- Proposals must cite specific trajectories + quality signals.
- Human override always possible (the pool mechanism).

## 7. Implementation Phases (for SPRINTPLAN)
See separate INVEST list in the proposal response. Minimal first slice: harvest + basic list + one memory category + injection of one verified few-shot into GUIDE.

## 8. Open Questions (to be resolved in first implementation cycle)
- Exact storage layout (single JSON vs per-cycle files) — prototype will decide.
- How aggressively to compress the handoff_chain inside a trajectory (reuse existing compression logic?).
- Whether to persist raw successful handoff JSONs or only the distilled form.
- Initial set of "safe_to_auto" change_types.

**This spec is intentionally narrow and evolutionary** — it adds one new collector/analyzer on top of the existing excellent self-improvement machinery rather than replacing any of it.

Maintainer note: After implementation, update this file with "Implemented in v3.2" marker and move detailed examples of harvested trajectories / proposals into the file itself or SELF_IMPROVEMENT_LOG.
