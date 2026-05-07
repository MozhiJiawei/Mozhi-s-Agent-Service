from __future__ import annotations

import json
import ipaddress
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
from fastapi.responses import HTMLResponse, JSONResponse


SERVICE_NAME = "mozhi-agent-service-api"
SERVICE_VERSION = "0.2.0"
DEFAULT_REPOSITORY = "MozhiJiawei/Mozhi-s-Agent-Service"
DEFAULT_ISSUE_LABEL = "agent-briefing"
SOURCE_PREVIEW_CHARS = 60000
DEFAULT_MAX_SOURCE_BYTES = 1024 * 1024
SERVICE_TIMEZONE = timezone(timedelta(hours=8))
IN_PROGRESS_STATUSES = {
    "running",
    "generating",
    "generation_completed",
    "qa_passed",
    "publishing",
}
DEFAULT_MONITOR_STALE_AFTER_MINUTES = 30
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

    def delete_issue(self, issue_number: int) -> None:
        if not self.token:
            raise GitHubIssueCreateError("GITHUB_TOKEN is not configured.")

        owner, name = split_repository(self.repository)
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              id
            }
          }
        }
        """
        data = self._send_graphql(
            query,
            {"owner": owner, "name": name, "number": issue_number},
        )
        issue = data.get("repository", {}).get("issue") if isinstance(data, dict) else None
        issue_id = issue.get("id") if isinstance(issue, dict) else None
        if not issue_id:
            raise GitHubIssueCreateError(f"GitHub Issue #{issue_number} was not found.")

        mutation = """
        mutation($id: ID!) {
          deleteIssue(input: {issueId: $id}) {
            clientMutationId
          }
        }
        """
        self._send_graphql(mutation, {"id": issue_id})

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

    def _send_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "mozhi-agent-service-api",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubIssueCreateError(
                f"GitHub GraphQL returned {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubIssueCreateError(f"GitHub GraphQL request failed: {exc}") from exc

        if payload.get("errors"):
            raise GitHubIssueCreateError(f"GitHub GraphQL returned errors: {payload['errors']}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GitHubIssueCreateError("GitHub GraphQL response was missing data.")
        return data


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

    def delete_issue(self, issue_number: int) -> None:
        try:
            subprocess.run(
                [
                    "gh",
                    "issue",
                    "delete",
                    str(issue_number),
                    "--repo",
                    self.repository,
                    "--yes",
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
            raise GitHubIssueCreateError(f"gh issue delete failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubIssueCreateError("gh issue delete timed out.") from exc


class JsonlTaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, task: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n"
        with _task_store_lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line)

    def get(self, request_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with _task_store_lock:
            with self.path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(data.get("request_id") or "") == request_id:
                        return data
        return None

    def delete(self, request_id: str) -> dict[str, Any]:
        if not self.path.exists():
            raise ValueError("Task store does not exist.")

        removed: dict[str, Any] | None = None
        kept_lines: list[str] = []
        with _task_store_lock:
            with self.path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        kept_lines.append(line)
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        kept_lines.append(line)
                        continue
                    if str(data.get("request_id") or "") != request_id:
                        kept_lines.append(line)
                        continue
                    removed = data

            if removed is None:
                raise ValueError("Task was not found.")

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8", newline="\n") as file:
                file.writelines(kept_lines)

        return removed


def default_task_store_path() -> str:
    return str(default_runtime_root() / "api" / "tasks.jsonl")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_runtime_root() -> Path:
    return repo_root() / ".runtime"


def default_worker_state_dir() -> Path:
    return default_runtime_root() / "worker" / "state"


def default_worker_log_dir() -> Path:
    return default_runtime_root() / "worker" / "logs"


class WorkerLauncher:
    def __init__(self, root: Path) -> None:
        self.root = root

    def running(self) -> list[dict[str, Any]]:
        if os.name == "nt":
            return self.running_windows()
        return []

    def launch(self, action: str, request_id: str | None = None) -> dict[str, Any]:
        command = self.command_for(action, request_id)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "apps" / "worker")
        log_dir = Path(os.environ.get("MOZHI_WORKER_LOG_DIR", default_worker_log_dir()))
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_service_time().strftime("%Y%m%d-%H%M%S")
        stdout_path = log_dir / f"monitor-worker-{action}-{stamp}.out.log"
        stderr_path = log_dir / f"monitor-worker-{action}-{stamp}.err.log"
        stdout = stdout_path.open("w", encoding="utf-8")
        stderr = stderr_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root / "apps" / "worker",
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
        finally:
            stdout.close()
            stderr.close()
        return {
            "pid": process.pid,
            "action": action,
            "request_id": request_id,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }

    def command_for(self, action: str, request_id: str | None) -> list[str]:
        base = ["python", "-m", "mozhi_worker.cli", "run"]
        if action == "once":
            if not request_id:
                raise ValueError("request_id is required for action `once`.")
            return [*base, "--once", "--request-id", request_id]
        if action == "drain":
            return [*base, "--drain"]
        if action == "forever":
            return base
        raise ValueError("action must be one of: once, drain, forever.")

    def stop(self, pid: int) -> dict[str, Any]:
        running = {process["pid"]: process for process in self.running()}
        if pid not in running:
            raise ValueError(f"Worker process is not running or is not monitor-managed: {pid}")
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr.strip() or completed.stdout.strip())
        else:
            os.kill(pid, 15)
        return {"pid": pid, "status": "stopped", "process": running[pid]}

    def running_windows(self) -> list[dict[str, Any]]:
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match 'mozhi_worker\\.cli' -and $_.CommandLine -match ' run( |$)' } | "
            "Select-Object ProcessId,CommandLine,CreationDate | ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return []
        records = raw if isinstance(raw, list) else [raw]
        return [
            self.describe_process(record)
            for record in records
            if isinstance(record, dict) and record.get("ProcessId")
        ]

    def describe_process(self, record: dict[str, Any]) -> dict[str, Any]:
        command_line = str(record.get("CommandLine") or "")
        action = "forever"
        if "--once" in command_line:
            action = "once"
        elif "--drain" in command_line:
            action = "drain"
        request_id = None
        match = re.search(r"--request-id\s+([^\s]+)", command_line)
        if match:
            request_id = match.group(1)
        return {
            "pid": int(record["ProcessId"]),
            "action": action,
            "request_id": request_id,
            "command": command_line,
            "started_at": str(record.get("CreationDate") or ""),
        }


def create_app(
    settings: Settings | None = None,
    issue_client: GitHubIssueClient | GhCliIssueClient | None = None,
    task_store: JsonlTaskStore | None = None,
    worker_launcher: WorkerLauncher | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_issue_client = issue_client or default_issue_client(
        resolved_settings.github_token,
        resolved_settings.github_repository,
        resolved_settings.issue_label,
    )
    resolved_task_store = task_store or JsonlTaskStore(resolved_settings.task_store_path)
    resolved_worker_launcher = worker_launcher or WorkerLauncher(repo_root())

    app = FastAPI(title="Mozhi Agent Service API", version=SERVICE_VERSION)
    app.state.settings = resolved_settings
    app.state.issue_client = resolved_issue_client
    app.state.task_store = resolved_task_store
    app.state.worker_launcher = resolved_worker_launcher

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "status": "ok",
            "server_time": now_service_time().isoformat(),
        }

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor_page(request: Request) -> HTMLResponse:
        if not is_local_request(request):
            return HTMLResponse("Forbidden", status_code=403)
        from .monitor import monitor_html

        return HTMLResponse(monitor_html())

    @app.get("/api/monitor/state")
    def monitor_state(request: Request) -> JSONResponse:
        if not is_local_request(request):
            return error_response(403, "forbidden", "Monitor routes are local-only.")
        from .monitor import build_monitor_snapshot

        snapshot = build_monitor_snapshot(resolved_settings)
        snapshot["workers"] = {"running": resolved_worker_launcher.running()}
        return JSONResponse(snapshot)

    @app.post("/api/monitor/worker/start")
    async def monitor_worker_start(request: Request) -> JSONResponse:
        if not is_local_request(request):
            return error_response(403, "forbidden", "Monitor routes are local-only.")
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return error_response(400, "invalid_json", "Request body must be JSON.")

        action = str(payload.get("action") or "")
        request_id = str(payload.get("request_id") or "").strip() or None
        if action == "once" and request_id and is_task_currently_running(
            request_id,
            resolved_worker_launcher,
        ):
            return error_response(
                409,
                "task_currently_running",
                "This task is currently running and cannot be started again.",
            )
        try:
            launched = resolved_worker_launcher.launch(action, request_id)
        except ValueError as exc:
            return error_response(400, "invalid_worker_action", str(exc))
        except OSError as exc:
            return error_response(500, "worker_launch_failed", str(exc))
        return JSONResponse({"status": "started", **launched})

    @app.post("/api/monitor/worker/stop")
    async def monitor_worker_stop(request: Request) -> JSONResponse:
        if not is_local_request(request):
            return error_response(403, "forbidden", "Monitor routes are local-only.")
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return error_response(400, "invalid_json", "Request body must be JSON.")

        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return error_response(400, "invalid_worker_pid", "pid must be an integer.")
        try:
            stopped = resolved_worker_launcher.stop(pid)
        except ValueError as exc:
            return error_response(400, "invalid_worker_pid", str(exc))
        except OSError as exc:
            return error_response(500, "worker_stop_failed", str(exc))
        return JSONResponse(stopped)

    @app.post("/api/monitor/tasks/delete")
    async def monitor_task_delete(request: Request) -> JSONResponse:
        if not is_local_request(request):
            return error_response(403, "forbidden", "Monitor routes are local-only.")
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return error_response(400, "invalid_json", "Request body must be JSON.")

        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return error_response(400, "missing_request_id", "request_id is required.")

        try:
            task = resolved_task_store.get(request_id)
        except (OSError, json.JSONDecodeError) as exc:
            return error_response(500, "task_store_read_failed", str(exc))
        if not task:
            return error_response(404, "task_not_found", "Task was not found.")

        state_dir = Path(
            os.environ.get("MOZHI_WORKER_STATE_DIR", default_worker_state_dir())
        )
        state_path = state_dir / f"{request_id}.json"
        state = read_worker_state(state_path)
        if is_task_currently_running(request_id, resolved_worker_launcher, state):
            return error_response(
                409,
                "task_currently_running",
                "This task is currently running and cannot be cleaned up.",
            )

        issue = task.get("issue") if isinstance(task.get("issue"), dict) else {}
        try:
            issue_number = int(issue.get("number"))
        except (TypeError, ValueError):
            return error_response(500, "task_issue_missing", "Task record is missing an Issue number.")

        try:
            resolved_issue_client.delete_issue(issue_number)
        except GitHubIssueCreateError as exc:
            return error_response(
                502,
                "github_issue_delete_failed",
                f"Failed to delete GitHub Issue: {exc}",
            )

        try:
            deleted = resolved_task_store.delete(request_id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return error_response(409, "task_delete_failed", str(exc))
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            return error_response(500, "task_state_delete_failed", str(exc))

        return JSONResponse(
            {
                "status": "deleted",
                "request_id": request_id,
                "issue_number": issue_number,
                "title": deleted.get("title"),
            }
        )


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


def split_repository(repository: str) -> tuple[str, str]:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name:
        raise GitHubIssueCreateError(
            f"GitHub repository must be in owner/name form: {repository}"
        )
    return owner, name


def read_worker_state(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def is_currently_running_state(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    status = str(state.get("status") or "")
    if status not in IN_PROGRESS_STATUSES:
        return False
    updated = parse_datetime(str(state.get("updated_at") or state.get("claimed_at") or ""))
    if not updated:
        return True
    stale_after = timedelta(minutes=DEFAULT_MONITOR_STALE_AFTER_MINUTES)
    return now_service_time() - updated <= stale_after


def is_task_currently_running(
    request_id: str,
    worker_launcher: WorkerLauncher,
    state: dict[str, Any] | None = None,
) -> bool:
    active_request_ids = {
        str(process.get("request_id"))
        for process in worker_launcher.running()
        if process.get("request_id")
    }
    if request_id in active_request_ids:
        return True
    if state is None:
        state_dir = Path(
            os.environ.get("MOZHI_WORKER_STATE_DIR", default_worker_state_dir())
        )
        state = read_worker_state(state_dir / f"{request_id}.json")
    return is_currently_running_state(state)


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SERVICE_TIMEZONE)
    return parsed.astimezone(SERVICE_TIMEZONE)


def default_issue_client(
    github_token: str | None,
    github_repository: str,
    issue_label: str,
) -> GitHubIssueClient | GhCliIssueClient:
    if github_token:
        return GitHubIssueClient(github_token, github_repository, issue_label)
    return GhCliIssueClient(github_repository, issue_label)


def is_local_request(request: Request) -> bool:
    if not request.client:
        return False
    host = request.client.host
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


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
    stripped = raw_title.strip()
    try:
        stripped = stripped.encode("latin-1").decode("utf-8")
    except UnicodeError:
        pass
    return urllib.parse.unquote(stripped, encoding="utf-8", errors="replace").strip()


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
