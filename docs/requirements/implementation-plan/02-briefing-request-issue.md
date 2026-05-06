# Iteration 2: Briefing Request API and GitHub Issue

## Goal

Accept briefing generation requests through the public API and immediately return
a GitHub Issue URL as the caller-visible status page.

## External Acceptance Feature

A caller can submit:

```text
POST /api/briefings
```

and receive `202 Accepted` with a `request_id`, `issue_url`, and `status:
queued`.

## Scope

- Implement the `POST /api/briefings` route under `apps/api/`.
- Run the API service on the home desktop behind the ECS edge gateway established
  in Iteration 1.
- Accept arbitrary plain text request bodies as defined in
  `docs/requirements/briefing-generation-api.md`.
- Validate the bearer token, required `X-Mozhi-Title` header, `text/plain`
  content type, and non-empty body.
- Generate a stable `request_id`.
- Persist the queued task in the JSONL task store.
- Create a GitHub Issue in this repository.
- Write the initial Issue body with request metadata, source summary, request
  ID, and status `queued`.
- Apply the default `agent-briefing` label to generated Issues.
- Return quickly without running PPT generation in the HTTP request.

## Out of Scope

- Worker execution.
- Codex CLI invocation.
- PPT generation or QA.
- Final archive creation.
- Independent task status pages outside GitHub Issues.

## Key Decisions

- GitHub Issue is the public status page for the request.
- The API must return before generation starts.
- The public API is reached through the Alibaba Cloud ECS gateway, but the real
  API process still runs on the home desktop.
- The ECS instance should not store full request source content or run generation
  work in this iteration.
- Request validation errors should be returned synchronously.
- PPT generation errors cannot occur in this iteration because generation is not
  executed yet.
- The API response must not expose implementation-specific queue or storage
  details.
- The API should accept unstructured source material instead of requiring the
  caller to classify source types.

## Implementation Notes

- Iteration 1 infrastructure is currently available for HTTP-by-IP validation:
  `http://39.105.78.135/health` returns `200 OK` through the ECS Caddy edge,
  ECS `frps`, desktop `frpc`, and the desktop mock health service.
- Domain HTTPS remains pending because ICP备案 is not complete. Until then,
  external API validation should use the ECS public IP path instead of
  `https://<domain>`.
- Real FRP tokens and future service credentials must stay outside the
  repository, following `docs/operations/secret-material.md`.
  - Windows operator secrets are stored under
    `%USERPROFILE%\.mozhi-agent-service\edge\`.
  - ECS edge secrets are stored under `/etc/mozhi-agent-service/edge/.env`.
  - Desktop API runtime secrets and task state are stored under
    `%USERPROFILE%\.mozhi-agent-service\api\`.
- The next implementation should replace the mock desktop health service with
  the real API service behind the same FRP/Caddy path, without moving request
  handling or generation work onto the ECS instance.
- The request must use `Content-Type: text/plain; charset=utf-8`, bearer token
  auth, and a required `X-Mozhi-Title` header. Non-ASCII title values should be
  UTF-8 percent-encoded by callers and decoded by the API.
- The request body must be valid UTF-8 and stay within the configured maximum
  body size. The API default is 1 MiB and the ECS edge should enforce a matching
  `/api/*` request body limit.
- The Issue title should be derived from `X-Mozhi-Title` and include enough
  request ID context to distinguish multiple requests.
- Generated Issues should carry the default `agent-briefing` label. The label
  name may be overridden with `MOZHI_ISSUE_LABEL`.
- The Issue body should avoid dumping very large source content. It should show a
  bounded summary and store the full source in task state for worker use.
- Source previews must be embedded in GitHub Markdown without allowing submitted
  text to escape the preview code block.
- The initial Issue should include a concise "Current status: queued" section.
- GitHub API failures should return a clear API error because the Issue URL is
  required for the external contract.
- GitHub Issue creation may use `GITHUB_TOKEN` through the GitHub REST API, or
  fall back to the authenticated desktop `gh` CLI when `GITHUB_TOKEN` is not set.
  The CLI fallback should pass the Issue body through stdin rather than command
  arguments.
- If task persistence fails after Issue creation, the API should mark the Issue
  failed/closed before returning `task_store_failed`, so operators do not see an
  orphaned queued status page.
- Public `/api/*` traffic must use HTTPS because it carries bearer credentials.
  HTTP-by-IP validation remains acceptable for `/health` only while ICP/domain
  HTTPS is pending. A temporary public-IP HTTP E2E check may be enabled only
  with an explicit edge flag and a matching request header, using a short-lived
  validation token.
- Public routing should preserve enough request context for diagnosis, including
  method, path, status, and request ID, without logging full source content at
  the ECS gateway.

## E2E Acceptance Test

### Preconditions

- Iteration 1 is complete.
- The API service is running on the home desktop.
- The ECS Caddy and FRP stack routes `https://<domain>/api/briefings` to the
  desktop API service.
- The service has credentials to create Issues in this GitHub repository.
- The task store is available.

### Steps

1. From outside the home network, send a valid `POST /api/briefings` request to
   `https://<domain>/api/briefings` with bearer token auth,
   `Content-Type: text/plain; charset=utf-8`, and `X-Mozhi-Title`.
2. Use a short plain text body that may contain Markdown or source links.
3. Confirm the HTTP response status is `202 Accepted`.
4. Confirm the response body contains `request_id`, `issue_url`, and
   `status: queued`.
5. Open the returned Issue URL in a browser.
6. Confirm the Issue body contains the request ID, title, source preview, and
   queued status.

### Expected Result

- The API returns within a few seconds.
- A GitHub Issue exists and is readable by the requester.
- The Issue is the authoritative status page for the request.
- No PPT generation happens during the HTTP request.

## Risks & Diagnostics

- **Invalid request data:** return a validation response that identifies the
  invalid field.
- **ECS gateway routing failure:** verify Caddy route configuration, FRP tunnel
  status, and desktop API health before debugging application validation.
- **GitHub credentials missing or invalid:** fail the request before queueing an
  orphaned task.
- **Task is persisted but Issue creation fails:** avoid returning success unless
  the implementation has an explicit recovery path.
- **Large source content:** summarize in Issue while preserving full source in
  task state.
- **Duplicate submissions:** the plan does not require idempotency in this
  iteration; implementation may add it later if needed.

## Done Criteria

- A valid external request creates a GitHub Issue and returns its URL.
- The Issue starts in `queued`.
- Invalid requests are rejected before Issue creation.
- The API performs no synchronous generation work.

## Current Execution Result

- Implemented the FastAPI desktop API under `apps/api/`.
- Implemented JSONL task persistence with default path
  `%USERPROFILE%\.mozhi-agent-service\api\tasks.jsonl`.
- Updated ECS Caddy routing so `/health` and `/api/*` are forwarded through the
  desktop FRP tunnel, while `/api/*` rejects cleartext HTTP and enforces a body
  size limit.
- Verified automated API coverage with `python -m pytest` from `apps/api`.
- Ran a real local E2E submission through the API using authenticated `gh` CLI
  Issue creation.
  - Request ID: `brf_20260506132518_4078d5`
  - GitHub Issue:
    `https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/1`
  - Returned status: `queued`
  - Confirmed the JSONL task store preserved the full source text.
- Ran a real external E2E submission through ECS Caddy, FRP, desktop FastAPI,
  GitHub Issue creation, and JSONL task persistence.
  - Request ID: `brf_20260506140307_af0507`
  - GitHub Issue:
    `https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/4`
  - Returned status: `queued`
