import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mozhi_worker import cli as worker_cli
from mozhi_worker.archive import Archiver, archive_relative_dir, sha256_file
from mozhi_worker.codex_runner import build_codex_prompt, codex_command, handoff_contract, load_handoff, status_script
from mozhi_worker.config import WorkerSettings, default_agent_workspace_path, repo_root
from mozhi_worker.git_publish import FakeGitPublisher, branch_name_for, ensure_lfs_tracking
from mozhi_worker.issue import FakeIssueClient, IssueUpdateError, comment_issue_milestone, completed_details
from mozhi_worker.models import GenerationRecord, IssueRef, QaResult, Task, WorkerError
from mozhi_worker.qa import AgentWorkspaceQaRunner, FakeQaRunner, result_from_report
from mozhi_worker.state import StateStore
from mozhi_worker.task_store import JsonlTaskReader
from mozhi_worker.worker import Worker


def make_task(tmp_path, request_id="brf_20260506231043_dfcb53"):
    return Task(
        request_id=request_id,
        status="queued",
        title="暗黑新DLC介绍",
        source_text="根据暗黑4最新DLC内容",
        created_at="2026-05-06T23:10:43+08:00",
        issue=IssueRef(
            number=9,
            url="https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/9",
            repository="MozhiJiawei/Mozhi-s-Agent-Service",
        ),
    )


def write_task_store(path, task):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "request_id": task.request_id,
                "status": task.status,
                "title": task.title,
                "source_text": task.source_text,
                "created_at": task.created_at,
                "issue": {
                    "number": task.issue.number,
                    "url": task.issue.url,
                    "repository": task.issue.repository,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_jsonl_reader_loads_queued_tasks(tmp_path):
    task = make_task(tmp_path)
    store_path = tmp_path / "tasks.jsonl"
    write_task_store(store_path, task)

    tasks = JsonlTaskReader(store_path).queued_tasks()

    assert len(tasks) == 1
    assert tasks[0].request_id == task.request_id
    assert tasks[0].source_text == "根据暗黑4最新DLC内容"


def test_claim_task_writes_controlled_state(tmp_path):
    task = make_task(tmp_path)
    state = StateStore(tmp_path / "state").claim_task(task)

    assert state["status"] == "running"
    assert state["issue"]["number"] == 9
    assert (tmp_path / "state" / f"{task.request_id}.json").exists()


def test_issue_milestone_uses_allowed_templates(tmp_path):
    task = make_task(tmp_path)
    client = FakeIssueClient()

    milestone = comment_issue_milestone(client, task, "generating", "codex")

    assert milestone.status == "generating"
    assert "## Worker status: `generating`" in client.comments[0]["body"]
    assert "| Stage | `codex` |" in client.comments[0]["body"]


def test_issue_milestone_rejects_unknown_status(tmp_path):
    task = make_task(tmp_path)

    with pytest.raises(ValueError):
        comment_issue_milestone(FakeIssueClient(), task, "surprising", "codex")


def test_codex_prompt_is_generic_and_points_to_workspace_contract(tmp_path):
    task = make_task(tmp_path)
    prompt = build_codex_prompt(
        task,
        tmp_path / "source.md",
        tmp_path / "worker_contract.json",
        tmp_path / "handoff.json",
        tmp_path / "report-status.ps1",
    )

    assert "Read AGENTS.md" in prompt
    assert "briefing PPTX" in prompt
    assert "report-status.ps1" in prompt
    assert "GitHub Issue: https://github.com/MozhiJiawei/Mozhi-s-Agent-Service/issues/9" in prompt
    assert "Do not update GitHub Issues" not in prompt
    assert "暗黑新DLC介绍" in prompt


def test_codex_contract_exposes_ai_issue_status_script(tmp_path):
    contract = handoff_contract(tmp_path / "handoff.json", tmp_path / "report-status.ps1")

    assert contract["status_script_path"] == str(tmp_path / "report-status.ps1")
    assert "Update the GitHub Issue yourself" in contract["rules"][0]


def test_status_script_calls_worker_issue_milestone_cli(tmp_path):
    script = status_script("brf_20260506231043_dfcb53")

    assert "python -m mozhi_worker.cli comment-issue-milestone brf_20260506231043_dfcb53" in script
    assert "apps\\worker" in script


def test_codex_command_prefers_windows_cmd_shim(monkeypatch):
    calls = []

    def fake_which(candidate):
        calls.append(candidate)
        return "C:/Users/example/AppData/Roaming/npm/codex.cmd" if candidate == "codex.cmd" else None

    monkeypatch.setattr("mozhi_worker.codex_runner.os.name", "nt")
    monkeypatch.setattr("mozhi_worker.codex_runner.shutil.which", fake_which)

    assert codex_command("codex").endswith("codex.cmd")
    assert calls == ["codex.cmd"]


def test_load_handoff_validates_required_paths(tmp_path):
    pptx = tmp_path / "deck.pptx"
    summary = tmp_path / "qa-summary.md"
    qa_json = tmp_path / "qa.json"
    slides = tmp_path / "slides"
    deck_workspace = tmp_path / "deck"
    for path in [pptx, summary, qa_json]:
        path.write_text("x", encoding="utf-8")
    slides.mkdir()
    deck_workspace.mkdir()
    handoff = tmp_path / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "candidate_pptx": str(pptx),
                "qa_summary": str(summary),
                "qa_json": str(qa_json),
                "slides_dir": str(slides),
                "deck_workspace": str(deck_workspace),
                "status": "generation_completed",
            }
        ),
        encoding="utf-8",
    )

    record = load_handoff(handoff)

    assert record.candidate_pptx == pptx
    assert record.qa_summary == summary


def test_load_handoff_accepts_inline_qa_summary(tmp_path):
    pptx = tmp_path / "deck.pptx"
    qa_json = tmp_path / "qa.json"
    slides = tmp_path / "slides"
    deck_workspace = tmp_path / "deck"
    for path in [pptx, qa_json]:
        path.write_text("x", encoding="utf-8")
    slides.mkdir()
    deck_workspace.mkdir()
    handoff = tmp_path / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "candidate_pptx": str(pptx),
                "qa_summary": {"status": "passed", "slide_count": 10},
                "qa_json": str(qa_json),
                "slides_dir": str(slides),
                "deck_workspace": str(deck_workspace),
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    record = load_handoff(handoff)

    assert record.qa_summary == tmp_path / "qa-summary.json"
    assert json.loads(record.qa_summary.read_text(encoding="utf-8"))["slide_count"] == 10


def test_load_handoff_rejects_missing_candidate(tmp_path):
    handoff = tmp_path / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "candidate_pptx": str(tmp_path / "missing.pptx"),
                "qa_summary": str(tmp_path / "missing.md"),
                "qa_json": str(tmp_path / "qa.json"),
                "slides_dir": str(tmp_path / "slides"),
                "deck_workspace": str(tmp_path / "deck"),
                "status": "generation_completed",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkerError):
        load_handoff(handoff)


def test_qa_report_without_passed_uses_error_count(tmp_path):
    report = tmp_path / "qa.json"
    summary = tmp_path / "qa-summary.md"
    report.write_text('{"summary": {"errors": 0, "warnings": 3}, "issues": []}', encoding="utf-8")
    summary.write_text("QA summary", encoding="utf-8")

    result = result_from_report(report, summary)

    assert result.passed is True
    assert result.report_path == report


def test_agent_workspace_qa_passes_when_powerpoint_export_succeeds(monkeypatch, tmp_path):
    agent_workspace = tmp_path / "AgentWorkspace"
    export_script = agent_workspace / "skills" / "hw-ppt-gen" / "scripts" / "pptx" / "export_pptx_images.js"
    export_script.parent.mkdir(parents=True)
    export_script.write_text("export", encoding="utf-8")
    candidate = tmp_path / "candidate.pptx"
    summary = tmp_path / "qa-summary.md"
    stale_report = tmp_path / "stale.qa.json"
    deck_workspace = tmp_path / "deck"
    slides = deck_workspace / "slides"
    candidate.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    stale_report.write_text('{"passed": false, "issues": [{"severity": "error"}]}', encoding="utf-8")
    deck_workspace.mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        out_path = Path(command[command.index("--out") + 1])
        out_path.mkdir(parents=True)
        (out_path / "slide_01.png").write_bytes(b"png")
        (out_path / "render_manifest.json").write_text(
            '{"renderer": "powerpoint", "slide_count": 1}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = AgentWorkspaceQaRunner(agent_workspace).run(
        GenerationRecord(
            candidate_pptx=candidate,
            qa_summary=summary,
            qa_json=stale_report,
            slides_dir=slides,
            deck_workspace=deck_workspace,
        )
    )

    assert result.passed is True
    assert calls
    assert "--renderer" in calls[0]
    assert calls[0][calls[0].index("--renderer") + 1] == "powerpoint"
    assert result.report_path == slides / "render_manifest.json"


def test_agent_workspace_qa_wraps_missing_node(monkeypatch, tmp_path):
    agent_workspace = tmp_path / "AgentWorkspace"
    export_script = agent_workspace / "skills" / "hw-ppt-gen" / "scripts" / "pptx" / "export_pptx_images.js"
    export_script.parent.mkdir(parents=True)
    export_script.write_text("export", encoding="utf-8")
    candidate = tmp_path / "candidate.pptx"
    summary = tmp_path / "qa-summary.md"
    deck_workspace = tmp_path / "deck"
    candidate.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    deck_workspace.mkdir()

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(WorkerError, match="Node.js is required"):
        AgentWorkspaceQaRunner(agent_workspace).run(
            GenerationRecord(
                candidate_pptx=candidate,
                qa_summary=summary,
                deck_workspace=deck_workspace,
            )
        )


def test_archiver_copies_curated_files_and_manifest_metadata(tmp_path):
    task = make_task(tmp_path)
    pptx = tmp_path / "candidate.pptx"
    summary = tmp_path / "summary.md"
    qa_json = tmp_path / "qa.json"
    pptx.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    qa_json.write_text('{"passed": true, "issues": []}', encoding="utf-8")
    generation = GenerationRecord(candidate_pptx=pptx, qa_summary=summary, qa_json=qa_json)
    qa = QaResult(passed=True, summary_path=summary, report_path=qa_json)

    result = Archiver(tmp_path).archive(
        task,
        generation,
        qa,
        "codex/briefing-issue-9-暗黑新DLC介绍",
        task.issue.repository,
    )

    archive_dir = tmp_path / archive_relative_dir(task)
    manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
    pptx_meta = next(item for item in manifest["artifacts"] if item["artifact_id"] == "pptx-main")
    assert (archive_dir / "source.md").read_text(encoding="utf-8") == "根据暗黑4最新DLC内容"
    assert (archive_dir / "brief.pptx").read_bytes() == b"pptx"
    assert pptx_meta["storage_backend"] == "git_lfs"
    assert pptx_meta["sha256"] == sha256_file(archive_dir / "brief.pptx")
    assert result.links["pptx"].endswith("/brief.pptx")


def test_ensure_lfs_tracking_is_idempotent(tmp_path):
    attributes = tmp_path / ".gitattributes"
    attributes.write_text("*.ps1 text eol=crlf\n", encoding="utf-8")

    ensure_lfs_tracking(tmp_path)
    ensure_lfs_tracking(tmp_path)

    lines = attributes.read_text(encoding="utf-8").splitlines()
    assert lines.count("briefings/**/*.pptx filter=lfs diff=lfs merge=lfs -text") == 1


class FakeCodexRunner:
    def __init__(self, generation):
        self.generation = generation
        self.calls = []

    def run(self, task, agent_workspace, runtime_dir, model, timeout_seconds):
        self.calls.append((task, agent_workspace, runtime_dir, model, timeout_seconds))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "source.md").write_text(task.source_text, encoding="utf-8")
        return self.generation


def test_worker_fake_success_flow(tmp_path):
    task = make_task(tmp_path)
    task_store = tmp_path / "tasks.jsonl"
    write_task_store(task_store, task)
    agent_workspace = tmp_path / "AgentWorkspace"
    agent_workspace.mkdir()
    candidate = tmp_path / "candidate.pptx"
    summary = tmp_path / "qa-summary.md"
    qa_json = tmp_path / "qa.json"
    candidate.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    qa_json.write_text('{"passed": true, "issues": []}', encoding="utf-8")
    generation = GenerationRecord(candidate_pptx=candidate, qa_summary=summary, qa_json=qa_json)
    qa_result = QaResult(passed=True, summary_path=summary, report_path=qa_json)
    issue_client = FakeIssueClient()
    publisher = FakeGitPublisher()
    settings = WorkerSettings(
        repository=task.issue.repository,
        github_token=None,
        task_store_path=task_store,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        agent_workspace=agent_workspace,
        codex_model="gpt-5.5",
        codex_timeout_seconds=30,
        poll_interval_seconds=1,
        git_remote="origin",
        base_branch="main",
    )
    worker = Worker(
        settings=settings,
        task_reader=JsonlTaskReader(task_store),
        state_store=StateStore(tmp_path / "state"),
        issue_client=issue_client,
        codex_runner=FakeCodexRunner(generation),
        qa_runner=FakeQaRunner(qa_result),
        archiver=Archiver(tmp_path),
        git_publisher=publisher,
    )

    assert worker.run_once(task.request_id) is True

    statuses = [
        comment["body"].split("`")[1]
        for comment in issue_client.comments
        if comment["body"].startswith("## Worker status:")
    ]
    assert statuses == [
        "running",
        "generating",
        "generation_completed",
        "qa_passed",
        "publishing",
        "completed",
    ]
    assert publisher.published == [(9, branch_name_for(task))]
    state = StateStore(tmp_path / "state").read(task.request_id)
    assert state["status"] == "completed"
    assert state["links"]["manifest"].endswith("/manifest.json")


class FlakyIssueClient(FakeIssueClient):
    def __init__(self, fail_status):
        super().__init__()
        self.fail_status = fail_status

    def add_comment(self, repository, issue_number, body):
        if f"## Worker status: `{self.fail_status}`" in body:
            raise IssueUpdateError("temporary GitHub outage")
        super().add_comment(repository, issue_number, body)


def test_worker_issue_comment_failure_does_not_abort_success_flow(tmp_path):
    task = make_task(tmp_path)
    task_store = tmp_path / "tasks.jsonl"
    write_task_store(task_store, task)
    agent_workspace = tmp_path / "AgentWorkspace"
    agent_workspace.mkdir()
    candidate = tmp_path / "candidate.pptx"
    summary = tmp_path / "qa-summary.md"
    qa_json = tmp_path / "qa.json"
    candidate.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    qa_json.write_text('{"passed": true, "issues": []}', encoding="utf-8")
    generation = GenerationRecord(candidate_pptx=candidate, qa_summary=summary, qa_json=qa_json)
    qa_result = QaResult(passed=True, summary_path=summary, report_path=qa_json)
    publisher = FakeGitPublisher()
    settings = WorkerSettings(
        repository=task.issue.repository,
        github_token=None,
        task_store_path=task_store,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        agent_workspace=agent_workspace,
        codex_model="gpt-5.5",
        codex_timeout_seconds=30,
        poll_interval_seconds=1,
        git_remote="origin",
        base_branch="main",
    )
    worker = Worker(
        settings=settings,
        task_reader=JsonlTaskReader(task_store),
        state_store=StateStore(tmp_path / "state"),
        issue_client=FlakyIssueClient("qa_passed"),
        codex_runner=FakeCodexRunner(generation),
        qa_runner=FakeQaRunner(qa_result),
        archiver=Archiver(tmp_path),
        git_publisher=publisher,
    )

    worker.run_once(task.request_id)

    state = StateStore(tmp_path / "state").read(task.request_id)
    assert state["status"] == "completed"
    assert state["issue_update_failed"] is True
    assert publisher.published == [(9, branch_name_for(task))]


def test_completed_details_uses_clickable_archive_and_source_links(tmp_path):
    task = make_task(tmp_path)
    pptx = tmp_path / "candidate.pptx"
    summary = tmp_path / "summary.md"
    qa_json = tmp_path / "qa.json"
    pptx.write_bytes(b"pptx")
    summary.write_text("QA passed", encoding="utf-8")
    qa_json.write_text('{"passed": true, "issues": []}', encoding="utf-8")
    generation = GenerationRecord(candidate_pptx=pptx, qa_summary=summary, qa_json=qa_json)
    qa = QaResult(passed=True, summary_path=summary, report_path=qa_json)
    archive = Archiver(tmp_path).archive(
        task,
        generation,
        qa,
        branch_name_for(task),
        task.issue.repository,
    )

    details = completed_details(archive)

    assert f"- Archive: {archive.links['archive']}" in details
    assert f"- Source: {archive.links['source']}" in details


def test_worker_skips_tasks_with_terminal_state(tmp_path):
    task = make_task(tmp_path)
    task_store = tmp_path / "tasks.jsonl"
    write_task_store(task_store, task)
    state_store = StateStore(tmp_path / "state")
    state_store.update(task.request_id, "completed")
    settings = WorkerSettings(
        repository=task.issue.repository,
        github_token=None,
        task_store_path=task_store,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        agent_workspace=tmp_path / "AgentWorkspace",
        codex_model="gpt-5.5",
        codex_timeout_seconds=30,
        poll_interval_seconds=1,
        git_remote="origin",
        base_branch="main",
    )
    worker = Worker(
        settings=settings,
        task_reader=JsonlTaskReader(task_store),
        state_store=state_store,
        issue_client=FakeIssueClient(),
    )

    assert worker.run_once() is False


def test_worker_cli_drain_runs_until_no_queued_tasks(monkeypatch, tmp_path, capsys):
    calls = []

    class FakeDrainWorker:
        def __init__(self, **kwargs):
            pass

        def run_once(self, request_id=None):
            calls.append(request_id)
            return len(calls) < 3

    monkeypatch.setattr(sys, "argv", ["mozhi-worker", "run", "--drain"])
    monkeypatch.setenv("MOZHI_TASK_STORE_PATH", str(tmp_path / "tasks.jsonl"))
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MOZHI_WORKER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOZHI_AGENT_WORKSPACE", str(tmp_path / "AgentWorkspace"))
    monkeypatch.setattr(worker_cli, "Worker", FakeDrainWorker)

    exit_code = worker_cli.main()

    assert exit_code == 0
    assert calls == [None, None, None]
    assert json.loads(capsys.readouterr().out)["processed"] == 2


def test_worker_settings_defaults_use_durable_runtime_dir(monkeypatch):
    monkeypatch.delenv("MOZHI_TASK_STORE_PATH", raising=False)
    monkeypatch.delenv("MOZHI_WORKER_STATE_DIR", raising=False)
    monkeypatch.delenv("MOZHI_WORKER_LOG_DIR", raising=False)

    settings = WorkerSettings.from_env()

    assert settings.task_store_path.parts[-3:] == (".runtime", "api", "tasks.jsonl")
    assert settings.state_dir.parts[-3:] == (".runtime", "worker", "state")
    assert settings.log_dir.parts[-3:] == (".runtime", "worker", "logs")


def test_worker_default_agent_workspace_is_repo_local(monkeypatch):
    monkeypatch.delenv("MOZHI_AGENT_WORKSPACE", raising=False)
    monkeypatch.setenv("MOZHI_TASK_STORE_PATH", "tasks.jsonl")
    monkeypatch.setenv("MOZHI_WORKER_STATE_DIR", "state")
    monkeypatch.setenv("MOZHI_WORKER_LOG_DIR", "logs")

    settings = WorkerSettings.from_env()

    assert default_agent_workspace_path() == repo_root() / "AgentWorkspace"
    assert settings.agent_workspace == default_agent_workspace_path()


def test_worker_fake_qa_failure_does_not_publish(tmp_path):
    task = make_task(tmp_path)
    task_store = tmp_path / "tasks.jsonl"
    write_task_store(task_store, task)
    agent_workspace = tmp_path / "AgentWorkspace"
    agent_workspace.mkdir()
    candidate = tmp_path / "candidate.pptx"
    summary = tmp_path / "qa-summary.md"
    candidate.write_bytes(b"pptx")
    summary.write_text("QA failed", encoding="utf-8")
    generation = GenerationRecord(candidate_pptx=candidate, qa_summary=summary)
    issue_client = FakeIssueClient()
    publisher = FakeGitPublisher()
    settings = WorkerSettings(
        repository=task.issue.repository,
        github_token=None,
        task_store_path=task_store,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        agent_workspace=agent_workspace,
        codex_model="gpt-5.5",
        codex_timeout_seconds=30,
        poll_interval_seconds=1,
        git_remote="origin",
        base_branch="main",
    )
    worker = Worker(
        settings=settings,
        task_reader=JsonlTaskReader(task_store),
        state_store=StateStore(tmp_path / "state"),
        issue_client=issue_client,
        codex_runner=FakeCodexRunner(generation),
        qa_runner=FakeQaRunner(QaResult(passed=False, summary_path=summary, reason="layout overflow")),
        archiver=Archiver(tmp_path),
        git_publisher=publisher,
    )

    worker.run_once(task.request_id)

    state = StateStore(tmp_path / "state").read(task.request_id)
    assert state["status"] == "qa_failed"
    assert publisher.published == []
    assert any("layout overflow" in comment["body"] for comment in issue_client.comments)
