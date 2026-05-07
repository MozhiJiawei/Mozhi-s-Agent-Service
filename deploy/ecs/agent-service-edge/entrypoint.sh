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
export DESKTOP_API_PROXY_PORT="${DESKTOP_API_PROXY_PORT:-${HEALTH_PROXY_PORT}}"
export API_MAX_BODY_SIZE="${API_MAX_BODY_SIZE:-1MB}"
export ALLOW_HTTP_API="${ALLOW_HTTP_API:-false}"
export CADDY_TLS_MODE="${CADDY_TLS_MODE:-auto}"

if [[ "${ALLOW_HTTP_API}" == "true" ]]; then
  export ALLOW_HTTP_API_HEADER_VALUE="true"
else
  export ALLOW_HTTP_API_HEADER_VALUE="__disabled__"
fi

if [[ -n "${CADDY_HTTP_SITE_ADDRESS:-}" ]]; then
  export CADDY_HTTP_SITE_ADDRESS
elif [[ -n "${CADDY_SITE_ADDRESS:-}" && "${CADDY_SITE_ADDRESS}" == :* ]]; then
  export CADDY_HTTP_SITE_ADDRESS="${CADDY_SITE_ADDRESS}"
else
  export CADDY_HTTP_SITE_ADDRESS=":80"
fi

if [[ -n "${CADDY_HTTPS_SITE_ADDRESS:-}" ]]; then
  export CADDY_HTTPS_SITE_ADDRESS
elif [[ -n "${DOMAIN:-}" ]]; then
  export CADDY_HTTPS_SITE_ADDRESS="${DOMAIN}"
elif [[ -n "${CADDY_SITE_ADDRESS:-}" && "${CADDY_SITE_ADDRESS}" != :* ]]; then
  export CADDY_HTTPS_SITE_ADDRESS="${CADDY_SITE_ADDRESS}"
else
  export CADDY_HTTPS_SITE_ADDRESS="https://localhost"
fi

case "${CADDY_TLS_MODE}" in
  auto)
    export CADDY_TLS_DIRECTIVE=""
    ;;
  internal)
    export CADDY_TLS_DIRECTIVE="	tls internal"
    ;;
  *)
    echo "Invalid CADDY_TLS_MODE=${CADDY_TLS_MODE}; expected auto or internal" >&2
    exit 64
    ;;
esac

envsubst '${CADDY_HTTP_SITE_ADDRESS} ${CADDY_HTTPS_SITE_ADDRESS} ${CADDY_TLS_DIRECTIVE} ${HEALTH_PROXY_PORT} ${DESKTOP_API_PROXY_PORT} ${API_MAX_BODY_SIZE} ${ALLOW_HTTP_API_HEADER_VALUE}' \
  < /etc/mozhi-edge/templates/Caddyfile.template \
  > /etc/mozhi-edge/generated/Caddyfile

envsubst '${FRP_BIND_PORT} ${FRP_TOKEN}' \
  < /etc/mozhi-edge/templates/frps.toml.template \
  > /etc/mozhi-edge/generated/frps.toml

echo "Starting frps on port ${FRP_BIND_PORT}" >&2
frps -c /etc/mozhi-edge/generated/frps.toml &
frps_pid="$!"

echo "Starting Caddy HTTP at ${CADDY_HTTP_SITE_ADDRESS}, HTTPS at ${CADDY_HTTPS_SITE_ADDRESS} (${CADDY_TLS_MODE}); health proxy port ${HEALTH_PROXY_PORT}; desktop API proxy port ${DESKTOP_API_PROXY_PORT}" >&2
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
