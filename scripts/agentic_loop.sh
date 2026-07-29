#!/usr/bin/env bash
# Lightweight parallel-ready cycle scaffold (inspired by agent-box).
# Creates worktrees for named workstreams; agents fill in the work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

MAIN_BRANCH="${MAIN_BRANCH:-main}"
WT_BASE="${WT_BASE:-$PROJECT_ROOT/../agentic-loop-worktrees}"
CYCLE_ID="${CYCLE_ID:-$(date +%Y%m%d-%H%M%S)}"
DRY_RUN="${DRY_RUN:-false}"
WORKSTREAMS_DEFAULT="harness,docs"

usage() {
  cat <<EOF
Usage: scripts/agentic_loop.sh [--cycle ID] [--workstreams a,b] [--dry-run]

Creates isolated worktrees feature/<cycle>-<stream> under WT_BASE.
Does not auto-merge; Orchestrator decides merge_gate per PARALLEL_PROTOCOL.md.
EOF
}

WORKSTREAMS_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cycle) CYCLE_ID="$2"; shift 2 ;;
    --workstreams) WORKSTREAMS_ARG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 2 ;;
  esac
done

IFS=',' read -r -a WORKSTREAMS <<< "${WORKSTREAMS_ARG:-$WORKSTREAMS_DEFAULT}"

log() { echo "[agentic-loop $CYCLE_ID] $*"; }

log "root=$PROJECT_ROOT streams=${WORKSTREAMS[*]}"
mkdir -p "$WT_BASE"

for ws in "${WORKSTREAMS[@]}"; do
  branch="feature/${CYCLE_ID}-${ws}"
  wt="$WT_BASE/${CYCLE_ID}-${ws}"
  if [[ "$DRY_RUN" == "true" ]]; then
    log "DRY would: worktree add $wt -b $branch"
    continue
  fi
  if [[ -d "$wt" ]]; then
    log "exists $wt"
    continue
  fi
  git worktree add -b "$branch" "$wt" "$MAIN_BRANCH"
  log "created $wt on $branch"
  # Init bounded state in worktree
  if [[ -x "$wt/Agent-Init.sh" ]]; then
    (cd "$wt" && ./Agent-Init.sh --quiet 2>/dev/null) || true
  fi
done

log "DONE — assign owned_paths per stream; merge only via Reviewer gate"
echo "CYCLE_ID=$CYCLE_ID"
echo "WT_BASE=$WT_BASE"
