#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="$ROOT/docker-compose.yml"
PROFILES=(regular 24GB 16GB)

usage() {
  echo "Usage: $0 <regular|24GB|16GB> <up|down|restart|status|logs|test>" >&2
  exit 2
}

profile="${1:-}"
action="${2:-}"
shift $(( $# >= 2 ? 2 : $# ))
(( $# == 0 )) || usage
[[ " ${PROFILES[*]} " == *" $profile "* ]] || usage
env_file="$ROOT/$profile/.env"
[[ -f "$env_file" ]] || { echo "Missing $env_file; copy .env.example first." >&2; exit 1; }

compose() { docker compose --env-file "$env_file" -f "$COMPOSE" "$@"; }
stop_others() {
  local other
  for other in "${PROFILES[@]}"; do
    [[ "$other" == "$profile" ]] && continue
    local other_env="$ROOT/$other/.env"
    [[ -f "$other_env" ]] && docker compose --env-file "$other_env" -f "$COMPOSE" down --remove-orphans || true
  done
}
check_images() {
  local main_image
  local image
  main_image="$(sed -n 's/^VLLM_IMAGE=//p' "$env_file" | tail -1)"
  for image in \
    "$main_image" \
    joyai-vl-app:latest \
    joyai-vl-asr-model:vllm0.22.0 \
    joyai-vl-asr-adapter:latest \
    joyai-vl-tts-model:vllm0.22.0 \
    joyai-vl-tts-adapter:latest \
    joyai-vl-background-model:latest; do
    docker image inspect "$image" >/dev/null 2>&1 || {
      echo "Missing image: $image" >&2
      echo "Run ./container/scripts/build-images.sh before starting." >&2
      exit 1
    }
  done
}

case "$action" in
  up)
    stop_others
    check_images
    compose up -d
    ;;
  down) compose down --remove-orphans ;;
  restart)
    stop_others
    check_images
    compose down --remove-orphans
    compose up -d
    ;;
  status) compose ps -a ;;
  logs) compose logs -f --tail=200 ;;
  test)
    compose ps -a
    curl -fsS http://127.0.0.1:7060/v1/models
    curl -fsS http://127.0.0.1:8065/v1/models
    curl -fsS http://127.0.0.1:8070/health
    curl -fsS http://127.0.0.1:8079/health
    curl -fsS http://127.0.0.1:8994/health
    curl -fsS http://127.0.0.1:8992/health
    curl -kfsS https://127.0.0.1:8099/ >/dev/null
    ;;
  *) usage ;;
esac
