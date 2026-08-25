#!/usr/bin/env bash
# Ищем официальный Blackbox AI CLI. Ничего не ставим, npm не зовём.
set -euo pipefail
HINT="Install: curl -fsSL https://blackbox.ai/install.sh | bash"

_lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
# те же подстроки, что _WM_MARKERS / _AI_MARKERS в memory/adapters/blackbox.py
_is_wm() {
  local t; t=$(_lower "$1")
  [[ "$t" == *"sean 'shaleh' perry"* || "$t" == *"bradley t hughes"* || \
     "$t" == *"blackbox 0.77"* || "$t" == *"-display"* ]]
}
_is_ai() {
  local t; t=$(_lower "$1")
  _is_wm "$t" && return 1
  [[ "$t" == *"blackbox cli"* || "$t" == *headless* || "$t" == *configure* || \
     "$t" == *session* || "$t" == *"blackbox run"* ]]
}
_help() {
  if command -v timeout >/dev/null 2>&1; then timeout 3 "$@"; else "$@"; fi
}
try() {
  local p="$1" h
  [[ -n "$p" && -x "$p" ]] || return 1
  h=$(_help "$p" --help </dev/null 2>&1 || true)
  _is_wm "$h" && { echo "reject WM: $p" >&2; return 1; }
  _is_ai "$h" || { echo "reject not-AI: $p" >&2; return 1; }
  echo "path=$p"
  echo "help=$(printf '%s\n' "$h" | head -n 1)"
  return 0
}

SEEN=""
consider() {
  local p="${1:-}"
  [[ -n "$p" && -x "$p" ]] || return 0
  [[ "$SEEN" == *"|$p|"* ]] && return 0
  SEEN+="|$p|"
  try "$p" && exit 0
  return 0
}

consider "${HOME}/.local/bin/blackbox"
consider "$(command -v blackbox 2>/dev/null || true)"
consider "${HOME}/.blackbox/bin/blackbox"
consider "${HOME}/.blackbox-cli-v2/blackbox"

echo "ERROR: Blackbox AI CLI not found. $HINT" >&2
echo "Forbidden: npm install blackbox-cli (IoT) or @blackbox/cli (404)." >&2
exit 1
