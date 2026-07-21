#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
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
MODEL_PATH="${MODEL_PATH:-/tmp/models/JoyAI-VL-Interaction-Preview}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-JoyAI-VL-Interaction-Preview}"
MAIN_MODEL_PORT="${MAIN_MODEL_PORT:-7060}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
MAIN_GPU_MEMORY_UTILIZATION="${MAIN_GPU_MEMORY_UTILIZATION:-0.9}"
PYTHON_BIN="${PYTHON_BIN:-python}"

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
echo "============================================================"

CUDA_VISIBLE_DEVICES="${MAIN_GPU}" "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_PATH}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --port "${MAIN_MODEL_PORT}" \
    --gpu-memory-utilization "${MAIN_GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --data-parallel-size "${DATA_PARALLEL_SIZE}" \
    --data-parallel-size-local "${DATA_PARALLEL_SIZE_LOCAL}" \
    --enable-prefix-caching \
    --enable-chunked-prefill
