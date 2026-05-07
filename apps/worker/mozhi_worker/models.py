from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "qa_failed"}
IN_PROGRESS_STATUSES = {
    "running",
    "generating",
    "generation_completed",
    "qa_passed",
    "publishing",
}


@dataclass(frozen=True)
class IssueRef:
    number: int
    url: str
    repository: str


@dataclass(frozen=True)
class Task:
    request_id: str
    status: str
    title: str
    source_text: str
    created_at: str
    issue: IssueRef

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Task":
        issue = data["issue"]
        return cls(
            request_id=str(data["request_id"]),
            status=str(data.get("status", "queued")),
            title=str(data["title"]),
            source_text=str(data["source_text"]),
            created_at=str(data["created_at"]),
            issue=IssueRef(
                number=int(issue["number"]),
                url=str(issue["url"]),
                repository=str(issue.get("repository", "")),
            ),
        )


@dataclass
class GenerationRecord:
    candidate_pptx: Path
    qa_summary: Path
    qa_json: Path | None = None
    slides_dir: Path | None = None
    deck_workspace: Path | None = None
    status: str = "generation_completed"
    log_summary: str = ""


@dataclass
class QaResult:
    passed: bool
    summary_path: Path
    report_path: Path | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ArchiveResult:
    archive_dir: Path
    branch_name: str
    manifest_path: Path
    pptx_path: Path
    qa_summary_path: Path
    source_path: Path
    links: dict[str, str]


class WorkerError(RuntimeError):
    status = "failed"
    retryable = True
    stage = "worker"


class QaFailedError(WorkerError):
    status = "qa_failed"
    retryable = True
    stage = "qa"
