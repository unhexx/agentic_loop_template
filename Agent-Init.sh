#!/bin/bash
# Agent-Init.sh - Cross-platform (Linux/Mac) equivalent for Agentix loop setup (P2-CROSS-01)
# Supports venv, deps, env for Blackbox-like agents. Follows Windows ps1 logic.
set -e
echo "Initializing Agentix env (cross-platform)..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "Created .venv"
fi
source .venv/bin/activate
pip install -q pyyaml  # minimal; expand per project
echo "Env ready. source .venv/bin/activate"
echo "Use: python -m agentic_loop_template.memory.playbooks select ..."
echo "Git self-cycle §11 mandatory before planning."
