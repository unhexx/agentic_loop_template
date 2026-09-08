# Operator Compose stack

[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../README.md)
[![Version](https://img.shields.io/badge/version-3.13.0-blue?style=flat-square)](../CHANGELOG.md)

Local Deep Research–style tooling for Agentix: **SearXNG** (JSON metasearch), optional **Local Deep Research** (LangGraph agent), optional **Ollama**. Live model calls stay on the existing path:

```
agent / LDR  →  gateway :8110  →  host pxpipe :8100  →  Grok
```

Gateway (`:8110`) and Control Plane (`:8112`) stay **host processes** (loopback peer check). pxpipe is **not vendored**.

## Why this split

The Xakep LDR write-up runs SearXNG + research UI + a local LLM in Docker. Agentix already compresses every live Grok request through pxpipe. Putting the gateway inside Compose would DNAT loopback and break dashboard/gateway SR-04. So:

| Runs in Compose | Stays on the host |
|-----------------|-------------------|
| SearXNG `:8080` | pxpipe `:8100` |
| LDR `:5000` (profile `research`) | Agentix gateway `:8110` |
| Ollama (profile `ollama`, **no host port**) | Control Plane `:8112` |

## Quick start

```bash
python -m memory.stack check
bash scripts/agentix-stack.sh up                 # SearXNG only
curl -sS 'http://127.0.0.1:8080/search?q=test&format=json' | head
python -m memory search --q "langgraph agent" --json
```

Deep research UI (needs Docker image pull + running gateway/pxpipe):

```bash
python -m memory.proxy health --strict
bash scripts/agentix-stack.sh up --research
# http://127.0.0.1:5000  — LLM endpoint is host.docker.internal:8110/v1
```

Fully local LLM instead of Grok (optional, large):

```bash
bash scripts/agentix-stack.sh up --research --ollama
# then set LDR_LLM_PROVIDER=ollama in deploy/compose.env
```

## Files

| Path | Role |
|------|------|
| [`deploy/compose.yaml`](../deploy/compose.yaml) | Profiles `search` / `research` / `ollama` |
| [`deploy/compose.env.example`](../deploy/compose.env.example) | Copy to `deploy/compose.env` |
| [`deploy/searxng/settings.yml`](../deploy/searxng/settings.yml) | `formats: [html, json]` |
| [`deploy/searxng/limiter.toml`](../deploy/searxng/limiter.toml) | silences SearXNG 2026 missing-config warning |
| [`scripts/agentix-stack.sh`](../scripts/agentix-stack.sh) | up / down / ps / health |
| [`memory/stack.py`](../memory/stack.py) | Port/profile contract |
| [`memory/search.py`](../memory/search.py) | stdlib JSON client |

## Security

- Publish `127.0.0.1` only.
- `no-new-privileges:true`.
- SearXNG is not a public instance; JSON is on because LDR and `memory.search` need it (default image 403s `format=json`).
- Ollama is unpublished: the daemon has no auth.
- Replace `SEARXNG_SECRET` / `server.secret_key` before any reverse proxy.

## pxpipe

`proxy.mode=required` is unchanged. LDR’s OpenAI-compatible endpoint points at the **host gateway**, which fronts pxpipe. Mock/CI never talk to a model. Opt out of the live proxy with `AGENTIX_PROXY=0`.

Contract tests: `python -m pytest -q memory/test_stack.py memory/test_search.py`.
