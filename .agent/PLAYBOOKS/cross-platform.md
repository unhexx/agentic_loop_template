# Playbook: cross-platform
Scope: phase:cross-platform

- [0.90] Use .venv/bin/python on *nix, .venv/Scripts on Win. Prefer python3 -m for portability.  (tags: ['venv', 'paths'])
- [0.85] Agent-Init.sh for bash: source .venv/bin/activate; handle shebangs.  (tags: ['linux', 'mac'])