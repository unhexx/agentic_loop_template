# Experience Extraction Toolkit (v3.3)

## Purpose

Pull recurring failures and strategies from real project runs into workspace memory so Orchestrator snapshots prevent repeats.

## Commands

```bash
# Seed high-value defaults (from multi-project analysis)
python -m memory.experience_harvester seed-defaults --apply

# Scan parent folder of many projects (read-only sources)
python -m memory.experience_harvester scan --parent /path/to/_PROJECT --apply

# Dry-run
python -m memory.experience_harvester scan --parent /path/to/_PROJECT
```

## Categories written

- `Common Failure Patterns`
- `Effective Loop Strategies`
- `High-Value Compression Patterns`
- `Meta Improvement Patterns`

## Integration

- **Orchestrator:** `python -m memory query --top 5 --category "Common Failure Patterns"` at cycle start.  
- **Reviewer:** after DONE, optional meta harvest + experience seed if new lessons appear.  
- Skill-compatible with eegent `agentic-loop-error-collector` (same memory update path).

## Sources scanned

- `.agent/LESSONS.md`
- `SELF_IMPROVEMENT_LOG.md`
- `.agent/SELF_IMPROVEMENT_LOG.md`
