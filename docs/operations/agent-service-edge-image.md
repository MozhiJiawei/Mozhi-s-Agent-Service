# Agent-Service Edge Docker Image

This runbook covers the ECS edge gateway Docker image for
`Mozhi-s-Agent-Service`.

The ECS host should use the official Ubuntu 24.04 64-bit system image. This
repository builds a separate Docker image, `mozhi-agent-service-edge`, that runs
Caddy and `frps` inside one container.

## What The Image Contains

- Ubuntu 24.04 base image.
- Caddy for public HTTP/HTTPS termination.
- FRP server (`frps`) for the outbound tunnel from the home desktop.
- Runtime templates for Caddy and FRP configuration.

The image does not contain the briefing API, worker, Codex, PPT generation, real
domain names, FRP tokens, certificates, or generated artifacts.

## Build Locally

Run these commands from the repository root on the Windows desktop with Docker
Desktop running:

```powershell
.\scripts\ecs\build-agent-service-edge-image.ps1
.\scripts\ecs\test-local-edge-image.ps1
.\scripts\ecs\save-agent-service-edge-image.ps1
```

The test starts only one Docker container for the edge image. It also runs a
host-side Python mock desktop health service. For tunnel verification, the
script uses `docker exec` to start the image's bundled `frpc` inside that same
edge container. This keeps the Docker footprint to one container and avoids
installing `frpc` on Windows.

It verifies:

```text
http://localhost:18080/health
```

The request path is:

```text
Caddy container -> frps in the same container -> temporary frpc in the same container -> host mock desktop service
```

Stop the verification stack when finished:

```powershell
.\scripts\ecs\stop-local-edge-image-test.ps1
```

## Run On ECS

Upload the tar archive from `dist/docker/` to the ECS instance, then load and run
it:

```bash
docker load -i mozhi-agent-service-edge-local.tar

docker run -d --name mozhi-agent-service-edge \
  --restart unless-stopped \
  -p 80:80 -p 443:443 -p 7000:7000 \
  -e DOMAIN=<domain> \
  -e FRP_TOKEN=<token> \
  -e FRP_BIND_PORT=7000 \
  -e HEALTH_PROXY_PORT=18081 \
  -v mozhi-caddy-data:/data \
  mozhi-agent-service-edge:local
```

Delete the uploaded tar after `docker load` succeeds:

```bash
rm mozhi-agent-service-edge-local.tar
```

## Desktop frpc Mapping

The desktop `frpc` should connect to the ECS public IP and expose the local
desktop service to the same remote port used by Caddy:

```toml
serverAddr = "39.105.78.135"
serverPort = 7000
auth.method = "token"
auth.token = "<token>"

[[proxies]]
name = "mozhi-health"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8080
remotePort = 18081
```

## Disk Notes

The ECS system disk is 40GB, so keep the host clean:

```bash
docker system df
docker image prune
```

Do not build the Docker image on ECS. Build locally, load or pull on ECS, and
delete image archives after loading. Do not place PPTX files, Codex scratch
workspaces, briefing archives, or Docker build cache on the ECS host.

Configure Docker log rotation on the ECS host if the service runs for long
periods.
