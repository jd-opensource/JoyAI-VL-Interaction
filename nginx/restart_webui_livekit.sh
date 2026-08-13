#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${JOYVL_PUBLIC_ENV:-${SCRIPT_DIR}/livekit.env}"
LOG_FILE="${JOYVL_WEBUI_LOG:-/tmp/joyvl-webui-public.log}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy livekit-public.env.example to livekit.env and edit it first." >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

WEBUI_PORT="${WEBUI_PORT:-7099}"

# Clean up either WebUI port layout by process identity, never by the listener port.
previous_webui_pids=()
if command -v pgrep >/dev/null 2>&1; then
  mapfile -t previous_webui_pids < <(
    pgrep -f 'joy_interaction_webui[.]server.*--port (7099|7100)([[:space:]]|$)' || true
  )
fi
if [[ ${#previous_webui_pids[@]} -gt 0 ]]; then
  echo "Stopping previous WebUI PIDs ${previous_webui_pids[*]}"
  kill "${previous_webui_pids[@]}" 2>/dev/null || true
  for _ in $(seq 1 100); do
    remaining_webui_pids=()
    for pid in "${previous_webui_pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        remaining_webui_pids+=("${pid}")
      fi
    done
    if [[ ${#remaining_webui_pids[@]} -eq 0 ]]; then
      break
    fi
    sleep 0.1
  done
  if [[ ${#remaining_webui_pids[@]} -gt 0 ]]; then
    echo "Forcing previous WebUI PIDs ${remaining_webui_pids[*]}"
    kill -9 "${remaining_webui_pids[@]}" 2>/dev/null || true
  fi
fi

WEBUI_STOP_BY_PORT=false bash "${PROJECT_ROOT}/services/webui/scripts/stop_server.sh"

if command -v setsid >/dev/null 2>&1; then
  setsid -f bash "${PROJECT_ROOT}/services/webui/scripts/start_server.sh" >>"${LOG_FILE}" 2>&1
else
  nohup bash "${PROJECT_ROOT}/services/webui/scripts/start_server.sh" >>"${LOG_FILE}" 2>&1 &
fi

for _ in $(seq 1 60); do
  if curl -ksS --max-time 2 "https://127.0.0.1:${WEBUI_PORT}/" >/dev/null; then
    echo "WebUI and LiveKit restarted. WebUI upstream: https://127.0.0.1:${WEBUI_PORT}/"
    exit 0
  fi
  sleep 1
done

echo "Timed out waiting for WebUI. Check ${LOG_FILE}." >&2
exit 1
