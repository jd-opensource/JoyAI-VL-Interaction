#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$SERVICE_DIR/../.." && pwd)"
INSTALL_DIR="$REPO_ROOT/install"
TTS_VENV_DIR="${TTS_VENV_DIR:-$SERVICE_DIR/.venv}"
MODEL_ROOT="${MODEL_ROOT:-/tmp/models}"
TTS_MODEL_DIR="${TTS_MODEL_DIR:-$MODEL_ROOT/Qwen3-TTS-12Hz-1.7B-CustomVoice}"
TTS_MODEL_NAME="${TTS_MODEL_NAME:-Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice}"
TTS_DEPLOY_CONFIG="${TTS_DEPLOY_CONFIG:-}"
TTS_LOW_MEMORY_CONFIG="${TTS_LOW_MEMORY_CONFIG:-1}"
TTS_PORT="${TTS_PORT:-8991}"
TTS_GPU="${TTS_GPU:-2}"
TTS_TENSOR_PARALLEL_SIZE="${TTS_TENSOR_PARALLEL_SIZE:-1}"
TTS_GPU_MEMORY_UTILIZATION="${TTS_GPU_MEMORY_UTILIZATION:-}"

if [ ! -d "$TTS_VENV_DIR" ]; then
  echo "TTS 虚拟环境不存在: $TTS_VENV_DIR" >&2
  echo "请先运行 $INSTALL_DIR/install-audio-runtime.sh --tts" >&2
  exit 1
fi

if [ ! -d "$TTS_MODEL_DIR" ] || [ -z "$(find "$TTS_MODEL_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  echo "TTS 模型目录不存在或为空: $TTS_MODEL_DIR" >&2
  echo "请先运行 $INSTALL_DIR/download-models.sh --all" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$TTS_VENV_DIR/bin/activate"

if [ -z "$TTS_DEPLOY_CONFIG" ] && [ "$TTS_LOW_MEMORY_CONFIG" != "0" ]; then
  TTS_DEPLOY_CONFIG="$SERVICE_DIR/config/qwen3_tts_lowmem.yaml"
fi

if [ -z "$TTS_DEPLOY_CONFIG" ]; then
  TTS_DEPLOY_CONFIG="$(python - <<'PY'
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec("vllm_omni")
if spec is None or not spec.submodule_search_locations:
    raise SystemExit("missing package: vllm_omni")
path = Path(next(iter(spec.submodule_search_locations))).resolve() / "deploy" / "qwen3_tts.yaml"
if not path.is_file():
    raise SystemExit(f"missing deploy config: {path}")
print(path)
PY
)"
fi

if [ ! -f "$TTS_DEPLOY_CONFIG" ]; then
  echo "TTS deploy config 不存在: $TTS_DEPLOY_CONFIG" >&2
  exit 1
fi

TTS_MEMORY_ARGS=()
if [ -n "$TTS_GPU_MEMORY_UTILIZATION" ]; then
  TTS_MEMORY_ARGS=(--gpu-memory-utilization "$TTS_GPU_MEMORY_UTILIZATION")
fi

exec env CUDA_VISIBLE_DEVICES="$TTS_GPU" vllm-omni serve "$TTS_MODEL_DIR" \
  --served-model-name "$TTS_MODEL_NAME" \
  --deploy-config "$TTS_DEPLOY_CONFIG" \
  --omni \
  --port "$TTS_PORT" \
  --tensor-parallel-size "$TTS_TENSOR_PARALLEL_SIZE" \
  "${TTS_MEMORY_ARGS[@]}" \
  --trust-remote-code \
  --enforce-eager
