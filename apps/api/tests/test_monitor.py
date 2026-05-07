import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mozhi_api.monitor as monitor
from mozhi_api.main import JsonlTaskStore, Settings, WorkerLauncher, create_app
from mozhi_api.monitor import MonitorPaths, build_monitor_snapshot, monitor_html


NOW = datetime(2026, 5, 7, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        api_token="secret-token",
        github_token="github-token",
        github_repository="MozhiJiawei/Mozhi-s-Agent-Service",
        issue_label="agent-briefing",
        task_store_path=tmp_path / "api" / "tasks.jsonl",
        max_source_bytes=1024 * 1024,
    )


def paths_for(tmp_path: Path, settings: Settings) -> MonitorPaths:
    return MonitorPaths(
        task_store_path=settings.task_store_path,
        state_dir=tmp_path / "worker" / "state",
        api_log_dir=tmp_path / "api" / "logs",
        worker_log_dir=tmp_path / "worker" / "logs",
        agent_workspace=tmp_path / "AgentWorkspace",
        repo_root=tmp_path,
        public_base_url="http://127.0.0.1:9",
        frpc_container_name="mozhi-ecs-frpc-test",
        edge_container_name="mozhi-agent-service-edge",
        ecs_ssh_target=None,
    )


@pytest.fixture(autouse=True)
def stub_external_health_checks(monkeypatch):
    monkeypatch.setattr(
        monitor,
        "frpc_container_check",
        lambda container_name: {
            "id": "frpc_client",
            "label": "反向代理客户端 frpc",
            "status": "pass",
            "message": f"{container_name} Up",
        },
    )
    monkeypatch.setattr(
        monitor,
        "public_edge_health_check",
        lambda public_base_url: {
            "id": "public_edge_health",
            "label": "ECS 公网健康入口",
            "status": "pass",
            "message": f"{public_base_url}/health HTTP 200",
        },
    )
    monkeypatch.setattr(
        monitor,
        "ecs_edge_process_check",
        lambda ssh_target, container_name: {
            "id": "ecs_edge_processes",
            "label": "ECS 反向代理服务端",
            "status": "warn",
            "message": "未配置 SSH 探测",
        },
    )


def write_jsonl(path: Path, *records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def task_record(request_id="brf_20260507093000_abc123", title="AI briefing") -> dict:
    return {
        "request_id": request_id,
        "status": "queued",
        "title": title,
        "source_text": "source text",
        "created_at": "2026-05-07T09:30:00+08:00",
        "issue": {
            "number": 12,
            "url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/12",
            "repository": "MozhiJiawei/Mozhi-s-Agent-Service",
        },
    }


def prepare_paths(paths: MonitorPaths) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.api_log_dir.mkdir(parents=True, exist_ok=True)
    paths.worker_log_dir.mkdir(parents=True, exist_ok=True)
    paths.agent_workspace.mkdir(parents=True, exist_ok=True)
    (paths.repo_root / "briefings").mkdir(parents=True, exist_ok=True)
    (paths.repo_root / ".gitattributes").write_text(
        "briefings/**/*.pptx filter=lfs diff=lfs merge=lfs -text\n",
        encoding="utf-8",
    )


def test_snapshot_merges_queued_task_with_worker_state(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())
    (paths.state_dir / "brf_20260507093000_abc123.json").write_text(
        json.dumps(
            {
                "request_id": "brf_20260507093000_abc123",
                "status": "generating",
                "title": "AI briefing",
                "created_at": "2026-05-07T09:30:00+08:00",
                "updated_at": "2026-05-07T09:45:00+08:00",
                "runtime_dir": str(tmp_path / "runtime"),
                "issue": {
                    "number": 12,
                    "url": "https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/12",
                    "repository": "MozhiJiawei/Mozhi-s-Agent-Service",
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["statistics"]["counts_by_status"]["generating"] == 1
    assert snapshot["tasks"]["current"][0]["status"] == "generating"
    assert snapshot["tasks"]["current"][0]["issue"]["number"] == 12


def test_completed_state_contributes_to_success_rate_and_recent_terminal_jobs(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())
    (paths.state_dir / "brf_20260507093000_abc123.json").write_text(
        json.dumps(
            {
                "request_id": "brf_20260507093000_abc123",
                "status": "completed",
                "title": "AI briefing",
                "created_at": "2026-05-07T09:00:00+08:00",
                "updated_at": "2026-05-07T09:30:00+08:00",
                "archive_path": "briefings/2026/05/issue-12-ai-briefing",
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["statistics"]["counts_by_status"]["completed"] == 1
    assert snapshot["statistics"]["success_rate"] == 1
    assert snapshot["statistics"]["average_completed_duration_seconds"] == 1800
    assert snapshot["tasks"]["recent_terminal"][0]["archive_path"].endswith("ai-briefing")


def test_malformed_jsonl_line_is_reported_without_failing_snapshot(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    settings.task_store_path.parent.mkdir(parents=True, exist_ok=True)
    settings.task_store_path.write_text(
        json.dumps(task_record(), ensure_ascii=False) + "\n{not json\n",
        encoding="utf-8",
    )

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["statistics"]["total_tasks"] == 1
    assert any("Malformed task store line 2" in item["message"] for item in snapshot["diagnostics"])


def test_missing_task_store_degrades_health_without_server_error(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["statistics"]["total_tasks"] == 0
    assert any(check["id"] == "task_store" and check["status"] == "warn" for check in snapshot["health"])


def test_old_in_progress_state_is_marked_stale(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    (paths.state_dir / "brf_20260507080000_old.json").write_text(
        json.dumps(
            {
                "request_id": "brf_20260507080000_old",
                "status": "running",
                "title": "Old task",
                "created_at": "2026-05-07T08:00:00+08:00",
                "updated_at": "2026-05-07T08:20:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["tasks"]["current"][0]["is_stale"] is True
    assert snapshot["statistics"]["stale_tasks"] == 1


def test_malformed_state_file_appears_in_diagnostics(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())
    (paths.state_dir / "broken.json").write_text("{broken", encoding="utf-8")

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert snapshot["statistics"]["total_tasks"] == 1
    assert any("Malformed worker state file broken.json" in item["message"] for item in snapshot["diagnostics"])


def test_gitattributes_lfs_rule_marks_health_as_passing(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    assert any(check["id"] == "git_lfs" and check["status"] == "pass" for check in snapshot["health"])


def test_snapshot_includes_edge_and_reverse_proxy_health_checks(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    health_ids = {check["id"] for check in snapshot["health"]}
    assert {"frpc_client", "public_edge_health", "ecs_edge_processes"} <= health_ids
    assert snapshot["paths"]["public_base_url"] == "http://127.0.0.1:9"
    assert snapshot["paths"]["frpc_container"] == "mozhi-ecs-frpc-test"


def test_monitor_html_contains_polling_endpoint_and_root():
    html = monitor_html()

    assert 'id="monitor-root"' in html
    assert 'const endpoint = "/api/monitor/state"' in html
    assert 'data-tab="overview"' in html
    assert 'data-tab="current"' in html
    assert 'data-tab="health"' in html
    assert 'data-tab="history"' in html
    assert "Mozhi 本地监控大屏" in html
    assert "系统健康" in html
    assert "历史结果" in html
    assert 'id="health-tabs"' in html
    assert "本地状态" in html
    assert "边缘链路" in html
    assert "启动/再次运行" in html
    assert "删除选中任务" in html
    assert "清理全部 Pending" in html
    assert "长期启动 Worker" in html
    assert "停止 Worker" in html


def test_monitor_routes_allow_local_clients(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    write_jsonl(settings.task_store_path, task_record())
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(paths.state_dir))
    monkeypatch.setenv("MOZHI_API_LOG_DIR", str(paths.api_log_dir))
    monkeypatch.setenv("MOZHI_WORKER_LOG_DIR", str(paths.worker_log_dir))
    monkeypatch.setenv("MOZHI_AGENT_WORKSPACE", str(paths.agent_workspace))
    app = create_app(settings=settings)
    client = TestClient(app, client=("127.0.0.1", 50000))

    html_response = client.get("/monitor")
    state_response = client.get("/api/monitor/state")

    assert html_response.status_code == 200
    assert "Mozhi Monitor" in html_response.text
    assert state_response.status_code == 200
    assert {"statistics", "health", "tasks"} <= state_response.json().keys()


def test_monitor_routes_reject_non_loopback_clients(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings=settings)
    client = TestClient(app, client=("203.0.113.9", 50000))

    assert client.get("/monitor").status_code == 403
    response = client.get("/api/monitor/state")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


class FakeWorkerLauncher:
    def __init__(self):
        self.calls = []
        self.stops = []

    def launch(self, action, request_id=None):
        self.calls.append({"action": action, "request_id": request_id})
        if action == "once" and not request_id:
            raise ValueError("request_id is required for action `once`.")
        return {
            "pid": 4321,
            "action": action,
            "request_id": request_id,
            "stdout_log": "out.log",
            "stderr_log": "err.log",
        }

    def running(self):
        return [
            {
                "pid": 4321,
                "action": "forever",
                "request_id": None,
                "command": "python -m mozhi_worker.cli run",
                "started_at": "20260507170000.000000+480",
            }
        ]

    def stop(self, pid):
        self.stops.append(pid)
        if pid != 4321:
            raise ValueError("Worker process is not running or is not monitor-managed.")
        return {"pid": pid, "status": "stopped"}


class FakeIssueClient:
    def __init__(self):
        self.deleted = []

    def create_issue(self, title, body):
        raise AssertionError("create_issue should not be called")

    def mark_issue_failed(self, issue_number, message):
        raise AssertionError("mark_issue_failed should not be called")

    def delete_issue(self, issue_number):
        self.deleted.append(issue_number)


def test_monitor_worker_start_launches_selected_task_locally(tmp_path):
    settings = settings_for(tmp_path)
    launcher = FakeWorkerLauncher()
    app = create_app(settings=settings, worker_launcher=launcher)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/monitor/worker/start",
        json={"action": "once", "request_id": "brf_20260507093000_abc123"},
    )

    assert response.status_code == 200
    assert response.json()["pid"] == 4321
    assert launcher.calls == [
        {"action": "once", "request_id": "brf_20260507093000_abc123"}
    ]


def test_monitor_state_includes_running_workers(tmp_path):
    settings = settings_for(tmp_path)
    launcher = FakeWorkerLauncher()
    app = create_app(settings=settings, worker_launcher=launcher)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/monitor/state")

    assert response.status_code == 200
    assert response.json()["workers"]["running"][0]["pid"] == 4321


def test_worker_launcher_lists_only_repo_local_registered_workers(tmp_path, monkeypatch):
    root = tmp_path / "service-a"
    other_root = tmp_path / "service-b"
    launcher = WorkerLauncher(root)
    launcher.write_registry(
        pid=111,
        action="forever",
        request_id=None,
        command=["python", "-m", "mozhi_worker.cli", "run"],
        stdout_log=root / ".tmp" / "worker" / "logs" / "out.log",
        stderr_log=root / ".tmp" / "worker" / "logs" / "err.log",
    )
    launcher.registry_dir.mkdir(parents=True, exist_ok=True)
    (launcher.registry_dir / "222.json").write_text(
        json.dumps(
            {
                "pid": 222,
                "action": "forever",
                "request_id": None,
                "command": "python -m mozhi_worker.cli run",
                "repo_root": str(other_root),
                "started_at": "2026-05-07T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "pid_is_running", lambda pid: True)

    running = launcher.running()

    assert [process["pid"] for process in running] == [111]


def test_worker_launcher_prunes_stale_registered_workers(tmp_path, monkeypatch):
    launcher = WorkerLauncher(tmp_path)
    launcher.write_registry(
        pid=333,
        action="once",
        request_id="brf_20260507093000_abc123",
        command=["python", "-m", "mozhi_worker.cli", "run", "--once"],
        stdout_log=tmp_path / ".tmp" / "worker" / "logs" / "out.log",
        stderr_log=tmp_path / ".tmp" / "worker" / "logs" / "err.log",
    )
    monkeypatch.setattr(launcher, "pid_is_running", lambda pid: False)

    assert launcher.running() == []
    assert not launcher.registry_path(333).exists()


def test_monitor_worker_start_supports_drain_and_forever(tmp_path):
    settings = settings_for(tmp_path)
    launcher = FakeWorkerLauncher()
    app = create_app(settings=settings, worker_launcher=launcher)
    client = TestClient(app, client=("127.0.0.1", 50000))

    drain = client.post("/api/monitor/worker/start", json={"action": "drain"})
    forever = client.post("/api/monitor/worker/start", json={"action": "forever"})

    assert drain.status_code == 200
    assert forever.status_code == 200
    assert launcher.calls == [
        {"action": "drain", "request_id": None},
        {"action": "forever", "request_id": None},
    ]


def test_monitor_worker_start_rejects_missing_request_id(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings=settings, worker_launcher=FakeWorkerLauncher())
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post("/api/monitor/worker/start", json={"action": "once"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_worker_action"


def test_monitor_worker_start_rejects_non_loopback_clients(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings=settings, worker_launcher=FakeWorkerLauncher())
    client = TestClient(app, client=("203.0.113.9", 50000))

    response = client.post("/api/monitor/worker/start", json={"action": "drain"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_monitor_worker_start_rejects_currently_running_task(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    request_id = "brf_20260507111111_running"
    (paths.state_dir / f"{request_id}.json").write_text(
        json.dumps(
            {
                "request_id": request_id,
                "status": "generating",
                "updated_at": "2099-01-01T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(paths.state_dir))
    launcher = FakeWorkerLauncher()
    app = create_app(settings=settings, worker_launcher=launcher)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/monitor/worker/start",
        json={"action": "once", "request_id": request_id},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_currently_running"
    assert launcher.calls == []


def test_monitor_worker_stop_stops_worker_locally(tmp_path):
    settings = settings_for(tmp_path)
    launcher = FakeWorkerLauncher()
    app = create_app(settings=settings, worker_launcher=launcher)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post("/api/monitor/worker/stop", json={"pid": 4321})

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert launcher.stops == [4321]


def test_monitor_worker_stop_rejects_unknown_pid(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings=settings, worker_launcher=FakeWorkerLauncher())
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post("/api/monitor/worker/stop", json={"pid": 9876})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_worker_pid"


def test_monitor_worker_stop_rejects_non_loopback_clients(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings=settings, worker_launcher=FakeWorkerLauncher())
    client = TestClient(app, client=("203.0.113.9", 50000))

    response = client.post("/api/monitor/worker/stop", json={"pid": 4321})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_monitor_task_delete_removes_queued_task_and_issue_locally(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    record = task_record(request_id="brf_20260507111111_delete")
    write_jsonl(settings.task_store_path, record)
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(paths.state_dir))
    issue_client = FakeIssueClient()
    app = create_app(
        settings=settings,
        issue_client=issue_client,
        task_store=JsonlTaskStore(settings.task_store_path),
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/monitor/tasks/delete",
        json={"request_id": "brf_20260507111111_delete"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert issue_client.deleted == [12]
    assert settings.task_store_path.read_text(encoding="utf-8") == ""


def test_monitor_task_delete_rejects_currently_running_task_with_worker_state(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    request_id = "brf_20260507111111_started"
    write_jsonl(settings.task_store_path, task_record(request_id=request_id))
    (paths.state_dir / f"{request_id}.json").write_text(
        json.dumps(
            {
                "request_id": request_id,
                "status": "running",
                "updated_at": "2099-01-01T00:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(paths.state_dir))
    issue_client = FakeIssueClient()
    app = create_app(
        settings=settings,
        issue_client=issue_client,
        task_store=JsonlTaskStore(settings.task_store_path),
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post("/api/monitor/tasks/delete", json={"request_id": request_id})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_currently_running"
    assert issue_client.deleted == []


def test_monitor_task_delete_allows_stale_in_progress_task(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    request_id = "brf_20260507111111_stale"
    record = task_record(request_id=request_id)
    write_jsonl(settings.task_store_path, record)
    (paths.state_dir / f"{request_id}.json").write_text(
        json.dumps(
            {
                "request_id": request_id,
                "status": "generating",
                "updated_at": "2026-05-07T08:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(paths.state_dir))
    issue_client = FakeIssueClient()
    app = create_app(
        settings=settings,
        issue_client=issue_client,
        task_store=JsonlTaskStore(settings.task_store_path),
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/monitor/tasks/delete",
        json={"request_id": request_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert issue_client.deleted == [12]
    assert not (paths.state_dir / f"{request_id}.json").exists()


def test_monitor_task_delete_rejects_non_loopback_clients(tmp_path):
    settings = settings_for(tmp_path)
    issue_client = FakeIssueClient()
    app = create_app(settings=settings, issue_client=issue_client)
    client = TestClient(app, client=("203.0.113.9", 50000))

    response = client.post(
        "/api/monitor/tasks/delete",
        json={"request_id": "brf_20260507111111_delete"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert issue_client.deleted == []


def test_monitor_snapshot_truncates_long_titles_and_source_previews(tmp_path):
    settings = settings_for(tmp_path)
    paths = paths_for(tmp_path, settings)
    prepare_paths(paths)
    record = task_record(title="T" * 320)
    record["source_text"] = "A" * 500
    write_jsonl(settings.task_store_path, record)

    snapshot = build_monitor_snapshot(settings, paths=paths, now=NOW)

    task = snapshot["tasks"]["current"][0]
    assert len(task["title"]) < 180
    assert task["title"].endswith("...")
    assert len(task["source_preview"]) < 260
    assert task["source_preview"].endswith("...")
