#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ -n "${VENV_ACTIVATE:-}" ]]; then
  set +u
  source "${VENV_ACTIVATE}"
  set -u
fi
MAIN_GPU="${MAIN_GPU:-0}"
IFS=',' read -ra GPU_LIST <<< "${MAIN_GPU}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-${#GPU_LIST[@]}}"
DATA_PARALLEL_SIZE="${DATA_PARALLEL_SIZE:-1}"
DATA_PARALLEL_SIZE_LOCAL="${DATA_PARALLEL_SIZE_LOCAL:-${DATA_PARALLEL_SIZE}}"
MODEL_PATH="${MODEL_PATH:-/tmp/models/jdopensource/JoyAI-VL-Interaction}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-jdopensource/JoyAI-VL-Interaction}"
MAIN_MODEL_PORT="${MAIN_MODEL_PORT:-7060}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
MAIN_GPU_MEMORY_UTILIZATION="${MAIN_GPU_MEMORY_UTILIZATION:-0.9}"
LINEAR_BACKEND="${LINEAR_BACKEND:-auto}"
PYTHON_BIN="${PYTHON_BIN:-python}"
MAIN_SMOKE_ENABLE="${MAIN_SMOKE_ENABLE:-1}"
MAIN_SMOKE_ATTEMPTS="${MAIN_SMOKE_ATTEMPTS:-120}"
MAIN_SMOKE_INTERVAL="${MAIN_SMOKE_INTERVAL:-2.0}"
MAIN_SMOKE_TIMEOUT="${MAIN_SMOKE_TIMEOUT:-60.0}"
MAIN_LOG_FILE=""
LINEAR_BACKEND_ARGS=()
if [[ "${LINEAR_BACKEND}" != "auto" ]]; then
  LINEAR_BACKEND_ARGS=(--linear-backend "${LINEAR_BACKEND}")
fi
if [[ "${SAVE_SERVICE_LOGS:-0}" == "1" ]]; then
  MAIN_LOG_DIR="${MAIN_LOG_DIR:-${SERVICE_DIR}/main_vllm_logs}"
  mkdir -p "${MAIN_LOG_DIR}"
  MAIN_LOG_FILE="${MAIN_LOG_FILE:-${MAIN_LOG_DIR}/vllm_${MAIN_MODEL_PORT}.log}"
  exec > >(tee -a "${MAIN_LOG_FILE}") 2>&1
fi

if [[ ! -d "${MODEL_PATH}" ]] || [[ -z "$(find "${MODEL_PATH}" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "主模型目录不存在或为空: ${MODEL_PATH}" >&2
    echo "请先从仓库根目录运行: ./install/download-models.sh --all" >&2
    exit 1
fi

echo "============================================================"
echo "Starting Main VLM Model (vLLM OpenAI API Server)"
echo "  Model: ${MODEL_PATH}"
echo "  Served model name: ${SERVED_MODEL_NAME}"
echo "  Port:  ${MAIN_MODEL_PORT}"
echo "  GPU:   ${MAIN_GPU}"
echo "  Tensor parallel size: ${TENSOR_PARALLEL_SIZE}"
echo "  Data parallel size: ${DATA_PARALLEL_SIZE}"
echo "  Local data parallel size: ${DATA_PARALLEL_SIZE_LOCAL}"
echo "  Max model len: ${MAX_MODEL_LEN}"
echo "  Linear backend: ${LINEAR_BACKEND}"
echo "  Smoke warmup: ${MAIN_SMOKE_ENABLE} (attempts=${MAIN_SMOKE_ATTEMPTS}, interval=${MAIN_SMOKE_INTERVAL}s, timeout=${MAIN_SMOKE_TIMEOUT}s)"
if [[ -n "${MAIN_LOG_FILE}" ]]; then
    echo "  Log:   ${MAIN_LOG_FILE}"
else
    echo "  Log:   disabled"
fi
echo "============================================================"

MODEL_PID=""
cleanup() {
    if [[ -n "${MODEL_PID}" ]]; then
        kill "${MODEL_PID}" 2>/dev/null || true
    fi
}
trap cleanup INT TERM

CUDA_VISIBLE_DEVICES="${MAIN_GPU}" "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_PATH}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --port "${MAIN_MODEL_PORT}" \
    --gpu-memory-utilization "${MAIN_GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --data-parallel-size "${DATA_PARALLEL_SIZE}" \
    --data-parallel-size-local "${DATA_PARALLEL_SIZE_LOCAL}" \
    "${LINEAR_BACKEND_ARGS[@]}" \
    --enable-prefix-caching \
    --enable-chunked-prefill &
MODEL_PID=$!

if [[ "${MAIN_SMOKE_ENABLE}" != "0" ]]; then
    (
        echo "Main VLM smoke: warming ${SERVED_MODEL_NAME} on http://127.0.0.1:${MAIN_MODEL_PORT}/v1"
        if "${PYTHON_BIN}" "${SCRIPT_DIR}/../smoke.py" main-vlm \
            --api-base "http://127.0.0.1:${MAIN_MODEL_PORT}/v1" \
            --model "${SERVED_MODEL_NAME}" \
            --attempts "${MAIN_SMOKE_ATTEMPTS}" \
            --interval "${MAIN_SMOKE_INTERVAL}" \
            --timeout "${MAIN_SMOKE_TIMEOUT}"; then
            echo "Main VLM smoke: completed"
        else
            echo "Main VLM smoke: failed; model server will keep running" >&2
        fi
    ) &
fi

wait "${MODEL_PID}"
