# Local Monitoring Dashboard

The desktop API exposes a read-only local dashboard for operator checks:

```text
http://127.0.0.1:8080/monitor
```

Start the local development API with:

```powershell
.\scripts\api\start-desktop-api.ps1 -Profile A
```

The page polls this local JSON endpoint every 5 seconds:

```text
http://127.0.0.1:8080/api/monitor/state
```

The FRP-backed edge entrypoint should use profile `B -- 边缘接入环境`, which
listens on `0.0.0.0:18082`.
From Explorer, double-click `scripts\api\restart-edge-api.cmd` to restart that
Edge process.

Both routes are guarded by the API process and reject non-loopback clients. The
monitor does not use the public bearer token in v1 because the selected safety
boundary is local network access. Do not forward these paths through the ECS
edge gateway.

## What It Shows

- Current queued and in-progress briefing tasks.
- Recent completed, failed, and QA-failed jobs.
- Counts by worker status, active task count, success rate, recent volume, and
  average completed duration when timestamps are available.
- Stale in-progress warnings when a task has not updated for more than 30
  minutes.
- Local health checks for task store, worker state, AgentWorkspace, briefing
  archives, Git LFS tracking, ECS public health routing, and reverse-proxy
  client/server status.

## Worker Controls

The dashboard also exposes local-only worker start controls:

- Start or rerun one selected task.
- Delete one selected task, its GitHub Issue, and any local worker state once
  the task is no longer actively running.
- Start one drain run that processes pending tasks until none remain.
- Start a long-running worker loop.
- Stop a running worker that was launched through the monitor controls.

These controls call the desktop API process at
`POST /api/monitor/worker/start`, `POST /api/monitor/worker/stop`, and
`POST /api/monitor/tasks/delete`. They are guarded by the same loopback-only
rule as the dashboard and should not be exposed through the ECS edge gateway.
Each start action launches a background worker process and writes stdout/stderr
under the worker log directory. Start/rerun and delete actions reject tasks that
are currently running; stale in-progress tasks and failed or QA-failed tasks can
be selected for cleanup or rerun.

## Data Sources

The dashboard reads only service-owned local files:

- `.runtime/api/tasks.jsonl`
- `.runtime/worker/state/*.json`
- `.runtime/api/logs/*`
- `.runtime/worker/logs/*`
- `briefings/**/manifest.json`
- `.gitattributes`
- Docker container status for the desktop `frpc` client.
- Public ECS `/health` through the edge gateway.
- Optional SSH-based ECS container process check when configured.

It does not call GitHub, mutate worker state, create archive files, write Issue
comments, run Git commands, retry jobs, or cancel jobs outside the explicit
local-only controls listed above.

## Edge And Proxy Checks

The monitor checks the desktop-to-ECS edge path in layers:

- `MOZHI_FRPC_CONTAINER_NAME`, default `mozhi-ecs-frpc-test`: local Docker
  container running the desktop `frpc` client.
- `MOZHI_PUBLIC_BASE_URL`, default `http://39.105.78.135`: public edge URL used
  for `/health`.
- `MOZHI_EDGE_CONTAINER_NAME`, default `mozhi-agent-service-edge`: ECS Docker
  container expected to run Caddy and `frps`.
- `MOZHI_ECS_SSH_TARGET`, default `root@39.105.78.135`: SSH target for deep ECS
  checks. Override it if the ECS login user, host, or SSH alias changes.

If the SSH target is unreachable or the local machine has no non-interactive key
for it, the monitor still checks the local `frpc` container and public
`/health`, but marks the ECS container-internal `frps`/Caddy process check as a
warning or failure.

## Common Warnings

- Missing task store: no briefing request has been queued at the configured path
  yet, or the desktop API is using a different `MOZHI_TASK_STORE_PATH`.
- Missing worker state directory: the worker has not started yet, or
  `MOZHI_WORKER_STATE_DIR` points somewhere else.
- Missing AgentWorkspace: `MOZHI_AGENT_WORKSPACE` does not exist on this host.
- Missing Git LFS tracking: `.gitattributes` does not contain
  `briefings/**/*.pptx filter=lfs diff=lfs merge=lfs -text`.
- Stale task: an in-progress task has not refreshed `updated_at` for more than
  30 minutes.
- Missing frpc client: the desktop reverse-proxy client container is not running
  or Docker is unavailable.
- ECS public health failure: the public edge URL cannot route `/health` back to
  the desktop API.
- ECS SSH failure: confirm `MOZHI_ECS_SSH_TARGET`, SSH key access, Docker on the
  ECS host, and the `mozhi-agent-service-edge` container.

## Safety Boundary

The monitor is an operator page, not a delivery UI or worker control plane. Keep
public routing focused on `/health` and `/api/briefings`. Future remote access
should use an explicit monitor token, VPN, or authenticated tunnel rather than
publishing the local dashboard directly.

## Runtime Migration

The monitor now treats `.tmp/` as disposable scratch and reads durable service
state from `.runtime/` by default. Before deleting an existing `.tmp/`, stop the
desktop API and Worker, then move any local state you still need:

```powershell
New-Item -ItemType Directory -Force -Path .runtime | Out-Null
Move-Item .tmp\api .runtime\api -ErrorAction SilentlyContinue
Move-Item .tmp\worker .runtime\worker -ErrorAction SilentlyContinue
```

After migration, restart the API and Worker so they use the new defaults, or set
`MOZHI_TASK_STORE_PATH`, `MOZHI_WORKER_STATE_DIR`, `MOZHI_API_LOG_DIR`, and
`MOZHI_WORKER_LOG_DIR` explicitly if an operator-managed path is preferred.
