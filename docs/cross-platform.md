# Cross-Platform Guide

Agentix supports Windows (primary) and Linux/macOS with platform-adaptive bootstrap.

## Bootstrap Scripts

| Platform | Script | Python path |
|----------|--------|-------------|
| Windows | `Agent-Init.ps1` | `.venv/Scripts/python` |
| Linux/Mac | `Agent-Init.sh` | `.venv/bin/python` |

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