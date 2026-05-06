FROM ubuntu:24.04

ARG TARGETARCH
ARG CADDY_VERSION=2.9.1
ARG FRP_VERSION=0.62.1

LABEL org.opencontainers.image.title="mozhi-agent-service-edge"
LABEL org.opencontainers.image.description="ECS edge gateway for Mozhi Agent Service: Caddy + frps"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gettext-base \
        tar \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) caddy_arch="amd64"; frp_arch="amd64" ;; \
        arm64) caddy_arch="arm64"; frp_arch="arm64" ;; \
        *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=${caddy_arch}&version=v${CADDY_VERSION}" -o /usr/local/bin/caddy; \
    chmod +x /usr/local/bin/caddy; \
    curl -fsSL "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${frp_arch}.tar.gz" -o /tmp/frp.tgz; \
    mkdir -p /tmp/frp; \
    tar -xzf /tmp/frp.tgz -C /tmp/frp --strip-components=1; \
    install -m 0755 /tmp/frp/frps /usr/local/bin/frps; \
    install -m 0755 /tmp/frp/frpc /usr/local/bin/frpc; \
    rm -rf /tmp/frp /tmp/frp.tgz; \
    caddy version; \
    frps --version

RUN mkdir -p /etc/mozhi-edge/templates /etc/mozhi-edge/generated /data /config

COPY Caddyfile.template /etc/mozhi-edge/templates/Caddyfile.template
COPY frps.toml.template /etc/mozhi-edge/templates/frps.toml.template
COPY entrypoint.sh /usr/local/bin/mozhi-edge-entrypoint

RUN chmod +x /usr/local/bin/mozhi-edge-entrypoint

EXPOSE 80 443 7000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/mozhi-edge-entrypoint"]

