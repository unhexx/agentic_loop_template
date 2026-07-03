#!/bin/bash
# Agent-Init.sh — Linux/Mac equivalent of Agent-Init.ps1 (P2 cross-platform start)
# Follows same logic: create .venv, install deps, set env for agentic loop.
# Usage: ./Agent-Init.sh

set -e

echo "Initializing Agentix loop env (Linux/Mac)..."

# Create venv if not exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Created .venv"
fi

source .venv/bin/activate

# Install core deps (expand as needed)
pip install -q pyyaml  # example, add real from setup

echo "Env ready. Activate with: source .venv/bin/activate"
echo "Run python -m agentic_loop_template.memory.playbooks seed etc."
echo "Follow DEVELOPMENT_STANDARDS §11 for git self-cycle."
