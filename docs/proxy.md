# Agentix request proxy

[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../README.md)
[![Version](https://img.shields.io/badge/version-3.7.0-blue?style=flat-square)](../CHANGELOG.md)

By default, **live** agent LLM requests in derived projects go through an in-template OpenAI/Responses gateway that **fronts host pxpipe**. Mock adapter and CI mock cycles stay proxy-free.

```
Grok CLI / GrokAdapter
  → Agentix gateway :8110  (stdlib, loopback, /v1/*)
      → audit JSONL → exact cache (project-scoped) → fidelity sidecar → distill old turns
      → pxpipe :8100 (host imager; not vendored)
      → cli-chat-proxy.grok.com
```

Knowledge retrieval and harvest stay supervisor/ritual-side. The HTTP gateway does not guess `cwd`.

## Contract

| Knob | Default | Meaning |
|------|---------|---------|
| `proxy.mode` | `required` | Live adapters fail closed if pxpipe is down |
| `AGENTIX_PROXY=0` | — | Explicit opt-out (`mode=off`) |
| `proxy.mode=preferred` | not the example default | Warn and continue; documented escape hatch |
| Mock / CI | exempt | Never talks to a model |

Do **not** auto-edit `~/.grok/config.toml`. Init writes exports into `.venv/bin/activate`. Opt-in: `python -m memory.proxy install-host`.

## Commands

```bash
python -m memory.proxy health --json
python -m memory.proxy health --strict          # exit 1 if pxpipe is down
python -m memory.proxy serve --host 127.0.0.1 --port 8110
python -m memory.proxy stats --json
python -m memory.proxy install-venv
python -m memory.proxy install-host --dry-run   # optional TOML merge
bash scripts/agentix-proxy.sh start|health|stats
```

Env (highest wins):

| Variable | Role |
|----------|------|
| `AGENTIX_PROXY` | `1` / `0` / `off` |
| `AGENTIX_PROXY_MODE` | `required` \| `preferred` \| `off` |
| `GROK_CLI_CHAT_PROXY_BASE_URL` | What Grok CLI dials (`http://127.0.0.1:8110/v1`) |
| `AGENTIX_GATEWAY_URL` | Gateway base |
| `AGENTIX_PXPIPE_URL` | Upstream imager (`http://127.0.0.1:8100`) |
| `AGENTIX_PROJECT_ROOT` | Enables exact-hash cache + JSONL audit |

## Token SLOs (honest)

Three instruments: pxpipe `stats --json`, project `.agent/proxy_events.jsonl`, file-side compressor reports.

| SLO | Target | Instrument | Notes |
|-----|--------|------------|-------|
| Proxy path coverage | 100% of non-mock LLM calls when `mode=required` | gateway/pxpipe health in GrokAdapter | CI mock exempt |
| File-side compress when over budget | ≥25% token cut vs raw `tokens_in` | compressor report | Acon paper cited in compressor: 26–54% peak |
| pxpipe eligible-request compress rate | ≥60% when pxpipe present | `pxpipe stats` `compressed` | Host is typically ~73% |
| System-prompt reuse | ≥90% when pxpipe present | `systemShaHist` | Host is typically ~99% |
| Gateway add-on latency | p50 < 100 ms (exclude upstream) | `proxy_events.jsonl` `duration_ms` vs pxpipe `transform_ms` | |
| Exact-cache | report hit rate | `.agent/proxy_cache.sqlite` | no numeric SLO until n≥50 |
| **Measured raw-token saved %** | **unslod** | pxpipe `count_tokens` probes | **0 on this host; `measured_saved_pct` is null** |

Do not claim a raw-token savings percentage until `baselineMeasuredEvents > 0`.

```bash
python -m memory.proxy stats --json
```

## Foreign CLIs (documented, not auto-wrapped)

Cursor / Claude Code / Blackbox do **not** honor `GROK_CLI_CHAT_PROXY_BASE_URL`. Optional `pxpipe warp --route` recipes can intercept some of them. Agentix 3.7 does **not** auto-wrap foreign CLIs (too easy to break first-party gates). Manual warp is an ops choice, not a template default.

## Rollback

1. `export AGENTIX_PROXY=0`
2. Point `GROK_CLI_CHAT_PROXY_BASE_URL` back at `http://127.0.0.1:8100/v1` and stop `agentix-gateway`
3. `proxy.fidelity=false` / FTS unused if query falls back to LIKE

pxpipe is **not** vendored. Imaging stays in the host process; the Python gateway only reverse-proxies.
