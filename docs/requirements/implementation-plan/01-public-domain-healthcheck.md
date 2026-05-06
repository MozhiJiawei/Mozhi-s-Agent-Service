# Iteration 1: ECS Edge Gateway and Health Check

## Goal

Make the service running on the home desktop reachable from the public internet
through the user's domain and the purchased Alibaba Cloud ECS edge gateway.

## External Acceptance Feature

A requester outside the home network can open:

```text
https://<domain>/health
```

and receive a successful health response from the service running on the home
desktop through the ECS gateway.

## Scope

- Use the purchased Alibaba Cloud ECS instance as the stable public edge:
  Ubuntu 24.04, 2 vCPU, 2 GiB RAM, 40 GiB system disk, 3 Mbps fixed bandwidth,
  public IP `39.105.78.135`, region `cn-beijing`.
- Configure domain DNS so the public A record points to `39.105.78.135`.
- Configure the ECS security group to allow SSH, HTTP, HTTPS, and the selected
  FRP control/proxy ports.
- Install Docker and Docker Compose on the ECS instance.
- Define a containerized edge stack for the ECS instance:
  - Caddy terminates HTTPS for the public domain.
  - `frps` receives the home desktop's outbound tunnel connection.
  - Caddy reverse-proxies `/health` traffic through the FRP tunnel to the home
    desktop service.
- Configure the home desktop to run `frpc` as an outbound client to the ECS
  instance.
- Configure Windows Firewall only for local service and `frpc` needs; no router
  inbound port forwarding is required.
- Document operational diagnostics for DNS, ECS security group, Docker services,
  Caddy HTTPS, FRP connectivity, Windows service reachability, and tunnel
  failures.

## Out of Scope

- Implementing `POST /api/briefings`.
- Creating GitHub Issues.
- Running Codex CLI or PPT generation.
- Buying additional servers or using the home broadband public IP directly.
- Running the real API or worker workload on the ECS instance.
- Designing a production multi-region deployment.

## Key Decisions

- The service runs on the home desktop rather than a cloud server.
- The Alibaba Cloud ECS instance is a stable edge gateway, not the primary
  application host.
- The domain points to the ECS public IP `39.105.78.135`, not to the home
  broadband IP.
- The home desktop initiates an outbound FRP tunnel to the ECS instance, so the
  design does not require a home public IPv4 address, router port forwarding, or
  DDNS.
- ECS-side services should be containerized so the edge gateway can be rebuilt or
  moved with minimal manual server state.
- HTTPS is part of the external acceptance feature, not a later polish step.

## Implementation Notes

- The health endpoint should return enough information for humans to confirm
  they are reaching the intended service instance: service name, version or build
  identifier, current server time, and status.
- Keep the ECS stack declarative under repository-managed deployment files so the
  gateway can be tested locally and then applied to the server.
- Caddy should own public HTTP/HTTPS ports and route only the required public
  paths to the tunnel target.
- FRP should require an authentication token and avoid exposing unnecessary proxy
  surfaces.
- The home desktop service can bind to `localhost` if `frpc` connects to the
  local port directly.
- The runbook should include how to inspect the domain A record, ECS public IP,
  ECS security group, Docker Compose service status, Caddy certificate status,
  FRP client/server logs, and the local desktop service.

## Current Progress

- Added an ECS edge Docker image delivery under
  `deploy/ecs/agent-service-edge/`.
  - The image is built from `ubuntu:24.04` for compatibility with the ECS Ubuntu
    24.04 64-bit host.
  - The image packages Caddy and FRP server (`frps`) in one container.
  - Runtime templates generate Caddy and `frps` configuration from environment
    variables; real domain names, tokens, certificates, and ECS credentials are
    not committed.
- Added repository-managed scripts under `scripts/ecs/`.
  - `build-agent-service-edge-image.ps1` builds
    `mozhi-agent-service-edge:local` with Docker Desktop.
  - `test-local-edge-image.ps1` verifies the local edge path.
  - `save-agent-service-edge-image.ps1` exports the image tar for manual upload
    and `docker load` on ECS.
  - `stop-local-edge-image-test.ps1` cleans up the local verification stack.
- Built the local `mozhi-agent-service-edge:local` image successfully on the
  Windows desktop with platform `linux/amd64`.
  - The generated image matches the ECS server architecture
    (`x86_64`/`amd64`).
  - The local image size is approximately 175 MB.
- Verified the local edge chain with Docker Desktop.
  - The test starts one edge container locally.
  - The mock desktop `/health` service runs as a host-side Python process.
  - A temporary `frpc` process runs inside the same edge container for local
    tunnel verification.
  - `http://localhost:18080/health` returned `200 OK` and identified the mock
    desktop service.
  - Stopping temporary `frpc` produced a visible `502 Bad Gateway`; restarting
    it restored the health response.
- Added `docs/operations/agent-service-edge-image.md` with local build, local
  verification, ECS `docker load`, runtime environment, and disk hygiene notes.
- Added temporary long-running local preview scripts under `.tmp/` for manual
  browser inspection; these are scratch helpers and are intentionally excluded
  from Git.

Remaining work for this iteration:

- Export the edge image tar and upload it to ECS.
- Install or verify Docker on the ECS Ubuntu 24.04 host.
- Load and run `mozhi-agent-service-edge:local` on ECS with real `DOMAIN`,
  `FRP_TOKEN`, `FRP_BIND_PORT`, and `HEALTH_PROXY_PORT` values.
- Configure DNS A record for `<domain>` to `39.105.78.135`.
- Configure the ECS security group for SSH, HTTP, HTTPS, and FRP.
- Run the real desktop-side `frpc` and real desktop `/health` service.
- Validate `https://<domain>/health` from outside the home network.

## E2E Acceptance Test

### Preconditions

- The service is running on the home desktop.
- The ECS instance `39.105.78.135` is running Ubuntu 24.04.
- Docker Compose is running Caddy and `frps` on the ECS instance.
- The home desktop is running `frpc` connected to the ECS instance.
- DNS contains an A record for `<domain>` pointing to `39.105.78.135`.
- The ECS security group allows the selected SSH, HTTP, HTTPS, and FRP ports.
- HTTPS is configured for `<domain>` through Caddy.
- The tester has a phone with mobile data that is not connected to the home Wi-Fi.

### Steps

1. On the phone, disable Wi-Fi and use mobile data.
2. Open `https://<domain>/health`.
3. Confirm the response is `200 OK`.
4. Confirm the response identifies the briefing generation service.
5. Stop `frpc` on the home desktop.
6. Confirm `https://<domain>/health` fails with a clear gateway or service
   unavailable response.
7. Restart `frpc` on the home desktop.
8. Open `https://<domain>/health` again from mobile data.

### Expected Result

- The health endpoint is reachable from outside the home network.
- The response proves traffic reaches the desktop service through the ECS edge
  gateway and FRP tunnel.
- When the desktop tunnel is unavailable, the public gateway fails visibly rather
  than silently routing to the wrong service.
- After the tunnel is restored, the health endpoint becomes reachable again
  without DNS or router changes.

## Risks & Diagnostics

- **DNS points to the wrong address:** compare the domain A record with
  `39.105.78.135`.
- **ECS security group blocks traffic:** verify inbound rules for 80, 443, SSH,
  and the selected FRP ports.
- **Docker stack is not running:** inspect Caddy and `frps` container health and
  logs.
- **FRP tunnel is down:** inspect `frpc` logs on the home desktop and `frps` logs
  on the ECS instance.
- **Local desktop service is unreachable:** test `http://localhost:<port>/health`
  on the desktop before testing the public domain.
- **HTTPS certificate fails:** record whether the failure is issuance, renewal,
  hostname mismatch, Caddy configuration, or DNS propagation.
- **3 Mbps bandwidth limit:** keep the gateway focused on API/status traffic and
  avoid using it as the primary large-artifact download channel.

## Done Criteria

- `https://<domain>/health` works from mobile data.
- The ECS Docker Compose gateway and home desktop FRP client behavior are
  documented.
- Failure diagnostics are written clearly enough for a human operator to follow.
- No API, worker, Codex, or archive behavior is implemented in this iteration.
