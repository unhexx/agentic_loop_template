# Case Study: Agentix Self-Improvement (Dogfood)

Sanitized summary of the Business Efficiency Initiative — 50+ cycles on the template itself.

## Challenge

Transform Agentix from a capable template into a measurable, adoptable, self-improving product with proof points.

## Approach

Closed-loop execution (Orchestrator → Reviewer) across seven phases (P1–P7), dogfooding memory, playbooks, meta-harvester, and performance ledger.

## Results

| Metric | Before | After |
|--------|--------|-------|
| Avg cycle elapsed | ~3+ min (early) | ~1.6 min |
| Avg confidence | ~0.85 | 0.94 |
| Playbooks | Docs only | Runtime + Hub export |
| Cross-platform | Windows-only prompts | Platform-adaptive |
| Productization | README only | docs/, Hub, consumer-starter |
| Enterprise | None | Audit log, policy sample, CI trigger |

## Key Wins

1. **P1 Ledger** — evidence-backed ROI claims in README and docs/metrics-roi.md
2. **P4 Meta + Playbooks** — compounding safe improvements each cycle
3. **P3 GTM** — v3.3.0 release with Hub foundation
4. **P5–P7** — governance, DX wizard, resume/eval harness, case study (v3.4.0)

## Sanitized Trajectory

See [examples/case-study/sanitized-summary.md](../examples/case-study/sanitized-summary.md) for a redacted cycle narrative suitable for public sharing.

## Reproduce

```bash
bash scripts/demo-loop.sh
python -m memory.eval_harness --recent 10
cat .agent/PERFORMANCE_LEDGER.md
```

## Quote (Maintainer)

> "The loop improved itself faster once playbooks and ledger were first-class — numbers replaced anecdotes."