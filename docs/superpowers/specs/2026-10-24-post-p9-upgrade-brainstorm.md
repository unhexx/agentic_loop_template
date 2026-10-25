# Post-P9 upgrade brainstorm (2026-10-24)

**Status:** Ideas 1–7 are in tree. Phase E.1 is the default-off gate, now tested. Phase E.2 is this TOP10 closeout. Phase E.3 deferred. Sibling harvest is not a finish criterion.  
**Host probes:** `python -m memory.proxy health` → `ok=true`, `pxpipe_ok=true` `:8100`, `gateway_ok=true` `:8110`  
**Constraint:** Live LLM path stays host pxpipe; P10 must not set `GOOGLE_GEMINI_BASE_URL` / use `agy-pxpipe` as a Gemini region bypass.  
**Parallel:** Hub leases empty at plan time; streams may claim disjoint `owned_paths` per `PARALLEL_PROTOCOL.md`.

## Verdict

Highest leverage after v3.13.0 is not another Compose profile. It is **(1) close Loop-6 hygiene**, **(2) bring `local_mcp` home from agentix-hub**, **(3) make operator preflight + role→adapter routing real**, then **(4) park hard P10 until a region-eligible `GEMINI_API_KEY` exists**.

## Top ideas (ranked)

| Rank | Idea | Impact | Effort | Why now |
|------|------|--------|--------|---------|
| **1** | **Loop-6 hygiene** — L6-02 gitignore (`agentic_loop_template` no trailing `/`); sync `.agent/PLAN`/`TODO`/`SYSTEM_PROMPT` version narrative; treat init as already on `main` (`170a9ee`) | High trust | **S** | Open TODO; consumer symlink bug still in tree |
| **2** | **Backport `memory/local_mcp/` from agentix-hub → SSOT** (stdlib MCP, schemas, `proxy_health`/`proxy_stats`, local pipeline/act) | High DX / CI | **M–L** | Hub already implemented; products fork memory otherwise |
| **3** | **`agentix doctor`** — extend beyond TCP health: port ledger 8100–8114, gateway auth taxonomy (401 vs 400), compose vs host, no restart of `pxpipe.service` during verify | High ops | **S–M** | Chrome-assistant + execute-plan verify pain |
| **4** | **Per-role adapter routing** — `supervisor.adapters_by_role`; leader stays Grok+pxpipe; Coder/Tester may use `cursor` / future `agy` (not Blackbox) | High quality/cost | **M** | `model_routing` is prompt-only today |
| **5** | **Supervisor cold path uses P9 stack** — opt-in `memory.stack check` + optional search before Orchestrator turn; demo parity | Medium | **S** | Stack shipped; loop cold-start still memory/tools-only |
| **6** | **Meta harvest on DONE** — config-gated auto `meta_optimizer` / harvester (META_OPTIMIZER_SPEC §2) | Medium–High | **M** | Machinery exists; trigger still manual |
| **7** | **Live-verify playbook** — pass / environment-blocked / product-bug; regression-tests table template | Medium | **S** | Distilled from execute-plan-7390f537 reviews |
| **8** | **Parallel scaffold** — generator for stream tables (`owned_paths`, worktrees, merge order) from `PARALLEL_PROTOCOL` | Medium | **M** | Protocol mature; product teams still copy-paste |
| **9** | **P10 `agy` delegate** — fail-closed preflight; hermetic fake; no pxpipe URL | High *if* geo OK | **M** | Blocked: 400 location / no valid Gemini key on this host |
| **10** | **Grok-path cost knob pack** — document + default `exact_cache` / fidelity / body budget; `proxy stats` SLOs in operator ritual | Medium | **S** | Already in gateway; under-operated |

## Phased plan

### Phase A — Loop 7 (same week, no parallel conflict)

**Owned paths:** `examples/consumer-starter/`, `.agent/PLAN.md`, `.agent/TODO.md`, `SYSTEM_PROMPT.md`, `docs/superpowers/specs/2026-10-24-*`  
**Do not touch:** `memory/adapters/`, `memory/proxy/`, `deploy/`

1. L6-02 gitignore fix + close L6-01 as superseded by `170a9ee` (note bundling debt in CHANGELOG if needed).  
2. Refresh living PLAN/TODO to Loop 7 / this brainstorm.  
3. Bump `SYSTEM_PROMPT.md` template version string to 3.13.0.  
4. Add short “operator doctor” section draft in `docs/proxy.md` (commands only; code optional).

**Exit:** green `pytest` subset for consumer/init if touched; human review; no `main` push from agents.

### Phase B — P11 Local MCP SSOT (parallel-friendly)

**Stream A `owned_paths`:** `memory/local_mcp/`, `memory/test_local_mcp*.py`, `pyproject.toml` entry points  
**Stream B `owned_paths`:** `docs/`, `TOOLS_REGISTRY.md`, `skills/`  
**Integration:** worktree merge; Reviewer gate; hub leases for hub `.agent/` writers only

1. Port hub `memory/local_mcp` with hermetic tests (no live Jenkins/Bitbucket required).  
2. Register `agentix-mcp` / `python -m memory.local_mcp` entry.  
3. Document external `tool-*` repos as loopback HTTP, not vendored.  
4. Keep hub as consumer (symlink/pin), not second SSOT.

**Exit:** mock MCP tool list + one `proxy_health` tool against fake TCP; CI green.

### Phase C — Routing + doctor (after or overlapping B docs stream)

**Owned paths:** `memory/supervisor.py`, `memory/adapters/`, `.agent/project_config.example.json`, `docs/model-routing.md`

1. Implement `adapters_by_role` (default: all → `supervisor.adapter`).  
2. Optional `cursor` (or later `agy`) for Coder/Tester — do **not** route roles to Blackbox.  
3. `doctor` CLI aggregating `proxy health --strict`, port ledger, stack check.  
4. Wire optional `stack check` into init / supervisor preflight (config flag).

**Exit:** hermetic tests for role→adapter selection; live dogfood still Grok+pxpipe for Orchestrator/Reviewer.

### Phase D — P10 when unblocked (parked)

Only when a real `GEMINI_API_KEY` works in-region:

1. `memory/adapters/agy.py` + P10-01–04 as already specified in ROADMAP.  
2. Map `model_routing.roles.*.model` → `agy --print --model …`.  
3. Assert env has no `GOOGLE_GEMINI_BASE_URL`.

Until then: keep ROADMAP P10 Planned; do not shim via pxpipe-agy.

### Phase E — Self-improve (later)

1. Config-gated meta harvest on Reviewer DONE.  
2. Refresh `docs/TOP10_IMPROVEMENTS.md` with P8–P11 rows.  
3. Re-run `experience_harvester cycle` on `_PROJECT` (chrome-assistant, tool-*, docloom).

## Parallel session recipe (this initiative)

```bash
# Hub stays on main; claim before worktrees
python -m memory.stream_lease claim \
  --stream p11-mcp:memory/local_mcp/,memory/test_local_mcp.py,pyproject.toml \
  --workdir . --worktree ../agentic-loop-worktrees/p11-local-mcp

python -m memory.stream_lease claim \
  --stream p11-docs:docs/,TOOLS_REGISTRY.md,skills/ \
  --workdir . --worktree ../agentic-loop-worktrees/p11-docs
```

Hot-file ban while streams live: `DEVELOPMENT_STANDARDS.md`, `schemas/handoff.schema.json`, package root `__init__.py` without Integration owner.

## Non-goals

- Moving pxpipe/gateway into Docker  
- Using `agy-pxpipe` / second `:8101` shim as P10  
- Hosted Hub SaaS / messenger (ROADMAP Future)  
- Merging `main` from the agent loop

## Research sources (this cycle)

- Template: ROADMAP, PARALLEL_PROTOCOL, proxy/stack/model-routing docs, `.agent/TODO` Loop 6  
- Sibling: `agentix-hub/memory/local_mcp`, chrome-assistant execute-plan verify docs, `tool-mcp-bridge`  
- Live: gateway health JSON ok; pxpipe `:8100` nginx HTML on bare `/health` (TCP ok; use Agentix health CLI)
