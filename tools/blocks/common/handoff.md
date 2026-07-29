# Handoff validation

End each role with exactly one JSON object. Validate before Reviewer DONE:

```bash
python -m memory.validate_handoff .agent/last_handoff.json
# or
python -m memory.validate_handoff --json '{"role":"Reviewer",...}'
```

DONE requires:
- `handoff_to`: `"None"`
- `git_sync_status.verified: true` **or** `sync_waived` + reason
- `lessons_learned` non-empty **or** `distillation_performed: true`
- `metrics` object present
