# Agentix Public Roadmap

[![Version](https://img.shields.io/badge/version-3.14.0-blue?style=flat-square)](CHANGELOG.md)
[![Main README](https://img.shields.io/badge/Main-README-blue?style=flat-square)](README.md)
[![Docs](https://img.shields.io/badge/docs-available-brightgreen?style=flat-square)](docs/README.md)

**Status Date:** 2026-10-25 · **Initiative:** Business Efficiency — **COMPLETE** · **P9 Operator Compose Stack** — **COMPLETE (v3.13.0)** · **P11 Local MCP SSOT** — **v3.14.0** · **Next:** P10 stays Planned until geo-eligible

---

## Completed (P0–P8)

| Phase | Deliverables |
|-------|--------------|
| P1 Metrics | Performance ledger, [metrics-roi](docs/metrics-roi.md) |
| P2 Cross-Platform | Agent-Init.sh, platform-adaptive prompts |
| P3 Productization | docs/, Hub, [consumer-starter](examples/consumer-starter/) |
| P4 Meta | Playbooks runtime, meta harvester |
| P5 Enterprise | Audit log, policy sample, [GitHub Actions](.github/workflows/agentix-loop.yml) |
| P6 DX | Wizard, [demo-loop.sh](scripts/demo-loop.sh), stack templates |
| P7 Sustain | Resume, eval harness, [case study](docs/case-study.md) |
| P8 Harness Hardening | `pyproject.toml` / `agentix` entry points, logging on critical swallows, schema-backed extract+persist, Init.sh/ps1 cold-start parity, state `agent_dir=` (no bind+chdir), CI pytest + mock cycle on `pull_request`. Spec: [2026-08-24-p8-harness-hardening-design.md](docs/superpowers/specs/2026-08-24-p8-harness-hardening-design.md) |

---

## P8 — done (v3.9.0)

Shipped 2026-08-24. Criteria:

- Consumers install via pip/uv without a PYTHONPATH hack.
- No silent swallow on critical supervisor / adapters / proxy paths.
- Adapter handoffs go through schema + `validate_handoff` + atomic persist.
- Init.ps1 and Init.sh give equivalent cold-start (proxy, knowledge, playbooks).
- CI green on the full mock supervisor cycle.

| ID | Task | Status |
|----|------|--------|
| P8-01 | Packaging (`pyproject.toml`, extras, entry points) | Done |
| P8-02 | Observability (WARNING on critical swallows) | Done |
| P8-03 | JSON extraction + adapter persist | Done |
| P8-04 | Init.sh / Init.ps1 cold-start parity | Done |
| P8-05 | State path DI (`agent_dir=`, no bind+chdir) | Done |
| P8-06 | `validate_handoff` ↔ `schemas/handoff.schema.json` | Done |
| P8-07 | CI pytest + mock O→C→T→R on `pull_request` | Done |

Leftover nice-to-haves (docs i18n, embeddings, …) moved to Future.

---

## P9 — done (v3.13.0)

Shipped 2026-09-08 on `feature/p9-operator-stack`. `c198787` is on `main` (`929ff64`); `origin/feature/p9-operator-stack` at `c047efe` has 0 commits that `main` lacks (`HEAD..c047efe` is 0, `c047efe..HEAD` is 31). Criteria:

- Compose profiles for SearXNG (JSON), optional Local Deep Research (LangGraph), optional unpublished Ollama.
- Host bind `127.0.0.1` only; `host.docker.internal` for gateway → pxpipe.
- Gateway/dashboard/pxpipe remain host processes (SR-04).
- Hermetic tests; Docker daemon not required in CI.

| ID | Task | Status |
|----|------|--------|
| P9-01 | `deploy/compose.yaml` + SearXNG JSON settings | Done |
| P9-02 | LDR research profile via gateway `:8110` | Done |
| P9-03 | Optional Ollama profile, no host port | Done |
| P9-04 | `memory.stack` / `memory.search` + `agentix-stack.sh` | Done |
| P9-05 | Docs, roadmap, 3.13.0 | Done |

Spec: [2026-09-08-p9-operator-stack-design.md](docs/superpowers/specs/2026-09-08-p9-operator-stack-design.md). Operator guide: [docs/stack.md](docs/stack.md).

---

## P11 — Local MCP SSOT (v3.14.0)

Port of hub `memory/local_mcp` into this template so consumers get stdlib MCP tools + `run_local_pipeline` without a second memory tree. Shipped in v3.14.0 with `agentix-doctor` and the local supervisor hooks. P10 stays Planned.

| ID | Task | Status |
|----|------|--------|
| P11-01 | `memory/local_mcp/` + schemas + `agentix-local-mcp` | Done |
| P11-02 | Minimal `memory/integrations` for config/notify | Done |
| P11-03 | Knowledge readonly + snapshot non-mutate + logutil LocalRedact | Done |
| P11-04 | Hermetic core tests (defer live Jenkins/Bitbucket CI) | Done |
| P11-05 | `agentix-doctor` + supervisor `adapters_by_role` / stack / meta hooks | Done |

Spec: [2026-10-24-p11-local-mcp-ssot-design.md](docs/superpowers/specs/2026-10-24-p11-local-mcp-ssot-design.md).

---

## P10 — planned: Antigravity CLI as a delegate

The leader stays the Orchestrator (supervisor). `agy` is a **delegate** for role turns the leader assigns (Coder / Tester mechanical work), not a second harness and not a replacement for the Grok path on `:8100`.

Host check on 2026-09-28. The VS Code extension Universal Chat Provider stores an OAuth grant for `cloudcode-pa` in `globalStorage/maxdewald.universal-chat-provider/auth/antigravity-*.json`. That file is not copied into the repo. After the account was authorized again, the access token was valid until `2026-09-28T16:36:39+03:00`.

| Probe | Result |
|-------|--------|
| `fetchAvailableModels` with the VS Code token | **200**. 27 models, including `gemini-3.7-flash-tiered` and `gemini-3.8-flash-tiered`. `agy models` lists the same family plus Claude and GPT-OSS. |
| `generateContent` (`gemini-2.5-flash`, `gemini-3.7-flash-tiered`) | **429** `RESOURCE_EXHAUSTED`. Auth works; quota does not. |
| `agy --print --model gemini-3.7-flash-high` (2026-09-29, active account set, no pxpipe) | Login passes. Google answers **400** `FAILED_PRECONDITION`: `User location is not supported for the API use.` No response text. |
| pxpipe-agy | Shim `:8101` is up. This logged-in `agy --print` never reached it (`events.jsonl` unchanged). The earlier same-day probe still stands: a raw OAuth token is not a Gemini API key (`API_KEY_INVALID`), and `gemini-3.1-*` is `unsupported_model`. |

The delegate does **not** use pxpipe and does **not** read the VS Code OAuth file. As of 2026-09-29 the `agy` login itself succeeds (the earlier `ineligible` check is gone). A direct `--print` still fails on Google's location check, **400** `User location is not supported for the API use.` pxpipe-agy is not a region bypass: P10 must not set `GOOGLE_GEMINI_BASE_URL` and must not call `agy-pxpipe`. The VS Code OAuth token remains unusable as `GEMINI_API_KEY`.

The call is `agy --print` with `modelProvider` `gemini` and `GEMINI_API_KEY` set in the environment. The key is not read from the VS Code auth JSON, not taken from the keyring OAuth grant, and not written into the repo. A later probe still needs a real Gemini API key: the OAuth access token returns `API_KEY_INVALID`, and the keyring `AQ.` token returns `ACCESS_TOKEN_TYPE_UNSUPPORTED`.

| ID | Task | Status |
|----|------|--------|
| P10-01 | `memory/adapters/agy.py`: `RoleAdapter.run_role_turn` via `agy --print` (optional `--agent`) | Planned |
| P10-02 | `get_adapter("agy")` and `supervisor.adapters.agy` in `project_config.example.json` | Planned |
| P10-03 | Preflight: require `GEMINI_API_KEY`; do not open a browser login and do not read the VS Code OAuth file | Planned |
| P10-04 | Adapter env unsets `GOOGLE_GEMINI_BASE_URL`; hermetic test with a fake `agy` that asserts no pxpipe URL | Planned |
| P10-05 | Leader prompt: Orchestrator may hand Coder/Tester work to the `agy` delegate; Reviewer stays on the leader | Planned |

---

## Future

- Hosted Agentix Hub SaaS (optional)
- Full MCP skills for Linear/Jira/Slack
- Operator Messenger (Telegram/MAX)
- Mobile / non-MCP major rewrites (out of scope)

---

## Milestones

| Version | Highlight |
|---------|-----------|
| **v3.14.0** | P11 local MCP, operator doctor, `adapters_by_role`, stack check on start, opt-in harvest on DONE; P10 stays Planned |
| **v3.13.0** | P9: operator Compose stack (SearXNG JSON, LDR LangGraph, optional Ollama); live LLM still pxpipe |
| **v3.12.0** | P8-10: optional embeddings extra; hybrid 0.2 cosine; fail-open substring |
| **v3.11.4** | P8-12: thin loaders + `memory/meta/` + `memory/experience/` + Init.ps1 dotsource |
| **v3.11.3** | P8-13: MultiLLM ontology extracted to `memory/llm_ontology.py` |
| **v3.11.2** | P8-09: Path 1 Russian siblings (`README.ru.md`, `docs/ru/`) |
| **v3.11.1** | NG11: `agent_dir=` + named locks on meta_harvester / eval_harness / resume |
| **v3.11.0** | Conflict-free parallel sessions: leases, `--push`, STOP fan-out, Streams page, live CLI identity |
| **v3.10.1** | Configurable supervisor context caps from `context_budget` / env (P8-14) |
| **v3.10.0** | Opt-in `run-parallel --concurrent` + stdlib `.agent/` lock (P8-11) |
| **v3.9.4** | Token estimate: tiktoken extra, per-model encoding, chars/4 fallback |
| **v3.9.3** | Harvest/reflect skill split (experience-accumulation vs loop-self-improve) |
| **v3.9.2** | Blackbox AI CLI adapter hardening (WM collision, hermetic tests, probe) |
| **v3.9.1** | Opt-in `git-commit-to-jira-tasks` skill (disabled by default; never `--intent git`) |
| **v3.9.0** | P8 Harness Hardening — packaging, observability, extraction/validation, init parity, state DI, CI mock cycle |
| **v3.8.1** | Parallel streams `run-parallel` on 3.8 line; README badges (CI, pxpipe default) |
| **v3.8.0** | Operator Control Plane (`memory.dashboard` HTMX sidecar on `:8112`), not the runner |
| **v3.7.0** | Default request proxy (Agentix gateway `:8110` fronts host pxpipe), fidelity sidecar, FTS5, honest token SLOs |
| **v3.6.0** | Skills + rule compressor + knowledge store + cross-project experience harvest (`audit`/`cycle`) |
| **v3.5.0** | Supervisor CLI, multi-frontend adapters, mock CI cycle |
| **v3.4.0** | P5–P7 complete, initiative closed |
| **v3.3.0** | docs/, Hub, Pro tier |
| **v3.2** | Meta + MCP/vision/isolation |

---

Contributions via the [loop process](README.md#contributing) or [GitHub issues](https://github.com/unhexx/agentic_loop_template/issues). Maintained by **exception.expert**.
