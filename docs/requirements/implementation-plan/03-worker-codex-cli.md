# Iteration 3: Async Worker and Codex CLI Execution

## Goal

Run briefing generation asynchronously after the API request has returned, using
Codex CLI and the external `Mozhi-s-AgentWorkspace` repository.

## External Acceptance Feature

After a request is queued, the GitHub Issue automatically progresses through
worker milestone updates such as `running`, `generating`, and
`generation_completed`.

## Scope

- Implement asynchronous worker behavior under `apps/worker/`.
- Run the worker on the home desktop, colocated with the external
  `Mozhi-s-AgentWorkspace` dependency and the Codex CLI environment.
- Claim queued tasks from the task store.
- Update task state to `running` when a worker starts processing.
- Add a GitHub Issue milestone comment when processing begins.
- Invoke Codex CLI with its working directory set to
  `D:\Agent Repo\Mozhi-s-AgentWorkspace`.
- Pass the request source and generation intent to Codex CLI.
- Use an isolated runtime directory for each task.
- Capture Codex CLI exit status, high-level stage summary, and final output
  locations.
- Add Issue milestone comments for generation start, generation completion, and
  generation failure.

## Out of Scope

- Reimplementing any `hw-ppt-gen` skill behavior in this repository.
- Copying AgentWorkspace skill code into this repository.
- Running Codex CLI, PPT generation, or worker execution on the Alibaba Cloud ECS
  edge gateway.
- Final QA policy.
- Final archive creation.
- Fine-grained streaming of every Codex tool call into the Issue.

## Key Decisions

- Worker execution is asynchronous and decoupled from the HTTP request.
- The home desktop remains the execution host for worker and Codex operations.
- The Alibaba Cloud ECS instance remains only the public edge gateway and tunnel
  server.
- `Mozhi-s-AgentWorkspace` is an external dependency, not a subtree or copied
  module of this repository.
- Runtime scratch files belong outside this repository.
- Service-owned runtime state and logs belong under this repository's `.tmp/`
  directory and must not be committed. Secret files belong under
  `%USERPROFILE%\.mozhi-agent-service\api\`. AgentWorkspace generation scratch
  remains in `Mozhi-s-AgentWorkspace`, not this repository.
- Issue updates are milestone-level to keep the status page readable.
- Generation completion means Codex produced candidate outputs; it does not mean
  the job is completed or archived.

## Iteration 2 Handoff State

Iteration 2 is complete enough for the worker implementation to consume queued
briefing requests:

- The desktop API is implemented under `apps/api/` with `GET /health` and
  `POST /api/briefings`.
- `POST /api/briefings` accepts authenticated `text/plain` source material,
  requires `X-Mozhi-Title`, and returns `202 Accepted` with `request_id`,
  `issue_url`, and `status: queued`.
- API secrets are loaded by `scripts/api/start-desktop-api.ps1` from
  `%USERPROFILE%\.mozhi-agent-service\api\api-token.txt` and
  `%USERPROFILE%\.mozhi-agent-service\api\github-token.txt`.
- The default queued task store is `.tmp/api/tasks.jsonl`. The user-profile
  secret directory must contain only secret files, not logs or task state.
- Generated Issues use the default `agent-briefing` label, configurable through
  `MOZHI_ISSUE_LABEL`.
- The service uses UTC+8 timestamps in request IDs, health responses, task
  records, and Issue bodies.
- GitHub Issue creation uses `GITHUB_TOKEN` through the GitHub REST API when
  present and falls back to authenticated `gh issue create` otherwise.
- The ECS edge routes `/health` and `/api/*` to the desktop through FRP.
  Production `/api/*` traffic should use HTTPS. Temporary public-IP HTTP E2E
  checks require both `ALLOW_HTTP_API=true` on the edge and request header
  `X-Mozhi-Allow-Http-Api: true`.
- External E2E through ECS Caddy -> FRP -> desktop FastAPI -> GitHub Issue ->
  JSONL task store was verified. Test Issues were later deleted after
  validation.

## Implementation Notes

- The worker should handle process failures, timeouts, and missing output paths
  as generation failures.
- The worker should write enough internal logs for diagnosis while keeping GitHub
  Issue comments concise.
- The worker should read queued records from `.tmp/api/tasks.jsonl` unless
  `MOZHI_TASK_STORE_PATH` overrides it.
- The task record should keep the path to the isolated runtime directory and any
  candidate output files needed by later QA. Worker-owned state may use
  additional files under `.tmp/worker/` as long as they are ignored by Git.
- The worker should be restart-safe enough that an interrupted task can be
  diagnosed and retried manually, even if automated retry is deferred.
- The implementation should inspect `git status` before any future commit step
  to ensure scratch files were not placed in this repository.
- Worker logs should distinguish application failures from ECS gateway or FRP
  tunnel failures. A tunnel outage affects public request ingress but should not
  be mistaken for a Codex generation failure.

## E2E Acceptance Test

### Preconditions

- Iterations 1 and 2 are complete.
- The public request enters through the Alibaba Cloud ECS gateway and reaches the
  home desktop API service.
- Codex CLI is installed on the desktop.
- `D:\Agent Repo\Mozhi-s-AgentWorkspace` exists and contains the required PPT
  generation capability.
- The worker can read queued tasks and update GitHub Issues.

### Steps

1. Submit a valid briefing request through `POST /api/briefings`.
2. Confirm the HTTP response has already completed with `202 Accepted`.
3. Start or wait for the worker.
4. Open the returned Issue URL.
5. Confirm the Issue receives a `running` milestone update.
6. Confirm the Issue receives a `generating` milestone update.
7. Confirm the Issue receives either `generation_completed` or a clear
   generation failure update.
8. Inspect the Service repository working tree.
9. Inspect the AgentWorkspace runtime area for task-specific generation traces.

### Expected Result

- The worker processes the task after the API has returned.
- The Issue shows visible progress without requiring access to local logs.
- Codex CLI runs against `Mozhi-s-AgentWorkspace`.
- The Service repository does not contain Codex runtime files, PPT scratch
  directories, or AgentWorkspace `.tmp` content.

## Risks & Diagnostics

- **Codex CLI missing:** fail with a clear Issue comment and internal log entry.
- **AgentWorkspace path missing:** fail before attempting generation.
- **Desktop offline after request queueing:** leave the task queued or mark it
  failed according to the chosen task-store recovery policy; do not diagnose it
  as an ECS failure unless the gateway is also unreachable.
- **Generation timeout:** mark the task failed with timeout context.
- **Unexpected output location:** record the observed output paths and fail the
  task if no candidate PPT can be found.
- **Repository pollution:** inspect `git status` and remove or ignore scratch
  paths before any archive step.

## Done Criteria

- Queued tasks are processed by a background worker.
- Issue milestone updates show running and generation progress.
- Codex CLI is invoked through the external AgentWorkspace.
- No AgentWorkspace skill implementation or scratch files are copied into this
  repository.
