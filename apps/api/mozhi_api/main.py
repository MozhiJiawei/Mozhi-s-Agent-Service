from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


SERVICE_NAME = "mozhi-agent-service-api"
SERVICE_VERSION = "0.2.0"
DEFAULT_REPOSITORY = "MozhiJiawei/Mozhi-s-Agent-Service"
DEFAULT_ISSUE_LABEL = "agent-briefing"
SOURCE_PREVIEW_CHARS = 1200
DEFAULT_MAX_SOURCE_BYTES = 1024 * 1024
SERVICE_TIMEZONE = timezone(timedelta(hours=8))
_task_store_lock = threading.Lock()


@dataclass(frozen=True)
class Settings:
    api_token: str | None
    github_token: str | None
    github_repository: str
    issue_label: str
    task_store_path: Path
    max_source_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_token=os.environ.get("MOZHI_API_TOKEN"),
            github_token=os.environ.get("GITHUB_TOKEN"),
            github_repository=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
            issue_label=os.environ.get("MOZHI_ISSUE_LABEL", DEFAULT_ISSUE_LABEL),
            task_store_path=Path(
                os.environ.get("MOZHI_TASK_STORE_PATH", default_task_store_path())
            ),
            max_source_bytes=int(
                os.environ.get("MOZHI_MAX_SOURCE_BYTES", DEFAULT_MAX_SOURCE_BYTES)
            ),
        )


@dataclass(frozen=True)
class Issue:
    number: int
    html_url: str


class GitHubIssueCreateError(RuntimeError):
    pass


class GitHubIssueClient:
    def __init__(self, token: str | None, repository: str, issue_label: str) -> None:
        self.token = token
        self.repository = repository
        self.issue_label = issue_label

    def create_issue(self, title: str, body: str) -> Issue:
        if not self.token:
            raise GitHubIssueCreateError("GITHUB_TOKEN is not configured.")

        url = f"https://api.github.com/repos/{self.repository}/issues"
        payload = json.dumps(
            {"title": title, "body": body, "labels": [self.issue_label]}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "mozhi-agent-service-api",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubIssueCreateError(
                f"GitHub API returned {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubIssueCreateError(f"GitHub API request failed: {exc}") from exc

        try:
            return Issue(number=int(data["number"]), html_url=str(data["html_url"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubIssueCreateError("GitHub API response was missing issue data.") from exc

    def mark_issue_failed(self, issue_number: int, message: str) -> None:
        if not self.token:
            raise GitHubIssueCreateError("GITHUB_TOKEN is not configured.")

        comment_url = f"https://api.github.com/repos/{self.repository}/issues/{issue_number}/comments"
        self._send_json(comment_url, {"body": message}, method="POST")

        issue_url = f"https://api.github.com/repos/{self.repository}/issues/{issue_number}"
        self._send_json(issue_url, {"state": "closed", "state_reason": "not_planned"}, method="PATCH")

    def _send_json(self, url: str, payload: dict[str, Any], method: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "mozhi-agent-service-api",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubIssueCreateError(
                f"GitHub API returned {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubIssueCreateError(f"GitHub API request failed: {exc}") from exc


class GhCliIssueClient:
    def __init__(self, repository: str, issue_label: str) -> None:
        self.repository = repository
        self.issue_label = issue_label

    def create_issue(self, title: str, body: str) -> Issue:
        try:
            result = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    self.repository,
                    "--title",
                    title,
                    "--label",
                    self.issue_label,
                    "--body-file",
                    "-",
                ],
                check=True,
                capture_output=True,
                input=body,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise GitHubIssueCreateError(
                "GITHUB_TOKEN is not configured and gh CLI is not installed."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise GitHubIssueCreateError(f"gh issue create failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubIssueCreateError("gh issue create timed out.") from exc

        issue_url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        match = re.search(r"/issues/(\d+)$", issue_url)
        if not issue_url or not match:
            raise GitHubIssueCreateError("gh issue create did not return an Issue URL.")
        return Issue(number=int(match.group(1)), html_url=issue_url)

    def mark_issue_failed(self, issue_number: int, message: str) -> None:
        try:
            subprocess.run(
                [
                    "gh",
                    "issue",
                    "close",
                    str(issue_number),
                    "--repo",
                    self.repository,
                    "--reason",
                    "not planned",
                    "--comment",
                    message,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise GitHubIssueCreateError("gh CLI is not installed.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise GitHubIssueCreateError(f"gh issue close failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubIssueCreateError("gh issue close timed out.") from exc


class JsonlTaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, task: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n"
        with _task_store_lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line)


def default_task_store_path() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return str(repo_root / ".tmp" / "api" / "tasks.jsonl")


def create_app(
    settings: Settings | None = None,
    issue_client: GitHubIssueClient | GhCliIssueClient | None = None,
    task_store: JsonlTaskStore | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_issue_client = issue_client or default_issue_client(
        resolved_settings.github_token,
        resolved_settings.github_repository,
        resolved_settings.issue_label,
    )
    resolved_task_store = task_store or JsonlTaskStore(resolved_settings.task_store_path)

    app = FastAPI(title="Mozhi Agent Service API", version=SERVICE_VERSION)
    app.state.settings = resolved_settings
    app.state.issue_client = resolved_issue_client
    app.state.task_store = resolved_task_store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "status": "ok",
            "server_time": now_service_time().isoformat(),
        }

    @app.post("/api/briefings", status_code=202)
    async def create_briefing(request: Request) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        if not is_authorized(authorization, resolved_settings.api_token):
            return error_response(401, "unauthorized", "A valid bearer token is required.")

        content_type = request.headers.get("content-type", "")
        media_type, charset = parse_content_type(content_type)
        if media_type != "text/plain":
            return error_response(
                415,
                "unsupported_media_type",
                "Content-Type must be text/plain; charset=utf-8.",
            )
        if charset and charset.lower() not in {"utf-8", "utf8"}:
            return error_response(
                415,
                "unsupported_charset",
                "Content-Type charset must be utf-8.",
            )

        title = parse_header_title(request.headers.get("x-mozhi-title", ""))
        if not title:
            return error_response(400, "missing_title", "X-Mozhi-Title header is required.")

        try:
            source_text = await read_text_body(request, resolved_settings.max_source_bytes)
        except BodyTooLargeError:
            return error_response(
                413,
                "body_too_large",
                f"Request body must be at most {resolved_settings.max_source_bytes} bytes.",
            )
        except UnicodeDecodeError:
            return error_response(400, "invalid_utf8", "Request body must be valid UTF-8.")
        if not source_text.strip():
            return error_response(400, "empty_body", "Request body must not be empty.")

        request_id = generate_request_id()
        created_at = now_service_time().isoformat()
        issue_title = format_issue_title(title, request_id)
        issue_body = format_issue_body(
            request_id=request_id,
            title=title,
            created_at=created_at,
            source_text=source_text,
        )

        try:
            issue = resolved_issue_client.create_issue(issue_title, issue_body)
        except GitHubIssueCreateError as exc:
            return error_response(
                502,
                "github_issue_create_failed",
                f"Failed to create GitHub Issue: {exc}",
            )

        task = {
            "request_id": request_id,
            "status": "queued",
            "title": title,
            "source_text": source_text,
            "created_at": created_at,
            "issue": {
                "number": issue.number,
                "url": issue.html_url,
                "repository": resolved_settings.github_repository,
            },
        }

        try:
            resolved_task_store.append(task)
        except OSError as exc:
            mark_issue_failed(
                resolved_issue_client,
                issue.number,
                f"Task store persistence failed after Issue creation. Request ID `{request_id}` was not queued.\n\nError: `{exc}`",
            )
            return error_response(
                500,
                "task_store_failed",
                f"Failed to persist queued task: {exc}",
            )

        return JSONResponse(
            status_code=202,
            content={
                "request_id": request_id,
                "issue_url": issue.html_url,
                "status": "queued",
            },
        )

    return app


class BodyTooLargeError(RuntimeError):
    pass


async def read_text_body(request: Request, max_source_bytes: int) -> str:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_source_bytes:
                raise BodyTooLargeError
        except ValueError:
            raise BodyTooLargeError

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > max_source_bytes:
            raise BodyTooLargeError
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="strict")


def parse_content_type(content_type: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in content_type.split(";") if part.strip()]
    media_type = parts[0].lower() if parts else ""
    charset = None
    for parameter in parts[1:]:
        name, separator, value = parameter.partition("=")
        if separator and name.strip().lower() == "charset":
            charset = value.strip().strip('"')
    return media_type, charset


def mark_issue_failed(
    issue_client: GitHubIssueClient | GhCliIssueClient,
    issue_number: int,
    message: str,
) -> None:
    try:
        issue_client.mark_issue_failed(issue_number, message)
    except GitHubIssueCreateError:
        pass


def default_issue_client(
    github_token: str | None,
    github_repository: str,
    issue_label: str,
) -> GitHubIssueClient | GhCliIssueClient:
    if github_token:
        return GitHubIssueClient(github_token, github_repository, issue_label)
    return GhCliIssueClient(github_repository, issue_label)


def is_authorized(authorization: str, configured_token: str | None) -> bool:
    if not configured_token:
        return False
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    supplied_token = authorization[len(prefix) :]
    return secrets.compare_digest(supplied_token, configured_token)


def generate_request_id() -> str:
    timestamp = now_service_time().strftime("%Y%m%d%H%M%S")
    return f"brf_{timestamp}_{secrets.token_hex(3)}"


def now_service_time() -> datetime:
    return datetime.now(SERVICE_TIMEZONE)


def format_issue_title(title: str, request_id: str) -> str:
    suffix = request_id.rsplit("_", 1)[-1]
    return f"{title} ({suffix})"


def parse_header_title(raw_title: str) -> str:
    return urllib.parse.unquote(raw_title.strip(), encoding="utf-8", errors="replace").strip()


def format_issue_body(
    request_id: str,
    title: str,
    created_at: str,
    source_text: str,
) -> str:
    preview = source_preview(source_text)
    fence = markdown_fence_for(preview)
    return "\n".join(
        [
            "## Current status: queued",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Request ID | `{request_id}` |",
            f"| Title | {escape_table_cell(title)} |",
            f"| Created at | `{created_at}` |",
            "",
            "## Source preview",
            "",
            f"{fence}text",
            preview,
            fence,
            "",
            "Generation has not started yet. A worker will update this Issue when later iterations add execution support.",
        ]
    )


def source_preview(source_text: str) -> str:
    normalized = source_text.strip()
    if len(normalized) <= SOURCE_PREVIEW_CHARS:
        return normalized
    return f"{normalized[:SOURCE_PREVIEW_CHARS].rstrip()}\n\n[truncated]"


def markdown_fence_for(text: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return fence


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


app = create_app()
