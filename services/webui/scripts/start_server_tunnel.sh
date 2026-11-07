#!/bin/bash
# Start the WebUI for SSH-tunnel access, including a local TURN-over-TCP relay.
#
# This script does two things:
#   1. Launches a local coturn TURN server (background) so WebRTC media can be
#      relayed over TCP through your SSH tunnel.
#   2. Starts the WebUI (foreground) with the matching WEBRTC_* env vars so both
#      the server-side aiortc PeerConnection and the browser use that relay and
#      force iceTransportPolicy=relay.
#
# When this script exits (Ctrl+C), the coturn it started is stopped too.
#
# Usage:
#   ./scripts/start_server_tunnel.sh
#
# Then forward both ports from your Mac and open the WebUI:
#   ssh -L 8099:127.0.0.1:8099 -L 3478:127.0.0.1:3478 user@gpu-server
#   https://127.0.0.1:8099
#
# If coturn (turnserver) is not installed, this script attempts to install it
# automatically (conda / apt-get / yum / dnf), mirroring the README steps.
#
# Options (env vars):
#   SKIP_TURN=1    Do not start coturn here (e.g. it is managed by systemd).
#   AUTO_INSTALL=0 Do not attempt to auto-install coturn; only check & error.
#   TURN_CONF=...  Path to a custom turnserver.conf.
#   WEBRTC_TURN_USERNAME=... WEBRTC_TURN_PASSWORD=...  Override TURN credentials
#                  (must match turnserver.conf).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TURN_CONF="${TURN_CONF:-${SCRIPT_DIR}/turnserver.conf}"
TURN_LOG="${TURN_LOG:-${SCRIPT_DIR}/turnserver.log}"

# Disable public STUN (unreachable on internal networks) and route media
# through the local TURN-over-TCP relay.
export WEBRTC_STUN_URLS="${WEBRTC_STUN_URLS-}"
export WEBRTC_TURN_URLS="${WEBRTC_TURN_URLS:-turn:127.0.0.1:3478?transport=tcp}"
export WEBRTC_TURN_USERNAME="${WEBRTC_TURN_USERNAME:-joyvl}"
export WEBRTC_TURN_PASSWORD="${WEBRTC_TURN_PASSWORD:-joyvl-turn-secret}"
export WEBRTC_FORCE_RELAY="${WEBRTC_FORCE_RELAY:-1}"

TURN_PID=""

cleanup() {
  if [ -n "${TURN_PID}" ] && kill -0 "${TURN_PID}" 2>/dev/null; then
    echo ""
    echo "Stopping coturn (pid ${TURN_PID})..."
    kill "${TURN_PID}" 2>/dev/null || true
    wait "${TURN_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Run a command as root when possible (direct if already root, else via sudo).
run_root() {
  if [ "$(id -u)" = "0" ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "ERROR: need root to run: $*" >&2
    echo "Run this script as root or install sudo." >&2
    return 1
  fi
}

# Ensure coturn (turnserver) is installed, auto-installing if necessary.
ensure_coturn() {
  if command -v turnserver >/dev/null 2>&1; then
    return 0
  fi

  if [ "${AUTO_INSTALL:-1}" != "1" ]; then
    echo "ERROR: 'turnserver' (coturn) not found and AUTO_INSTALL=0." >&2
    echo "Install it manually (e.g. 'sudo apt-get install -y coturn')." >&2
    exit 1
  fi

  echo "coturn (turnserver) not found. Attempting automatic installation..."

  # Prefer conda if we are inside a conda environment.
  if [ -n "${CONDA_PREFIX:-}" ] && command -v conda >/dev/null 2>&1; then
    echo "  -> installing via conda (conda-forge)..."
    if conda install -y -c conda-forge coturn; then
      :
    else
      echo "  conda install failed, trying system package manager..." >&2
    fi
  fi

  if ! command -v turnserver >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      echo "  -> installing via apt-get..."
      run_root apt-get update -y
      run_root apt-get install -y coturn
    elif command -v dnf >/dev/null 2>&1; then
      echo "  -> installing via dnf..."
      run_root dnf install -y coturn
    elif command -v yum >/dev/null 2>&1; then
      echo "  -> installing via yum..."
      run_root yum install -y coturn
    fi
  fi

  if ! command -v turnserver >/dev/null 2>&1; then
    echo "ERROR: failed to install coturn automatically." >&2
    echo "Please install it manually:" >&2
    echo "  Debian/Ubuntu : sudo apt-get install -y coturn" >&2
    echo "  CentOS/RHEL   : sudo yum install -y coturn" >&2
    echo "  conda         : conda install -c conda-forge coturn" >&2
    echo "Or set SKIP_TURN=1 if you manage TURN separately." >&2
    exit 1
  fi
  echo "coturn installed successfully."
}

start_turn() {
  if [ "${SKIP_TURN:-0}" = "1" ]; then
    echo "SKIP_TURN=1 set, not starting coturn (assuming it runs externally)."
    return
  fi

  # If something already listens on 3478, assume coturn is already running.
  if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -q ':3478 '; then
    echo "Port 3478 already in use, assuming coturn is already running."
    return
  fi

  ensure_coturn

  if [ ! -f "${TURN_CONF}" ]; then
    echo "ERROR: TURN config not found: ${TURN_CONF}" >&2
    exit 1
  fi

  echo "Starting coturn (config: ${TURN_CONF}, log: ${TURN_LOG})..."
  turnserver -c "${TURN_CONF}" -v >"${TURN_LOG}" 2>&1 &
  TURN_PID=$!

  # Give it a moment and verify it stayed up.
  sleep 1
  if ! kill -0 "${TURN_PID}" 2>/dev/null; then
    echo "ERROR: coturn failed to start. Last log lines:" >&2
    tail -n 20 "${TURN_LOG}" >&2 || true
    exit 1
  fi
  echo "coturn started (pid ${TURN_PID})."
}

start_turn

echo ""
echo "WebRTC tunnel mode enabled:"
echo "  WEBRTC_TURN_URLS    = ${WEBRTC_TURN_URLS}"
echo "  WEBRTC_FORCE_RELAY  = ${WEBRTC_FORCE_RELAY}"
echo ""

# Run the WebUI in the foreground (not exec'd, so the EXIT trap can clean up
# coturn when the WebUI stops).
bash "${SCRIPT_DIR}/start_server.sh" "$@"