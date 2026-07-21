#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICES_DIR="$(cd "${PROJECT_ROOT}/.." && pwd)"
WEBUI_HOST="${WEBUI_HOST:-0.0.0.0}"
WEBUI_PORT="${WEBUI_PORT:-8099}"
cd "${PROJECT_ROOT}"
if [[ "${SAVE_SERVICE_LOGS:-0}" == "1" ]]; then
  WEBUI_LOG_DIR="${WEBUI_LOG_DIR:-${PROJECT_ROOT}/logs}"
  mkdir -p "${WEBUI_LOG_DIR}"
  WEBUI_LOG_FILE="${WEBUI_LOG_FILE:-${WEBUI_LOG_DIR}/webui_${WEBUI_PORT}.log}"
  exec > >(tee -a "${WEBUI_LOG_FILE}") 2>&1
fi

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

if [[ "${SAVE_SERVICE_LOGS:-0}" == "1" ]]; then
  echo "WebUI log: ${WEBUI_LOG_FILE}"
fi

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m joy_interaction_webui.server \
  --ssl-cert cert.pem \
  --ssl-key key.pem \
  --host "${WEBUI_HOST}" \
  --port "${WEBUI_PORT}" \
  --model streaming-infer-adapter \
  --api-base http://127.0.0.1:8070/v1 \
  "$@"
