#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${DOCKER_HEALTH_AGENT_CONFIG_DIR:-$HOME/docker-health-agent-config}"
COMPOSE_FILE="$REPO_ROOT/compose.yml"
STATE_VOLUME="docker-health-agent-state"

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
docker volume create "$STATE_VOLUME" >/dev/null

export DOCKER_HEALTH_AGENT_CONFIG_DIR="$CONFIG_DIR"

existing_service="$(
  docker inspect docker-health-agent \
    --format '{{ index .Config.Labels "com.docker.compose.service" }}' \
    2>/dev/null || true
)"
if [[ -n "$existing_service" && "$existing_service" != "docker-health-agent" ]]; then
  echo "Removing legacy docker-health-agent container from service '$existing_service'."
  docker rm -f docker-health-agent >/dev/null
fi

docker compose -f "$COMPOSE_FILE" up -d --build docker-health-agent

echo "docker-health-agent is deployed."
echo "Source: $REPO_ROOT"
echo "Config: $CONFIG_DIR"
