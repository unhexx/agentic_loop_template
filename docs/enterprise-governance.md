# Enterprise Governance

Agentix v3.4 adds governance foundations for teams requiring auditability and policy control.

## Policy Engine

Sample configuration: [examples/policy/sample-policy.toml](../examples/policy/sample-policy.toml)

Key sections:
- **approval** — non-blocking human approval via `questions_collector` pool
- **tools** — per-tool allow/deny and sandbox requirements
- **audit** — signed audit trail entries
- **isolation** — sandbox routing hints (JobObject, Firecracker)

## Human Approval Flow (Non-Blocking)

1. Orchestrator/Coder hits gated action (deploy, force_push, etc.)
2. Add `clarification_questions` to handoff with `priority: high` and `approval_required: true`
3. `questions_collector` batches per `project_config.json` cadence
4. Product owner resolves via `python -m memory.questions_collector resolve`
5. Reviewer records outcome in audit log

```bash
python -m memory.audit_log append \
  --action "approval_requested" \
  --role "orchestrator" \
  --cycle 55 \
  --approval-required
```

## Audit Trail

```bash
python -m memory.audit_log list --limit 20
```

Entries are SHA-256 signed. Human view: `.agent/AUDIT_LOG.md`.

## Compliance Notes

- Git §11 multi-repo sync provides reproducible deployment history
- Handoff JSON schema enforces `git_sync_status` evidence
- Pro tier (`enterprise_governance` flag) unlocks premium policy scopes
- Isolation stories documented in architecture; consumer projects extend via MCP PolicyEngine

See [integrations.md](integrations.md) for CI and notification hooks.