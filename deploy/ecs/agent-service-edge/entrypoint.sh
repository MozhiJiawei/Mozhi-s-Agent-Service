#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 64
  fi
}

require_env "FRP_TOKEN"

export FRP_BIND_PORT="${FRP_BIND_PORT:-7000}"
export HEALTH_PROXY_PORT="${HEALTH_PROXY_PORT:-18081}"

if [[ -n "${CADDY_SITE_ADDRESS:-}" ]]; then
  export CADDY_SITE_ADDRESS
elif [[ -n "${DOMAIN:-}" ]]; then
  export CADDY_SITE_ADDRESS="${DOMAIN}"
else
  export CADDY_SITE_ADDRESS=":80"
fi

envsubst '${CADDY_SITE_ADDRESS} ${HEALTH_PROXY_PORT}' \
  < /etc/mozhi-edge/templates/Caddyfile.template \
  > /etc/mozhi-edge/generated/Caddyfile

envsubst '${FRP_BIND_PORT} ${FRP_TOKEN}' \
  < /etc/mozhi-edge/templates/frps.toml.template \
  > /etc/mozhi-edge/generated/frps.toml

echo "Starting frps on port ${FRP_BIND_PORT}" >&2
frps -c /etc/mozhi-edge/generated/frps.toml &
frps_pid="$!"

echo "Starting Caddy at ${CADDY_SITE_ADDRESS}; health proxy port ${HEALTH_PROXY_PORT}" >&2
caddy run --config /etc/mozhi-edge/generated/Caddyfile --adapter caddyfile &
caddy_pid="$!"

terminate() {
  echo "Stopping edge services" >&2
  kill -TERM "${caddy_pid}" "${frps_pid}" 2>/dev/null || true
  wait "${caddy_pid}" "${frps_pid}" 2>/dev/null || true
}

trap terminate TERM INT

while true; do
  if ! kill -0 "${frps_pid}" 2>/dev/null; then
    echo "frps exited unexpectedly" >&2
    terminate
    exit 1
  fi

  if ! kill -0 "${caddy_pid}" 2>/dev/null; then
    echo "Caddy exited unexpectedly" >&2
    terminate
    exit 1
  fi

  sleep 2
done

