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
- Final QA policy.
- Final archive creation.
- Fine-grained streaming of every Codex tool call into the Issue.

## Key Decisions

- Worker execution is asynchronous and decoupled from the HTTP request.
- `Mozhi-s-AgentWorkspace` is an external dependency, not a subtree or copied
  module of this repository.
- Runtime scratch files belong outside this repository.
- Issue updates are milestone-level to keep the status page readable.
- Generation completion means Codex produced candidate outputs; it does not mean
  the job is completed or archived.

## Implementation Notes

- The worker should handle process failures, timeouts, and missing output paths
  as generation failures.
- The worker should write enough internal logs for diagnosis while keeping GitHub
  Issue comments concise.
- The task record should keep the path to the isolated runtime directory and any
  candidate output files needed by later QA.
- The worker should be restart-safe enough that an interrupted task can be
  diagnosed and retried manually, even if automated retry is deferred.
- The implementation should inspect `git status` before any future commit step
  to ensure scratch files were not placed in this repository.

## E2E Acceptance Test

### Preconditions

- Iterations 1 and 2 are complete.
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

