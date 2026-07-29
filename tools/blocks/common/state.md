# Bounded LOOP_STATE (never load multi-MB archives)

```bash
python -m memory state snapshot --window 3
python -m memory state compact
python -m memory state append-delta --text "short outcome" --role Orchestrator
python -m memory state metrics-log --json '{"cycle":1,"tool_calls":3,"tests_failed":0}'
```

Rules:
- Working set: `.agent/LOOP_STATE.json` (+ slim `.md` projection).
- History: `.agent/history/` — do **not** read into the prompt.
- If `LOOP_STATE.md` is huge, run `compact` once, then use `snapshot` only.
