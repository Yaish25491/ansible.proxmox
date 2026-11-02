#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
TEMPLATE="${SCRIPT_DIR}/integration_config.yml.tpl"
OUTPUT="${SCRIPT_DIR}/integration_config.yml"

: "${PROXMOX_HOST:?PROXMOX_HOST must be set}"

if [[ -n "${PROXMOX_PASSWORD:-}" && ( -n "${PROXMOX_TOKEN_ID:-}" || -n "${PROXMOX_TOKEN_SECRET:-}" ) ]]; then
    echo "PROXMOX_PASSWORD cannot be combined with PROXMOX_TOKEN_ID or PROXMOX_TOKEN_SECRET." >&2
    exit 1
fi

if [[ -z "${PROXMOX_PASSWORD:-}" && ( -z "${PROXMOX_TOKEN_ID:-}" || -z "${PROXMOX_TOKEN_SECRET:-}" ) ]]; then
    echo "Set PROXMOX_PASSWORD or both PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET." >&2
    exit 1
fi

export PROXMOX_PORT="${PROXMOX_PORT:-8006}"
export PROXMOX_CA_PATH="${PROXMOX_CA_PATH:-}"
export PROXMOX_USER="${PROXMOX_USER:-}"
export PROXMOX_PASSWORD="${PROXMOX_PASSWORD:-}"
export PROXMOX_TOKEN_ID="${PROXMOX_TOKEN_ID:-}"
export PROXMOX_TOKEN_SECRET="${PROXMOX_TOKEN_SECRET:-}"
export PROXMOX_VALIDATE_CERTS="${PROXMOX_VALIDATE_CERTS:-true}"

python3 - "${TEMPLATE}" "${OUTPUT}" <<'PY'
import json
import os
from pathlib import Path
import sys

variables = (
    "PROXMOX_HOST",
    "PROXMOX_PORT",
    "PROXMOX_CA_PATH",
    "PROXMOX_USER",
    "PROXMOX_PASSWORD",
    "PROXMOX_TOKEN_ID",
    "PROXMOX_TOKEN_SECRET",
)

template = Path(sys.argv[1]).read_text(encoding="utf-8")
for variable in variables:
    template = template.replace("${%s}" % variable, json.dumps(os.environ[variable]))

template = template.replace(
    "${PROXMOX_VALIDATE_CERTS}",
    "true" if os.environ["PROXMOX_VALIDATE_CERTS"].lower() in ("1", "true", "yes", "on") else "false",
)
Path(sys.argv[2]).write_text(template, encoding="utf-8")
PY
