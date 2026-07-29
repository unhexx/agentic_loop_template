#!/usr/bin/env bash
# Linux parity for sync-worktree: verify or soft-sync current clone.
# Prints SYNC_DONE on success for machine-checkable gates.
set -euo pipefail

VERIFY_ONLY=0
MAIN_CLONE="${MAIN_CLONE_PATH:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only|-VerifyOnly) VERIFY_ONLY=1; shift ;;
    --main-clone) MAIN_CLONE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--verify-only] [--main-clone PATH]"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD="$(git rev-parse --short HEAD)"

echo "SYNC worktree root=$ROOT branch=$BRANCH head=$HEAD verify_only=$VERIFY_ONLY"

if [[ "$VERIFY_ONLY" == "1" ]]; then
  git status -sb
  if [[ -n "$MAIN_CLONE" && -d "$MAIN_CLONE/.git" ]]; then
    echo "main_clone=$MAIN_CLONE head=$(git -C "$MAIN_CLONE" rev-parse --short HEAD 2>/dev/null || echo '?')"
  fi
  echo "READY"
  echo "SYNC_DONE root=$ROOT branch=$BRANCH head=$HEAD"
  exit 0
fi

# Soft sync: fetch + status (no destructive reset)
git fetch --all --prune 2>/dev/null || git fetch 2>/dev/null || true
git status -sb
if [[ -n "$MAIN_CLONE" && -d "$MAIN_CLONE/.git" ]]; then
  echo "Fetching main clone..."
  git -C "$MAIN_CLONE" fetch --all --prune 2>/dev/null || true
  echo "main_clone_head=$(git -C "$MAIN_CLONE" rev-parse --short HEAD)"
fi

echo "READY"
echo "SYNC_DONE root=$ROOT branch=$BRANCH head=$(git rev-parse --short HEAD)"
exit 0
