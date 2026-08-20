#!/usr/bin/env bash
# Обёртка шлюза Agentix: start / health / stats.
# Usage: bash scripts/agentix-proxy.sh {start|health|stats}
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
cmd="${1:-health}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
case "$cmd" in
  start)
    exec python -m memory.proxy serve --host 127.0.0.1 --port 8110 --workdir "$ROOT"
    ;;
  health)
    python -m memory.proxy health --json
    curl -sf "http://127.0.0.1:8110/healthz" || true
    ;;
  stats)
    python -m memory.proxy stats --json 2>/dev/null || true
    curl -sf "http://127.0.0.1:8110/stats" || true
    ;;
  *)
    echo "Usage: $0 {start|health|stats}" >&2
    exit 2
    ;;
esac
