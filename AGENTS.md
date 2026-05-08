# AGENTS.md

This repository hosts the briefing generation service. Agents working in this
repository must keep service code, worker code, and archived outputs separated.

## Required Reading

Before changing code, directory structure, or repository conventions, read:

- `README.md`
- `docs/architecture-design.md`
- `docs/requirements/briefing-generation-api.md`

## Directory Boundaries

- Put HTTP API implementation under `apps/api/`.
- Put asynchronous worker implementation under `apps/worker/`.
- Put final archived briefing artifacts under `briefings/`.
- Put durable requirements and design notes under `docs/`.

## Artifact Rules

- Do not commit Codex, PPT generation, or skill runtime scratch files.
- Do not commit temporary `.tmp/` workspaces from `Mozhi-s-AgentWorkspace`.
- Archive only final curated artifacts, source snapshots, manifests, and QA
  summaries under `briefings/`.
- If large PPTX artifacts are committed later, prefer Git LFS and keep artifact
  metadata in a manifest so storage can migrate to object storage.

## External Capability Boundary

This repository must not copy or rewrite the `hw-ppt-gen` skill implementation.
PPT generation is provided by the separate `Mozhi-s-AgentWorkspace` repository,
which workers should invoke as an external dependency.

## Verification

Before committing future code changes, run the project's configured tests or
pre-commit gate once those commands exist. If no project-specific gate exists
yet, at minimum inspect `git status` and verify that only intended files are
included.
