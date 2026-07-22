#!/usr/bin/env bash
set -euo pipefail

MODEL_ROOT="${MODEL_ROOT:-/tmp/models}"
DOWNLOAD_CORE=0
DOWNLOAD_ASR=0
DOWNLOAD_TTS=0
HF_BIN="${HF_BIN:-}"
STREAMING_MODEL_REPO="${STREAMING_MODEL_REPO:-jdopensource/JoyAI-VL-Interaction}"
STREAMING_MODEL_DIR="${STREAMING_MODEL_DIR:-${MODEL_PATH:-$MODEL_ROOT/jdopensource/JoyAI-VL-Interaction}}"
SUMMARY_MODEL_REPO="${SUMMARY_MODEL_REPO:-Qwen/Qwen3-VL-4B-Instruct}"
SUMMARY_MODEL_DIR="${SUMMARY_MODEL_DIR:-${SUMMARY_MODEL_PATH:-$MODEL_ROOT/Qwen3-VL-4B-Instruct}}"
ASR_MODEL_REPO="${ASR_MODEL_REPO:-Qwen/Qwen3-ASR-1.7B}"
ASR_MODEL_DIR="${ASR_MODEL_DIR:-$MODEL_ROOT/Qwen3-ASR-1.7B}"
TTS_MODEL_REPO="${TTS_MODEL_REPO:-Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice}"
TTS_MODEL_DIR="${TTS_MODEL_DIR:-$MODEL_ROOT/Qwen3-TTS-12Hz-1.7B-CustomVoice}"
FORCE_DOWNLOAD="${FORCE_DOWNLOAD:-0}"

usage() {
  cat <<'USAGE'
Usage: download-models.sh [options]

统一下载 JoyVL 运行所需模型到 /tmp/models 下。

Options:
  --core      下载主交互模型和 summary 模型。
  --asr       下载 Qwen/Qwen3-ASR-1.7B。
  --tts       下载 Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice。
  --all       下载主交互、summary、ASR 和 TTS 模型。
  -h, --help  显示帮助。

Environment overrides:
  HF_BIN=hf
  FORCE_DOWNLOAD=0
  MODEL_ROOT=/tmp/models
  STREAMING_MODEL_REPO=jdopensource/JoyAI-VL-Interaction
  STREAMING_MODEL_DIR=/tmp/models/jdopensource/JoyAI-VL-Interaction
  MODEL_PATH=/tmp/models/jdopensource/JoyAI-VL-Interaction
  SUMMARY_MODEL_REPO=Qwen/Qwen3-VL-4B-Instruct
  SUMMARY_MODEL_DIR=/tmp/models/Qwen3-VL-4B-Instruct
  SUMMARY_MODEL_PATH=/tmp/models/Qwen3-VL-4B-Instruct
  ASR_MODEL_REPO=Qwen/Qwen3-ASR-1.7B
  ASR_MODEL_DIR=/tmp/models/Qwen3-ASR-1.7B
  TTS_MODEL_REPO=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
  TTS_MODEL_DIR=/tmp/models/Qwen3-TTS-12Hz-1.7B-CustomVoice
USAGE
}

load_bashrc() {
  if [ -f "$HOME/.bashrc" ]; then
    set +u
    export PS1="${PS1:-codex$ }"
    # shellcheck source=/dev/null
    source "$HOME/.bashrc"
    set -u
  fi
}

download_model() {
  local repo="$1"
  local target="$2"

  mkdir -p "$target"
  echo "下载 $repo -> $target"
  if [ "$FORCE_DOWNLOAD" = "1" ]; then
    "$HF_BIN" download "$repo" --local-dir "$target" --force-download
  else
    "$HF_BIN" download "$repo" --local-dir "$target"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --core)
      DOWNLOAD_CORE=1
      ;;
    --asr)
      DOWNLOAD_ASR=1
      ;;
    --tts)
      DOWNLOAD_TTS=1
      ;;
    --all)
      DOWNLOAD_CORE=1
      DOWNLOAD_ASR=1
      DOWNLOAD_TTS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知选项: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$DOWNLOAD_CORE" -eq 0 ] && [ "$DOWNLOAD_ASR" -eq 0 ] && [ "$DOWNLOAD_TTS" -eq 0 ]; then
  usage >&2
  exit 2
fi

load_bashrc

if [ -z "$HF_BIN" ]; then
  if command -v hf >/dev/null 2>&1; then
    HF_BIN=hf
  elif command -v huggingface-cli >/dev/null 2>&1; then
    HF_BIN=huggingface-cli
  else
    echo "找不到 hf 或 huggingface-cli，请先安装 huggingface_hub。" >&2
    exit 1
  fi
elif ! command -v "$HF_BIN" >/dev/null 2>&1; then
  echo "找不到 Hugging Face 下载命令: $HF_BIN" >&2
  exit 1
fi

if [ "$DOWNLOAD_CORE" -eq 1 ]; then
  download_model "$STREAMING_MODEL_REPO" "$STREAMING_MODEL_DIR"
  download_model "$SUMMARY_MODEL_REPO" "$SUMMARY_MODEL_DIR"
fi

if [ "$DOWNLOAD_ASR" -eq 1 ]; then
  download_model "$ASR_MODEL_REPO" "$ASR_MODEL_DIR"
fi

if [ "$DOWNLOAD_TTS" -eq 1 ]; then
  download_model "$TTS_MODEL_REPO" "$TTS_MODEL_DIR"
fi
