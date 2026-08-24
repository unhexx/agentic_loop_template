#!/usr/bin/env bash
# Bootstrap a product repo against the Agentix SSOT without vendoring a stale copy.
# Usage (from the product root): bash Agent-Init.consumer.sh [--wizard]
set -euo pipefail

WIZARD=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wizard) WIZARD=true; shift ;;
    -h|--help)
      echo "Usage: bash Agent-Init.consumer.sh [--wizard]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# If this file is still inside examples/consumer-starter, the product root is two levels up
# when copied to the product root, ROOT is the product.
if [[ -d "$ROOT/../../../memory" && "$(basename "$ROOT")" == "consumer-starter" ]]; then
  echo "Copy this script to the product repo root first (see README.md)." >&2
  exit 2
fi
cd "$ROOT"

log() { echo "[Agent-Init] $*"; }

if [[ -d "$ROOT/../agentic_loop_template/memory" ]]; then
  TEMPLATE="$(cd "$ROOT/../agentic_loop_template" && pwd)"
elif [[ -d "$ROOT/agentic_loop_template/memory" ]]; then
  TEMPLATE="$(cd "$ROOT/agentic_loop_template" && pwd)"
else
  echo "agentic_loop_template not found (expected ../agentic_loop_template or ./agentic_loop_template)" >&2
  exit 1
fi

if [[ ! -e "$ROOT/agentic_loop_template" ]]; then
  ln -s "$TEMPLATE" "$ROOT/agentic_loop_template"
  log "symlink agentic_loop_template -> $TEMPLATE"
fi

if [[ ! -d .venv ]]; then
  log "creating .venv"
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
  else
    python3 -m venv .venv
  fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if command -v uv >/dev/null 2>&1; then
  uv pip install -e "${TEMPLATE}[dev]" \
    || uv pip install 'jsonschema>=4.18,<5' 'pytest>=8.0,<9'
else
  python -m pip install -U pip -q 2>/dev/null || true
  python -m pip install -e "${TEMPLATE}[dev]" \
    || python -m pip install 'jsonschema>=4.18,<5' 'pytest>=8.0,<9'
fi
if ! python -c "import memory, memory.supervisor" >/dev/null 2>&1; then
  export PYTHONPATH="${TEMPLATE}${PYTHONPATH:+:$PYTHONPATH}"
fi

python -m memory.proxy install-venv >/dev/null 2>&1 || python -m memory.proxy install-venv || true
# shellcheck disable=SC1091
source .venv/bin/activate

mkdir -p .agent
if [[ ! -f .agent/project_config.json && -f "$TEMPLATE/.agent/project_config.example.json" ]]; then
  cp "$TEMPLATE/.agent/project_config.example.json" .agent/project_config.json
  log "wrote .agent/project_config.json from example"
fi

python -m memory state init 2>/dev/null || true
python -m memory.experience_harvester seed-defaults --apply >/dev/null 2>&1 || true
python -m memory.knowledge ingest-if-empty --root docs --budget 800 >/dev/null 2>&1 || true
python -m memory.context_budget cold-start --budget 16000 --compress >/dev/null 2>&1 || true
python -m memory.proxy health --init >/dev/null 2>&1 || true

VERSION="$(cat "$TEMPLATE/VERSION" 2>/dev/null || echo unknown)"
log "template=$TEMPLATE version=$VERSION"
log "source .venv/bin/activate"
if [[ "$WIZARD" == true ]]; then
  echo "Lite: fill AGENTS.md from examples/consumer-starter/AGENTS.md.example"
  echo "Full: paste $TEMPLATE/prompts/short_orchestrator_prompt.md"
fi
echo "AGENT_INIT_OK version=$VERSION template=$TEMPLATE"
