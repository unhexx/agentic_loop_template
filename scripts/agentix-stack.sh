#!/usr/bin/env bash
# Операторский Compose-стек: SearXNG / LDR / опционально Ollama.
# Шлюз и pxpipe на хосте не поднимаем.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/compose.yaml"
ENV_EX="$ROOT/deploy/compose.env.example"
ENV_FILE="$ROOT/deploy/compose.env"
cd "$ROOT"

usage() {
  cat <<EOF
Usage: bash scripts/agentix-stack.sh <up|down|ps|health|config> [--research] [--ollama]
  up         --profile search (SearXNG :8080)
  --research also profile research (LDR :5000, needs SearXNG)
  --ollama   also profile ollama (no host port)
EOF
}

cmd="${1:-}"
shift || true
PROFILES=(search)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --research) PROFILES+=(research); shift ;;
    --ollama) PROFILES+=(ollama); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "$ENV_FILE" && -f "$ENV_EX" ]]; then
  cp "$ENV_EX" "$ENV_FILE"
  if command -v openssl >/dev/null 2>&1; then
    gen="$(openssl rand -hex 32)"
    sed -i "s/replace-with-openssl-rand-hex-32/${gen}/" "$ENV_FILE"
  fi
  echo "wrote $ENV_FILE from example" >&2
fi

dc() {
  local args=(docker compose -f "$COMPOSE_FILE" --project-directory "$ROOT")
  if [[ -f "$ENV_FILE" ]]; then
    args+=(--env-file "$ENV_FILE")
  fi
  local p
  for p in "${PROFILES[@]}"; do
    args+=(--profile "$p")
  done
  "${args[@]}" "$@"
}

case "$cmd" in
  up)
    dc up -d
    ;;
  down)
    # down без профилей не снимает сервисы из неактивных профилей —
    # передаём все известные.
    PROFILES=(search research ollama)
    dc down
    ;;
  ps)
    dc ps
    ;;
  config)
    dc config
    ;;
  health)
    python -m memory.stack check
    curl -fsS "http://127.0.0.1:8080/healthz" >/dev/null && echo "searxng=ok" || echo "searxng=down"
    python -m memory.proxy health --json 2>/dev/null || echo "gateway/pxpipe=unchecked"
    ;;
  ""|-h|--help)
    usage
    exit 0
    ;;
  *)
    usage
    exit 2
    ;;
esac
