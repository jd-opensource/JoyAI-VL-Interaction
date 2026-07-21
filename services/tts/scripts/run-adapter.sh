#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$SERVICE_DIR/../.." && pwd)"
INSTALL_DIR="$REPO_ROOT/install"
TTS_VENV_DIR="${TTS_VENV_DIR:-$SERVICE_DIR/.venv}"
TTS_ADAPTER_HOST="${TTS_ADAPTER_HOST:-0.0.0.0}"
TTS_ADAPTER_PORT="${TTS_ADAPTER_PORT:-8992}"
TTS_UPSTREAM_URL="${TTS_UPSTREAM_URL:-ws://127.0.0.1:8991/v1/audio/speech/stream}"
TTS_UPSTREAM_WAIT_INTERVAL="${TTS_UPSTREAM_WAIT_INTERVAL:-5}"
export TTS_UPSTREAM_URL
TTS_ENABLE_WARMUP="${TTS_ENABLE_WARMUP:-1}"
TTS_WARMUP_HOST="${TTS_WARMUP_HOST:-127.0.0.1}"
TTS_WARMUP_URL="${TTS_WARMUP_URL:-ws://$TTS_WARMUP_HOST:$TTS_ADAPTER_PORT/ws/tts}"
TTS_WARMUP_HEALTH_URL="${TTS_WARMUP_HEALTH_URL:-http://$TTS_WARMUP_HOST:$TTS_ADAPTER_PORT/health}"
TTS_WARMUP_TEXT="${TTS_WARMUP_TEXT:-你好。}"
TTS_WARMUP_OUTPUT="${TTS_WARMUP_OUTPUT:-/tmp/joyvl_tts_warmup.pcm}"
TTS_WARMUP_TIMEOUT="${TTS_WARMUP_TIMEOUT:-180}"
TTS_WARMUP_HEALTH_ATTEMPTS="${TTS_WARMUP_HEALTH_ATTEMPTS:-120}"
TTS_WARMUP_HEALTH_INTERVAL="${TTS_WARMUP_HEALTH_INTERVAL:-1}"

if [ ! -d "$TTS_VENV_DIR" ]; then
  echo "TTS 虚拟环境不存在: $TTS_VENV_DIR" >&2
  echo "请先运行 $INSTALL_DIR/install-audio-runtime.sh --tts" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$TTS_VENV_DIR/bin/activate"

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

read -r TTS_UPSTREAM_HOST TTS_UPSTREAM_PORT < <(parse_host_port "$TTS_UPSTREAM_URL")
wait_for_port "TTS" "$TTS_UPSTREAM_HOST" "$TTS_UPSTREAM_PORT" "$TTS_UPSTREAM_WAIT_INTERVAL"

if [ "$TTS_ENABLE_WARMUP" != "0" ]; then
  (
    for _ in $(seq 1 "$TTS_WARMUP_HEALTH_ATTEMPTS"); do
      if python - "$TTS_WARMUP_HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.0) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
      then
        echo "TTS warmup: adapter is healthy, warming full synthesis path..."
        if joyvl-tts-adapter smoke \
          --url "$TTS_WARMUP_URL" \
          --text "$TTS_WARMUP_TEXT" \
          --output "$TTS_WARMUP_OUTPUT" \
          --timeout "$TTS_WARMUP_TIMEOUT"; then
          echo "TTS warmup: completed, output=$TTS_WARMUP_OUTPUT"
        else
          echo "TTS warmup: failed; adapter will keep running" >&2
        fi
        exit 0
      fi
      sleep "$TTS_WARMUP_HEALTH_INTERVAL"
    done
    echo "TTS warmup: skipped because adapter health did not become ready at $TTS_WARMUP_HEALTH_URL" >&2
  ) &
fi

exec joyvl-tts-adapter --host "$TTS_ADAPTER_HOST" --port "$TTS_ADAPTER_PORT"
