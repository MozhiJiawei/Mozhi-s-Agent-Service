# Briefing Generation API Requirements

## Summary

`Mozhi-s-Agent-Service` provides an internal automation API for generating
briefing decks from source material.

The API receives source content, immediately creates and returns a GitHub Issue
link, then completes PPT generation asynchronously. The GitHub Issue is the
caller-visible status page for the request.

This repository is both the service repository and the final artifact archive.
`Mozhi-s-AgentWorkspace` remains a separate Agent skills workspace and is used
only as an external worker dependency.

## Service Positioning

- The first stage targets internal automation.
- The API must return quickly and must not run PPT generation synchronously in
  the HTTP request.
- GitHub Issues are used for request tracking, status updates, and final result
  links.
- The system should favor an auditable, reliable workflow over a public
  multi-tenant API surface.

## Repository Relationships

- `Mozhi-s-Agent-Service` owns API service code, worker code, GitHub Issue
  lifecycle updates, and final artifact archives.
- `Mozhi-s-AgentWorkspace` owns Agent skills and PPT generation capability.
- Workers may call `Mozhi-s-AgentWorkspace`, but this repository must not copy
  or rewrite the `hw-ppt-gen` skill.
- Intermediate Codex and PPT generation files belong in the workspace runtime
  directory, not in this repository.

## Core Flow

1. A caller submits source material to `POST /api/briefings`.
2. The service validates the request and creates a GitHub Issue in this
   repository.
3. The service records task state and returns `202 Accepted` with the Issue URL.
4. An asynchronous worker picks up the queued task.
5. The worker updates the Issue to `running`.
6. The worker calls Codex with `Mozhi-s-AgentWorkspace` to generate a Huawei
   style PPT deck.
7. The worker runs the required PPT generation QA and collects final artifacts.
8. The worker archives final artifacts under
   `briefings/YYYY/MM/issue-<number>-<slug>/`.
9. The worker refreshes the Issue with final status, artifact links, QA summary,
   and failure details if any step fails.

## Archive Layout

Final artifacts should use this layout:

```text
briefings/
  YYYY/
    MM/
      issue-<number>-<slug>/
        source.md
        brief.pptx
        manifest.json
        qa-summary.md
```

The archive should contain final curated outputs only:

- Source snapshot.
- Final PPTX.
- Artifact manifest.
- QA summary.

Do not archive full temporary workspaces, generated scripts, slide PNG scratch
sets, or other intermediate files unless they are explicitly selected as final
QA evidence.

## Storage Strategy

Version 1 may store PPTX files with Git LFS.

Git LFS must be treated as a storage backend, not as the business contract. Each
briefing archive must include `manifest.json` so future migration to object
storage can update metadata and links without changing API semantics.

The API, worker, and GitHub Issue text should rely on stable artifact metadata:

- `artifact_id`
- `kind`
- `logical_path`
- `storage_backend`
- `sha256`
- `size_bytes`
- `download_url`
- Future object storage fields such as `object_key`

Example manifest:

```json
{
  "request_id": "brf_...",
  "issue_number": 123,
  "artifacts": [
    {
      "artifact_id": "pptx-main",
      "kind": "pptx",
      "logical_path": "briefings/2026/05/issue-123-ai-weekly-brief/brief.pptx",
      "storage_backend": "git_lfs",
      "sha256": "...",
      "size_bytes": 12345678,
      "download_url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/..."
    }
  ]
}
```

## Minimal API Contract

Endpoint:

```text
POST /api/briefings
```

Request:

```http
POST /api/briefings
Authorization: Bearer <MOZHI_API_TOKEN>
Content-Type: text/plain; charset=utf-8
X-Mozhi-Title: AI%20Agent%20%E5%95%86%E4%B8%9A%E5%8C%96%E8%BF%9B%E5%B1%95%E6%B1%87%E6%8A%A5
```

The request body is arbitrary plain text. It may contain a prompt, Markdown,
meeting notes, URLs, PDF references, or mixed source material. The API should
not require callers to classify the source before submission.

`X-Mozhi-Title` is required. ASCII titles may be sent directly. Non-ASCII
titles, such as Chinese titles, should be UTF-8 percent-encoded because many
HTTP clients reject raw non-ASCII header values.

The request body must be valid UTF-8 and is limited to 1 MiB by default. The
desktop API can override this with `MOZHI_MAX_SOURCE_BYTES`; the ECS edge should
keep a matching `/api/*` request body limit.

Example body:

```text
请基于下面材料生成一份华为风格 briefing PPT。

材料：
1. ...
2. ...
```

Response:

```json
{
  "request_id": "brf_...",
  "issue_url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/123",
  "status": "queued"
}
```

Error response:

```json
{
  "error": {
    "code": "missing_title",
    "message": "X-Mozhi-Title header is required."
  }
}
```

Common error codes include `unauthorized`, `missing_title`, `empty_body`,
`unsupported_media_type`, `unsupported_charset`, `invalid_utf8`,
`body_too_large`, `github_issue_create_failed`, and `task_store_failed`.

## Issue Status Model

The Issue should be refreshed as the job moves through these states:

- `queued`
- `running`
- `qa_failed`
- `completed`
- `failed`

Issue updates should include:

- Current status.
- Request ID.
- Worker stage.
- Archive path or commit link when available.
- Artifact links when available.
- QA summary when available.
- Failure reason and retry information on failure.

All Issues created by this service should carry the `agent-briefing` label by
default so generated briefing requests can be filtered and managed separately
from human-authored repository Issues.

## Current Implementation Status

Iteration 1 established the ECS edge gateway and desktop health routing.

Iteration 2 implements the first real API slice:

- `GET /health` is served by the FastAPI desktop API.
- `POST /api/briefings` accepts authenticated `text/plain` submissions.
- The API creates a GitHub Issue as the caller-visible status page.
- The API stores the queued task in a local JSONL task store outside the
  repository.

The service still does not implement worker execution, Codex CLI invocation, PPT
generation, QA, final archive creation, Git LFS configuration, or object storage
support.

## Future Acceptance Criteria

- `POST /api/briefings` creates a GitHub Issue and returns its URL.
- PPT generation happens asynchronously.
- Worker status changes are reflected in the Issue.
- Successful jobs archive final artifacts under `briefings/`.
- Every archived job includes a `manifest.json`.
- Git LFS can later be replaced by object storage without changing the public
  API response shape.
