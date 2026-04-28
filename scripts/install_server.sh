#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/docker-health-agent}"
SERVICE_NAME="${SERVICE_NAME:-docker-health-agent}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -d "${INSTALL_DIR}" ]]; then
  echo "Expected ${INSTALL_DIR} to exist. Copy the repository there before running this script." >&2
  exit 1
fi

if command -v sudo >/dev/null 2>&1 && [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  echo "python3 ensurepip is unavailable. Install python3-venv before running this script." >&2
  echo "On Debian, try: sudo apt install -y python3-venv python3-pip" >&2
  exit 1
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "python3 -m pip is unavailable. Install python3-pip before running this script." >&2
  echo "On Debian, try: sudo apt install -y python3-venv python3-pip" >&2
  exit 1
fi

python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
fi

if [[ ! -f "${INSTALL_DIR}/config.yaml" ]]; then
  cp "${INSTALL_DIR}/config.example.yaml" "${INSTALL_DIR}/config.yaml"
fi

${SUDO} install -d -m 0755 /var/lib/docker-health-agent
${SUDO} install -m 0644 "${INSTALL_DIR}/systemd/docker-health-agent.service" "${UNIT_PATH}"
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable "${SERVICE_NAME}.service"
${SUDO} systemctl restart "${SERVICE_NAME}.service"
${SUDO} systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
