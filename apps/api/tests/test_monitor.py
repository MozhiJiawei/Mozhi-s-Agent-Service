import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mozhi_api.monitor as monitor
from mozhi_api.main import Settings, create_app
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
