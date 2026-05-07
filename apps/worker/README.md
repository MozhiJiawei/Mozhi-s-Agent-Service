# Mozhi Agent Service Worker

The worker processes queued briefing requests after the API has returned the
GitHub Issue URL. It runs on the home desktop and calls the external
`Mozhi-s-AgentWorkspace` repository for PPT generation.

## Configure

The worker uses the same GitHub token source as the desktop API. The start
script loads `%USERPROFILE%\.mozhi-agent-service\api\github-token.txt` into
`GITHUB_TOKEN` when the environment variable is absent.

Optional settings:

```powershell
$env:GITHUB_REPOSITORY = "MozhiJiawei/Mozhi-s-Agent-Service"
$env:MOZHI_TASK_STORE_PATH = "<repo>\.tmp\api\tasks.jsonl"
$env:MOZHI_AGENT_WORKSPACE = "D:\Agent Repo\Mozhi-s-AgentWorkspace"
$env:MOZHI_CODEX_TIMEOUT_SECONDS = "7200"
$env:MOZHI_WORKER_POLL_SECONDS = "10"
$env:MOZHI_GIT_REMOTE = "origin"
$env:MOZHI_BASE_BRANCH = "main"
```

Runtime state, logs, and locks belong under `.tmp\worker\` and must not be
committed.

## Run

Process one queued task:

```powershell
.\scripts\worker\start-desktop-worker.ps1 -Once
```

Process one specific request:

```powershell
.\scripts\worker\start-desktop-worker.ps1 -Once -RequestId brf_...
```

Run continuously:

```powershell
.\scripts\worker\start-desktop-worker.ps1
```

## Controlled Atomic Capabilities

The worker exposes small, testable commands so external side effects remain
controlled:

```powershell
$env:PYTHONPATH = "apps\worker"
python -m mozhi_worker.cli claim-task <request_id>
python -m mozhi_worker.cli comment-issue-milestone <request_id> running claim-task
python -m mozhi_worker.cli record-generation <request_id> <handoff.json>
python -m mozhi_worker.cli run-qa <request_id> <handoff.json>
python -m mozhi_worker.cli record-qa-result <request_id> qa_passed --summary <qa-summary.md>
python -m mozhi_worker.cli archive-artifacts <request_id> <handoff.json> --qa-summary <qa-summary.md>
python -m mozhi_worker.cli complete-issue <request_id> <archive.json>
```

Codex generation is intentionally constrained to AgentWorkspace. It receives the
API body as `source.md`, reads AgentWorkspace `AGENTS.md`, uses the registered
skills there, and returns a structured `handoff.json`. It must not update
GitHub Issues, archive files into this repository, or push git branches.

The worker quality gate uses the `candidate_pptx` path from `handoff.json` and
passes when the AgentWorkspace PowerPoint COM export script renders slide PNGs
and a render manifest successfully. Skill-level QA JSON is useful evidence, but
the worker does not require a top-level `passed` field in that JSON.
