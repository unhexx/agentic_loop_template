---
name: reflective-improvement
author: agentix / exception.expert (adapted from eegent)
version: 1.1.0
---

# reflective-improvement

**Purpose**: Turn every completed task, error, or cycle into systematic, persistent improvement of agent behavior, knowledge, playbooks, and the loop itself.

**Owner context**: Agentix `.agent/` (LOOP_STATE, PERFORMANCE_LEDGER, PLAYBOOKS, META_PROPOSALS, TRAJECTORIES) + memory layer + Reviewer role.

## Core Principles

- **Ritual over ad-hoc**: Always follow the structured 6-step reflection. Never skip analysis or memory update.
- **Persistent memory first**: Prefer `python -m memory` update / playbooks curate / meta_harvester over free-text only.
- **Actionable output**: Every reflection ends with concrete proposed changes (playbook bullets, prompt deltas, INVEST items) with evidence.
- **Safety**: High-impact changes (skill rewrite, bulk memory, standards) require explicit confirmation or dry-run.
- **Evidence-based**: Reference handoff, ledger metrics, test results, prior trajectories.
- **Language split**: Skill/logic English for portability; commit messages and user notes may be Russian.
- **Loop integration**: Feed outputs into PLAN/TODO, ledger, playbooks, META_PROPOSALS.

## Standard Workflow (imperative)

1. **Trigger & gather**
   - Scope: single task, cycle, error cluster, full review, or **parent-folder harvest**.
   - Fetch: last handoff JSON, PERFORMANCE_LEDGER recent, LOOP_STATE snapshot, relevant playbooks, test output.
   - Parent-folder session: `python -m memory.experience_harvester cycle --parent <_PROJECT>` (dry-run first). Reads AGENTS.md / playbooks, not only LESSONS.md.

2. **Structured Reflection Ritual**
   - **Review**: Goal, actions, outcome vs expected, confidence.
   - **Successes**: What worked; which patterns/tools/playbooks helped.
   - **Issues**: Root causes (prompt, tool, assumption, missing context, env).
   - **Lessons**: 3–7 reusable insights, tagged `[PROMPT] [TOOL] [MEMORY] [PLANNING] [CONTEXT]`.
   - **Memory update**: `python -m memory update ...` or playbooks curate; link to cycle id.
   - **Propose actions**: playbook bullets, prompt micro-edits, new INVEST, meta proposals.

3. **Output format** (JSON preferred in handoff + human summary)
   - successes / reinforced_patterns
   - issues / root_causes
   - lessons (numbered, tagged)
   - memory_updates
   - proposed_changes (exact instructions or diffs)
   - next_experiments
   - confidence

4. **Apply & verify**
   - Low-risk: append LESSONS / update memory / curate playbook.
   - High-impact: propose + dry-run (`meta_harvester apply-safe --dry-run`) or ask confirmation.
   - Log in PERFORMANCE_LEDGER / META_PROPOSALS / SELF_IMPROVEMENT if present.

5. **Close the loop**
   - On DONE: Reviewer MUST run this skill (or equivalent meta_harvester harvest + reflect).
   - If the session spanned `../_PROJECT/*`, apply `experience_harvester cycle --parent ..` and record `memory_updated`.
   - Suggest decomposition into `.agent/TODO.md` / PLAN.

## Trigger Phrases

- "рефлексия после задачи" / "сделай рефлексию"
- "analyze approach" / "improve for X"
- "review session" / "self-improvement"
- Reviewer handoff with status DONE

## Integration with Agentix

| Component | How |
|-----------|-----|
| Reviewer role | Mandatory on DONE before final handoff |
| meta_harvester | harvest → analyze → propose after reflection |
| playbooks | curate high-value lessons as bullets |
| context_budget / compressor | Reflect on compression wins; update few-shots in PROMPT_COMPRESSION_GUIDE |
| supervisor | Can invoke on cycle end via adapter |

## Safety

- No bulk destructive memory without confirmation.
- Distinguish proposed vs applied.
- Keep reflections concise and evidence-rich.
