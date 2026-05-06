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
- Accept the request shape defined in `docs/requirements/briefing-generation-api.md`.
- Validate required fields and supported `source_type` values.
- Generate a stable `request_id`.
- Persist the queued task in the chosen task store.
- Create a GitHub Issue in this repository.
- Write the initial Issue body with request metadata, source summary, request
  ID, and status `queued`.
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

## Implementation Notes

- Supported `source_type` values are `text`, `markdown`, `url`, and
  `pdf_reference`.
- The Issue title should be derived from the request title and include enough
  context to distinguish multiple requests.
- The Issue body should avoid dumping very large source content. It should show a
  bounded summary and store the full source in task state for worker use.
- The initial Issue should include a concise "Current status: queued" section.
- GitHub API failures should return a clear API error because the Issue URL is
  required for the external contract.
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
   `https://<domain>/api/briefings`.
2. Use `source_type: markdown` and a short Markdown source.
3. Confirm the HTTP response status is `202 Accepted`.
4. Confirm the response body contains `request_id`, `issue_url`, and
   `status: queued`.
5. Open the returned Issue URL in a browser.
6. Confirm the Issue body contains the request ID, title, requester, source
   summary, and queued status.

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
