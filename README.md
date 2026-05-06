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
