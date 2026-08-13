#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICES_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

exec bash "${SERVICES_DIR}/scripts/stop.sh" webui "$@"
