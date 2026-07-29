#!/usr/bin/env bash
# Cheap git/gh preflight for agentic loop. Prefer this over multi-block gh rituals.
set -euo pipefail

STRICT_MULTI_REPO="${STRICT_MULTI_REPO:-0}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

echo "PREFLIGHT_GIT root=$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repo"
  exit 2
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD="$(git rev-parse --short HEAD)"
echo "branch=$BRANCH head=$HEAD"

echo "--- status ---"
git status -sb

echo "--- remotes ---"
git remote -v || true

if command -v gh >/dev/null 2>&1; then
  echo "--- gh auth (compact) ---"
  if gh auth status 2>&1 | head -n 20; then
    :
  else
    echo "WARN: gh auth not ready"
  fi
  # Only expensive calls when strict or origin is github
  origin_url="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "$STRICT_MULTI_REPO" == "1" ]] || [[ "$origin_url" == *github.com* ]]; then
    echo "--- gh repo view (compact) ---"
    gh repo view --json nameWithOwner,defaultBranchRef 2>/dev/null || echo "WARN: gh repo view failed"
  fi
else
  echo "INFO: gh not installed — raw git only"
fi

# Detect if agentic template files dirty (signals multi-repo discipline)
if git status --porcelain | grep -E 'agentic_loop_template/|SYSTEM_PROMPT|DEVELOPMENT_STANDARDS|AGENT_ROLES|TOOLS_' >/dev/null 2>&1; then
  echo "HINT: template/standards files dirty — consider multi-repo sync if you maintain a dedicated template clone"
fi

echo "PREFLIGHT_OK branch=$BRANCH head=$HEAD"
exit 0
