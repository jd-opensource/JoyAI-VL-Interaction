#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run.sh model      Start the ASR vLLM service.
  bash scripts/run.sh adapter    Start the ASR adapter.
  bash scripts/run.sh all        Start ASR model in background, then adapter in foreground.
EOF
}

run_model() {
  bash "$SCRIPT_DIR/run-model.sh" "$@"
}

run_adapter() {
  bash "$SCRIPT_DIR/run-adapter.sh" "$@"
}

run_all() {
  local model_pid=""

  cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n "$model_pid" ]]; then
      kill "$model_pid" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    exit "$status"
  }
  trap cleanup EXIT INT TERM

  run_model &
  model_pid="$!"

  while kill -0 "$model_pid" 2>/dev/null; do
    if (: >"/dev/tcp/127.0.0.1/8993") >/dev/null 2>&1; then
      run_adapter "$@"
      return
    fi
    sleep 1
  done

  wait "$model_pid"
  echo "ASR model process exited before upstream port 8993 became ready." >&2
  echo "Check that the model exists, or run from repo root: ./install/download-models.sh --all" >&2
  exit 1
}

case "$ACTION" in
  model)
    run_model "$@"
    ;;
  adapter)
    run_adapter "$@"
    ;;
  all)
    run_all "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage >&2
    exit 2
    ;;
esac
