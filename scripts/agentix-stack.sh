#!/usr/bin/env bash
# Операторский Compose-стек: SearXNG всегда, LDR/Ollama по профилю.
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
  up         SearXNG :8080 (без профиля, всегда с проектом)
  --research also profile research (LDR :5000, needs SearXNG)
  --ollama   also profile ollama (no host port)
EOF
}

cmd="${1:-}"
shift || true
# Строка, не массив: bash 3.2 + set -u падает на пустом "${arr[@]}".
PROFILES=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --research) PROFILES="${PROFILES} research"; shift ;;
    --ollama) PROFILES="${PROFILES} ollama"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "$ENV_FILE" && -f "$ENV_EX" ]]; then
  # Только cp: in-place GNU sed на macOS нет, секрет не из env.
  cp "$ENV_EX" "$ENV_FILE"
  echo "wrote $ENV_FILE from example" >&2
fi

dc() {
  local args=(docker compose -f "$COMPOSE_FILE" --project-directory "$ROOT")
  if [[ -f "$ENV_FILE" ]]; then
    args+=(--env-file "$ENV_FILE")
  fi
  local p
  for p in $PROFILES; do
    args+=(--profile "$p")
  done
  "${args[@]}" "$@"
}

case "$cmd" in
  up)
    dc up -d
    ;;
  down)
    # down без профилей не снимает сервисы из неактивных профилей.
    PROFILES="research ollama"
    dc down
    ;;
  ps)
    dc ps
    ;;
  config)
    dc config
    ;;
  health)
    python -m memory.stack check || exit 1
    if ! curl -fsS "http://127.0.0.1:8080/healthz" >/dev/null; then
      echo "searxng=down" >&2
      exit 1
    fi
    echo "searxng=ok"
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
