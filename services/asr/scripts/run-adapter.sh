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
ASR_ENABLE_WARMUP="${ASR_ENABLE_WARMUP:-1}"
ASR_WARMUP_HOST="${ASR_WARMUP_HOST:-127.0.0.1}"
ASR_WARMUP_URL="${ASR_WARMUP_URL:-ws://$ASR_WARMUP_HOST:$ASR_ADAPTER_PORT/ws/asr}"
ASR_WARMUP_HEALTH_URL="${ASR_WARMUP_HEALTH_URL:-http://$ASR_WARMUP_HOST:$ASR_ADAPTER_PORT/health}"
ASR_WARMUP_WAV="${ASR_WARMUP_WAV:-/tmp/joyvl_asr_warmup.wav}"
ASR_WARMUP_TIMEOUT="${ASR_WARMUP_TIMEOUT:-120}"
ASR_WARMUP_HEALTH_ATTEMPTS="${ASR_WARMUP_HEALTH_ATTEMPTS:-120}"
ASR_WARMUP_HEALTH_INTERVAL="${ASR_WARMUP_HEALTH_INTERVAL:-1}"
ASR_ADAPTER_LOG_FILE=""
if [[ "${SAVE_SERVICE_LOGS:-0}" == "1" ]]; then
  ASR_LOG_DIR="${ASR_LOG_DIR:-${SERVICE_DIR}/logs}"
  mkdir -p "$ASR_LOG_DIR"
  ASR_ADAPTER_LOG_FILE="${ASR_ADAPTER_LOG_FILE:-${ASR_LOG_DIR}/adapter_${ASR_ADAPTER_PORT}.log}"
  exec > >(tee -a "$ASR_ADAPTER_LOG_FILE") 2>&1
fi

if [[ -n "${ASR_ADAPTER_LOG_FILE}" ]]; then
  echo "ASR adapter log: $ASR_ADAPTER_LOG_FILE"
fi

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

if [ "$ASR_ENABLE_WARMUP" != "0" ]; then
  (
    for _ in $(seq 1 "$ASR_WARMUP_HEALTH_ATTEMPTS"); do
      if python - "$ASR_WARMUP_HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.0) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
      then
        if [ ! -f "$ASR_WARMUP_WAV" ]; then
          python - "$ASR_WARMUP_WAV" <<'PY'
import struct
import sys
import wave

path = sys.argv[1]
sample_rate = 16000
duration_seconds = 1
frames = b"".join(struct.pack("<h", 0) for _ in range(sample_rate * duration_seconds))
with wave.open(path, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(sample_rate)
    wav.writeframes(frames)
PY
        fi
        echo "ASR smoke: adapter is healthy, warming full transcription path..."
        if joyvl-asr-adapter smoke \
          --url "$ASR_WARMUP_URL" \
          --wav "$ASR_WARMUP_WAV" \
          --timeout "$ASR_WARMUP_TIMEOUT"; then
          echo "ASR smoke: completed, wav=$ASR_WARMUP_WAV"
        else
          echo "ASR smoke: failed; adapter will keep running" >&2
        fi
        exit 0
      fi
      sleep "$ASR_WARMUP_HEALTH_INTERVAL"
    done
    echo "ASR smoke: skipped because adapter health did not become ready at $ASR_WARMUP_HEALTH_URL" >&2
  ) &
fi

exec joyvl-asr-adapter --host "$ASR_ADAPTER_HOST" --port "$ASR_ADAPTER_PORT"
