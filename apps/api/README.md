# Mozhi Agent Service API

This app runs on the home desktop behind the ECS Caddy/FRP edge gateway.

## Install

```powershell
cd apps\api
python -m pip install -r requirements-dev.txt
```

## Configure

Store runtime secrets outside the repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.mozhi-agent-service\api" | Out-Null
Set-Content -NoNewline "$env:USERPROFILE\.mozhi-agent-service\api\api-token.txt" "<internal-api-token>"
Set-Content -NoNewline "$env:USERPROFILE\.mozhi-agent-service\api\github-token.txt" "<github-token-with-issues-write-access>"
```

`start-desktop-api.ps1` loads `api-token.txt` and `github-token.txt` at process
start. Environment variables with the same names still override the files.

If `github-token.txt` and `GITHUB_TOKEN` are both absent, the API falls back to
the authenticated GitHub CLI on the desktop (`gh issue create`). Run
`gh auth status` to confirm the active account can create Issues in this
repository.

Optional settings:

```powershell
$env:GITHUB_REPOSITORY = "MozhiJiawei/Mozhi-s-Agent-Service"
$env:MOZHI_ISSUE_LABEL = "agent-briefing"
$env:MOZHI_TASK_STORE_PATH = "<repo>\.tmp\api\tasks.jsonl"
$env:MOZHI_MAX_SOURCE_BYTES = "1048576"
```

Runtime logs, temporary JSONL task stores, and E2E outputs belong under the
repository `.tmp\api\` directory. `%USERPROFILE%\.mozhi-agent-service\api\`
should contain secret files only.

## Run

```powershell
cd ..\..
.\scripts\api\start-desktop-api.ps1
```

The service listens on `127.0.0.1:8080`.

For the current FRP-backed ECS route, run it on the tunnel target port:

```powershell
$env:MOZHI_API_HOST = "0.0.0.0"
$env:MOZHI_API_PORT = "18082"
.\scripts\api\start-desktop-api.ps1
```

## Submit A Briefing

`X-Mozhi-Title` is required. Non-ASCII titles should be UTF-8 percent-encoded.
The request body must be valid UTF-8 and is limited to 1 MiB by default.

```powershell
$title = [uri]::EscapeDataString("AI Agent 商业化进展汇报")
$body = @"
请基于下面材料生成一份华为风格 briefing PPT。

材料：
1. ...
2. ...
"@

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/briefings" `
  -Headers @{
    Authorization = "Bearer $env:MOZHI_API_TOKEN"
    "X-Mozhi-Title" = $title
  } `
  -ContentType "text/plain; charset=utf-8" `
  -Body $body
```

## Verified E2E

A real local API submission was verified through the authenticated GitHub CLI
fallback:

```text
Request ID: brf_20260506132518_4078d5
Issue URL: https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/1
Status: queued
```
