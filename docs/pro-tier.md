# Pro Tier

Agentix Free tier includes the full core loop. Pro tier unlocks premium ecosystem features.

## Feature Matrix

| Feature | Free | Pro |
|---------|------|-----|
| Core loop (O→C→T→D→R) | Yes | Yes |
| Performance ledger | Yes | Yes |
| Playbooks (core scopes) | Yes | Yes |
| Meta harvester | Yes | Yes |
| Hub premium playbooks | — | Yes |
| Eval harness (trajectory replay) | — | Yes |
| Enterprise governance previews | — | Yes |
| Priority decomposition rituals | — | Yes |

## Configuration

Tier and feature flags live in `.agent/project_config.json`:

```json
"tier": {
  "level": "free",
  "feature_flags": {
    "hub_premium": false,
    "eval_harness": false,
    "enterprise_governance": false
  }
}
```

Set `"level": "pro"` and enable flags to unlock Pro scopes. Premium playbook scopes are prefixed `hub:premium:*` and gated at select time.

## Upgrade Path

Pro tier is documentation + config hooks in v3.3.0. Hosted billing and premium Hub catalog are future work (P5+).