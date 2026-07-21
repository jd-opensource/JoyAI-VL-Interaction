#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

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

if [[ ! -d "${SUMMARY_MODEL_PATH}" ]] || [[ -z "$(find "${SUMMARY_MODEL_PATH}" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "摘要模型目录不存在或为空: ${SUMMARY_MODEL_PATH}" >&2
    echo "请先从仓库根目录运行: ./install/download-models.sh --all" >&2
    exit 1
fi

CUDA_VISIBLE_DEVICES="${SUMMARY_GPU}" nohup "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
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

echo ""
echo "摘要服务已在后台启动。"
echo "查看日志:  tail -f ${LOG_DIR}/vllm_${SUMMARY_PORT}.log"
echo "停止服务:  kill \$(cat ${LOG_DIR}/vllm_${SUMMARY_PORT}.pid)"

# 如果想脚本前台等待进程结束，取消下面注释
# wait $PID
