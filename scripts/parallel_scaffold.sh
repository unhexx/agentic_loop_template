#!/usr/bin/env bash
# Scaffold conflict-free parallel stream worktrees + lease claims (hub stays on main).
# Usage:
#   bash scripts/parallel_scaffold.sh \
#     --hub /path/to/agentic_loop_template \
#     --stream p11-mcp:memory/local_mcp/,memory/test_local_mcp.py,pyproject.toml \
#     --stream p11-docs:docs/,TOOLS_REGISTRY.md,skills/
set -euo pipefail

HUB=""
STREAMS=()
WT_BASE=""

usage() {
  cat <<'EOF' >&2
Usage:
  bash scripts/parallel_scaffold.sh --hub PATH \
    --stream name:path[,path...] [--stream ...] [--wt-base DIR]
EOF
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub) HUB="${2:-}"; shift 2 ;;
    --stream) STREAMS+=("${2:-}"); shift 2 ;;
    --wt-base) WT_BASE="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -n "$HUB" ]] || usage
[[ ${#STREAMS[@]} -ge 1 ]] || usage
HUB="$(cd "$HUB" && pwd)"
WT_BASE="${WT_BASE:-$(cd "$HUB/.." && pwd)/agentic-loop-worktrees}"
mkdir -p "$WT_BASE"

if [[ ! -d "$HUB/.git" ]] && [[ ! -f "$HUB/.git" ]]; then
  echo "hub is not a git checkout: $HUB" >&2
  exit 1
fi

PY="${HUB}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

cd "$HUB"
echo "hub=$HUB"
echo "wt_base=$WT_BASE"
echo "Do not checkout integration branches in the hub clone. Human merges PRs to main."

for spec in "${STREAMS[@]}"; do
  name="${spec%%:*}"
  paths="${spec#*:}"
  if [[ "$name" == "$spec" || -z "$paths" ]]; then
    echo "stream must be name:path[,path...]: $spec" >&2
    exit 1
  fi
  branch="stream/${name}"
  worktree="${WT_BASE}/${name}"
  if [[ -e "$worktree" ]]; then
    echo "worktree exists, skip create: $worktree"
  else
    git -C "$HUB" worktree add -b "$branch" "$worktree" HEAD
    echo "created worktree=$worktree branch=$branch"
  fi
  "$PY" -m memory.stream_lease claim \
    --stream "${name}:${paths}" \
    --workdir "$HUB" \
    --worktree "$worktree"
  echo "claimed stream=$name owned_paths=$paths"
done

"$PY" -m memory.stream_lease status --workdir "$HUB"
cat <<'EOF'

Next:
  1. Work only under each stream's owned_paths in its worktree.
  2. Hot-file ban: DEVELOPMENT_STANDARDS.md, schemas/, package __init__ without Integration owner.
  3. Never git push main. PR from integration worktree; human merges.
  4. Release: python -m memory.stream_lease release --stream NAME --workdir HUB
EOF
