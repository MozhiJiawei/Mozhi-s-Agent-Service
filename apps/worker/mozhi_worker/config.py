from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_REPOSITORY = "MozhiJiawei/Mozhi-s-Agent-Service"
DEFAULT_AGENT_WORKSPACE = r"D:\Agent Repo\Mozhi-s-AgentWorkspace"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_task_store_path() -> Path:
    return repo_root() / ".tmp" / "api" / "tasks.jsonl"


@dataclass(frozen=True)
class WorkerSettings:
    repository: str
    github_token: str | None
    task_store_path: Path
    state_dir: Path
    log_dir: Path
    agent_workspace: Path
    codex_model: str
    codex_timeout_seconds: int
    poll_interval_seconds: int
    git_remote: str
    base_branch: str

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        root = repo_root()
        return cls(
            repository=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
            github_token=os.environ.get("GITHUB_TOKEN"),
            task_store_path=Path(
                os.environ.get("MOZHI_TASK_STORE_PATH", default_task_store_path())
            ),
            state_dir=Path(os.environ.get("MOZHI_WORKER_STATE_DIR", root / ".tmp" / "worker" / "state")),
            log_dir=Path(os.environ.get("MOZHI_WORKER_LOG_DIR", root / ".tmp" / "worker" / "logs")),
            agent_workspace=Path(
                os.environ.get("MOZHI_AGENT_WORKSPACE", DEFAULT_AGENT_WORKSPACE)
            ),
            codex_model=os.environ.get("MOZHI_CODEX_MODEL", "gpt-5.5"),
            codex_timeout_seconds=int(os.environ.get("MOZHI_CODEX_TIMEOUT_SECONDS", "7200")),
            poll_interval_seconds=int(os.environ.get("MOZHI_WORKER_POLL_SECONDS", "10")),
            git_remote=os.environ.get("MOZHI_GIT_REMOTE", "origin"),
            base_branch=os.environ.get("MOZHI_BASE_BRANCH", "main"),
        )
