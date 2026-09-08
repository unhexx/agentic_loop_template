# SPRINTPLAN.md

**Sprint:** P9 Operator Compose Stack
**Status:** COMPLETE (v3.13.0) on `feature/p9-operator-stack`
**Goal:** Containerize article-matching research tools (SearXNG, LDR/LangGraph, optional Ollama) without moving pxpipe/gateway off the host.

## Completed This Sprint

- `deploy/compose.yaml` profiles `search` / `research` / `ollama`
- SearXNG JSON (`deploy/searxng/settings.yml`)
- LDR default LLM endpoint = host gateway `:8110` → pxpipe `:8100`
- `memory.stack` contract CLI, `memory.search` JSON client
- `scripts/agentix-stack.sh`, `docs/stack.md`
- Hermetic tests `memory/test_stack.py`, `memory/test_search.py`

## Loop 5 (post-P9, same branch)

- Living PLAN/TODO for 3.13.0
- Init.ps1 / Init.sh `memory.stack` parity
- demo-loop.sh non-fatal stack check
- SearXNG `limiter.toml`
- consumer-starter stack pointer + `AGENTIX_SEARXNG_URL`

## Next

Evaluate `feature/p9-operator-stack`, then merge/tag 3.13.0. Do not merge `main` from the loop.
