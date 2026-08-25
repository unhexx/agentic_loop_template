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
| `proxy.mode` | `required` | Live adapters fail closed if pxpipe **or** the URL Grok dials is down |
| `AGENTIX_PROXY=0` | — | Opt-out (`mode=off`): subprocess drops Agentix chat-proxy URL |
| `proxy.mode=preferred` | not the example default | Never fail-closed; public fallback exists only inside a running gateway |
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

Cursor / Claude Code / Blackbox / Antigravity (`agy`) do **not** honor `GROK_CLI_CHAT_PROXY_BASE_URL`. Optional `pxpipe warp --route` recipes can intercept some of them. Agentix 3.7 does **not** auto-wrap foreign CLIs (too easy to break first-party gates). Manual wrap is an ops choice, not a template default.

### agy (Antigravity CLI) — optional second pxpipe

`agy` in API-key mode (`security.auth.selectedType=gemini-api-key`) posts to `generativelanguage.googleapis.com/v1beta/models/{id}:generateContent`. pxpipe 0.13.2 only transforms `/google-ai-studio/(v1|v1beta)/models/{id}:(generateContent|streamGenerateContent)` and the measured key `gemini-3.7-flash`. Ids `gemini-3.7-flash-high` / `-medium` hit `hasGeminiMeasuredProfile` → `unsupported_model` even if they are on `PXPIPE_MODELS`. `PXPIPE_GPT_PROFILES` does not clear that Google-transformer gate. Warp MITM does not rewrite the path.

Stand up a **second** instance. Do not add Gemini to the Grok unit (`scripts/systemd/pxpipe.service.example`, host `:8100`).

| Port | Unit | Role |
|------|------|------|
| `:8100` | `pxpipe.service` | Grok imager — leave it |
| `:8101` | `pxpipe-agy-shim` | agy-facing rewrite |
| `:8103` | `pxpipe-agy` | `PXPIPE_MODELS=gemini-3.7-flash` |
| `:8102` | shim outbound | strip `/google-ai-studio` → Google |

Templates: [`scripts/pxpipe-agy/shim.py`](../scripts/pxpipe-agy/shim.py), [`scripts/pxpipe-agy/agy-pxpipe`](../scripts/pxpipe-agy/agy-pxpipe), [`scripts/systemd/pxpipe-agy.service.example`](../scripts/systemd/pxpipe-agy.service.example), [`scripts/systemd/pxpipe-agy-shim.service.example`](../scripts/systemd/pxpipe-agy-shim.service.example). Copy-paste install and print examples: [README — pxpipe for agy](../README.md#pxpipe-for-agy-gemini-37-flash).

Shim rewrite:

1. `/v1beta/models/gemini-3.7-flash-high:generateContent` → `/google-ai-studio/v1beta/models/gemini-3.7-flash:generateContent`
2. If the body has no `generationConfig.thinkingConfig`, inject `thinkingLevel` `HIGH` / `MEDIUM` / `LOW` from the suffix
3. Forward `x-goog-api-key` / `key=` / `Authorization` as-is; do not log bodies

```bash
# health + last rewrite
curl -sS http://127.0.0.1:8101/health
# live
agy-pxpipe --model gemini-3.7-flash-high --print='Reply with exactly PONG and nothing else.'
# events (compressed=true, model=gemini-3.7-flash)
tail -n 1 ~/.pxpipe-agy/events.jsonl
```

Tests stay skip/exempt for this frontend (`memory/test_proxy.py` still requires pxpipe only for live Grok under `mode=required`). Geo eligibility (`agy` “not available in your location”) fails before generateContent — then only the synthetic POST to `:8101` proves imaging.

Do not claim billed `measured_saved_pct` until Google `:countTokens` probes populate on this instance.

## Rollback

`AGENTIX_PROXY=0` is enough for **supervisor / GrokAdapter**: the grok subprocess no longer receives `GROK_CLI_CHAT_PROXY_BASE_URL` / `AGENTIX_GATEWAY_URL`, so it uses the CLI public default.

Interactive `grok` in a shell that already sourced `.venv/bin/activate` still has the Init export. Full public rollback:

1. `export AGENTIX_PROXY=0`
2. `unset GROK_CLI_CHAT_PROXY_BASE_URL AGENTIX_GATEWAY_URL` (or start a new shell without sourcing activate)
3. Stop `agentix-gateway` / pxpipe if you no longer need them
4. `proxy.fidelity=false` / FTS unused if query falls back to LIKE

pxpipe is **not** vendored. Imaging stays in the host process; the Python gateway only reverse-proxies.
