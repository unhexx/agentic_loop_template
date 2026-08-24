#!/usr/bin/env bash
# Agent-Init.sh — cross-platform Agentix bootstrap
# Usage: bash Agent-Init.sh [--wizard] [--quiet] [--output-file PATH]
set -euo pipefail

WIZARD=false
QUIET=0
OUT_PROMPT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wizard) WIZARD=true; shift ;;
    --quiet|-q) QUIET=1; shift ;;
    --output-file) OUT_PROMPT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash Agent-Init.sh [--wizard] [--quiet] [--output-file PATH]"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { [[ "$QUIET" == "1" ]] || echo "[Agent-Init] $*"; }

log "root=$ROOT os=$(uname -s) arch=$(uname -m)"

if [[ ! -d .venv ]]; then
  log "creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip -q
python -m pip install -e ".[dev]" \
  || python -m pip install 'jsonschema>=4.18,<5' 'pytest>=8.0,<9'
if ! python -c "import memory, memory.supervisor" >/dev/null 2>&1; then
  export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
fi

python -m memory.proxy install-venv >/dev/null 2>&1 || python -m memory.proxy install-venv || true
# shellcheck disable=SC1091
source .venv/bin/activate

mkdir -p .agent
if [[ ! -f .agent/project_config.json && -f .agent/project_config.example.json ]]; then
  cp .agent/project_config.example.json .agent/project_config.json
  log "wrote .agent/project_config.json from example"
fi

python -m memory state init 2>/dev/null || true
python -m memory state compact >/dev/null 2>&1 || true
python -m memory.experience_harvester seed-defaults --apply >/dev/null 2>&1 || true
python -m memory.knowledge ingest-if-empty --root docs --budget 800 >/dev/null 2>&1 || true
python -m memory.playbooks seed --from-standards >/dev/null 2>&1 || true

chmod +x tools/select.py scripts/*.sh Agent-Init.sh 2>/dev/null || true

VERSION="$(cat VERSION 2>/dev/null || echo 3.4.1)"
WID="$(python -m memory info 2>/dev/null | python -c 'import sys,json; print(json.load(sys.stdin).get("workspace_id",""))' 2>/dev/null || true)"

if [[ "$WIZARD" == true ]]; then
  echo ""
  echo "=== Agentix Onboarding Wizard ==="
  read -rp "Project name: " PROJECT_NAME || true
  PROJECT_NAME=${PROJECT_NAME:-my-project}
  echo "Platform: 1) Linux 2) macOS 3) Windows (via WSL)"
  read -rp "Choice [1]: " PLATFORM_CHOICE || true
  echo "Frontend: 1) Grok  2) Cursor  3) Claude Code  4) Blackbox"
  read -rp "Choice [1]: " FRONTEND_CHOICE || true
  read -rp "Spec file [TASK_SPECIFICATION.md]: " SPEC_FILE || true
  SPEC_FILE=${SPEC_FILE:-TASK_SPECIFICATION.md}

  if [[ ! -f "$SPEC_FILE" && -f examples/consumer-starter/TASK_SPECIFICATION.example.md ]]; then
    cp examples/consumer-starter/TASK_SPECIFICATION.example.md "$SPEC_FILE"
    log "Created $SPEC_FILE from consumer-starter template"
  fi
  if [[ ! -f PROJECT_CONTEXT.md && -f examples/consumer-starter/PROJECT_CONTEXT.example.md ]]; then
    cp examples/consumer-starter/PROJECT_CONTEXT.example.md PROJECT_CONTEXT.md
    log "Created PROJECT_CONTEXT.md from template"
  fi

  echo ""
  echo "Setup complete for: $PROJECT_NAME"
  echo "  Platform choice: ${PLATFORM_CHOICE:-1}"
  echo "  Frontend choice: ${FRONTEND_CHOICE:-1}"
  echo "  Spec: $SPEC_FILE"
  echo ""
  echo "Next steps:"
  echo "  1. bash scripts/demo-loop.sh"
  echo "  2. Paste prompts/short_orchestrator_prompt.md to your agent"
  echo "  3. Read docs/onboarding-wizard.md / docs/TOP10_IMPROVEMENTS.md"
else
  log "Env ready. source .venv/bin/activate"
  log "Tip: bash Agent-Init.sh --wizard for interactive setup"
fi

INIT_FE=""
if [[ "$WIZARD" == true ]]; then
  case "${FRONTEND_CHOICE:-1}" in
    2) INIT_FE="cursor" ;;
    3) INIT_FE="claude" ;;
    4) INIT_FE="blackbox" ;;
    *) INIT_FE="grok" ;;
  esac
fi
if [[ -n "$INIT_FE" ]]; then
  if ! python -m memory.proxy health --init --frontend "$INIT_FE"; then
    echo "AGENT_INIT: pxpipe required for frontend=$INIT_FE (or export AGENTIX_PROXY=0)" >&2
    echo "  systemctl --user enable --now pxpipe.service" >&2
    echo "  template: scripts/systemd/pxpipe.service.example" >&2
    exit 1
  fi
else
  python -m memory.proxy health --init >/dev/null 2>&1 || true
fi

PROMPT_PATH="${OUT_PROMPT:-$ROOT/.agent/starter_prompt_grok.txt}"
cat > "$PROMPT_PATH" <<EOP
You are running the Agentic Development Loop (template $VERSION).

Cold-start (first, max 4 tool calls):
1. \`python -m memory.proxy health\`
2. \`python -m memory state snapshot --window 3\`
3. \`python -m memory query --top 5 --category "Common Failure Patterns"\`
4. \`python -m memory.knowledge query --q "cycle" --top 3\` (or \`python tools/select.py --intent bootstrap\`)

Then act as **Orchestrator**:
- prompts/short_orchestrator_prompt.md; .agent/PLAN.md + TODO if present
- Playbooks: python -m memory.playbooks select … when available
- Do NOT load multi-MB .agent/history archives
- PLAN → ACT (≤3 tools) → REFLECT; one JSON handoff; validate with memory.validate_handoff
- Commits: natural Russian human voice, no AI/model mentions
- Parallel: PARALLEL_PROTOCOL.md + scripts/agentic_loop.sh

Begin as Orchestrator.
EOP

log "starter_prompt=$PROMPT_PATH"
log "template_version=$VERSION workspace_id=$WID"
python -m memory.context_budget cold-start --budget 16000 --compress 2>/dev/null || true

log "Use: python -m memory.playbooks select ... | python -m memory state snapshot"
log "Git: scripts/preflight_git.sh; multi-repo §11 when STRICT_MULTI_REPO=1"
echo "AGENT_INIT_OK version=$VERSION workspace=$WID prompt=$PROMPT_PATH"
