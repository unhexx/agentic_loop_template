# Agentix request proxy

[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](../README.md)
[![Version](https://img.shields.io/badge/version-3.13.0-blue?style=flat-square)](../CHANGELOG.md)

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

## Operator doctor

```bash
python -m memory doctor --strict
# or: agentix-doctor --strict
```

Aggregates `memory.proxy health`, a loopback port ledger (`:8100` pxpipe, `:8110` gateway, `:8112` control plane, optional SearXNG/LDR), deploy stack contract, and a P10-safe Gemini env check (`GEMINI_API_KEY` presence; flags `GOOGLE_GEMINI_BASE_URL` if set). Findings use pass / environment / product taxonomy. Does **not** restart `pxpipe.service` or invent secrets. A healthy TCP path can still return HTTP 401 without a bearer — classify that as environment-blocked.

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

The live CLI posts Cloud Code traffic to `daily-cloudcode-pa.googleapis.com` (`/v1internal:streamGenerateContent` and `/v1internal:loadCodeAssist`). It accepts only `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, and `gemini-3.8-flash-low`. pxpipe's measured imager key is still `gemini-3.7-flash`. `proxy.mjs` calls `transformGoogleGenerateContent` with that key, then forwards the envelope to the same Cloud Code host over HTTPS.

Do not add Gemini to the Grok unit (`scripts/systemd/pxpipe.service.example`, host `:8100`).

| Port | Unit | Role |
|------|------|------|
| `:8100` | `pxpipe.service` | Grok imager — leave it |
| `:8101` | `pxpipe-agy-shim` | `proxy.mjs`: compress, then HTTPS to Cloud Code |
| `:8103` | `pxpipe-agy` | Anthropic shadow only. Upstream `:8102` is closed, so `/health` is 502 |
| `:47821` | — | warp's built-in Anthropic target. Closed. The wrapper shadows it with `:8103` |

Templates: [`scripts/pxpipe-agy/proxy.mjs`](../scripts/pxpipe-agy/proxy.mjs), [`scripts/pxpipe-agy/agy-pxpipe`](../scripts/pxpipe-agy/agy-pxpipe), [`scripts/pxpipe-agy/agy-pxpipe-run`](../scripts/pxpipe-agy/agy-pxpipe-run), [`scripts/systemd/pxpipe-agy-shim.service.example`](../scripts/systemd/pxpipe-agy-shim.service.example). [`scripts/pxpipe-agy/shim.py`](../scripts/pxpipe-agy/shim.py) is the old sidecar and binds both `:8101` and `:8102`. Do not start it. Copy-paste install: [README — pxpipe for agy](../README.md#pxpipe-for-agy-gemini-38-flash).

The wrapper does not set `GOOGLE_GEMINI_BASE_URL` or `PORT`. It writes the agy argv to a mode-600 NUL file so `pxpipe warp` does not log the `--print` prompt, then routes `daily-cloudcode-pa.googleapis.com/*` to `:8101`.

```bash
curl -sS http://127.0.0.1:8101/health
agy-pxpipe --model gemini-3.8-flash-high --print 'Reply with exactly PONG and nothing else.'
```

`status` in `~/.pxpipe-agy/events.jsonl` for a `proxy.mjs` line is the upstream HTTP status. A line with `error: upstream_error: fetch failed` on `/health` is `:8103` talking to the closed `:8102`, not a failure of `:8101`.

A hang in `SYN-SENT` means DNS returned an address that never accepts TCP (seen for Cloud Code names and `antigravity-unleash.goog` when the VPN resolver answers `89.39.161.8`). systemd-resolved is consulted before `/etc/hosts`, so a hosts pin does not fix `getaddrinfo`. Route only those names at a loopback stub that answers from DNS-over-HTTPS. Leave every other name on the tunnel resolver.

Tests stay skip/exempt for this frontend (`memory/test_proxy.py` still requires pxpipe only for live Grok under `mode=required`). Geo eligibility fails as HTTP 400 `FAILED_PRECONDITION` / `User location is not supported for the API use.` on `loadCodeAssist` even after DNS and compression succeed. That is not a proxy bug and not something this path can bypass.

Do not claim billed `measured_saved_pct` until Google `:countTokens` probes populate on this instance.

The OAuth token stored by the VS Code extension Universal Chat Provider (`antigravity-*.json`) is a `cloudcode-pa` grant, not a Gemini API key. A 2026-09-28 probe got `API_KEY_INVALID` for that token. Planned P10 in [ROADMAP.md](../ROADMAP.md) does not call `agy-pxpipe` and does not set `GOOGLE_GEMINI_BASE_URL`: the delegate uses `GEMINI_API_KEY` and talks to Google directly. Grok traffic stays on `:8100`.

## Rollback

`AGENTIX_PROXY=0` is enough for **supervisor / GrokAdapter**: the grok subprocess no longer receives `GROK_CLI_CHAT_PROXY_BASE_URL` / `AGENTIX_GATEWAY_URL`, so it uses the CLI public default.

Interactive `grok` in a shell that already sourced `.venv/bin/activate` still has the Init export. Full public rollback:

1. `export AGENTIX_PROXY=0`
2. `unset GROK_CLI_CHAT_PROXY_BASE_URL AGENTIX_GATEWAY_URL` (or start a new shell without sourcing activate)
3. Stop `agentix-gateway` / pxpipe if you no longer need them
4. `proxy.fidelity=false` / FTS unused if query falls back to LIKE

pxpipe is **not** vendored. Imaging stays in the host process; the Python gateway only reverse-proxies.
