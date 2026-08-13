#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_NAME="${1:-}"
CERTIFICATE="${2:-}"
PRIVATE_KEY="${3:-}"
NGINX_RUNTIME_DIR="/etc/nginx/joyvl"
SITE_AVAILABLE="/etc/nginx/sites-available/joyvl"
SITE_ENABLED="/etc/nginx/sites-enabled/joyvl"

usage() {
  echo "Usage: sudo bash nginx/install.sh <domain-or-public-ip> <fullchain.pem> <privkey.pem>" >&2
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

if [[ -z "${SERVER_NAME}" || -z "${CERTIFICATE}" || -z "${PRIVATE_KEY}" ]]; then
  usage
  exit 1
fi

if [[ ! "${SERVER_NAME}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid domain or public IP: ${SERVER_NAME}" >&2
  exit 1
fi

if [[ "${CERTIFICATE}" != /* || "${PRIVATE_KEY}" != /* ]]; then
  echo "Certificate and private key paths must be absolute." >&2
  exit 1
fi

if [[ ! -r "${CERTIFICATE}" || ! -r "${PRIVATE_KEY}" ]]; then
  echo "Certificate or private key is not readable." >&2
  exit 1
fi

if [[ ! -s "${SCRIPT_DIR}/.htpasswd" ]]; then
  echo "Missing ${SCRIPT_DIR}/.htpasswd; run nginx/set_password.sh first." >&2
  exit 1
fi

install -d -m 0750 -o root -g www-data "${NGINX_RUNTIME_DIR}"
install -m 0640 -o root -g www-data "${SCRIPT_DIR}/.htpasswd" "${NGINX_RUNTIME_DIR}/.htpasswd"
ln -sfn "${CERTIFICATE}" "${NGINX_RUNTIME_DIR}/fullchain.pem"
ln -sfn "${PRIVATE_KEY}" "${NGINX_RUNTIME_DIR}/privkey.pem"

temp_config="$(mktemp)"
trap 'rm -f "${temp_config}"' EXIT
sed "s/__SERVER_NAME__/${SERVER_NAME}/g" "${SCRIPT_DIR}/joyvl.conf" >"${temp_config}"
install -m 0644 "${temp_config}" "${SITE_AVAILABLE}"
ln -sfn "${SITE_AVAILABLE}" "${SITE_ENABLED}"

nginx -t
if pgrep -x nginx >/dev/null 2>&1; then
  nginx -s reload
else
  nginx
fi

echo "JoyVL Nginx is listening on internal port 7100. Public URL: https://${SERVER_NAME}:7099/"
