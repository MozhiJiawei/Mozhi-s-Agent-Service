from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import ArchiveResult, QaResult, Task


class IssueUpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class IssueMilestone:
    status: str
    stage: str
    body: str


class IssueClient:
    def add_comment(self, repository: str, issue_number: int, body: str) -> None:
        raise NotImplementedError


class GitHubApiIssueClient(IssueClient):
    def __init__(self, token: str) -> None:
        self.token = token

    def add_comment(self, repository: str, issue_number: int, body: str) -> None:
        url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments"
        request = urllib.request.Request(
            url,
            data=json.dumps({"body": body}).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "mozhi-agent-service-worker",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20):
                return
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise IssueUpdateError(f"GitHub API returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IssueUpdateError(f"GitHub API request failed: {exc}") from exc


class GhCliIssueClient(IssueClient):
    def add_comment(self, repository: str, issue_number: int, body: str) -> None:
        try:
            subprocess.run(
                ["gh", "issue", "comment", str(issue_number), "--repo", repository, "--body", body],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise IssueUpdateError("gh CLI is not installed.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise IssueUpdateError(f"gh issue comment failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise IssueUpdateError("gh issue comment timed out.") from exc


class FakeIssueClient(IssueClient):
    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []

    def add_comment(self, repository: str, issue_number: int, body: str) -> None:
        self.comments.append(
            {"repository": repository, "issue_number": issue_number, "body": body}
        )


def default_issue_client(token: str | None) -> IssueClient:
    if token:
        return GitHubApiIssueClient(token)
    return GhCliIssueClient()


def milestone_body(status: str, request_id: str, stage: str, details: str = "") -> str:
    lines = [
        f"## Worker status: `{status}`",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Request ID | `{request_id}` |",
        f"| Stage | `{stage}` |",
    ]
    if details.strip():
        lines.extend(["", details.strip()])
    return "\n".join(lines)


def comment_issue_milestone(
    issue_client: IssueClient,
    task: Task,
    status: str,
    stage: str,
    details: str = "",
) -> IssueMilestone:
    allowed = {
        "running",
        "generating",
        "generation_completed",
        "qa_passed",
        "qa_failed",
        "publishing",
        "completed",
        "failed",
    }
    if status not in allowed:
        raise ValueError(f"Unsupported issue milestone: {status}")
    body = milestone_body(status, task.request_id, stage, details)
    issue_client.add_comment(task.issue.repository, task.issue.number, body)
    return IssueMilestone(status=status, stage=stage, body=body)


def qa_failure_details(result: QaResult) -> str:
    reason = result.reason or "QA rejected the generated PPT candidate."
    return "\n".join(
        [
            f"Failure stage: `{result.report_path or result.summary_path}`",
            f"Reason: {reason}",
            "Retryability: retryable after correcting source material or rerunning generation.",
            "Suggested next action: review the QA summary and rerun the worker for this request.",
        ]
    )


def completed_details(result: ArchiveResult) -> str:
    return "\n".join(
        [
            "Final briefing artifacts are available on the delivery branch.",
            "",
            f"- Branch: `{result.branch_name}`",
            f"- Archive: {result.links['archive']}",
            f"- Source: {result.links['source']}",
            f"- PPTX: {result.links['pptx']}",
            f"- Manifest: {result.links['manifest']}",
            f"- QA summary: {result.links['qa_summary']}",
        ]
    )
