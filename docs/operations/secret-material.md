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

## Adding New Secrets

When adding a new token or key:

1. Add a placeholder to the relevant `.env.example` or template.
2. Document the real file path under this `docs/operations/` area.
3. Update scripts or Compose files to read from the documented external path.
4. Confirm `git status` does not include real secret files.
5. Rotate the secret if it was ever pasted into Git, chat, logs, or issue text.

