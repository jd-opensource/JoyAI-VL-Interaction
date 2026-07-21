#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$SERVICE_DIR/../.." && pwd)"
INSTALL_DIR="$REPO_ROOT/install"
ASR_VENV_DIR="${ASR_VENV_DIR:-$SERVICE_DIR/.venv}"
ASR_ADAPTER_HOST="${ASR_ADAPTER_HOST:-0.0.0.0}"
ASR_ADAPTER_PORT="${ASR_ADAPTER_PORT:-8994}"
ASR_UPSTREAM_URL="${ASR_UPSTREAM_URL:-http://127.0.0.1:8993/v1/audio/transcriptions}"
ASR_UPSTREAM_WAIT_INTERVAL="${ASR_UPSTREAM_WAIT_INTERVAL:-5}"
export ASR_UPSTREAM_URL

if [ ! -d "$ASR_VENV_DIR" ]; then
  echo "ASR 虚拟环境不存在: $ASR_VENV_DIR" >&2
  echo "请先运行 $INSTALL_DIR/install-audio-runtime.sh --asr" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ASR_VENV_DIR/bin/activate"

parse_host_port() {
  python - "$1" <<'PY'
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
host = url.hostname or "127.0.0.1"
if url.port is not None:
    port = url.port
elif url.scheme in {"https", "wss"}:
    port = 443
else:
    port = 80
print(f"{host} {port}")
PY
}

wait_for_port() {
  local name="$1"
  local host="$2"
  local port="$3"
  local interval="$4"

  echo "Waiting for ${name} upstream at ${host}:${port} before starting adapter..."
  while ! (: >"/dev/tcp/${host}/${port}") >/dev/null 2>&1; do
    echo "${name} upstream ${host}:${port} is not ready; retrying in ${interval}s..."
    sleep "$interval"
  done
  echo "${name} upstream ${host}:${port} is ready."
}

read -r ASR_UPSTREAM_HOST ASR_UPSTREAM_PORT < <(parse_host_port "$ASR_UPSTREAM_URL")
wait_for_port "ASR" "$ASR_UPSTREAM_HOST" "$ASR_UPSTREAM_PORT" "$ASR_UPSTREAM_WAIT_INTERVAL"

exec joyvl-asr-adapter --host "$ASR_ADAPTER_HOST" --port "$ASR_ADAPTER_PORT"
