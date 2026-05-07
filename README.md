# Mozhi's Agent Service

This repository hosts the briefing generation service for internal automation.

The service accepts source material through an HTTP API, creates a GitHub Issue
as the caller-visible status page, runs an asynchronous worker to generate a
Huawei-style PPT deck, and archives the final artifacts back into this
repository.

## Repository Role

This repository is both the service repository and the archive repository for
briefing outputs.

It owns:

- API service code.
- Asynchronous worker code.
- GitHub Issue lifecycle updates.
- Task state and operational documentation.
- Final archived briefing artifacts.

It does not own:

- Codex skill implementations.
- Huawei PPT generation logic.
- Runtime scratch files created while Codex or a skill is generating a deck.

PPT generation capability comes from the separate `Mozhi-s-AgentWorkspace`
repository. Workers should call that workspace as an external dependency and
copy only final, curated artifacts back into this repository.

## Directory Layout

```text
.
|-- AGENTS.md
|-- README.md
|-- apps/
|   |-- api/
|   `-- worker/
|-- briefings/
`-- docs/
    `-- requirements/
```

- `apps/api/` contains the HTTP API service.
- `apps/worker/` contains asynchronous job execution code.
- `briefings/` contains final archived briefing artifacts.
- `docs/requirements/` contains product and architecture requirements.

## Current Stage

The first implementation stage initializes the repository and records the
aligned requirements for the briefing generation API. API and worker code will
be added in later implementation stages.

## Runtime Files And Secrets

Agents and operators must keep secret material separate from runtime scratch
files:

- Store API secrets only under
  `%USERPROFILE%\.mozhi-agent-service\api\`.
- Store local runtime scratch files, logs, temporary task stores, and E2E
  outputs under this repository's `.tmp/` directory.
- Do not put logs, JSONL task stores, generated source snapshots, or test
  outputs in `%USERPROFILE%\.mozhi-agent-service\api\`.
- Do not commit `.tmp/` contents or any files from
  `%USERPROFILE%\.mozhi-agent-service\api\`.

## Local Monitoring

Operators can open the desktop-only monitoring dashboard at
`http://127.0.0.1:8080/monitor` when the local API process is running.
Start it with `.\scripts\api\start-desktop-api.ps1 -Profile A`. The
FRP-backed edge entrypoint uses profile `B` on `0.0.0.0:18082`.

The monitor is read-only and local-only. It summarizes queued tasks, worker
state, recent terminal jobs, archive metadata, and local health checks without
calling GitHub or mutating task state. See
`docs/operations/monitoring-dashboard.md` for details and ECS exposure
guidance.

Current desktop API secret files:

```text
%USERPROFILE%\.mozhi-agent-service\api\
  api-token.txt
  github-token.txt
```
