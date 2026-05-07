from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .main import Settings


SERVICE_TIMEZONE = timezone(timedelta(hours=8))
TERMINAL_STATUSES = {"completed", "failed", "qa_failed"}
IN_PROGRESS_STATUSES = {
    "running",
    "generating",
    "generation_completed",
    "qa_passed",
    "publishing",
}
CURRENT_STATUSES = {"queued"} | IN_PROGRESS_STATUSES
ALL_KNOWN_STATUSES = CURRENT_STATUSES | TERMINAL_STATUSES
DEFAULT_STALE_AFTER_MINUTES = 30
SOURCE_PREVIEW_CHARS = 240
TITLE_CHARS = 160
@dataclass(frozen=True)
class MonitorPaths:
    task_store_path: Path
    state_dir: Path
    api_log_dir: Path
    worker_log_dir: Path
    agent_workspace: Path
    repo_root: Path
    public_base_url: str
    frpc_container_name: str
    edge_container_name: str
    ecs_ssh_target: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> "MonitorPaths":
        root = repo_root()
        return cls(
            task_store_path=settings.task_store_path,
            state_dir=Path(
                os.environ.get("MOZHI_WORKER_STATE_DIR", root / ".tmp" / "worker" / "state")
            ),
            api_log_dir=Path(os.environ.get("MOZHI_API_LOG_DIR", root / ".tmp" / "api" / "logs")),
            worker_log_dir=Path(
                os.environ.get("MOZHI_WORKER_LOG_DIR", root / ".tmp" / "worker" / "logs")
            ),
            agent_workspace=Path(
                os.environ.get(
                    "MOZHI_AGENT_WORKSPACE",
                    r"D:\Agent Repo\Mozhi-s-AgentWorkspace",
                )
            ),
            repo_root=root,
            public_base_url=os.environ.get("MOZHI_PUBLIC_BASE_URL", "http://39.105.78.135"),
            frpc_container_name=os.environ.get("MOZHI_FRPC_CONTAINER_NAME", "mozhi-ecs-frpc-test"),
            edge_container_name=os.environ.get("MOZHI_EDGE_CONTAINER_NAME", "mozhi-agent-service-edge"),
            ecs_ssh_target=os.environ.get("MOZHI_ECS_SSH_TARGET", "root@39.105.78.135"),
        )


def build_monitor_snapshot(
    settings: Settings,
    *,
    paths: MonitorPaths | None = None,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=DEFAULT_STALE_AFTER_MINUTES),
) -> dict[str, Any]:
    resolved_paths = paths or MonitorPaths.from_settings(settings)
    resolved_now = now or datetime.now(SERVICE_TIMEZONE)
    diagnostics: list[dict[str, str]] = []

    task_records = read_task_records(resolved_paths.task_store_path, diagnostics)
    state_records = read_state_records(resolved_paths.state_dir, diagnostics)
    merged_tasks = merge_tasks(task_records, state_records, resolved_now, stale_after)
    manifests = read_archive_manifests(resolved_paths.repo_root / "briefings", diagnostics)
    attach_archive_metadata(merged_tasks, manifests)

    health = build_health_checks(resolved_paths, diagnostics)
    statistics = build_statistics(merged_tasks, resolved_now)
    highest = highest_health_severity(health)

    return {
        "generated_at": resolved_now.isoformat(),
        "status": highest,
        "statistics": statistics,
        "health": health,
        "tasks": {
            "current": [
                task for task in merged_tasks if task["status"] in CURRENT_STATUSES
            ],
            "recent_terminal": [
                task for task in merged_tasks if task["status"] in TERMINAL_STATUSES
            ][:10],
            "all": merged_tasks,
        },
        "diagnostics": diagnostics,
        "paths": {
            "task_store": str(resolved_paths.task_store_path),
            "state_dir": str(resolved_paths.state_dir),
            "api_log_dir": str(resolved_paths.api_log_dir),
            "worker_log_dir": str(resolved_paths.worker_log_dir),
            "agent_workspace": str(resolved_paths.agent_workspace),
            "repo_root": str(resolved_paths.repo_root),
            "public_base_url": resolved_paths.public_base_url,
            "frpc_container": resolved_paths.frpc_container_name,
            "edge_container": resolved_paths.edge_container_name,
            "ecs_ssh_target": resolved_paths.ecs_ssh_target or "",
        },
    }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def read_task_records(path: Path, diagnostics: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        diagnostics.append(
            warning("task_store", f"Task store is missing: {path}")
        )
        return {}

    records: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    request_id = str(data["request_id"])
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    diagnostics.append(
                        warning(
                            "task_store",
                            f"Malformed task store line {line_number}: {exc}",
                        )
                    )
                    continue
                records[request_id] = normalize_task_record(data)
    except OSError as exc:
        diagnostics.append(error("task_store", f"Task store is unreadable: {exc}"))
    return records


def read_state_records(state_dir: Path, diagnostics: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    if not state_dir.exists():
        diagnostics.append(warning("worker_state", f"Worker state directory is missing: {state_dir}"))
        return {}
    if not state_dir.is_dir():
        diagnostics.append(error("worker_state", f"Worker state path is not a directory: {state_dir}"))
        return {}

    records: dict[str, dict[str, Any]] = {}
    try:
        state_files = sorted(state_dir.glob("*.json"))
    except OSError as exc:
        diagnostics.append(error("worker_state", f"Worker state directory is unreadable: {exc}"))
        return records

    for path in state_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            request_id = str(data.get("request_id") or path.stem)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            diagnostics.append(
                warning("worker_state", f"Malformed worker state file {path.name}: {exc}")
            )
            continue
        records[request_id] = normalize_state_record(data, request_id)
    return records


def normalize_task_record(data: dict[str, Any]) -> dict[str, Any]:
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    source_text = str(data.get("source_text", ""))
    return {
        "request_id": str(data.get("request_id", "")),
        "status": str(data.get("status") or "queued"),
        "title": truncate(str(data.get("title") or "Untitled briefing"), TITLE_CHARS),
        "source_preview": truncate(source_text.strip(), SOURCE_PREVIEW_CHARS),
        "created_at": optional_str(data.get("created_at")),
        "updated_at": optional_str(data.get("updated_at") or data.get("created_at")),
        "issue": {
            "number": issue.get("number"),
            "url": optional_str(issue.get("url")),
            "repository": optional_str(issue.get("repository")),
        },
    }


def normalize_state_record(data: dict[str, Any], request_id: str) -> dict[str, Any]:
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    return {
        **data,
        "request_id": request_id,
        "status": str(data.get("status") or "unknown"),
        "title": truncate(str(data.get("title") or ""), TITLE_CHARS) or None,
        "created_at": optional_str(data.get("created_at")),
        "updated_at": optional_str(data.get("updated_at")),
        "issue": {
            "number": issue.get("number"),
            "url": optional_str(issue.get("url")),
            "repository": optional_str(issue.get("repository")),
        },
    }


def merge_tasks(
    task_records: dict[str, dict[str, Any]],
    state_records: dict[str, dict[str, Any]],
    now: datetime,
    stale_after: timedelta,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for request_id in sorted(task_records.keys() | state_records.keys()):
        task = dict(task_records.get(request_id, {}))
        state = state_records.get(request_id, {})
        merged = {**task, **state}
        merged["request_id"] = request_id
        merged["status"] = str(merged.get("status") or "unknown")
        merged["title"] = truncate(str(merged.get("title") or "Untitled briefing"), TITLE_CHARS)
        merged["issue"] = merge_issue(task.get("issue"), state.get("issue"))
        merged["timestamps"] = timestamps_for(merged, now)
        merged["is_stale"] = is_stale(merged, now, stale_after)
        merged["stale_after_minutes"] = int(stale_after.total_seconds() // 60)
        merged["severity"] = severity_for_task(merged)
        merged["links"] = merged.get("links") if isinstance(merged.get("links"), dict) else {}
        tasks.append(compact_task(merged))
    return sorted(tasks, key=task_sort_key)


def merge_issue(task_issue: Any, state_issue: Any) -> dict[str, Any]:
    base = task_issue if isinstance(task_issue, dict) else {}
    override = state_issue if isinstance(state_issue, dict) else {}
    return {
        "number": override.get("number") or base.get("number"),
        "url": override.get("url") or base.get("url") or "",
        "repository": override.get("repository") or base.get("repository") or "",
    }


def compact_task(task: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "request_id",
        "status",
        "title",
        "source_preview",
        "created_at",
        "updated_at",
        "claimed_at",
        "issue",
        "timestamps",
        "is_stale",
        "stale_after_minutes",
        "severity",
        "runtime_dir",
        "candidate_pptx",
        "qa_summary",
        "qa_report",
        "archive_path",
        "branch",
        "links",
        "reason",
    ]
    return {field: task.get(field) for field in fields if task.get(field) not in (None, "")}


def task_sort_key(task: dict[str, Any]) -> tuple[int, float]:
    status = str(task.get("status", ""))
    timestamp = parse_datetime(task.get("updated_at") or task.get("created_at"))
    epoch_seconds = timestamp.timestamp() if timestamp else 0
    return (0 if status in CURRENT_STATUSES else 1, -epoch_seconds)


def timestamps_for(task: dict[str, Any], now: datetime) -> dict[str, Any]:
    created = parse_datetime(task.get("created_at"))
    updated = parse_datetime(task.get("updated_at"))
    completed = updated if task.get("status") in TERMINAL_STATUSES else None
    started = parse_datetime(task.get("claimed_at")) or created
    duration_source = completed or now
    duration_seconds = int((duration_source - started).total_seconds()) if started else None
    return {
        "created_at": created.isoformat() if created else None,
        "updated_at": updated.isoformat() if updated else None,
        "duration_seconds": max(duration_seconds, 0) if duration_seconds is not None else None,
    }


def is_stale(task: dict[str, Any], now: datetime, stale_after: timedelta) -> bool:
    if task.get("status") not in IN_PROGRESS_STATUSES:
        return False
    updated = parse_datetime(task.get("updated_at")) or parse_datetime(task.get("created_at"))
    return bool(updated and now - updated > stale_after)


def severity_for_task(task: dict[str, Any]) -> str:
    if task.get("status") in {"failed", "qa_failed"}:
        return "fail"
    if task.get("is_stale"):
        return "warn"
    return "pass"


def build_statistics(tasks: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(ALL_KNOWN_STATUSES)}
    for task in tasks:
        status = str(task.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1

    terminal = [task for task in tasks if task.get("status") in TERMINAL_STATUSES]
    completed = [task for task in terminal if task.get("status") == "completed"]
    durations = [
        task["timestamps"]["duration_seconds"]
        for task in completed
        if isinstance(task.get("timestamps"), dict)
        and isinstance(task["timestamps"].get("duration_seconds"), int)
    ]
    return {
        "counts_by_status": counts,
        "total_tasks": len(tasks),
        "active_tasks": sum(counts.get(status, 0) for status in CURRENT_STATUSES),
        "terminal_tasks": len(terminal),
        "success_rate": round(len(completed) / len(terminal), 3) if terminal else None,
        "recent_24h": count_since(tasks, now - timedelta(hours=24)),
        "recent_7d": count_since(tasks, now - timedelta(days=7)),
        "average_completed_duration_seconds": int(sum(durations) / len(durations)) if durations else None,
        "stale_tasks": sum(1 for task in tasks if task.get("is_stale")),
    }


def count_since(tasks: list[dict[str, Any]], threshold: datetime) -> int:
    count = 0
    for task in tasks:
        created = parse_datetime(task.get("created_at"))
        if created and created >= threshold:
            count += 1
    return count


def build_health_checks(paths: MonitorPaths, diagnostics: list[dict[str, str]]) -> list[dict[str, str]]:
    checks = [
        path_check("task_store", "任务队列文件", paths.task_store_path, file_ok=True),
        path_check("worker_state", "Worker 状态目录", paths.state_dir, dir_ok=True),
        path_check("agent_workspace", "AgentWorkspace", paths.agent_workspace, dir_ok=True),
        path_check("briefings", "归档目录", paths.repo_root / "briefings", dir_ok=True),
        git_lfs_check(paths.repo_root / ".gitattributes"),
        frpc_container_check(paths.frpc_container_name),
        public_edge_health_check(paths.public_base_url),
        ecs_edge_process_check(paths.ecs_ssh_target, paths.edge_container_name),
    ]
    for diagnostic in diagnostics:
        checks.append(
            {
                "id": diagnostic["source"],
                "label": f"诊断：{diagnostic['source']}",
                "status": diagnostic["severity"],
                "message": diagnostic["message"],
            }
        )
    return checks


def path_check(
    check_id: str,
    label: str,
    path: Path,
    *,
    file_ok: bool = False,
    dir_ok: bool = False,
) -> dict[str, str]:
    if not path.exists():
        return {"id": check_id, "label": label, "status": "warn", "message": f"缺失：{path}"}
    if file_ok and path.is_file():
        return {"id": check_id, "label": label, "status": "pass", "message": f"可读取：{path}"}
    if dir_ok and path.is_dir():
        return {"id": check_id, "label": label, "status": "pass", "message": f"可读取：{path}"}
    return {"id": check_id, "label": label, "status": "fail", "message": f"路径类型异常：{path}"}


def git_lfs_check(path: Path) -> dict[str, str]:
    expected = "briefings/**/*.pptx filter=lfs diff=lfs merge=lfs -text"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"id": "git_lfs", "label": "Git LFS 跟踪", "status": "warn", "message": str(exc)}
    if expected in content.splitlines():
        return {"id": "git_lfs", "label": "Git LFS 跟踪", "status": "pass", "message": expected}
    return {
        "id": "git_lfs",
        "label": "Git LFS 跟踪",
        "status": "warn",
        "message": f"缺少跟踪规则：{expected}",
    }


def frpc_container_check(container_name: str) -> dict[str, str]:
    if not shutil.which("docker"):
        return {
            "id": "frpc_client",
            "label": "反向代理客户端 frpc",
            "status": "warn",
            "message": "未找到 Docker CLI，无法检查本机 frpc 容器。",
        }

    result = run_command(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^/{container_name}$",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout_seconds=5,
    )
    if result["returncode"] != 0:
        return {
            "id": "frpc_client",
            "label": "反向代理客户端 frpc",
            "status": "warn",
            "message": f"Docker 查询失败：{result['stderr'] or result['stdout']}",
        }
    output = result["stdout"].strip()
    if not output:
        return {
            "id": "frpc_client",
            "label": "反向代理客户端 frpc",
            "status": "warn",
            "message": f"未找到本机 frpc 容器：{container_name}",
        }
    status = output.split("\t")[1] if "\t" in output else output
    return {
        "id": "frpc_client",
        "label": "反向代理客户端 frpc",
        "status": "pass" if status.lower().startswith("up") else "fail",
        "message": output,
    }


def public_edge_health_check(public_base_url: str) -> dict[str, str]:
    url = public_base_url.rstrip("/") + "/health"
    request = urllib.request.Request(url, headers={"User-Agent": "mozhi-monitor"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            status_code = response.status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "id": "public_edge_health",
            "label": "ECS 公网健康入口",
            "status": "fail",
            "message": f"{url} 不可达：{exc}",
        }
    status = "pass" if 200 <= status_code < 300 else "fail"
    return {
        "id": "public_edge_health",
        "label": "ECS 公网健康入口",
        "status": status,
        "message": f"HTTP {status_code}：{body[:160]}",
    }


def ecs_edge_process_check(ssh_target: str | None, container_name: str) -> dict[str, str]:
    if not ssh_target:
        return {
            "id": "ecs_edge_processes",
            "label": "ECS 反向代理服务端",
            "status": "warn",
            "message": "未配置 MOZHI_ECS_SSH_TARGET，跳过 ECS 容器内 frps/Caddy 进程检查。",
        }
    if not shutil.which("ssh"):
        return {
            "id": "ecs_edge_processes",
            "label": "ECS 反向代理服务端",
            "status": "warn",
            "message": "未找到 ssh 命令，无法检查 ECS 远端进程。",
        }

    remote_command = (
        f"docker inspect -f '{{{{.State.Running}}}} {{{{.State.Status}}}}' {container_name} && "
        f"docker exec {container_name} sh -lc 'pgrep -x frps >/dev/null && pgrep -x caddy >/dev/null'"
    )
    result = run_command(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            ssh_target,
            remote_command,
        ],
        timeout_seconds=10,
    )
    if result["returncode"] != 0:
        return {
            "id": "ecs_edge_processes",
            "label": "ECS 反向代理服务端",
            "status": "fail",
            "message": f"远端检查失败：{result['stderr'] or result['stdout']}",
        }
    return {
        "id": "ecs_edge_processes",
        "label": "ECS 反向代理服务端",
        "status": "pass",
        "message": f"{container_name} 正在运行，frps 与 Caddy 进程存在。",
    }


def run_command(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def highest_health_severity(health: list[dict[str, str]]) -> str:
    statuses = {check["status"] for check in health}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def read_archive_manifests(briefings_dir: Path, diagnostics: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    if not briefings_dir.exists():
        return manifests
    for path in briefings_dir.glob("**/manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            request_id = str(manifest["request_id"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            diagnostics.append(warning("archive", f"Malformed archive manifest {path}: {exc}"))
            continue
        manifests[request_id] = manifest
    return manifests


def attach_archive_metadata(tasks: list[dict[str, Any]], manifests: dict[str, dict[str, Any]]) -> None:
    for task in tasks:
        manifest = manifests.get(str(task.get("request_id")))
        if not manifest:
            continue
        task.setdefault("archive_path", manifest.get("archive_path"))
        task.setdefault("branch", manifest.get("branch"))
        links = task.setdefault("links", {})
        if isinstance(links, dict):
            for artifact in manifest.get("artifacts", []):
                if isinstance(artifact, dict) and artifact.get("download_url"):
                    links[str(artifact.get("kind") or artifact.get("artifact_id"))] = artifact["download_url"]


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SERVICE_TIMEZONE)
    return parsed


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def warning(source: str, message: str) -> dict[str, str]:
    return {"severity": "warn", "source": source, "message": message}


def error(source: str, message: str) -> dict[str, str]:
    return {"severity": "fail", "source": source, "message": message}


MONITOR_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mozhi Monitor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #09111f;
      --panel: rgba(17, 29, 50, 0.86);
      --panel-strong: rgba(25, 43, 73, 0.94);
      --line: rgba(142, 169, 205, 0.2);
      --text: #edf5ff;
      --muted: #8fa7c4;
      --accent: #46c2ff;
      --accent-2: #5ee0a0;
      --pass: #4ade80;
      --warn: #fbbf24;
      --fail: #fb7185;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      overflow: hidden;
      background:
        radial-gradient(circle at 18% 12%, rgba(70, 194, 255, 0.2), transparent 28%),
        linear-gradient(135deg, #08101d 0%, #0b1628 48%, #0c1f2c 100%);
      color: var(--text);
      font: 14px/1.35 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(142, 169, 205, 0.07) 1px, transparent 1px),
        linear-gradient(90deg, rgba(142, 169, 205, 0.07) 1px, transparent 1px);
      background-size: 42px 42px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,0.78), transparent 88%);
    }
    main {
      position: relative;
      z-index: 1;
      height: 100vh;
      padding: 18px 22px;
      display: grid;
      grid-template-rows: auto auto 1fr;
      gap: 14px;
    }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      gap: 16px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 {
      font-size: 28px;
      line-height: 1;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    h2 {
      font-size: 13px;
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .subline { margin-top: 7px; color: var(--muted); font-size: 13px; }
    .status-lockup {
      display: flex;
      align-items: center;
      gap: 10px;
      justify-self: end;
    }
    .beacon {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--warn);
      box-shadow: 0 0 0 7px rgba(251, 191, 36, 0.12), 0 0 22px rgba(251, 191, 36, 0.7);
      animation: pulse 1.8s ease-in-out infinite;
    }
    .beacon.pass { background: var(--pass); box-shadow: 0 0 0 7px rgba(74, 222, 128, 0.12), 0 0 22px rgba(74, 222, 128, 0.7); }
    .beacon.fail { background: var(--fail); box-shadow: 0 0 0 7px rgba(251, 113, 133, 0.12), 0 0 22px rgba(251, 113, 133, 0.7); }
    .state-word {
      font-size: 34px;
      font-weight: 800;
      line-height: 1;
      text-transform: uppercase;
    }
    .metrics {
      display: grid;
      grid-template-columns: 1.15fr repeat(4, minmax(130px, 1fr));
      gap: 12px;
    }
    .hero-metric, .metric, .panel {
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(24, 42, 70, 0.96), rgba(13, 24, 42, 0.88));
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.25);
    }
    .hero-metric, .metric { border-radius: 8px; min-height: 88px; padding: 14px; }
    .hero-metric strong, .metric strong {
      display: block;
      margin-top: 4px;
      font-size: 34px;
      line-height: 1;
      font-weight: 850;
      letter-spacing: 0;
    }
    .hero-metric strong { font-size: 40px; color: var(--accent); }
    .metric span, .hero-metric span, .tiny { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
    .tabs {
      display: grid;
      grid-template-columns: 190px 1fr;
      gap: 14px;
      min-height: 0;
    }
    nav {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(7, 17, 31, 0.68);
      padding: 10px;
      display: grid;
      align-content: start;
      gap: 8px;
    }
    .tab-button {
      appearance: none;
      border: 1px solid transparent;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      min-height: 46px;
      padding: 0 12px;
      text-align: left;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    .tab-button:hover, .tab-button:focus-visible {
      outline: none;
      color: var(--text);
      border-color: rgba(70, 194, 255, 0.45);
    }
    .tab-button.active {
      color: var(--text);
      background: linear-gradient(90deg, rgba(70, 194, 255, 0.18), rgba(94, 224, 160, 0.08));
      border-color: rgba(70, 194, 255, 0.55);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .tab-panel {
      min-height: 0;
      display: none;
      animation: enter 180ms ease-out;
    }
    .tab-panel.active { display: block; }
    .screen-grid {
      height: 100%;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(360px, 1.15fr) minmax(320px, 0.85fr);
      grid-template-rows: minmax(0, 1fr) 190px;
      gap: 12px;
    }
    .panel {
      min-height: 0;
      border-radius: 8px;
      padding: 14px;
      overflow: hidden;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 12px;
    }
    .wide { grid-column: 1 / -1; }
    .scroll-area { min-height: 0; overflow: auto; padding-right: 4px; }
    .scroll-area::-webkit-scrollbar { width: 8px; height: 8px; }
    .scroll-area::-webkit-scrollbar-thumb { background: rgba(142, 169, 205, 0.28); border-radius: 8px; }
    .rows { display: grid; gap: 8px; }
    .row {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px;
      border: 1px solid rgba(142, 169, 205, 0.13);
      border-radius: 8px;
      background: rgba(8, 19, 34, 0.62);
    }
    .row-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 750;
    }
    .row-detail {
      margin-top: 3px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 64px;
      height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .pass { color: #082413; background: var(--pass); }
    .warn { color: #2d1f00; background: var(--warn); }
    .fail { color: #310712; background: var(--fail); }
    .queued, .running, .generating, .generation_completed, .qa_passed, .publishing, .completed {
      color: #061622;
      background: var(--accent);
    }
    .completed { background: var(--accent-2); }
    .qa_failed, .failed { color: #310712; background: var(--fail); }
    .stage-bars { display: grid; align-content: center; gap: 9px; }
    .bar-row { display: grid; grid-template-columns: 138px 1fr 34px; gap: 10px; align-items: center; }
    .bar-label { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bar-track { height: 11px; border-radius: 999px; background: rgba(142, 169, 205, 0.13); overflow: hidden; }
    .bar-fill { height: 100%; min-width: 2px; border-radius: inherit; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
    .check-list { display: grid; gap: 8px; }
    .check {
      display: grid;
      grid-template-columns: 72px 190px minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      padding: 9px 10px;
      border-bottom: 1px solid rgba(142, 169, 205, 0.12);
    }
    .path-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .path-box {
      min-height: 76px;
      border: 1px solid rgba(142, 169, 205, 0.14);
      border-radius: 8px;
      padding: 10px;
      background: rgba(8, 19, 34, 0.62);
      overflow: hidden;
    }
    .path-box code { display: block; margin-top: 8px; color: var(--text); overflow-wrap: anywhere; font-size: 12px; }
    .empty {
      height: 100%;
      min-height: 120px;
      display: grid;
      place-items: center;
      color: var(--muted);
      border: 1px dashed rgba(142, 169, 205, 0.22);
      border-radius: 8px;
    }
    .health-layout {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
    }
    .health-tabs {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .health-tab {
      appearance: none;
      min-height: 58px;
      border: 1px solid rgba(142, 169, 205, 0.18);
      border-radius: 8px;
      background: rgba(8, 19, 34, 0.62);
      color: var(--text);
      cursor: pointer;
      padding: 9px 10px;
      text-align: left;
      display: grid;
      grid-template-columns: auto 1fr;
      align-items: center;
      gap: 9px;
      font: inherit;
    }
    .health-tab:hover, .health-tab:focus-visible {
      outline: none;
      border-color: rgba(70, 194, 255, 0.55);
    }
    .health-tab.active {
      background: linear-gradient(90deg, rgba(70, 194, 255, 0.18), rgba(8, 19, 34, 0.72));
      border-color: rgba(70, 194, 255, 0.68);
    }
    .lamp {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--warn);
      box-shadow: 0 0 16px rgba(251, 191, 36, 0.65);
    }
    .lamp.pass { background: var(--pass); box-shadow: 0 0 16px rgba(74, 222, 128, 0.65); }
    .lamp.fail { background: var(--fail); box-shadow: 0 0 16px rgba(251, 113, 133, 0.65); }
    .health-tab-title { display: block; font-weight: 850; }
    .health-tab-note { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }
    .health-panel {
      min-height: 0;
      display: none;
    }
    .health-panel.active {
      min-height: 0;
      height: 100%;
      display: block;
    }
    @keyframes pulse {
      0%, 100% { transform: scale(1); opacity: 0.9; }
      50% { transform: scale(1.22); opacity: 1; }
    }
    @keyframes enter {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 980px) {
      body { overflow: auto; }
      main { height: auto; min-height: 100vh; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .tabs { grid-template-columns: 1fr; }
      nav { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .screen-grid { grid-template-columns: 1fr; grid-template-rows: auto; }
      .wide { grid-column: auto; }
    }
  </style>
</head>
<body>
  <main id="monitor-root">
    <header>
      <div>
        <h1>Mozhi 本地监控大屏</h1>
        <p class="subline" id="generated-at">正在加载...</p>
      </div>
      <div class="status-lockup">
        <span class="beacon" id="status-beacon"></span>
        <strong class="state-word" id="overall-status">加载中</strong>
      </div>
    </header>
    <section class="metrics" id="metrics"></section>
    <section class="tabs">
      <nav aria-label="Monitor sections">
        <button class="tab-button active" type="button" data-tab="overview">总览</button>
        <button class="tab-button" type="button" data-tab="current">当前任务</button>
        <button class="tab-button" type="button" data-tab="health">系统健康</button>
        <button class="tab-button" type="button" data-tab="history">历史结果</button>
      </nav>
      <div id="tab-content">
        <section class="tab-panel active" data-panel="overview">
          <div class="screen-grid">
            <div class="panel">
              <h2>实时任务队列</h2>
              <div class="scroll-area" id="overview-current"></div>
            </div>
            <div class="panel">
              <h2>阶段分布</h2>
              <div id="stage-bars" class="stage-bars"></div>
            </div>
            <div class="panel wide">
              <h2>健康信号板</h2>
              <div class="scroll-area" id="overview-health"></div>
            </div>
          </div>
        </section>
        <section class="tab-panel" data-panel="current">
          <div class="panel" style="height:100%;">
            <h2>当前任务</h2>
            <div class="scroll-area" id="current-tasks"></div>
          </div>
        </section>
        <section class="tab-panel" data-panel="health">
          <div class="screen-grid">
            <div class="panel">
              <h2>健康检查</h2>
              <div class="health-layout">
                <div class="health-tabs" id="health-tabs"></div>
                <div class="scroll-area" id="health-panels"></div>
              </div>
            </div>
            <div class="panel">
              <h2>运行路径</h2>
              <div id="paths"></div>
            </div>
            <div class="panel wide">
              <h2>解析诊断</h2>
              <div class="scroll-area" id="diagnostics"></div>
            </div>
          </div>
        </section>
        <section class="tab-panel" data-panel="history">
          <div class="panel" style="height:100%;">
            <h2>近期完成/失败任务</h2>
            <div class="scroll-area" id="terminal-tasks"></div>
          </div>
        </section>
      </div>
    </section>
  </main>
  <script>
    const endpoint = "/api/monitor/state";
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    const statusClass = (status) => ["pass", "warn", "fail"].includes(status) ? status : String(status || "").replace(/[^a-z0-9_]+/g, "_");
    const statusText = (status) => ({
      pass: "正常",
      warn: "警告",
      fail: "故障",
      queued: "排队中",
      running: "运行中",
      generating: "生成中",
      generation_completed: "生成完成",
      qa_passed: "QA 通过",
      publishing: "发布中",
      completed: "已完成",
      qa_failed: "QA 失败",
      failed: "失败",
    }[status] || status || "");
    const pathText = (key) => ({
      task_store: "任务队列文件",
      state_dir: "状态目录",
      api_log_dir: "API 日志目录",
      worker_log_dir: "Worker 日志目录",
      agent_workspace: "AgentWorkspace",
      repo_root: "服务仓库",
    }[key] || key);
    const pill = (status) => `<span class="pill ${statusClass(status)}">${esc(statusText(status))}</span>`;
    const severityRank = { pass: 0, warn: 1, fail: 2 };
    let activeHealthGroup = "local";
    const link = (url, text) => url ? `<a href="${esc(url)}">${esc(text)}</a>` : esc(text || "");

    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".tab-button").forEach((item) => item.classList.toggle("active", item === button));
        document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === button.dataset.tab));
      });
    });

    function renderMetrics(stats, status) {
      const success = stats.success_rate == null ? "n/a" : `${Math.round(stats.success_rate * 100)}%`;
      const duration = stats.average_completed_duration_seconds == null ? "n/a" : `${Math.round(stats.average_completed_duration_seconds / 60)}m`;
      const items = [
        ["系统状态", statusText(status), "最高健康等级", true],
        ["活跃任务", stats.active_tasks, "排队中 + 进行中"],
        ["已完成", stats.counts_by_status.completed || 0, "终态成功任务"],
        ["成功率", success, `${stats.terminal_tasks} 个终态任务`],
        ["平均耗时", duration, `24 小时内创建 ${stats.recent_24h} 个`],
      ];
      document.getElementById("metrics").innerHTML = items.map(([label, value, note, hero]) => `<article class="${hero ? "hero-metric" : "metric"}"><span>${esc(label)}</span><strong>${esc(value)}</strong><p class="tiny">${esc(note)}</p></article>`).join("");
    }

    function renderRows(target, tasks, empty) {
      const html = tasks.map((task) => `<article class="row">
        ${pill(task.severity === "pass" ? task.status : task.severity)}
        <div>
          <div class="row-title">${esc(task.title)}</div>
          <div class="row-detail">${esc(task.request_id)} · ${esc(task.updated_at || task.created_at || "")}</div>
          <div class="row-detail">${esc(task.source_preview || task.reason || task.archive_path || "")}</div>
        </div>
        <div>${link(task.issue && task.issue.url, task.issue && task.issue.number ? `#${task.issue.number}` : "")}</div>
      </article>`).join("");
      document.getElementById(target).innerHTML = html || `<div class="empty">${esc(empty)}</div>`;
    }

    function renderHealth(target, checks, limit) {
      const rows = (limit ? checks.slice(0, limit) : checks).map((check) => `<article class="check">
        ${pill(check.status)}
        <strong>${esc(check.label)}</strong>
        <span class="row-detail">${esc(check.message)}</span>
      </article>`).join("");
      document.getElementById(target).innerHTML = `<div class="check-list">${rows}</div>`;
    }

    function healthGroupFor(check) {
      if (["task_store", "worker_state", "agent_workspace", "briefings", "git_lfs"].includes(check.id)) return "local";
      if (["frpc_client", "public_edge_health", "ecs_edge_processes"].includes(check.id)) return "edge";
      return "diagnostics";
    }

    function groupSeverity(checks) {
      return checks.reduce((worst, check) => severityRank[check.status] > severityRank[worst] ? check.status : worst, "pass");
    }

    function groupNote(checks) {
      const failCount = checks.filter((check) => check.status === "fail").length;
      const warnCount = checks.filter((check) => check.status === "warn").length;
      if (failCount) return `${failCount} 故障 / ${checks.length} 项`;
      if (warnCount) return `${warnCount} 警告 / ${checks.length} 项`;
      return `全部正常 / ${checks.length} 项`;
    }

    function renderHealthGroups(checks, diagnostics) {
      const groups = [
        { id: "local", label: "本地状态", note: "文件、目录、Git LFS" },
        { id: "edge", label: "边缘链路", note: "frpc / ECS / Caddy" },
        { id: "diagnostics", label: "解析诊断", note: "坏 JSON 与异常文件" },
      ];
      const grouped = Object.fromEntries(groups.map((group) => [group.id, []]));
      checks.forEach((check) => grouped[healthGroupFor(check)].push(check));
      const diagnosticChecks = diagnostics.map((item) => ({
        id: item.source,
        label: item.source,
        status: item.severity,
        message: item.message,
      }));
      grouped.diagnostics = diagnosticChecks;

      if (!groups.some((group) => group.id === activeHealthGroup)) activeHealthGroup = "local";
      document.getElementById("health-tabs").innerHTML = groups.map((group) => {
        const severity = grouped[group.id].length ? groupSeverity(grouped[group.id]) : "pass";
        return `<button class="health-tab ${group.id === activeHealthGroup ? "active" : ""}" type="button" data-health-tab="${group.id}">
          <span class="lamp ${statusClass(severity)}"></span>
          <span><span class="health-tab-title">${esc(group.label)}</span><span class="health-tab-note">${esc(groupNote(grouped[group.id]))}</span></span>
        </button>`;
      }).join("");
      document.querySelectorAll(".health-tab").forEach((button) => {
        button.addEventListener("click", () => {
          activeHealthGroup = button.dataset.healthTab;
          renderHealthGroups(window.__monitorChecks || [], window.__monitorDiagnostics || []);
        });
      });
      document.getElementById("health-panels").innerHTML = groups.map((group) => {
        const rows = grouped[group.id].map((check) => `<article class="check">
          ${pill(check.status)}
          <strong>${esc(check.label)}</strong>
          <span class="row-detail">${esc(check.message)}</span>
        </article>`).join("");
        return `<section class="health-panel ${group.id === activeHealthGroup ? "active" : ""}" data-health-panel="${group.id}">
          ${rows ? `<div class="check-list">${rows}</div>` : `<div class="empty">暂无${esc(group.label)}异常</div>`}
        </section>`;
      }).join("");
    }

    function renderStageBars(stats) {
      const entries = Object.entries(stats.counts_by_status).filter(([, count]) => count > 0);
      const max = Math.max(1, ...entries.map(([, count]) => count));
      document.getElementById("stage-bars").innerHTML = entries.map(([status, count]) => `<div class="bar-row">
        <span class="bar-label">${esc(statusText(status))}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${Math.max(6, count / max * 100)}%"></span></span>
        <strong>${esc(count)}</strong>
      </div>`).join("") || `<div class="empty">暂无任务记录</div>`;
    }

    function renderPaths(paths) {
      document.getElementById("paths").innerHTML = `<div class="path-grid">${Object.entries(paths).map(([key, value]) => `<div class="path-box"><span class="tiny">${esc(pathText(key))}</span><code>${esc(value)}</code></div>`).join("")}</div>`;
    }

    function renderDiagnostics(items) {
      document.getElementById("diagnostics").innerHTML = items.length
        ? `<div class="check-list">${items.map((item) => `<article class="check">${pill(item.severity)}<strong>${esc(item.source)}</strong><span class="row-detail">${esc(item.message)}</span></article>`).join("")}</div>`
        : `<div class="empty">暂无解析诊断</div>`;
    }

    async function refresh() {
      try {
        const response = await fetch(endpoint, { cache: "no-store" });
        const data = await response.json();
        document.getElementById("generated-at").textContent = `更新于 ${data.generated_at} · 每 5 秒刷新`;
        document.getElementById("overall-status").textContent = statusText(data.status);
        document.getElementById("status-beacon").className = `beacon ${statusClass(data.status)}`;
        renderMetrics(data.statistics, data.status);
        renderRows("overview-current", data.tasks.current.slice(0, 5), "暂无活跃任务");
        renderRows("current-tasks", data.tasks.current, "暂无排队中或进行中的任务");
        renderRows("terminal-tasks", data.tasks.recent_terminal, "暂无已完成、失败或 QA 失败任务");
        renderHealth("overview-health", data.health, 5);
        window.__monitorChecks = data.health;
        window.__monitorDiagnostics = data.diagnostics;
        renderHealthGroups(data.health, data.diagnostics);
        renderStageBars(data.statistics);
        renderPaths(data.paths);
        renderDiagnostics(data.diagnostics);
      } catch (error) {
        document.getElementById("generated-at").textContent = `监控刷新失败：${error}`;
      }
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def monitor_html() -> str:
    return MONITOR_HTML
