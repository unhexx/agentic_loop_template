# PROJECT_CONTEXT.md

> **Source of Truth:** `TASK_SPECIFICATION.md`

## Project Identification

| Parameter | Value |
|-----------|-------|
| **Project** | Agentix (agentic_loop_template) |
| **Goal** | Self-improving agentic dev loop template |
| **Version** | 3.13.0 |
| **Initiative** | P9 Operator Compose Stack — **COMPLETE** (branch `feature/p9-operator-stack`) |

## Current Status

| Field | Value |
|-------|-------|
| **Cycle** | P9 release |
| **Phase** | P9 done |
| **Status** | DONE on feature branch |
| **Confidence** | 0.9 |

## Final Deliverables

- Compose stack: SearXNG JSON, optional LDR (LangGraph), optional unpublished Ollama
- Live model path unchanged: gateway → host pxpipe
- `memory.stack` / `memory.search` / `scripts/agentix-stack.sh` / `docs/stack.md`

## Reviewer Notes

Control plane stays on host loopback. Ollama is not the default LLM (pxpipe/Grok is). Branch is for evaluation before merge to main.
