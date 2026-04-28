#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${DOCKER_HEALTH_AGENT_CONFIG_DIR:-$HOME/docker-health-agent-config}"
COMPOSE_FILE="$REPO_ROOT/compose.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "Missing required command: docker" >&2
  exit 1
fi

mkdir -p "$CONFIG_DIR"

if [[ ! -f "$CONFIG_DIR/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$CONFIG_DIR/.env"
fi

if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
  cp "$REPO_ROOT/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi

chmod 600 "$CONFIG_DIR/.env" 2>/dev/null || true
chmod 644 "$CONFIG_DIR/config.yaml" 2>/dev/null || true

export DOCKER_HEALTH_AGENT_CONFIG_DIR="$CONFIG_DIR"

docker compose -f "$COMPOSE_FILE" up -d --build watchdog

echo "docker-health-agent is deployed."
echo "Source: $REPO_ROOT"
echo "Config: $CONFIG_DIR"
