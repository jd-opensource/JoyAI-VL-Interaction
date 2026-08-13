#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$INSTALL_DIR/.." && pwd)"
SERVICES_DIR="$REPO_ROOT/services"
WEBUI_DIR="$SERVICES_DIR/webui"
ASR_DIR="$SERVICES_DIR/asr"
TTS_DIR="$SERVICES_DIR/tts"
BACKGROUND_AGENT_DIR="$SERVICES_DIR/background-agent"
VENV_DIR="${VENV_DIR:-$SERVICES_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
UV_BIN="${UV_BIN:-uv}"
CONSTRAINTS_FILE="${CONSTRAINTS_FILE:-$INSTALL_DIR/constraints.txt}"
VLLM_VERSION="0.22.0"
MAX_SUBAGENTS="6"
INSTALL_ASR=0
INSTALL_TTS=0
INSTALL_BACKGROUND_AGENT=0
INSTALL_VLLM=0
INSTALL_DEV=0
DRY_RUN=0
INSTALL_RETRIES="${INSTALL_RETRIES:-3}"

usage() {
  cat <<'USAGE'
Usage: install.sh [options]

安装 JoyVL WebUI。默认只安装 WebUI/LiveKit 依赖。

Options:
  --with-asr                 安装轻量 ASR 适配服务包。
  --with-tts                 安装轻量 TTS 适配服务包。
  --with-background-agent    安装后台 agent API 服务包。
  --with-vllm                额外安装 vLLM 0.22.0。
  --with-all                 启用 ASR/TTS/background-agent 适配包。
  --dev                      为可选包安装 dev extras。
  --max-subagents N          配置后台 agent 最大子代理数，默认 6。
  --dry-run                  只打印将要执行的命令，不真正安装。
  -h, --help                 显示帮助。

Environment overrides:
  VENV_DIR=/path/to/venv
  PYTHON_BIN=python3.12
  UV_BIN=/path/to/uv
  CONSTRAINTS_FILE=/path/to/constraints.txt

兼容性说明:
  - 默认环境只用于 WebUI；模型、ASR、TTS、background-agent 可继续使用已有服务端口。
  - 本脚本只为 ASR、TTS、background-agent 安装轻量 adapter/API 包。
  - ASR 文档里的 nightly vLLM/CUDA 环境建议单独建环境，不要混进主环境。
  - TTS 推理服务需要 vllm-omni==0.22.0 搭配 vllm==0.22.0；本安装目录统一使用
    Python 3.12。
USAGE
}

die() {
  echo "错误: $*" >&2
  exit 1
}

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

require_dir() {
  [ -d "$1" ] || die "目录不存在: $1"
}

require_file() {
  [ -f "$1" ] || die "文件不存在: $1"
}

copy_template() {
  local source="$1"
  local target="$2"
  require_file "$source"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '+ cp %q %q\n' "$source" "$target"
  else
    cp "$source" "$target"
  fi
}

pip_install_editable() {
  local package_dir="$1"
  local extra="${2:-}"
  if [ -n "$extra" ]; then
    uv_pip_install -e "$package_dir[$extra]"
  else
    uv_pip_install -e "$package_dir"
  fi
}

uv_pip_install() {
  local install_args=("$UV_BIN" pip install --python "$VENV_DIR/bin/python")

  if [ -n "$CONSTRAINTS_FILE" ]; then
    require_file "$CONSTRAINTS_FILE"
    install_args+=(-c "$CONSTRAINTS_FILE")
  fi

  run "${install_args[@]}" "$@"
}

write_background_agent_runtime() {
  local env_file="$BACKGROUND_AGENT_DIR/background-agent.env"

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ write $env_file"
    return
  fi

  cat >"$env_file" <<EOF
CODEX_API_MAX_SUBAGENTS=$MAX_SUBAGENTS
BACKGROUND_MAX_SUBAGENTS=$MAX_SUBAGENTS
BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079
EOF
}

verify_install() {
  run "$UV_BIN" pip check --python "$VENV_DIR/bin/python"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ verify imports and console scripts"
    return
  fi
  PATH="$VENV_DIR/bin:$PATH" "$VENV_DIR/bin/python" - <<'PY'
import importlib
import shutil

for module in [
    "joy_interaction_webui.server",
    "livekit.rtc",
    "livekit.api",
]:
    importlib.import_module(module)
    print(f"import ok: {module}")

for command in ["joy-interaction-webui", "joy-interaction-webui-stop"]:
    resolved = shutil.which(command)
    print(f"command {command}: {resolved}")
    if not resolved:
        raise RuntimeError(f"missing command: {command}")
PY
}

install_packages() {
  run "$UV_BIN" venv --python "$PYTHON_BIN" --seed "$VENV_DIR"
  pip_install_editable "$WEBUI_DIR"

  if [ "$INSTALL_VLLM" -eq 1 ]; then
    uv_pip_install "vllm==$VLLM_VERSION"
  fi

  if [ "$INSTALL_ASR" -eq 1 ]; then
    if [ "$INSTALL_DEV" -eq 1 ]; then
      pip_install_editable "$ASR_DIR" dev
    else
      pip_install_editable "$ASR_DIR"
    fi
  fi

  if [ "$INSTALL_TTS" -eq 1 ]; then
    if [ "$INSTALL_DEV" -eq 1 ]; then
      pip_install_editable "$TTS_DIR" dev
    else
      pip_install_editable "$TTS_DIR"
    fi
  fi

  if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
    pip_install_editable "$BACKGROUND_AGENT_DIR"
    write_background_agent_runtime
  fi

  verify_install
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-asr)
      INSTALL_ASR=1
      ;;
    --with-tts)
      INSTALL_TTS=1
      ;;
    --with-background-agent)
      INSTALL_BACKGROUND_AGENT=1
      ;;
    --with-vllm)
      INSTALL_VLLM=1
      ;;
    --with-all)
      INSTALL_ASR=1
      INSTALL_TTS=1
      INSTALL_BACKGROUND_AGENT=1
      ;;
    --dev)
      INSTALL_DEV=1
      ;;
    --max-subagents)
      shift
      [ "${1:-}" ] || die "--max-subagents requires a value"
      [[ "$1" =~ ^[0-9]+$ ]] || die "--max-subagents 必须是正整数"
      [ "$1" -ge 1 ] || die "--max-subagents 必须 >= 1"
      MAX_SUBAGENTS="$1"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知选项: $1"
      ;;
  esac
  shift
done

if [ "$DRY_RUN" -eq 0 ] && ! command -v "$UV_BIN" >/dev/null 2>&1; then
  die "找不到 uv: $UV_BIN。请先安装 uv，或通过 UV_BIN=/path/to/uv 指定路径"
fi

require_dir "$WEBUI_DIR"
copy_template "$INSTALL_DIR/pyproject.toml" "$WEBUI_DIR/pyproject.toml"

if [ "$INSTALL_ASR" -eq 1 ]; then
  require_dir "$ASR_DIR"
  copy_template "$INSTALL_DIR/pyproject.asr.toml" "$ASR_DIR/pyproject.toml"
fi

if [ "$INSTALL_TTS" -eq 1 ]; then
  require_dir "$TTS_DIR"
  copy_template "$INSTALL_DIR/pyproject.tts.toml" "$TTS_DIR/pyproject.toml"
fi

if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
  require_dir "$BACKGROUND_AGENT_DIR"
  copy_template "$INSTALL_DIR/pyproject.background-agent.toml" "$BACKGROUND_AGENT_DIR/pyproject.toml"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  printf '+ rm -rf %q\n' "$VENV_DIR"
  install_packages
else
  attempt=1
  while :; do
    rm -rf "$VENV_DIR"
    if install_packages; then
      break
    fi
    if [ "$attempt" -ge "$INSTALL_RETRIES" ]; then
      die "安装或校验失败，已重试 $attempt 次"
    fi
    echo "安装或校验失败，删除 $VENV_DIR 后重试 ($attempt/$INSTALL_RETRIES)..." >&2
    attempt=$((attempt + 1))
  done
fi

echo "安装完成。"
echo "激活环境: source $VENV_DIR/bin/activate"
if [ "$INSTALL_BACKGROUND_AGENT" -eq 1 ]; then
  echo "后台 agent 环境配置: $BACKGROUND_AGENT_DIR/background-agent.env"
  echo "后台 agent 启动脚本: $BACKGROUND_AGENT_DIR/scripts/run.sh"
fi
