#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICES_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ACTION="${1:-all}"
if [[ $# -gt 0 ]]; then
  shift
fi

GRACE_SECONDS="${GRACE_SECONDS:-10}"
ASR_MODEL_PORT="${ASR_MODEL_PORT:-8993}"
ASR_ADAPTER_PORT="${ASR_ADAPTER_PORT:-8994}"
TTS_MODEL_PORT="${TTS_MODEL_PORT:-8991}"
TTS_ADAPTER_PORT="${TTS_ADAPTER_PORT:-8992}"
BACKGROUND_AGENT_PORT="${BACKGROUND_AGENT_PORT:-8079}"
WEBUI_PORT="${WEBUI_PORT:-7099}"
WEBUI_STOP_BY_PORT="${WEBUI_STOP_BY_PORT:-true}"
LIVEKIT_SIGNAL_PORT="${LIVEKIT_SIGNAL_PORT:-8298}"
LIVEKIT_UDP_PORT="${LIVEKIT_UDP_PORT:-8299}"
LIVEKIT_TCP_PORT="${LIVEKIT_TCP_PORT:-${LIVEKIT_UDP_PORT}}"
PIDS_TO_KILL=()

usage() {
  cat <<EOF
Usage:
  bash services/scripts/stop.sh all               Stop all services.
  bash services/scripts/stop.sh webinfer          Stop webinfer.
  bash services/scripts/stop.sh asr               Stop ASR model + adapter.
  bash services/scripts/stop.sh tts               Stop TTS model + adapter.
  bash services/scripts/stop.sh background-agent  Stop background-agent.
  bash services/scripts/stop.sh webui             Stop WebUI.

Environment:
  GRACE_SECONDS=10
  ASR_MODEL_PORT=8993 ASR_ADAPTER_PORT=8994
  TTS_MODEL_PORT=8991 TTS_ADAPTER_PORT=8992
  BACKGROUND_AGENT_PORT=8079 WEBUI_PORT=7099 WEBUI_STOP_BY_PORT=true
  LIVEKIT_SIGNAL_PORT=8298 LIVEKIT_UDP_PORT=8299 LIVEKIT_TCP_PORT=8299
EOF
}

is_pid_running() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

add_pid() {
  local pid="$1"
  local existing

  [[ "${pid}" =~ ^[0-9]+$ ]] || return 0
  [[ "${pid}" != "$$" ]] || return 0

  for existing in "${PIDS_TO_KILL[@]:-}"; do
    [[ "${existing}" != "${pid}" ]] || return 0
  done

  PIDS_TO_KILL+=("${pid}")
}

add_descendants() {
  local parent="$1"
  local child

  command -v pgrep >/dev/null 2>&1 || return 0
  while IFS= read -r child; do
    add_pid "${child}"
    add_descendants "${child}"
  done < <(pgrep -P "${parent}" 2>/dev/null || true)
}

add_pids_on_port() {
  local port="$1"
  local pid

  if command -v lsof >/dev/null 2>&1; then
    while IFS= read -r pid; do
      add_pid "${pid}"
    done < <(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)
  elif command -v fuser >/dev/null 2>&1; then
    for pid in $(fuser -n tcp "${port}" 2>/dev/null || true); do
      add_pid "${pid}"
    done
  fi
}

add_pids_by_pattern() {
  local pattern="$1"
  local pid

  command -v pgrep >/dev/null 2>&1 || return 0
  while IFS= read -r pid; do
    add_pid "${pid}"
  done < <(pgrep -f -- "${pattern}" 2>/dev/null || true)
}

kill_collected_pids() {
  local label="$1"
  local pid
  local deadline
  local still_running=()

  if [[ ${#PIDS_TO_KILL[@]} -eq 0 ]]; then
    echo "${label}: no matching processes."
    return 0
  fi

  for pid in "${PIDS_TO_KILL[@]}"; do
    add_descendants "${pid}"
  done

  echo "${label}: stopping PIDs ${PIDS_TO_KILL[*]}"
  kill "${PIDS_TO_KILL[@]}" 2>/dev/null || true

  deadline=$((SECONDS + GRACE_SECONDS))
  while (( SECONDS < deadline )); do
    still_running=()
    for pid in "${PIDS_TO_KILL[@]}"; do
      if is_pid_running "${pid}"; then
        still_running+=("${pid}")
      fi
    done

    if [[ ${#still_running[@]} -eq 0 ]]; then
      echo "${label}: stopped."
      return 0
    fi

    sleep 1
  done

  still_running=()
  for pid in "${PIDS_TO_KILL[@]}"; do
    if is_pid_running "${pid}"; then
      still_running+=("${pid}")
    fi
  done

  if [[ ${#still_running[@]} -gt 0 ]]; then
    echo "${label}: forcing PIDs ${still_running[*]}"
    kill -9 "${still_running[@]}" 2>/dev/null || true
  fi
}

stop_webinfer() {
  bash "$SERVICES_DIR/webinfer/scripts/stop.sh" all
}

stop_asr() {
  PIDS_TO_KILL=()
  add_pids_on_port "${ASR_ADAPTER_PORT}"
  add_pids_on_port "${ASR_MODEL_PORT}"
  add_pids_by_pattern "${SERVICES_DIR}/asr/scripts/run.sh"
  add_pids_by_pattern "${SERVICES_DIR}/asr/scripts/run-model.sh"
  add_pids_by_pattern "${SERVICES_DIR}/asr/scripts/run-adapter.sh"
  add_pids_by_pattern "services/asr/scripts/run.sh"
  add_pids_by_pattern "services/asr/scripts/run-model.sh"
  add_pids_by_pattern "services/asr/scripts/run-adapter.sh"
  add_pids_by_pattern "joyvl-asr-adapter"
  add_pids_by_pattern "vllm serve .*Qwen3-ASR"
  kill_collected_pids "asr"
}

stop_tts() {
  PIDS_TO_KILL=()
  add_pids_on_port "${TTS_ADAPTER_PORT}"
  add_pids_on_port "${TTS_MODEL_PORT}"
  add_pids_by_pattern "${SERVICES_DIR}/tts/scripts/run.sh"
  add_pids_by_pattern "${SERVICES_DIR}/tts/scripts/run-model.sh"
  add_pids_by_pattern "${SERVICES_DIR}/tts/scripts/run-adapter.sh"
  add_pids_by_pattern "services/tts/scripts/run.sh"
  add_pids_by_pattern "services/tts/scripts/run-model.sh"
  add_pids_by_pattern "services/tts/scripts/run-adapter.sh"
  add_pids_by_pattern "joyvl-tts-adapter"
  add_pids_by_pattern "vllm-omni serve .*Qwen3-TTS"
  kill_collected_pids "tts"
}

stop_background_agent() {
  PIDS_TO_KILL=()
  add_pids_on_port "${BACKGROUND_AGENT_PORT}"
  add_pids_by_pattern "${SERVICES_DIR}/background-agent/scripts/run.sh"
  add_pids_by_pattern "services/background-agent/scripts/run.sh"
  add_pids_by_pattern "uvicorn codex_api.main:app.*--port ${BACKGROUND_AGENT_PORT}"
  add_pids_by_pattern "python -m uvicorn codex_api.main:app.*--port ${BACKGROUND_AGENT_PORT}"
  kill_collected_pids "background-agent"
}

stop_webui() {
  PIDS_TO_KILL=()
  if [[ "${WEBUI_STOP_BY_PORT}" == "true" ]]; then
    add_pids_on_port "${WEBUI_PORT}"
  fi
  add_pids_on_port "${LIVEKIT_SIGNAL_PORT}"
  add_pids_on_port "${LIVEKIT_TCP_PORT}"
  if command -v fuser >/dev/null 2>&1; then
    for pid in $(fuser -n udp "${LIVEKIT_UDP_PORT}" 2>/dev/null || true); do
      add_pid "${pid}"
    done
  fi
  add_pids_by_pattern "${SERVICES_DIR}/webui/.livekit/livekit-server.*${SERVICES_DIR}/webui/.livekit/livekit.yaml"
  add_pids_by_pattern "joy_interaction_webui.server.*--port ${WEBUI_PORT}"
  add_pids_by_pattern "${SERVICES_DIR}/webui/scripts/start_server.sh"
  kill_collected_pids "webui"
}

stop_all() {
  stop_webui
  stop_background_agent
  stop_tts
  stop_asr
  stop_webinfer
}

case "${ACTION}" in
  all)
    stop_all
    ;;
  webinfer)
    stop_webinfer
    ;;
  asr)
    stop_asr
    ;;
  tts)
    stop_tts
    ;;
  background-agent|background_agent|background)
    stop_background_agent
    ;;
  webui)
    stop_webui
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    usage >&2
    exit 2
    ;;
esac
