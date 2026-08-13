#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
USERNAME="${1:-joyvl}"
PASSWORD="${2:-}"
PASSWORD_FILE="${SCRIPT_DIR}/.htpasswd"

if [[ ! "${USERNAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "Username may contain only letters, numbers, dot, underscore, and hyphen." >&2
  exit 1
fi

if [[ -z "${PASSWORD}" ]]; then
  read -r -s -p "New password for ${USERNAME}: " PASSWORD
  echo
  read -r -s -p "Confirm password: " PASSWORD_CONFIRM
  echo
  if [[ "${PASSWORD}" != "${PASSWORD_CONFIRM}" ]]; then
    echo "Passwords do not match." >&2
    exit 1
  fi
fi

if [[ ${#PASSWORD} -lt 12 ]]; then
  echo "Password must contain at least 12 characters." >&2
  exit 1
fi

password_hash="$(openssl passwd -6 "${PASSWORD}")"
umask 077
temp_file="$(mktemp "${SCRIPT_DIR}/.htpasswd.tmp.XXXXXX")"
trap 'rm -f "${temp_file}"' EXIT
printf '%s:%s\n' "${USERNAME}" "${password_hash}" >"${temp_file}"
mv "${temp_file}" "${PASSWORD_FILE}"
trap - EXIT

echo "Updated ${PASSWORD_FILE} for user ${USERNAME}."
echo "Run nginx/install.sh again to install it, or copy it to /etc/nginx/joyvl/.htpasswd and reload Nginx."
