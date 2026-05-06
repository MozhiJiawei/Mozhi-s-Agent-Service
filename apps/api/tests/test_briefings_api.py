import json
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mozhi_api.main import (
    GhCliIssueClient,
    GitHubIssueClient,
    GitHubIssueCreateError,
    Settings,
    create_app,
    default_issue_client,
)


class FakeIssueClient:
    def __init__(self):
        self.created_issues = []
        self.failed_issues = []

    def create_issue(self, title, body):
        self.created_issues.append({"title": title, "body": body})
        return type(
            "Issue",
            (),
            {
                "number": 123,
                "html_url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/123",
            },
        )()

    def mark_issue_failed(self, issue_number, message):
        self.failed_issues.append({"issue_number": issue_number, "message": message})


class FailingIssueClient:
    def create_issue(self, title, body):
        from mozhi_api.main import GitHubIssueCreateError

        raise GitHubIssueCreateError("boom")


class FakeTaskStore:
    def __init__(self):
        self.tasks = []

    def append(self, task):
        self.tasks.append(task)


class FailingTaskStore:
    def append(self, task):
        raise OSError("disk full")


def build_client(issue_client=None, task_store=None):
    settings = Settings(
        api_token="secret-token",
        github_token="github-token",
        github_repository="MozhiJiawei/Mozhi-s-Agent-Service",
        issue_label="agent-briefing",
        task_store_path=Path("unused.jsonl"),
        max_source_bytes=1024 * 1024,
    )
    issue_client = issue_client or FakeIssueClient()
    task_store = task_store or FakeTaskStore()
    app = create_app(settings=settings, issue_client=issue_client, task_store=task_store)
    return TestClient(app), issue_client, task_store


def test_health_returns_service_status():
    client, _, _ = build_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "mozhi-agent-service-api"
    assert response.json()["status"] == "ok"


def test_default_issue_client_uses_gh_cli_when_github_token_is_missing():
    client = default_issue_client(None, "MozhiJiawei/Mozhi-s-Agent-Service", "agent-briefing")

    assert isinstance(client, GhCliIssueClient)
    assert client.issue_label == "agent-briefing"


def test_default_issue_client_uses_rest_when_github_token_is_present():
    client = default_issue_client("token", "MozhiJiawei/Mozhi-s-Agent-Service", "agent-briefing")

    assert isinstance(client, GitHubIssueClient)
    assert client.token == "token"
    assert client.repository == "MozhiJiawei/Mozhi-s-Agent-Service"
    assert client.issue_label == "agent-briefing"


def test_valid_plain_text_request_creates_issue_and_queues_task():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Mozhi-Title": urllib.parse.quote("AI Agent 商业化进展汇报"),
        },
        content="任意材料\nhttps://example.com/report",
    )

    assert response.status_code == 202
    assert response.json() == {
        "request_id": task_store.tasks[0]["request_id"],
        "issue_url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/123",
        "status": "queued",
    }
    assert task_store.tasks[0]["status"] == "queued"
    assert task_store.tasks[0]["title"] == "AI Agent 商业化进展汇报"
    assert task_store.tasks[0]["source_text"] == "任意材料\nhttps://example.com/report"
    assert task_store.tasks[0]["issue"] == {
        "number": 123,
        "url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/123",
        "repository": "MozhiJiawei/Mozhi-s-Agent-Service",
    }
    suffix = task_store.tasks[0]["request_id"].rsplit("_", 1)[-1]
    assert issue_client.created_issues[0]["title"] == f"AI Agent 商业化进展汇报 ({suffix})"
    assert issue_client.created_issues[0]["body"].count("任意材料") == 1


def test_request_ids_and_issue_times_use_utc_plus_8(monkeypatch):
    fixed_time = datetime.fromisoformat("2026-05-06T22:30:00+08:00")

    monkeypatch.setattr("mozhi_api.main.now_service_time", lambda: fixed_time)
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Mozhi-Title": "Title",
        },
        content="source",
    )

    assert response.status_code == 202
    assert task_store.tasks[0]["request_id"].startswith("brf_20260506223000_")
    assert task_store.tasks[0]["created_at"] == "2026-05-06T22:30:00+08:00"
    assert "| Created at | `2026-05-06T22:30:00+08:00` |" in issue_client.created_issues[0]["body"]


def test_missing_token_is_unauthorized():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={"Content-Type": "text/plain", "X-Mozhi-Title": "Title"},
        content="source",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_wrong_token_is_unauthorized():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer wrong",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content="source",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_missing_title_is_rejected_before_issue_creation():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={"Authorization": "Bearer secret-token", "Content-Type": "text/plain"},
        content="source",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_title"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_empty_body_is_rejected_before_issue_creation():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content="   \n\t",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_body"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_json_request_is_rejected():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "application/json",
            "X-Mozhi-Title": "Title",
        },
        content=json.dumps({"source_content": "source"}),
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_github_failure_does_not_queue_task():
    client, _, task_store = build_client(issue_client=FailingIssueClient())

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content="source",
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "github_issue_create_failed"
    assert task_store.tasks == []


def test_task_store_failure_returns_error_after_issue_creation():
    issue_client = FakeIssueClient()
    client, _, _ = build_client(
        issue_client=issue_client,
        task_store=FailingTaskStore(),
    )

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content="source",
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "task_store_failed"
    assert len(issue_client.created_issues) == 1
    assert issue_client.failed_issues[0]["issue_number"] == 123
    assert "was not queued" in issue_client.failed_issues[0]["message"]


def test_long_source_is_truncated_in_issue_but_preserved_in_task_store():
    client, issue_client, task_store = build_client()
    source = "A" * 1300

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content=source,
    )

    assert response.status_code == 202
    assert task_store.tasks[0]["source_text"] == source
    assert "[truncated]" in issue_client.created_issues[0]["body"]
    assert "A" * 1300 not in issue_client.created_issues[0]["body"]


def test_rejects_non_utf8_charset():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain; charset=utf-16",
            "X-Mozhi-Title": "Title",
        },
        content="source",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_charset"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_rejects_invalid_utf8_body():
    client, issue_client, task_store = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Mozhi-Title": "Title",
        },
        content=b"\xff",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_utf8"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_rejects_oversized_body():
    settings = Settings(
        api_token="secret-token",
        github_token="github-token",
        github_repository="MozhiJiawei/Mozhi-s-Agent-Service",
        issue_label="agent-briefing",
        task_store_path=Path("unused.jsonl"),
        max_source_bytes=5,
    )
    issue_client = FakeIssueClient()
    task_store = FakeTaskStore()
    client = TestClient(
        create_app(settings=settings, issue_client=issue_client, task_store=task_store)
    )

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Mozhi-Title": "Title",
        },
        content="too large",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"
    assert issue_client.created_issues == []
    assert task_store.tasks == []


def test_source_preview_uses_longer_fence_when_source_contains_backticks():
    client, issue_client, _ = build_client()

    response = client.post(
        "/api/briefings",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Type": "text/plain",
            "X-Mozhi-Title": "Title",
        },
        content="```spoofed status```",
    )

    assert response.status_code == 202
    assert "````text\n```spoofed status```\n````" in issue_client.created_issues[0]["body"]


def test_gh_cli_create_issue_uses_stdin_body_file(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/321\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    issue = GhCliIssueClient(
        "MozhiJiawei/Mozhi-s-Agent-Service", "agent-briefing"
    ).create_issue(
        "Title",
        "sensitive body",
    )

    assert issue.number == 321
    assert calls[0]["args"][0] == [
        "gh",
        "issue",
        "create",
        "--repo",
        "MozhiJiawei/Mozhi-s-Agent-Service",
        "--title",
        "Title",
        "--label",
        "agent-briefing",
        "--body-file",
        "-",
    ]
    assert calls[0]["kwargs"]["input"] == "sensitive body"


def test_gh_cli_create_issue_rejects_malformed_output(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="not a url\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GitHubIssueCreateError, match="did not return an Issue URL"):
        GhCliIssueClient(
            "MozhiJiawei/Mozhi-s-Agent-Service", "agent-briefing"
        ).create_issue("Title", "body")
