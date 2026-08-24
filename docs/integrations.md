# Integrations (P5)

MCP-ready integration patterns for enterprise workflows.

## GitHub Actions — Loop Trigger

Workflow: [.github/workflows/agentix-loop.yml](../.github/workflows/agentix-loop.yml)

Triggers:
- **pull_request** and **push** to `main`
- **Manual** (`workflow_dispatch`) with `cycle_goal` input
- **Weekly** schedule (Monday 08:00 UTC)

The `harness` job does an editable install (`pip install -e ".[dev,dashboard]"`), proves G1 (`import memory` from `/tmp` with PYTHONPATH unset), runs `pytest memory/` including the full mock O→C→T→R cycle, then Agent-Init + Hub export + audit. `stdlib-collect` installs `.[dev]` only and collect-only supervisor tests. Mock/CI skip live pxpipe.

```bash
gh workflow run agentix-loop.yml -f cycle_goal="P5-governance"
```

## Linear / Jira (MCP Pattern)

Add an MCP skill that:
1. Reads open INVEST tasks from `.agent/TODO.md`
2. Creates/updates issues on cycle start (Orchestrator)
3. Closes issues on Reviewer `DONE`

Stub config in consumer `project_config.json`:

```json
"integrations": {
  "issue_tracker": { "provider": "linear", "project_id": "AGX", "enabled": false }
}
```

## Slack Notifications (MCP Pattern)

On Reviewer `DONE` or `BLOCKED`:
- Post compact summary + link to commit
- Include performance metrics from ledger

Recommended: MCP skill wrapping Slack webhook; gate via `tier.feature_flags.enterprise_governance`.

## GitHub MCP (Built-in)

Use `grok_com_github` MCP tools for PR creation, status checks, and branch management per `TOOLS_REGISTRY.md`.

## Audit on Integration Events

```bash
python -m memory.audit_log append \
  --action "slack_notify" \
  --role "reviewer" \
  --cycle 60 \
  --details '{"channel":"#agentix","status":"DONE"}'
```