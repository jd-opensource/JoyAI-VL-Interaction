#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SERVICES_DIR="$(cd -- "$SERVICE_DIR/.." && pwd)"
VENV_DIR="${VENV_DIR:-$SERVICES_DIR/.venv}"
REPO_ROOT="$(cd -- "$SERVICES_DIR/.." && pwd)"
DEFAULT_WORKSPACE="$REPO_ROOT/agent-workspace"

if [ -f "$SERVICE_DIR/background-agent.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$SERVICE_DIR/background-agent.env"
  set +a
fi

export CODEX_HOME="${CODEX_HOME:-$SERVICE_DIR/codex-home}"
export CODEX_API_WORKSPACE="${CODEX_API_WORKSPACE:-$DEFAULT_WORKSPACE}"
export CODEX_API_HOST="${CODEX_API_HOST:-127.0.0.1}"
export CODEX_API_PORT="${CODEX_API_PORT:-8079}"
mkdir -p "$CODEX_API_WORKSPACE"

if [ ! -f "$CODEX_HOME/config.toml" ]; then
  echo "Missing Codex config: $CODEX_HOME/config.toml" >&2
  echo "Set CODEX_HOME or add codex-home/config.toml next to this script." >&2
  exit 1
fi

echo "Starting StreamingHarness Codex API"
echo "  host: $CODEX_API_HOST"
echo "  port: $CODEX_API_PORT"
echo "  workspace: $CODEX_API_WORKSPACE"
echo "  CODEX_HOME: $CODEX_HOME"

cd "$SERVICE_DIR"
if [ -d "$VENV_DIR" ]; then
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  exec python -m uvicorn codex_api.main:app --host "$CODEX_API_HOST" --port "$CODEX_API_PORT"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "虚拟环境不存在: $VENV_DIR" >&2
  echo "请先运行安装脚本安装共享虚拟环境，或安装 uv 后用于开发模式。" >&2
  exit 1
fi

exec uv run uvicorn codex_api.main:app --host "$CODEX_API_HOST" --port "$CODEX_API_PORT"
