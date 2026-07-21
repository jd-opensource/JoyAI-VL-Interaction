#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ACTION="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

# ==================== Python / environment ====================
# Auto-use the shared install venv when available. Set VENV_ACTIVATE= to use the
# current shell environment instead.
PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ -z "${VENV_ACTIVATE+x}" ]]; then
  DEFAULT_VENV_ACTIVATE="${SERVICE_DIR}/../.venv/bin/activate"
  if [[ -f "${DEFAULT_VENV_ACTIVATE}" ]]; then
    VENV_ACTIVATE="${DEFAULT_VENV_ACTIVATE}"
  else
    VENV_ACTIVATE=""
  fi
else
  VENV_ACTIVATE="${VENV_ACTIVATE:-}"
fi

# ==================== Model paths ====================
MODEL_ROOT="${MODEL_ROOT:-/tmp/models}"
STREAMING_MODEL_REPO="${STREAMING_MODEL_REPO:-jdopensource/JoyAI-VL-Interaction-Preview}"
STREAMING_MODEL_NAME="${STREAMING_MODEL_NAME:-JoyAI-VL-Interaction-Preview}"
MODEL_PATH="${MODEL_PATH:-${MODEL_ROOT}/${STREAMING_MODEL_NAME}}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${STREAMING_MODEL_NAME}}"

SUMMARY_MODEL_REPO="${SUMMARY_MODEL_REPO:-Qwen/Qwen3-VL-4B-Instruct}"
SUMMARY_MODEL_NAME="${SUMMARY_MODEL_NAME:-Qwen3-VL-4B-Instruct}"
SUMMARY_MODEL_PATH="${SUMMARY_MODEL_PATH:-${MODEL_ROOT}/${SUMMARY_MODEL_NAME}}"
SUMMARIZER_MODEL="${SUMMARIZER_MODEL:-${SUMMARY_MODEL_PATH}}"

# ==================== Runtime ports / GPUs ====================
MAIN_GPU="${MAIN_GPU:-0}"
MAIN_MODEL_PORT="${MAIN_MODEL_PORT:-7060}"
MAIN_MODEL="${MAIN_MODEL:-${SERVED_MODEL_NAME}}"
MAIN_API_BASE="${MAIN_API_BASE:-http://127.0.0.1:${MAIN_MODEL_PORT}/v1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

SUMMARY_GPU="${SUMMARY_GPU:-1}"
SUMMARY_PORT="${SUMMARY_PORT:-8065}"
SUMMARIZER_API_BASE="${SUMMARIZER_API_BASE:-http://127.0.0.1:${SUMMARY_PORT}/v1}"

ADAPTER_HOST="${ADAPTER_HOST:-127.0.0.1}"
ADAPTER_PORT="${ADAPTER_PORT:-8070}"
if [[ -z "${MAIN_BACKENDS:-}" ]]; then
  MAIN_BACKENDS="[{\"name\":\"${MAIN_MODEL}\",\"api_base\":\"${MAIN_API_BASE}\",\"model\":\"${SERVED_MODEL_NAME}\"}]"
fi

# Used only by `bash scripts/run.sh all`.
MODEL_START_DELAY="${MODEL_START_DELAY:-5}"

export PYTHON_BIN VENV_ACTIVATE
export MODEL_ROOT STREAMING_MODEL_REPO STREAMING_MODEL_NAME MODEL_PATH SERVED_MODEL_NAME
export SUMMARY_MODEL_REPO SUMMARY_MODEL_NAME SUMMARY_MODEL_PATH SUMMARIZER_MODEL
export MAIN_GPU MAIN_MODEL_PORT MAIN_MODEL MAIN_API_BASE MAX_MODEL_LEN MAIN_BACKENDS
export SUMMARY_GPU SUMMARY_PORT SUMMARIZER_API_BASE
export ADAPTER_HOST ADAPTER_PORT

usage() {
  cat <<EOF
Usage:
  bash scripts/run.sh summary         Start the summary vLLM service.
  bash scripts/run.sh models          Start all main model vLLM services.
  bash scripts/run.sh adapter         Start the web inference adapter.
  bash scripts/run.sh all             Start summary/models/adapter.

Common overrides:
  VENV_ACTIVATE=/path/to/bin/activate
  VENV_ACTIVATE=      # disable auto activation and use current shell environment
  PYTHON_BIN=/path/to/python
  MODEL_ROOT=/tmp/models
  MODEL_PATH=/tmp/models/JoyAI-VL-Interaction-Preview
  STREAMING_MODEL_REPO=jdopensource/JoyAI-VL-Interaction-Preview
  SUMMARY_MODEL_REPO=Qwen/Qwen3-VL-4B-Instruct
  SUMMARY_MODEL_PATH=/tmp/models/Qwen3-VL-4B-Instruct
  MAIN_GPU=0 SUMMARY_GPU=1 MAIN_MODEL_PORT=7060 SUMMARY_PORT=8065 ADAPTER_PORT=8070
EOF
}

run_summary() {
  bash "${SCRIPT_DIR}/start_summary_model.sh" "$@"
}

run_models() {
  bash "${SCRIPT_DIR}/start_all_models.sh" "$@"
}

run_adapter() {
  bash "${SCRIPT_DIR}/start_adapter.sh" "$@"
}

run_all() {
  local pids=()
  local summary_pid=""
  local summary_pid_file="${LOG_DIR:-${SERVICE_DIR}/summary_vllm_logs}/vllm_${SUMMARY_PORT}.pid"

  cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ ${#pids[@]} -gt 0 ]]; then
      echo "Stopping child services..."
      kill "${pids[@]}" 2>/dev/null || true
    fi
    if [[ -n "${summary_pid}" ]]; then
      kill "${summary_pid}" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    exit "${status}"
  }
  trap cleanup EXIT INT TERM

  run_summary
  if [[ -f "${summary_pid_file}" ]]; then
    summary_pid="$(cat "${summary_pid_file}")"
  fi

  run_models &
  pids+=("$!")
  sleep "${MODEL_START_DELAY}"

  run_adapter "$@"
}

case "${ACTION}" in
  summary)
    run_summary "$@"
    ;;
  model|models)
    run_models "$@"
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
    echo "Unknown action: ${ACTION}" >&2
    usage >&2
    exit 2
    ;;
esac
