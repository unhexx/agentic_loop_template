#!/usr/bin/env bash
# Linux/macOS bootstrap for agentic_loop_template (Grok / Cursor / headless friendly).
set -euo pipefail

QUIET=0
OUT_PROMPT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet|-q) QUIET=1; shift ;;
    --output-file) OUT_PROMPT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: ./Agent-Init.sh [--quiet] [--output-file PATH]"
      exit 0
      ;;
    *) echo "Unknown: $1"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { [[ "$QUIET" == "1" ]] || echo "[Agent-Init] $*"; }

log "root=$ROOT"
log "os=$(uname -s) arch=$(uname -m)"

# venv
if [[ ! -d .venv ]]; then
  log "creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip -q
python -m pip install pytest jsonschema -q 2>/dev/null || true

# package path: dedicated repo uses top-level memory package
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# .agent config
mkdir -p .agent
if [[ ! -f .agent/project_config.json ]]; then
  if [[ -f .agent/project_config.example.json ]]; then
    cp .agent/project_config.example.json .agent/project_config.json
    log "wrote .agent/project_config.json from example"
  fi
fi

# bounded state
python -m memory state init
python -m memory state compact >/dev/null || true

# seed failure patterns (idempotent merge)
python -m memory.experience_harvester seed-defaults --apply >/dev/null || true

# chmod helpers
chmod +x tools/select.py scripts/*.sh Agent-Init.sh 2>/dev/null || true

VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
WID="$(python -m memory info 2>/dev/null | python -c 'import sys,json; print(json.load(sys.stdin).get("workspace_id",""))' 2>/dev/null || true)"

log "template_version=$VERSION"
log "workspace_id=$WID"
log "python=$(command -v python)"

# starter prompt
PROMPT_PATH="${OUT_PROMPT:-$ROOT/.agent/starter_prompt_grok.txt}"
cat > "$PROMPT_PATH" <<EOF
You are running the Agentic Development Loop (template $VERSION) on Linux.

Cold-start (do this first, max 3 tool calls):
1. \`python -m memory state snapshot --window 3\`
2. \`python -m memory query --top 5 --category "Common Failure Patterns"\`
3. \`python tools/select.py --intent bootstrap\` (or git|test|memory as needed)

Then act as **Orchestrator**:
- Read only: prompts/short_orchestrator_prompt.md, PROJECT_CONTEXT if present, .agent/TODO.md if present.
- Do NOT read multi-MB .agent/history or full LESSONS archives.
- Follow PLAN → ACT (≤3 tools) → REFLECT; end with one JSON handoff (HANDOFF_SCHEMA.md).
- Validate: \`python -m memory.validate_handoff .agent/last_handoff.json\`
- Commits: natural Russian, human mid/senior voice, never mention AI/models.
- Parallel work: see PARALLEL_PROTOCOL.md and scripts/agentic_loop.sh.

Begin as Orchestrator for this repository.
EOF

log "starter_prompt=$PROMPT_PATH"

# cold-start budget report
python -m memory.context_budget cold-start --budget 16000 || true

log "READY"
echo "AGENT_INIT_OK version=$VERSION workspace=$WID prompt=$PROMPT_PATH"
