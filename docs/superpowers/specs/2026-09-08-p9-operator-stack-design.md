# P9 Operator Compose Stack — Design (Agentix v3.13.0)

**Date:** 2026-09-08  
**Status:** Accepted for implementation on `feature/p9-operator-stack`  
**Target version:** **3.13.0** (minor: new operator surface — Compose stack, search client, docs)

Source article: Xakep «трюки» on Local Deep Research (LearningCircuit): SearXNG metasearch, LangGraph Agent strategy, Docker, SQLCipher data volume, optional local LLM. Agentix already owns the live LLM path (Grok CLI → gateway `:8110` → **host pxpipe** `:8100`). This spec adds the **research/search containers** without moving the control plane off loopback.

## Goal

Ship a production-grade, profiled Docker Compose stack in the template so a derived project can stand up **the tools the article actually needs**, while every live model request still goes through pxpipe.

## Non-goals

- Vendoring pxpipe (host imager, not a template image).
- Default Ollama/GPU (huge, unauthenticated daemon; article’s local LLM stays optional).
- Reimplementing LangGraph inside Agentix (LDR already ships `langgraph-agent`).
- Putting dashboard/gateway in Compose by default (TeleGrok SR-04: loopback peer check breaks on Docker NAT).

## Stack

| Profile | Service | Host bind | Role |
|---------|---------|-----------|------|
| `search` (default up) | SearXNG | `127.0.0.1:8080` | Privacy metasearch; **JSON API on** |
| `research` | Local Deep Research | `127.0.0.1:5000` | LangGraph agentic research; SQLCipher volume |
| `ollama` | Ollama | **unpublished** | Optional local LLM on the compose network only |

Live LLM (default research path):

```
LDR  →  host.docker.internal:8110/v1  →  Agentix gateway  →  pxpipe :8100  →  Grok
```

`extra_hosts: host.docker.internal:host-gateway` on Linux. Gateway and dashboard stay **host processes**. pxpipe stays **host**. `proxy.mode=required` unchanged; mock/CI still skip.

## Security defaults

- Publish only `127.0.0.1`, never `0.0.0.0`.
- `no-new-privileges:true`.
- SearXNG: `public_instance: false`, `limiter: false` (single-operator), `formats: [html, json]`.
- Ollama: no host port (no auth).
- Secrets only in `deploy/compose.env` (gitignored); example file is committed.
- Control plane (gateway `:8110`, dashboard `:8112`) not containerized by default.

## Code surface

| Path | Responsibility |
|------|----------------|
| `deploy/compose.yaml` | Compose SSOT, profiles |
| `deploy/compose.env.example` | Operator env |
| `deploy/searxng/settings.yml` | JSON-enabled SearXNG |
| `memory/stack.py` | Contract + CLI (`python -m memory.stack`) |
| `memory/search.py` | stdlib SearXNG JSON client |
| `scripts/agentix-stack.sh` | up/down/ps/health |
| `docs/stack.md` | Operator guide |

Tests are hermetic (file contract + urllib mock). Docker daemon is **not** required in CI.

## Version

Minor **3.13.0**: new operator compose + CLI. Existing `python -m memory.proxy` / supervisor / mock cycle unchanged.
