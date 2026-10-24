# Live verify playbook

Classify every live check as one of:

| Class | Meaning | Example |
|-------|---------|---------|
| **pass** | Expected product behavior observed | Handoff validates; gateway returns 200 with auth |
| **environment-blocked** | Host/setup prevents the check; not a product bug | `pxpipe` down; HTTP 401 without bearer; missing `GEMINI_API_KEY` |
| **product-bug** | Code/config under test is wrong while env is ready | 500 with valid auth; wrong handoff schema |

## Rules

1. Run `python -m memory doctor` (or `agentix-doctor`) first. Record `ok`, port ledger, and findings.
2. Do **not** invent API keys, restart `pxpipe.service`, or force a new login session during verify.
3. Healthy TCP (`proxy health` / doctor `ok=true`) can still mean chat HTTP **401** without a bearer — that is **environment-blocked**, not a product regression.
4. Prefer worktree/install skew notes: compare installed unit vs the branch under review before filing product-bug.
5. Write evidence under `docs/review/` (or the stream worktree): timestamp, command, class, one-line result. No secrets in logs.

## Minimal ritual

```bash
python -m memory doctor --strict --json
python -m memory.proxy health --strict --json
# optional research stack files only (no daemon required):
python -m memory.stack check
```

For MCP tools instead of shell recipes:

```bash
python -m memory.local_mcp list-tools
# proxy_health / proxy_stats via MCP when the host attaches agentix-local-mcp
```

## Adapter note

Live leader path is **Grok → gateway :8110 → pxpipe :8100**. Do not route verify or role turns through Blackbox. Optional Coder/Tester overrides use `supervisor.adapters_by_role` with `grok`, `cursor`, `mock`, or (when unblocked) `agy` — never Blackbox.
