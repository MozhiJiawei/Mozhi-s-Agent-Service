# Secret Material Handling

This repository must not store real tokens, private keys, API keys, SSH keys,
certificates, cloud credentials, or generated secret values.

## Principle

Keep secret material in fixed operator-managed locations outside the repository
on every machine that needs it.

Use the repository only for:

- Secret location documentation.
- `.env.example` files with placeholder values.
- Config templates that read values from environment variables or external
  files.
- Scripts that consume secret files from documented paths.

Do not use the repository for:

- Real `.env` files.
- Real token values.
- Private keys or certificates.
- Temporary generated credentials.
- Runtime scratch directories that contain secrets.

## Location Rules

Each runtime should have one documented secret directory per deployment surface.
For example:

```text
Windows operator machine:
  %USERPROFILE%\.mozhi-agent-service\<surface>\

Linux server:
  /etc/mozhi-agent-service/<surface>/
```

Secret files should be readable only by the operating-system user that runs the
service. On Linux servers, prefer `root:root` ownership and `600` file
permissions unless a dedicated service user is introduced.

## Current Secret And State Locations

Windows desktop API runtime:

```text
%USERPROFILE%\.mozhi-agent-service\api\
  api-token.txt
  github-token.txt
```

The desktop API start script reads `api-token.txt` and `github-token.txt` into
environment variables at process start. Explicit environment variables override
the files:

```text
MOZHI_API_TOKEN
GITHUB_TOKEN              optional when authenticated gh CLI is available
GITHUB_REPOSITORY          optional, defaults to MozhiJiawei/Mozhi-s-Agent-Service
MOZHI_ISSUE_LABEL          optional, defaults to agent-briefing
MOZHI_TASK_STORE_PATH      optional, defaults to <repo>\.tmp\api\tasks.jsonl
MOZHI_MAX_SOURCE_BYTES     optional, defaults to 1048576
```

The token files above are operator-managed storage locations outside the
repository. Do not copy them into Git, issue text, logs, or chat.

Runtime logs, temporary task stores, E2E outputs, generated request snapshots,
and other scratch files belong under the repository `.tmp/` directory, not under
`%USERPROFILE%\.mozhi-agent-service\api\`.

When `GITHUB_TOKEN` is not loaded into the API process, the desktop runtime may
use the authenticated GitHub CLI keyring instead. Verify it with:

```powershell
gh auth status
```

## Adding New Secrets

When adding a new token or key:

1. Add a placeholder to the relevant `.env.example` or template.
2. Document the real file path under this `docs/operations/` area.
3. Update scripts or Compose files to read from the documented external path.
4. Confirm `git status` does not include real secret files.
5. Rotate the secret if it was ever pasted into Git, chat, logs, or issue text.
