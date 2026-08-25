# Cross-Platform Guide

Agentix supports Windows (primary) and Linux/macOS with platform-adaptive bootstrap.

## Bootstrap Scripts

| Platform | Script | Python path |
|----------|--------|-------------|
| Windows | `Agent-Init.ps1` | `.venv/Scripts/python` |
| Linux/Mac | `Agent-Init.sh` | `.venv/bin/python` |

## Cold-start parity

Both scripts run the same ritual. Wizard (`bash Agent-Init.sh --wizard` / `.\Agent-Init.ps1 -Wizard`) and explicit `-Frontend grok|cursor|blackbox` are fail-closed on proxy health. Non-wizard Init stays **best-effort** so CI and `demo-loop.sh` do not need pxpipe. Mock never fail-closes. `AGENTIX_PROXY=0` still opts out. Wizard/live default frontend is **grok**; `-Frontend blackbox` and `project_config.supervisor.adapter` still win. CLI adapter is opt-in `--adapter blackbox`.

After the editable install, `python -m memory` does **not** need `PYTHONPATH`. Init exports it only if `import memory` fails.

| Step | Unix (`Agent-Init.sh`) | Windows (`Agent-Init.ps1`) |
|------|------------------------|----------------------------|
| venv | yes | yes (repair) |
| install | `pip install -e ".[dev]"` (jsonschema fallback) | `Invoke-VenvPip @('install','-e',"$TemplateRoot[dev]")` |
| `memory state init` | yes | yes |
| `experience_harvester seed-defaults --apply` | yes | yes |
| `knowledge ingest-if-empty` | yes | yes |
| `context_budget cold-start` | yes | yes |
| `playbooks seed --from-standards` | yes | yes |
| `proxy install-venv` | yes | yes |
| `proxy health --init` | wizard fail-closed; else `\|\| true` | `-Wizard` / explicit `-Frontend` grok\|cursor\|blackbox fail-closed; else best-effort |
| starter prompt | always `.agent/starter_prompt_grok.txt` | always `.agent/starter_prompt_grok.txt` |
| wizard | `--wizard` | `-Wizard` (name, platform, frontend, spec) |

## Shell Hygiene

- **Windows:** Use PowerShell patterns from `DEVELOPMENT_STANDARDS.md`. Avoid cmd.exe mixing.
- **Linux/Mac:** Use bash. Activate venv with `source .venv/bin/activate`.
- **All roles:** Consult the `cross-platform` playbook scope before tool calls:
  ```bash
  python -m memory.playbooks select --query "venv paths" --scopes "cross-platform" --k 3
  ```

## Path Handling

- Use forward slashes in docs; scripts handle platform differences.
- Git operations are identical across platforms.
- Multi-repo sync (§11) applies on all platforms — verify with `git log --oneline -3` in every clone.

## Prompts

All `prompts/short_*.md` files include platform-adaptive bootstrap blocks. No role should assume PowerShell-only without a *nix alternative.