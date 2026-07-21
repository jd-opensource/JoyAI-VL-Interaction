#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -n "${VENV_ACTIVATE:-}" ]]; then
  set +u
  source "${VENV_ACTIVATE}"
  set -u
fi
ADAPTER_HOST="${ADAPTER_HOST:-127.0.0.1}"
ADAPTER_PORT="${ADAPTER_PORT:-8070}"
ADAPTER_MODEL="${ADAPTER_MODEL:-streaming-infer-adapter}"

MAIN_API_BASE="${MAIN_API_BASE:-http://127.0.0.1:7060/v1}"
MAIN_MODEL="${MAIN_MODEL:-JoyAI-VL-Interaction-Preview}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${MAIN_MODEL}}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# ==================== 主模型后端配置 ====================
# JSON array of backends. When set, adapter routes by model name.
_DEFAULT_BACKENDS="[{\"name\":\"${MAIN_MODEL}\",\"api_base\":\"${MAIN_API_BASE}\",\"model\":\"${SERVED_MODEL_NAME}\"}]"
MAIN_BACKENDS="${MAIN_BACKENDS:-${_DEFAULT_BACKENDS}}"

# ==================== 摘要模型配置 ====================
SUMMARIZER_API_BASE="${SUMMARIZER_API_BASE:-http://127.0.0.1:8065/v1}"
SUMMARIZER_MODEL="${SUMMARIZER_MODEL:-/tmp/models/Qwen3-VL-4B-Instruct}"

SUMMARIZER_MAX_PIXELS="${SUMMARIZER_MAX_PIXELS:-262144}"
SUMMARIZER_KEY_FRAMES="${SUMMARIZER_KEY_FRAMES:-0}"
SUMMARIZER_PHASE_SECONDS="${SUMMARIZER_PHASE_SECONDS:-10.0}"

# ==================== 流程控制参数 ====================
LIVE_SAVE_OUTPUTS="${LIVE_SAVE_OUTPUTS:-true}"
export LIVE_SAVE_OUTPUTS
FRAME_SAVE_DIR="${FRAME_SAVE_DIR:-/tmp/streaming_adapter_frames}"
ALLOWED_LOCAL_IMAGE_ROOTS="${ALLOWED_LOCAL_IMAGE_ROOTS:-${FRAME_SAVE_DIR}}"
CHUNK="${CHUNK:-100}"
COMPRESS_EVERY_N_CHUNKS="${COMPRESS_EVERY_N_CHUNKS:-5}"
FRAME_SECONDS="${FRAME_SECONDS:-1.0}"
ASYNC_SUMMARY_LEAD_FRAMES="${ASYNC_SUMMARY_LEAD_FRAMES:-10}"

# ==================== 主 8B 模型采样参数 ====================
MAIN_MAX_TOKENS="${MAIN_MAX_TOKENS:-256}"
MAIN_TEMPERATURE="${MAIN_TEMPERATURE:-0.8}"
MAIN_TOP_P="${MAIN_TOP_P:-0.9}"
MAIN_TOP_K="${MAIN_TOP_K:-40}"
MAIN_REPETITION_PENALTY="${MAIN_REPETITION_PENALTY:-1.1}"
MAIN_PRESENCE_PENALTY="${MAIN_PRESENCE_PENALTY:-1.5}"
SYSTEM_PROMPT_ARGS=()
if [[ -n "${SYSTEM_PROMPT+x}" ]]; then
  SYSTEM_PROMPT_ARGS=(--system-prompt "${SYSTEM_PROMPT}")
fi

# ==================== 中期摘要生成参数 ====================
MID_TERM_MAX_TOKENS="${MID_TERM_MAX_TOKENS:-4000}"
MID_TERM_TARGET_TOKEN_COUNT="${MID_TERM_TARGET_TOKEN_COUNT:-3000}"

# ==================== 中期摘要模型采样参数 ====================
MID_TERM_TEMPERATURE="${MID_TERM_TEMPERATURE:-0.8}"
MID_TERM_TOP_P="${MID_TERM_TOP_P:-0.9}"
MID_TERM_TOP_K="${MID_TERM_TOP_K:-40}"
MID_TERM_REPETITION_PENALTY="${MID_TERM_REPETITION_PENALTY:-1.1}"
MID_TERM_PRESENCE_PENALTY="${MID_TERM_PRESENCE_PENALTY:-1.0}"

# ==================== 长期记忆压缩参数 ====================
LONG_TERM_MAX_TOKENS="${LONG_TERM_MAX_TOKENS:-4000}"
LONG_TERM_TARGET_TOKEN_COUNT="${LONG_TERM_TARGET_TOKEN_COUNT:-2000}"
LONG_TERM_MEMORY_WINDOW="${LONG_TERM_MEMORY_WINDOW:-15}"

# ==================== 长期压缩模型采样参数 ====================
LONG_TERM_TEMPERATURE="${LONG_TERM_TEMPERATURE:-0.3}"
LONG_TERM_TOP_P="${LONG_TERM_TOP_P:-0.7}"
LONG_TERM_TOP_K="${LONG_TERM_TOP_K:-30}"
LONG_TERM_REPETITION_PENALTY="${LONG_TERM_REPETITION_PENALTY:-1.1}"
LONG_TERM_PRESENCE_PENALTY="${LONG_TERM_PRESENCE_PENALTY:-0.5}"

echo "============================================================"
echo "Starting StreamingHarness live adapter"
echo "  Adapter:       ${ADAPTER_MODEL}"
echo "  Listen:        http://${ADAPTER_HOST}:${ADAPTER_PORT}/v1"
echo "  Main model:    ${MAIN_MODEL} @ ${MAIN_API_BASE}"
echo "  Backends:      ${MAIN_BACKENDS}"
echo "  Summary model: ${SUMMARIZER_MODEL} @ ${SUMMARIZER_API_BASE}"
echo "  Local images:  ${ALLOWED_LOCAL_IMAGE_ROOTS:-disabled}"
echo "  Frame save:    ${FRAME_SAVE_DIR}"
echo "  Chunk:         ${CHUNK}"
echo "  Compress N:    ${COMPRESS_EVERY_N_CHUNKS}"
echo "  Async lead:    ${ASYNC_SUMMARY_LEAD_FRAMES}"
echo "  Main sampling: max_tokens=${MAIN_MAX_TOKENS}, temp=${MAIN_TEMPERATURE}, top_p=${MAIN_TOP_P}, top_k=${MAIN_TOP_K}, rep_penalty=${MAIN_REPETITION_PENALTY}"
echo "============================================================"

"${PYTHON_BIN}" "${SERVICE_DIR}/live_adapter.py" \
  --host "${ADAPTER_HOST}" \
  --port "${ADAPTER_PORT}" \
  --adapter-model "${ADAPTER_MODEL}" \
  --main-api-base "${MAIN_API_BASE}" \
  --main-model "${MAIN_MODEL}" \
  --main-backends "${MAIN_BACKENDS}" \
  --summarizer-api-base "${SUMMARIZER_API_BASE}" \
  --longterm-api-base "${SUMMARIZER_API_BASE}" \
  --summarizer-model "${SUMMARIZER_MODEL}" \
  --longterm-model "${SUMMARIZER_MODEL}" \
  --allowed-local-image-roots "${ALLOWED_LOCAL_IMAGE_ROOTS}" \
  --summarizer-max-pixels "${SUMMARIZER_MAX_PIXELS}" \
  --summarizer-key-frames "${SUMMARIZER_KEY_FRAMES}" \
  --summarizer-phase-seconds "${SUMMARIZER_PHASE_SECONDS}" \
  --main-max-tokens "${MAIN_MAX_TOKENS}" \
  --main-temperature "${MAIN_TEMPERATURE}" \
  --main-top-p "${MAIN_TOP_P}" \
  --main-top-k "${MAIN_TOP_K}" \
  --main-repetition-penalty "${MAIN_REPETITION_PENALTY}" \
  --main-presence-penalty "${MAIN_PRESENCE_PENALTY}" \
  --mid-term-max-tokens "${MID_TERM_MAX_TOKENS}" \
  --mid-term-target-tokens "${MID_TERM_TARGET_TOKEN_COUNT}" \
  --mid-term-temperature "${MID_TERM_TEMPERATURE}" \
  --mid-term-top-p "${MID_TERM_TOP_P}" \
  --mid-term-top-k "${MID_TERM_TOP_K}" \
  --mid-term-repetition-penalty "${MID_TERM_REPETITION_PENALTY}" \
  --mid-term-presence-penalty "${MID_TERM_PRESENCE_PENALTY}" \
  --long-term-max-tokens "${LONG_TERM_MAX_TOKENS}" \
  --long-term-target-tokens "${LONG_TERM_TARGET_TOKEN_COUNT}" \
  --long-term-temperature "${LONG_TERM_TEMPERATURE}" \
  --long-term-top-p "${LONG_TERM_TOP_P}" \
  --long-term-top-k "${LONG_TERM_TOP_K}" \
  --long-term-repetition-penalty "${LONG_TERM_REPETITION_PENALTY}" \
  --long-term-presence-penalty "${LONG_TERM_PRESENCE_PENALTY}" \
  --long-term-memory-window "${LONG_TERM_MEMORY_WINDOW}" \
  --chunk "${CHUNK}" \
  --compress-every-n-chunks "${COMPRESS_EVERY_N_CHUNKS}" \
  --async-summary-lead-frames "${ASYNC_SUMMARY_LEAD_FRAMES}" \
  --frame-seconds "${FRAME_SECONDS}" \
  --frame-save-dir "${FRAME_SAVE_DIR}" \
  "${SYSTEM_PROMPT_ARGS[@]}" \
  "$@"
