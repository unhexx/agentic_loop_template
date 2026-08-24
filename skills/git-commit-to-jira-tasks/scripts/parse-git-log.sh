#!/usr/bin/env bash
# Helper: structured commit extraction for git-commit-to-jira-tasks.
# Usage: ./parse-git-log.sh [--since=DATE] [--until=DATE] [--max-count=N] [rev-range]
# Outputs machine-friendly records separated by ---COMMIT---
set -euo pipefail

SINCE=""
UNTIL=""
MAX_COUNT=""
RANGE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --since=*) SINCE="${1#*=}"; shift ;;
    --until=*) UNTIL="${1#*=}"; shift ;;
    --max-count=*) MAX_COUNT="${1#*=}"; shift ;;
    --since|--until|--max-count)
      echo "Use $1=VALUE form" >&2
      exit 2
      ;;
    *) RANGE="$1"; shift ;;
  esac
done

ARGS=()
[[ -n "$SINCE" ]] && ARGS+=(--since="$SINCE")
[[ -n "$UNTIL" ]] && ARGS+=(--until="$UNTIL")
[[ -n "$MAX_COUNT" ]] && ARGS+=(--max-count="$MAX_COUNT")
[[ -n "$RANGE" ]] && ARGS+=("$RANGE")

git log \
  --pretty=format:'---COMMIT---%nHASH:%H%nAUTHOR:%an%nEMAIL:%ae%nDATE:%aI%nSUBJECT:%s%nBODY:%b%n---NUMSTAT---' \
  --numstat \
  --reverse \
  "${ARGS[@]}"
