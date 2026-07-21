#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL_BASE="${MODEL_BASE:-/tmp/models}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
MAIN_GPU="${MAIN_GPU:-0}"
MAIN_MODEL_PORT="${MAIN_MODEL_PORT:-7060}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-JoyAI-VL-Interaction-Preview}"
MODEL_PATH="${MODEL_PATH:-${MODEL_BASE}/${SERVED_MODEL_NAME}}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
DATA_PARALLEL_SIZE="${DATA_PARALLEL_SIZE:-1}"
DATA_PARALLEL_SIZE_LOCAL="${DATA_PARALLEL_SIZE_LOCAL:-${DATA_PARALLEL_SIZE}}"

declare -A MODELS
MODELS=(
  ["${SERVED_MODEL_NAME}"]="${MAIN_GPU}:${MAIN_MODEL_PORT}:${MODEL_PATH}:${TENSOR_PARALLEL_SIZE}:${DATA_PARALLEL_SIZE}:${DATA_PARALLEL_SIZE_LOCAL}"
)

PIDS=()

cleanup() {
  echo "Stopping all model servers..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait
  echo "All servers stopped."
}
trap cleanup EXIT INT TERM

for name in "${!MODELS[@]}"; do
  IFS=':' read -r gpu port model_path tensor_parallel_size data_parallel_size data_parallel_size_local <<< "${MODELS[$name]}"
  echo "============================================================"
  echo "Launching ${name}"
  echo "  GPU:   ${gpu}"
  echo "  Port:  ${port}"
  echo "  Model: ${model_path}"
  echo "  Tensor parallel size: ${tensor_parallel_size}"
  echo "  Data parallel size:   ${data_parallel_size}"
  echo "============================================================"

  MAIN_GPU="${gpu}" \
  DATA_PARALLEL_SIZE="${data_parallel_size}" \
  DATA_PARALLEL_SIZE_LOCAL="${data_parallel_size_local}" \
  TENSOR_PARALLEL_SIZE="${tensor_parallel_size}" \
  MAIN_MODEL_PORT="${port}" \
  SERVED_MODEL_NAME="${name}" \
  MODEL_PATH="${model_path}" \
  MAX_MODEL_LEN="${MAX_MODEL_LEN}" \
    bash "${SCRIPT_DIR}/start_model.sh" &
  PIDS+=($!)
done

echo ""
echo "============================================================"
echo "All ${#MODELS[@]} model servers launched. PIDs: ${PIDS[*]}"
echo "Press Ctrl+C to stop all."
echo "============================================================"

wait
