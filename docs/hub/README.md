# Agentix Hub

The Agentix Hub is a discovery and install foundation for playbooks and knowledge objects. It enables sharing structured loop knowledge across projects without a hosted server (static JSON feed).

## CLI Commands

```bash
# List all playbooks
python -m memory.playbooks list

# Export discovery index
python -m memory.playbooks export --format hub

# Search bullets
python -m memory.playbooks discover --query "git sync" --k 5
```

Output: `.agent/HUB_INDEX.json` — consumable by Hub UIs or CI pipelines.

## Install Flow

1. Export from source project: `python -m memory.playbooks export --format hub`
2. Copy desired playbook bullets from `HUB_INDEX.json` or `.agent/PLAYBOOKS.json`
3. Merge into consumer `.agent/PLAYBOOKS.json`
4. Run `python -m memory.playbooks seed --from-standards` if starting fresh

See [discovery.md](discovery.md) for detailed install steps.

## API Schema

Web-ready JSON schema: [api-schema.json](api-schema.json). Defines `list`, `discover`, and `install` endpoint shapes for future hosted Hub.

## Pro Tier

Premium playbook scopes (`hub_premium`) are gated via `tier.feature_flags` in `.agent/project_config.json`. See [../pro-tier.md](../pro-tier.md).