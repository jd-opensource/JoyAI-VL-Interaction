#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICES_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ACTION="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

PIDS=()
STARTED_COUNT=0
START_TOTAL=0
SERVICE_READY_TIMEOUT="${SERVICE_READY_TIMEOUT:-900}"
SERVICE_READY_INTERVAL="${SERVICE_READY_INTERVAL:-5}"

usage() {
  cat <<'EOF'
Usage:
  bash services/scripts/run.sh webinfer          Start webinfer.
  bash services/scripts/run.sh asr               Start ASR model + adapter.
  bash services/scripts/run.sh tts               Start TTS model + adapter.
  bash services/scripts/run.sh background-agent  Start background-agent.
  bash services/scripts/run.sh webui             Start WebUI.
  bash services/scripts/run.sh minimal           Start webinfer, then WebUI.
  bash services/scripts/run.sh all               Start webinfer, ASR, TTS, background-agent, then WebUI.

Environment:
  START_ASR=0                 Disable ASR when running all.
  START_TTS=0                 Disable TTS when running all.
  START_BACKGROUND_AGENT=0    Disable background-agent when running all.
  WEBINFER_ARGS="..."         Extra args for services/webinfer/scripts/run.sh all.
  WEBUI_ARGS="..."            Extra args for services/webui/scripts/start_server.sh.
  SERVICE_READY_TIMEOUT=900   Max seconds to wait for backend readiness before WebUI.
  SERVICE_READY_INTERVAL=5    Seconds between backend readiness checks.
EOF
}

start_background() {
  local name="$1"
  shift
  "$@" &
  PIDS+=("$!")
  announce_started "$name"
}

start_foreground() {
  local name="$1"
  local pid
  shift

  "$@" &
  pid="$!"
  PIDS+=("$pid")
  announce_started "$name"
  wait "$pid"
}

announce_started() {
  local name="$1"

  STARTED_COUNT=$((STARTED_COUNT + 1))
  if [[ "$START_TOTAL" -gt 0 ]]; then
    echo "[${STARTED_COUNT}/${START_TOTAL}] Started ${name}."
  else
    echo "Started ${name}."
  fi
}

set_start_total() {
  START_TOTAL="$1"
  STARTED_COUNT=0
}

is_enabled() {
  [[ "${1:-1}" != "0" ]]
}

all_service_count() {
  local total=2

  if is_enabled "${START_ASR:-1}"; then
    total=$((total + 1))
  fi
  if is_enabled "${START_TTS:-1}"; then
    total=$((total + 1))
  fi
  if is_enabled "${START_BACKGROUND_AGENT:-1}"; then
    total=$((total + 1))
  fi

  echo "$total"
}

http_ok() {
  local url="$1"

  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "$url" >/dev/null 2>&1
    return $?
  fi

  python - "$url" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2.0) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
}

is_pid_alive() {
  local pid="$1"
  local stat

  kill -0 "$pid" 2>/dev/null || return 1
  stat="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
  [[ -n "$stat" && "$stat" != Z* ]]
}

ensure_started_processes_alive() {
  local pid

  for pid in "${PIDS[@]:-}"; do
    if ! is_pid_alive "$pid"; then
      wait "$pid" 2>/dev/null || true
      echo "A backend process exited before all services became ready: PID $pid" >&2
      return 1
    fi
  done
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local timeout="${3:-$SERVICE_READY_TIMEOUT}"
  local deadline=$((SECONDS + timeout))

  echo "Waiting for ${name} at ${url}..."
  while (( SECONDS < deadline )); do
    if http_ok "$url"; then
      echo "Ready: ${name}."
      return 0
    fi

    ensure_started_processes_alive
    sleep "$SERVICE_READY_INTERVAL"
  done

  echo "Timed out waiting for ${name} after ${timeout}s: ${url}" >&2
  return 1
}

wait_for_webinfer_ready() {
  local main_model_port="${MAIN_MODEL_PORT:-7060}"
  local summary_port="${SUMMARY_PORT:-8065}"
  local adapter_port="${ADAPTER_PORT:-8070}"

  wait_for_http "webinfer main model" "http://127.0.0.1:${main_model_port}/v1/models"
  wait_for_http "webinfer summary model" "http://127.0.0.1:${summary_port}/v1/models"
  wait_for_http "webinfer adapter" "http://127.0.0.1:${adapter_port}/health"
}

wait_for_asr_ready() {
  local asr_model_port="${ASR_MODEL_PORT:-${ASR_PORT:-8993}}"
  local asr_adapter_port="${ASR_ADAPTER_PORT:-8994}"

  wait_for_http "ASR model" "http://127.0.0.1:${asr_model_port}/v1/models"
  wait_for_http "ASR adapter" "http://127.0.0.1:${asr_adapter_port}/health"
}

wait_for_tts_ready() {
  local tts_model_port="${TTS_MODEL_PORT:-${TTS_PORT:-8991}}"
  local tts_adapter_port="${TTS_ADAPTER_PORT:-8992}"

  wait_for_http "TTS model" "http://127.0.0.1:${tts_model_port}/v1/models"
  wait_for_http "TTS adapter" "http://127.0.0.1:${tts_adapter_port}/health"
}

wait_for_background_agent_ready() {
  local background_agent_port="${BACKGROUND_AGENT_PORT:-${CODEX_API_PORT:-8079}}"

  wait_for_http "background-agent" "http://127.0.0.1:${background_agent_port}/health"
}

wait_for_backends_ready() {
  echo "Waiting for backend services before starting WebUI..."
  wait_for_webinfer_ready
  echo "All required backend services are ready."
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    echo "Stopping services..."
    kill "${PIDS[@]}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit "$status"
}

run_webinfer() {
  cd "$SERVICES_DIR/webinfer"
  # shellcheck disable=SC2086
  exec bash scripts/run.sh all ${WEBINFER_ARGS:-} "$@"
}

run_asr() {
  exec bash "$SERVICES_DIR/asr/scripts/run.sh" all "$@"
}

run_tts() {
  exec bash "$SERVICES_DIR/tts/scripts/run.sh" all "$@"
}

run_background_agent() {
  exec bash "$SERVICES_DIR/background-agent/scripts/run.sh" "$@"
}

run_webui() {
  cd "$SERVICES_DIR/webui"
  # shellcheck disable=SC2086
  exec bash scripts/start_server.sh ${WEBUI_ARGS:-} "$@"
}

run_minimal() {
  trap cleanup EXIT INT TERM
  set_start_total 2
  start_background "webinfer" bash "$SCRIPT_DIR/run.sh" webinfer
  echo "Waiting for backend services before starting WebUI..."
  wait_for_webinfer_ready
  echo "All required backend services are ready."
  start_foreground "WebUI" run_webui "$@"
}

run_all() {
  trap cleanup EXIT INT TERM
  set_start_total "$(all_service_count)"
  start_background "webinfer" bash "$SCRIPT_DIR/run.sh" webinfer

  if is_enabled "${START_TTS:-1}"; then
    start_background "TTS" bash "$SCRIPT_DIR/run.sh" tts
    wait_for_tts_ready
  fi

  if is_enabled "${START_ASR:-1}"; then
    start_background "ASR" bash "$SCRIPT_DIR/run.sh" asr
    wait_for_asr_ready
  fi

  if is_enabled "${START_BACKGROUND_AGENT:-1}"; then
    start_background "background-agent" bash "$SCRIPT_DIR/run.sh" background-agent
    wait_for_background_agent_ready
  fi

  wait_for_backends_ready
  start_foreground "WebUI" run_webui "$@"
}

case "$ACTION" in
  webinfer)
    run_webinfer "$@"
    ;;
  asr)
    run_asr "$@"
    ;;
  tts)
    run_tts "$@"
    ;;
  background-agent)
    run_background_agent "$@"
    ;;
  webui)
    run_webui "$@"
    ;;
  minimal)
    run_minimal "$@"
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
