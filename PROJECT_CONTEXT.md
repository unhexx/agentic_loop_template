# PROJECT_CONTEXT.md

> **Source of Truth:** `TASK_SPECIFICATION.md`

## Project Identification

| Parameter | Value |
|-----------|-------|
| **Project** | Agentix (agentic_loop_template) |
| **Goal** | Self-improving agentic dev loop template |
| **Version** | 3.14.0 |
| **Initiative** | P9 is complete on main. P11, doctor, and local tools are 3.14.0. P10 stays Planned. |

## Current Status

| Field | Value |
|-------|-------|
| **Cycle** | 3.14.0 |
| **Phase** | Released on main |
| **Status** | v3.14.0 on this commit. |
| **Confidence** | 0.9 |

## Final Deliverables

- Compose stack: SearXNG JSON, optional LDR (LangGraph), optional unpublished Ollama
- Live model path unchanged: gateway → host pxpipe
- `memory.stack` / `memory.search` / `scripts/agentix-stack.sh` / `docs/stack.md`

## Reviewer Notes

Control plane stays on host loopback. Ollama is not the default LLM (pxpipe/Grok is). P9 is already on main. Do not revive the old P9 feature branch. P10 stays parked.
