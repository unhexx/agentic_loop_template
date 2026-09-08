# .agent/PLAN.md — Living Project Plan

**Initiative:** P9 Operator Compose Stack — **COMPLETE** on `feature/p9-operator-stack`
**Template Version:** 3.13.0
**Last Update:** 2026-09-08
**Loop:** 5 (post-P9 hardening, still not merging `main`)

## Phase Status

| Phase | Status |
|-------|--------|
| P0–P8 | COMPLETE (see ROADMAP) |
| P9 Operator stack | COMPLETE (v3.13.0) |
| Loop 5 follow-up | IN PROGRESS |

## P9 delivered

- `deploy/compose.yaml` profiles `search` / `research` / `ollama`
- SearXNG JSON on `127.0.0.1:8080`; LDR via host gateway → pxpipe
- `memory.stack` / `memory.search` / `scripts/agentix-stack.sh` / `docs/stack.md`
- Live pxpipe path unchanged (`proxy.mode=required`)

## Loop 5 INVEST (this cycle)

1. Living PLAN/TODO match 3.13.0
2. Init.ps1 stack tip (parity with Init.sh)
3. demo-loop.sh: non-fatal `memory.stack check`
4. SearXNG `limiter.toml` so 2026 image does not warn
5. consumer-starter pointer to stack + `AGENTIX_SEARXNG_URL`

Do **not** merge to `main` until operator review of the feature branch.
