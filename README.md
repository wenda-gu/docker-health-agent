# docker-health-agent

`docker-health-agent` is a conservative Docker watchdog for a single host running multiple Compose services behind a shared edge proxy.

It is intentionally boring:

- discovers managed services from an explicit Docker label contract
- inspects container state and Docker healthchecks on a poll interval
- optionally uses a labeled public health URL as a temporary fallback for legacy services
- restarts only explicitly allowed containers
- never recreates, deletes, prunes, pulls, or runs `docker compose down`
- throttles restart attempts and persists restart history to disk
- posts compact alerts by webhook or SMTP email when intervention or repeated failure happens

## Current Server Fit

On April 24, 2026, the live Hostinger Debian host at `100.121.74.70` was running these containers:

- `edge-proxy-proxy-1`
- `gu-wenda-site-app-1`
- `docker-app-1`
- `rms-postgres`
- `docker-postgres_backup-1`
- `ink2score-frontend-1`
- `ink2score-api-1`
- `ink2score-recognizer-1`

Current health coverage on that host:

- `edge-proxy-proxy-1`, `gu-wenda-site-app-1`, `docker-app-1`, `rms-postgres`, `ink2score-frontend-1`, `ink2score-api-1`, and `ink2score-recognizer-1` now have Docker healthchecks in their local compose contracts.
- `docker-postgres_backup-1` is still intentionally liveness-only and should usually stay `auto_restart: false`.

The compose contracts in the sibling repos now define the Docker label contract locally. Once those repos are redeployed on the host, the agent no longer needs a hardcoded service inventory.

## Project Layout

- `agent.py`: CLI entrypoint
- `docker_health_agent/config.py`: YAML and environment loading
- `docker_health_agent/docker_client.py`: Docker inspect and restart adapter
- `docker_health_agent/engine.py`: polling loop and recovery policy
- `docker_health_agent/notifier.py`: webhook and SMTP email delivery
- `docker_health_agent/recovery.py`: alert text and severity helpers
- `docker_health_agent/state.py`: persisted restart and alert state
- `tests/`: regression coverage
- `systemd/docker-health-agent.service`: Debian systemd unit
- `scripts/install_server.sh`: idempotent server install/update helper

## Quickstart

Create a virtualenv and install the dependencies:

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements-dev.txt
```

Create the runtime files:

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Run one poll locally:

```bash
./.venv/bin/python agent.py --config config.yaml --env-file .env --state-file state.json --once
```

Run the tests:

```bash
./.venv/bin/pytest
```

## Label Contract

The recommended contract is Docker-label discovery with Docker healthchecks as the source of truth.

Each managed service should define:

- `com.gu.health-agent.enabled=true`
- `com.gu.health-agent.name=<stable-service-name>`
- `com.gu.health-agent.auto-restart=true|false`
- `com.gu.health-agent.critical=true|false`
- optional `com.gu.health-agent.public-url=https://example.com/healthz`

Example:

```yaml
services:
  app:
    labels:
      com.gu.health-agent.enabled: "true"
      com.gu.health-agent.name: "ink2score-api"
      com.gu.health-agent.auto-restart: "true"
      com.gu.health-agent.critical: "true"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; raise SystemExit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status == 200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
```

Recommended semantics:

- `/healthz`: shallow container health suitable for Docker restart decisions
- `/readyz`: optional deeper dependency/readiness check for deploy workflows
- `public-url` label: migration-only fallback for services that are managed but not yet fully container-native

## Config Notes

Important discovery settings:

- `docker.discovery.*_label` lets you rename the label keys if needed, but the defaults are the intended contract
- `include_compose_projects` lets you restrict discovery to specific Compose projects
- `exclude_container_names` is a safety valve for one-off containers you never want the watchdog to touch

Important service flags, whether discovered from labels or provided as static overrides:

- `auto_restart: true` means the agent may restart that container after the unhealthy grace period
- `auto_restart: false` means the agent only alerts
- `critical: true` raises manual-intervention alerts from `warning` to `critical`
- `public_url` is optional and is only used when the container is running but does not expose a Docker healthcheck

Important recovery knobs:

- `unhealthy_grace_period_seconds`: how long an unhealthy service can stay degraded before restart is allowed
- `max_restarts_per_container_per_hour`: restart-loop protection window
- `cooldown_seconds_after_restart`: minimum wait between two recovery restarts
- `starting_alert_threshold_seconds`: how long a service may stay in `starting` before the agent alerts

Alert delivery:

- `alerts.enabled: false` disables all delivery, even when webhook or email fields are populated
- `alerts.webhook_url` sends the JSON alert payload by HTTP POST
- `alerts.email.enabled: true` sends the same alert by SMTP email
- Gmail delivery should use `smtp.gmail.com`, port `587`, STARTTLS, and a Gmail app password stored in `.env`

Example Gmail setup:

```yaml
alerts:
  enabled: true
  email:
    enabled: true
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    username: "${GMAIL_SMTP_USERNAME}"
    password: "${GMAIL_APP_PASSWORD}"
    from_address: "${ALERT_EMAIL_FROM}"
    to_addresses:
      - "${ALERT_EMAIL_TO}"
    starttls: true
```

The agent persists restart history and alert dedupe state in `/var/lib/docker-health-agent/state.json` by default.
The CLI defaults to a local `state.json` for rootless local runs; the systemd unit explicitly uses `/var/lib/docker-health-agent/state.json`.

The agent also supports an optional legacy `services:` list in `config.yaml`. That path remains available for exceptions, but the preferred steady-state setup is label discovery with `services: []`.

## Safety Guarantees

This project deliberately does not:

- delete containers
- delete volumes
- run `docker compose down`
- recreate missing services
- pull new images
- prune Docker resources
- repair databases
- expose Docker over TCP

If a container is marked `auto_restart: false`, the agent will never restart it.

## Deploy On The Hostinger Server

Copy the repository to the server, usually at `/opt/docker-health-agent`, then run the install helper:

```bash
ssh gwd@100.121.74.70
sudo apt update
sudo apt install -y python3-venv python3-pip
sudo mkdir -p /opt/docker-health-agent
sudo chown -R gwd:gwd /opt/docker-health-agent
cd /opt/docker-health-agent
./scripts/install_server.sh
```

On the current server checked on April 24, 2026, `python3 -m venv` failed because `ensurepip` was missing under Python `3.13.5`. If the generic `python3-venv` package does not satisfy that on this host, install `python3.13-venv` explicitly and rerun the installer.

The installer:

- creates `.venv`
- installs Python dependencies
- copies `.env.example` to `.env` if needed
- copies `config.example.yaml` to `config.yaml` if needed
- installs the systemd unit
- reloads systemd
- enables and restarts the agent

Useful verification commands on the server:

```bash
systemctl status docker-health-agent --no-pager
journalctl -u docker-health-agent -n 100 --no-pager
/opt/docker-health-agent/.venv/bin/python /opt/docker-health-agent/agent.py --config /opt/docker-health-agent/config.yaml --env-file /opt/docker-health-agent/.env --once
```

## Recommended Next Hardening

Phase 1 is intentionally limited. The best next improvements are:

- add the health-agent Docker labels to every managed compose service on the host
- point alerts at a real delivery target such as Gmail SMTP, Discord, Telegram bridge, or an internal notification endpoint
- add Uptime Kuma separately for public uptime, TLS expiry, and dashboard visibility
