# Metrics & ROI Proof

Agentix dogfoods its own performance ledger during the Business Efficiency Initiative.

## Measured Results (50+ cycles)

| Metric | Value |
|--------|-------|
| Cycles tracked | 50+ |
| Avg elapsed (recent) | ~1.6 min |
| Avg confidence | 0.94 |
| Meta proposals applied | 5+ per qualifying cycle band |
| Tests failed (recent) | 0 |

Source: `.agent/PERFORMANCE_LEDGER.md` and `.agent/PERFORMANCE_LEDGER.json`.

## What Improved

- **Playbooks runtime** — select/curate wired into all roles; measurable cycle time reduction.
- **Meta harvester** — safe auto-apply proposals compound process gains.
- **Cross-platform** — Linux worktree validates portable bootstrap without PowerShell dependency.

## How to Track Your Project

```bash
# Append cycle stats (Reviewer duty)
python -m memory.performance_ledger append --cycle N --elapsed 1.5 --confidence 0.9

# View report
cat .agent/PERFORMANCE_LEDGER.md
```

Include the `performance` object in every high-quality DONE handoff per `HANDOFF_SCHEMA.md`.

## Request-proxy stats (v3.7)

```bash
python -m memory.proxy stats --json
```

Reviewer may copy a subset into `performance_ledger` details and optional handoff `proxy_stats`. **Measured raw-token savings** (`count_tokens` probes) are **unprobed** on this host (`measured_saved_pct` is `null`). Do not claim a raw-token % we have not measured. File-side compressor reports and pxpipe `compressed` / system-prompt reuse remain the honest instruments. See [proxy.md](proxy.md).

## Business Claims

Replace generic productivity claims with ledger-backed numbers:

- "Reduced average cycle elapsed time to ~1.6 min with 0.94 confidence over 50+ self-improvement cycles."
- "Meta-optimizer applied 5+ safe improvements per iteration band."

Update this page as your project accumulates cycles.