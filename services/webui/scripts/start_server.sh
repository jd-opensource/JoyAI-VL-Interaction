#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICES_DIR="$(cd "${PROJECT_ROOT}/.." && pwd)"
WEBUI_HOST="${WEBUI_HOST:-0.0.0.0}"
WEBUI_PORT="${WEBUI_PORT:-7099}"
cd "${PROJECT_ROOT}"

LIVEKIT_VERSION="${LIVEKIT_VERSION:-1.13.2}"
LIVEKIT_SIGNAL_PORT="${LIVEKIT_SIGNAL_PORT:-8298}"
LIVEKIT_UDP_PORT="${LIVEKIT_UDP_PORT:-8299}"
LIVEKIT_TCP_PORT="${LIVEKIT_TCP_PORT:-${LIVEKIT_UDP_PORT}}"
LIVEKIT_USE_EXTERNAL_IP="${LIVEKIT_USE_EXTERNAL_IP:-false}"
LIVEKIT_RTC_IP="${LIVEKIT_RTC_IP:-}"
LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-joyvl}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-joyvl-secret-123456789012345678901234}"
LIVEKIT_DIR="${LIVEKIT_DIR:-${PROJECT_ROOT}/.livekit}"
LIVEKIT_BIN="${LIVEKIT_DIR}/livekit-server"
LIVEKIT_CONFIG="${LIVEKIT_DIR}/livekit.yaml"
LIVEKIT_LOG="${LIVEKIT_DIR}/livekit.log"

case "${LIVEKIT_USE_EXTERNAL_IP}" in
  true|false) ;;
  *)
    echo "LIVEKIT_USE_EXTERNAL_IP must be true or false." >&2
    exit 1
    ;;
esac

detect_node_ip() {
  local node_ip probe_ip

  if [ -n "${LIVEKIT_NODE_IP:-}" ]; then
    echo "${LIVEKIT_NODE_IP}"
    return
  fi

  probe_ip="${LIVEKIT_NODE_IP_PROBE:-1.1.1.1}"
  if command -v ip >/dev/null 2>&1; then
    node_ip="$(
      ip -o -4 route get "${probe_ip}" 2>/dev/null \
        | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}'
    )"
    if [ -n "${node_ip}" ]; then
      echo "${node_ip}"
      return
    fi
  fi

  hostname -I 2>/dev/null \
    | tr ' ' '\n' \
    | awk '$1 !~ /^127\./ && $1 !~ /^169\.254\./ {print; exit}'
}

download_livekit() {
  local os arch asset archive checksum_url version_no_v url

  mkdir -p "${LIVEKIT_DIR}"
  if [ -x "${LIVEKIT_BIN}" ]; then
    return
  fi

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  version_no_v="${LIVEKIT_VERSION#v}"

  if [ "${os}" != "linux" ]; then
    echo "Unsupported LiveKit download OS: ${os}" >&2
    exit 1
  fi

  case "${arch}" in
    x86_64|amd64)
      arch="amd64"
      ;;
    aarch64|arm64)
      arch="arm64"
      ;;
    *)
      echo "Unsupported LiveKit download architecture: ${arch}" >&2
      exit 1
      ;;
  esac

  asset="livekit_${version_no_v}_linux_${arch}.tar.gz"
  archive="${LIVEKIT_DIR}/${asset}"
  url="https://github.com/livekit/livekit/releases/download/v${version_no_v}/${asset}"
  checksum_url="https://github.com/livekit/livekit/releases/download/v${version_no_v}/checksums.txt"

  echo "Downloading LiveKit server ${LIVEKIT_VERSION} (${arch})..."
  curl -fL --retry 3 --retry-delay 2 --retry-all-errors "${url}" -o "${archive}"
  curl -fL --retry 3 --retry-delay 2 --retry-all-errors "${checksum_url}" -o "${LIVEKIT_DIR}/checksums.txt"

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "${LIVEKIT_DIR}" && grep " ${asset}$" checksums.txt | sha256sum -c -)
  else
    echo "sha256sum not found; cannot verify LiveKit download" >&2
    exit 1
  fi

  tar -xzf "${archive}" -C "${LIVEKIT_DIR}" livekit-server
  chmod +x "${LIVEKIT_BIN}"
}

write_livekit_config() {
  local node_ip

  node_ip="$(detect_node_ip)"
  if [ -z "${node_ip}" ]; then
    echo "Could not detect LiveKit node IP; set LIVEKIT_NODE_IP." >&2
    exit 1
  fi

  if [ -n "${LIVEKIT_RTC_IP}" ] && command -v ip >/dev/null 2>&1; then
    if ! ip -o -4 addr show | awk -v target="${LIVEKIT_RTC_IP}" '
      {
        split($4, address, "/")
        if (address[1] == target) found = 1
      }
      END { exit !found }
    '; then
      echo "LIVEKIT_RTC_IP is not assigned to a local interface: ${LIVEKIT_RTC_IP}" >&2
      exit 1
    fi
  fi

  mkdir -p "${LIVEKIT_DIR}"
  cat >"${LIVEKIT_CONFIG}" <<EOF
port: ${LIVEKIT_SIGNAL_PORT}
bind_addresses:
  - 127.0.0.1
rtc:
  udp_port: ${LIVEKIT_UDP_PORT}
  tcp_port: ${LIVEKIT_TCP_PORT}
  use_external_ip: ${LIVEKIT_USE_EXTERNAL_IP}
  node_ip: ${node_ip}
EOF
  if [ -n "${LIVEKIT_RTC_IP}" ]; then
    cat >>"${LIVEKIT_CONFIG}" <<EOF
  ips:
    includes:
      - ${LIVEKIT_RTC_IP}/32
EOF
  fi
  cat >>"${LIVEKIT_CONFIG}" <<EOF
  allow_tcp_fallback: true
keys:
  ${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}
logging:
  level: info
EOF
}

start_livekit() {
  local node_ip

  download_livekit
  write_livekit_config
  node_ip="$(detect_node_ip)"

  if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"${LIVEKIT_SIGNAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "LiveKit signaling already listening on 127.0.0.1:${LIVEKIT_SIGNAL_PORT}"
    return
  fi

  echo "Starting LiveKit server on 127.0.0.1:${LIVEKIT_SIGNAL_PORT}, UDP ${LIVEKIT_UDP_PORT}, TCP ${LIVEKIT_TCP_PORT}, node IP ${node_ip}, RTC IP ${LIVEKIT_RTC_IP:-all local interfaces}..."
  LIVEKIT_API_KEY="${LIVEKIT_API_KEY}" LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET}" \
    "${LIVEKIT_BIN}" --config "${LIVEKIT_CONFIG}" --node-ip "${node_ip}" >"${LIVEKIT_LOG}" 2>&1 &
  LIVEKIT_PID="$!"

  for _ in $(seq 1 30); do
    if ! kill -0 "${LIVEKIT_PID}" 2>/dev/null; then
      echo "LiveKit server exited early. Log: ${LIVEKIT_LOG}" >&2
      tail -50 "${LIVEKIT_LOG}" >&2 || true
      exit 1
    fi
    if command -v lsof >/dev/null 2>&1; then
      if lsof -tiTCP:"${LIVEKIT_SIGNAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        return
      fi
    else
      if (: >"/dev/tcp/127.0.0.1/${LIVEKIT_SIGNAL_PORT}") >/dev/null 2>&1; then
        return
      fi
    fi
    sleep 1
  done

  echo "Timed out waiting for LiveKit server. Log: ${LIVEKIT_LOG}" >&2
  tail -50 "${LIVEKIT_LOG}" >&2 || true
  exit 1
}

if [ -z "${VENV_ACTIVATE+x}" ]; then
  if [ -f "${SERVICES_DIR}/.venv/bin/activate" ]; then
    VENV_ACTIVATE="${SERVICES_DIR}/.venv/bin/activate"
  else
    VENV_ACTIVATE=".venv/bin/activate"
  fi
else
  VENV_ACTIVATE="${VENV_ACTIVATE:-}"
fi

if [ -f "${VENV_ACTIVATE}" ]; then
  source "${VENV_ACTIVATE}"
elif [ -z "${VIRTUAL_ENV:-}" ] && [ -z "${CONDA_DEFAULT_ENV:-}" ]; then
  echo "Virtual environment not found: ${VENV_ACTIVATE}" >&2
  echo "Set VENV_ACTIVATE or activate an environment before running this script." >&2
  exit 1
fi

if [ ! -f cert.pem ] || [ ! -f key.pem ]; then
  echo "SSL certificate not found, generating cert.pem/key.pem..."
  bash "${SCRIPT_DIR}/generate_cert.sh"
fi

start_livekit

export LIVEKIT_INTERNAL_URL="${LIVEKIT_INTERNAL_URL:-ws://127.0.0.1:${LIVEKIT_SIGNAL_PORT}}"
export LIVEKIT_API_KEY LIVEKIT_API_SECRET
NO_PROXY="${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}"
for host in localhost 127.0.0.1 ::1; do
  case ",${NO_PROXY}," in
    *",${host},"*) ;;
    *) NO_PROXY="${NO_PROXY},${host}" ;;
  esac
done
export NO_PROXY
export no_proxy="${NO_PROXY}"

PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" python -m joy_interaction_webui.server \
  --ssl-cert cert.pem \
  --ssl-key key.pem \
  --host "${WEBUI_HOST}" \
  --port "${WEBUI_PORT}" \
  --model streaming-infer-adapter \
  --api-base http://127.0.0.1:8070/v1 \
  "$@"
