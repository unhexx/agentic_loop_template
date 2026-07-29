#!/usr/bin/env bash
# Copy SSOT template files into a consumer project's agentic_loop_template/ folder.
# Usage: ./scripts/sync_template_from_ssot.sh /path/to/consumer-repo
set -euo pipefail

SSOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_ROOT="${1:-}"
if [[ -z "$DEST_ROOT" || ! -d "$DEST_ROOT" ]]; then
  echo "Usage: $0 /path/to/consumer-repo"
  exit 2
fi

DEST="$DEST_ROOT/agentic_loop_template"
mkdir -p "$DEST"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.agent/history' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '*.pyc' \
  "$SSOT/" "$DEST/"

echo "SYNC_TEMPLATE_DONE from=$SSOT to=$DEST version=$(cat "$SSOT/VERSION" 2>/dev/null || echo unknown)"
