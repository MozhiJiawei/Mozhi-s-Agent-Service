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
- Public `/health` routing to the desktop API service through FRP.
- HTTPS-only `/api/*` routing to the desktop API service through FRP, with an
  edge request body limit.

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

On Linux, including the ECS host, use the shell build entrypoint:

```bash
bash scripts/ecs/build-agent-service-edge-image.sh \
  --image-name mozhi-agent-service-edge \
  --image-tag local \
  --platform linux/amd64
```

If the ECS host cannot reach Docker Hub, configure a Docker registry mirror on
the host or pass a reachable Ubuntu mirror with `--base-image`.

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
  -e CADDY_HTTP_SITE_ADDRESS=:80 \
  -e CADDY_HTTPS_SITE_ADDRESS=https://39.105.78.135 \
  -e CADDY_DEFAULT_SNI=39.105.78.135 \
  -e CADDY_TLS_MODE=internal \
  -e FRP_TOKEN=<token> \
  -e FRP_BIND_PORT=7000 \
  -e HEALTH_PROXY_PORT=18081 \
  -e DESKTOP_API_PROXY_PORT=18081 \
  -v mozhi-caddy-data:/data \
  mozhi-agent-service-edge:local
```

This public-IP HTTPS mode makes Caddy listen on container port `443` and serve
an internal certificate for the public IP. Use it to prove that the edge HTTPS
path reaches the desktop API:

```bash
curl -k https://39.105.78.135/health
```

Because the certificate is internal, public-IP callers must use `-k` or an
equivalent "insecure TLS" option. For real caller traffic without `-k`, point a
domain at the ECS host and switch to automatic public certificates:

```text
CADDY_HTTP_SITE_ADDRESS=:80
CADDY_HTTPS_SITE_ADDRESS=<domain>
CADDY_DEFAULT_SNI=<domain>
CADDY_TLS_MODE=auto
```

Delete the uploaded tar after `docker load` succeeds:

```bash
rm mozhi-agent-service-edge-local.tar
```

## Secret Material

Follow the repository-wide secret handling rules in
`docs/operations/secret-material.md`.

Keep real tokens and keys out of this repository. Store them in fixed operator
directories so local tests and ECS restarts do not depend on temporary files.

Current fixed locations:

```text
Windows desktop:
  %USERPROFILE%\.mozhi-agent-service\edge\frp-token.txt
  %USERPROFILE%\.mozhi-agent-service\edge\frpc-ecs.toml
  %USERPROFILE%\.mozhi-agent-service\edge\ecs-edge.env

ECS host:
  /etc/mozhi-agent-service/edge/.env
```

The ECS `.env` file should be owned by `root:root` and readable only by root:

```bash
chown root:root /etc/mozhi-agent-service/edge/.env
chmod 600 /etc/mozhi-agent-service/edge/.env
```

The desktop `frpc` configuration and the ECS edge `.env` must use the same
`FRP_TOKEN` value.

## Desktop frpc Mapping

The desktop `frpc` should connect to the ECS public IP and expose the local
desktop API service to the same remote port used by Caddy. Once Iteration 2 is
running, the local service should be the FastAPI app on `127.0.0.1:8080`:

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

`DESKTOP_API_PROXY_PORT` defaults to `HEALTH_PROXY_PORT` for compatibility with
the original health check tunnel. When `/health` and `/api/*` share the same
desktop FastAPI service, both values may remain `18081`.

`/api/*` carries bearer credentials and should not be used over the public
HTTP-by-IP path. Keep HTTP-by-IP validation to `/health` unless you are doing a
short-lived end-to-end check with a disposable token.

For a one-off public-IP HTTP E2E check before domain HTTPS is available, set
`ALLOW_HTTP_API=true` on the ECS edge container and send
`X-Mozhi-Allow-Http-Api: true` with the request. Do not share production tokens
through this path; use a short-lived validation token only.

For a one-off public-IP HTTPS E2E check before domain certificates are
available, keep `CADDY_HTTPS_SITE_ADDRESS=https://39.105.78.135`,
`CADDY_DEFAULT_SNI=39.105.78.135`, and `CADDY_TLS_MODE=internal`, then add
`-k` to the curl command:

```bash
curl -k -i -sS -X POST "https://39.105.78.135/api/briefings" \
  -H "Authorization: Bearer <short-lived-token>" \
  -H "Content-Type: text/plain; charset=utf-8" \
  -H "X-Mozhi-Title: test" \
  --data-binary "test source"
```

## Disk Notes

The ECS system disk is 40GB, so keep the host clean:

```bash
docker system df
docker image prune
```

Prefer local builds for repeatability, but ECS-side builds are supported through
`scripts/ecs/build-agent-service-edge-image.sh` when upload bandwidth or local
Docker is inconvenient. Do not place PPTX files, Codex scratch workspaces, or
briefing archives on the ECS host.

Configure Docker log rotation on the ECS host if the service runs for long
periods.
