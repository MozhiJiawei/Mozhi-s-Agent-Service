# Docker Build Scripts

Linux operators should build the ECS edge image with the script that mirrors the
existing PowerShell entrypoint:

```bash
bash scripts/ecs/build-agent-service-edge-image.sh
```

That script delegates to the shared Docker build helper:

```bash
bash scripts/docker/build.sh
```

The default target is the ECS edge gateway image:

```text
mozhi-agent-service-edge:<git-short-sha>
```

Use explicit image metadata for deployment or CI:

```bash
bash scripts/docker/build.sh \
  --target edge \
  --image mozhi-agent-service-edge \
  --tag local \
  --platform linux/amd64
```

The script accepts `--context`, `--dockerfile`, `--caddy-version`,
`--frp-version`, `--base-image`, and repeated `--build-arg KEY=VALUE` options.
It exits non-zero when Docker is unavailable, the daemon is unreachable, the
build context is missing, or `docker build` fails.

`api` and `worker` are reserved target names. They fail with a clear message
until this repository adds Dockerfiles for those runtime images. The current
architecture keeps the briefing API and worker on the desktop host while the ECS
edge image runs Caddy and `frps`.
