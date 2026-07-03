#!/bin/bash
# Agent-Init.sh - Cross-platform Agentix setup (P2/P6)
# Usage: bash Agent-Init.sh [--wizard]
set -e

WIZARD=false
for arg in "$@"; do
  case "$arg" in
    --wizard) WIZARD=true ;;
  esac
done

echo "Initializing Agentix env (cross-platform)..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "Created .venv"
fi
source .venv/bin/activate
pip install -q pyyaml 2>/dev/null || pip install -q pyyaml

if [ "$WIZARD" = true ]; then
  echo ""
  echo "=== Agentix Onboarding Wizard ==="
  read -rp "Project name: " PROJECT_NAME
  PROJECT_NAME=${PROJECT_NAME:-my-project}
  echo "Platform: 1) Linux 2) macOS 3) Windows (via WSL)"
  read -rp "Choice [1]: " PLATFORM_CHOICE
  echo "Frontend: 1) Blackbox 2) Cursor 3) Claude Code"
  read -rp "Choice [2]: " FRONTEND_CHOICE
  read -rp "Spec file [TASK_SPECIFICATION.md]: " SPEC_FILE
  SPEC_FILE=${SPEC_FILE:-TASK_SPECIFICATION.md}

  if [ ! -f "$SPEC_FILE" ] && [ -f examples/consumer-starter/TASK_SPECIFICATION.example.md ]; then
    cp examples/consumer-starter/TASK_SPECIFICATION.example.md "$SPEC_FILE"
    echo "Created $SPEC_FILE from consumer-starter template"
  fi
  if [ ! -f PROJECT_CONTEXT.md ] && [ -f examples/consumer-starter/PROJECT_CONTEXT.example.md ]; then
    cp examples/consumer-starter/PROJECT_CONTEXT.example.md PROJECT_CONTEXT.md
    echo "Created PROJECT_CONTEXT.md from template"
  fi

  echo ""
  echo "Setup complete for: $PROJECT_NAME"
  echo "  Platform choice: ${PLATFORM_CHOICE:-1}"
  echo "  Frontend choice: ${FRONTEND_CHOICE:-2}"
  echo "  Spec: $SPEC_FILE"
  echo ""
  echo "Next steps:"
  echo "  1. bash scripts/demo-loop.sh"
  echo "  2. Paste prompts/short_orchestrator_prompt.md to your agent"
  echo "  3. Read docs/onboarding-wizard.md"
else
  echo "Env ready. source .venv/bin/activate"
  echo "Tip: bash Agent-Init.sh --wizard for interactive setup"
fi

echo "Use: python -m memory.playbooks select ..."
echo "Git self-cycle §11 mandatory before planning."