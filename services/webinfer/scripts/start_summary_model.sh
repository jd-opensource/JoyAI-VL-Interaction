#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

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

if [[ -n "${VENV_ACTIVATE:-}" ]]; then
  set +u
  source "${VENV_ACTIVATE}"
  set -u
fi

# 日志目录
LOG_DIR="${LOG_DIR:-${SERVICE_DIR}/summary_vllm_logs}"
mkdir -p "$LOG_DIR"

SUMMARY_GPU="${SUMMARY_GPU:-1}"
SUMMARY_PORT="${SUMMARY_PORT:-8065}"
SUMMARY_MODEL_PATH="${SUMMARY_MODEL_PATH:-/tmp/models/Qwen3-VL-4B-Instruct}"
SUMMARY_SERVED_MODEL_NAME="${SUMMARY_SERVED_MODEL_NAME:-${SUMMARY_MODEL_PATH}}"
SUMMARY_MAX_MODEL_LEN="${SUMMARY_MAX_MODEL_LEN:-65536}"
SUMMARY_GPU_MEMORY_UTILIZATION="${SUMMARY_GPU_MEMORY_UTILIZATION:-0.9}"
PYTHON_BIN="${PYTHON_BIN:-python}"
SUMMARY_SMOKE_ENABLE="${SUMMARY_SMOKE_ENABLE:-1}"
SUMMARY_SMOKE_ATTEMPTS="${SUMMARY_SMOKE_ATTEMPTS:-120}"
SUMMARY_SMOKE_INTERVAL="${SUMMARY_SMOKE_INTERVAL:-2.0}"
SUMMARY_SMOKE_TIMEOUT="${SUMMARY_SMOKE_TIMEOUT:-60.0}"
SUMMARY_LAUNCHER_LOG_FILE="${SUMMARY_LAUNCHER_LOG_FILE:-${LOG_DIR}/launcher_${SUMMARY_PORT}.log}"
SUMMARY_FOREGROUND="${SUMMARY_FOREGROUND:-0}"
SUMMARY_LAUNCH_PREFIX=()
if [[ "${SUMMARY_FOREGROUND}" != "1" ]]; then
    SUMMARY_LAUNCH_PREFIX=(nohup)
fi
exec > >(tee -a "${SUMMARY_LAUNCHER_LOG_FILE}") 2>&1
echo "Summary launcher log: ${SUMMARY_LAUNCHER_LOG_FILE}"

if [[ ! -d "${SUMMARY_MODEL_PATH}" ]] || [[ -z "$(find "${SUMMARY_MODEL_PATH}" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "摘要模型目录不存在或为空: ${SUMMARY_MODEL_PATH}" >&2
    echo "请先从仓库根目录运行: ./install/download-models.sh --all" >&2
    exit 1
fi

CUDA_VISIBLE_DEVICES="${SUMMARY_GPU}" \
  "${SUMMARY_LAUNCH_PREFIX[@]}" \
  "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --model "${SUMMARY_MODEL_PATH}" \
    --served-model-name "${SUMMARY_SERVED_MODEL_NAME}" \
    --port "${SUMMARY_PORT}" \
    --max-model-len "${SUMMARY_MAX_MODEL_LEN}" \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --gpu-memory-utilization "${SUMMARY_GPU_MEMORY_UTILIZATION}" \
    > "${LOG_DIR}/vllm_${SUMMARY_PORT}.log" 2>&1 &
PID=$!
echo "Started summary vLLM on GPU ${SUMMARY_GPU}, port ${SUMMARY_PORT} (PID=${PID})"

# 保存 PID，方便后续 kill
echo "${PID}" > "${LOG_DIR}/vllm_${SUMMARY_PORT}.pid"

if [[ "${SUMMARY_SMOKE_ENABLE}" != "0" ]]; then
    (
        echo "Summary smoke: warming ${SUMMARY_SERVED_MODEL_NAME} on http://127.0.0.1:${SUMMARY_PORT}/v1"
        if "${PYTHON_BIN}" "${SERVICE_DIR}/smoke.py" summary \
            --api-base "http://127.0.0.1:${SUMMARY_PORT}/v1" \
            --model "${SUMMARY_SERVED_MODEL_NAME}" \
            --attempts "${SUMMARY_SMOKE_ATTEMPTS}" \
            --interval "${SUMMARY_SMOKE_INTERVAL}" \
            --timeout "${SUMMARY_SMOKE_TIMEOUT}"; then
            echo "Summary smoke: completed"
        else
            echo "Summary smoke: failed; summary vLLM will keep running" >&2
        fi
    ) >> "${LOG_DIR}/vllm_${SUMMARY_PORT}.log" 2>&1 &
    SMOKE_PID=$!
fi

echo ""
echo "摘要服务已在后台启动。"
echo "查看日志:  tail -f ${LOG_DIR}/vllm_${SUMMARY_PORT}.log"
echo "停止服务:  kill \$(cat ${LOG_DIR}/vllm_${SUMMARY_PORT}.pid)"

if [[ "${SUMMARY_FOREGROUND}" == "1" ]]; then
    cleanup() {
        local status=$?
        trap - EXIT INT TERM
        if [[ -n "${SMOKE_PID:-}" ]]; then
            kill "${SMOKE_PID}" 2>/dev/null || true
        fi
        if [[ -n "${PID:-}" ]]; then
            kill "${PID}" 2>/dev/null || true
        fi
        wait 2>/dev/null || true
        exit "${status}"
    }
    trap cleanup EXIT INT TERM
    wait "${PID}"
fi
