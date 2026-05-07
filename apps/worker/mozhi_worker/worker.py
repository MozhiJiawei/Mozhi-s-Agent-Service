from __future__ import annotations

import time
from pathlib import Path

from .archive import Archiver
from .codex_runner import CodexRunner
from .config import WorkerSettings, repo_root
from .git_publish import GitPublisher, LocalGitPublisher, branch_name_for
from .issue import (
    IssueClient,
    IssueUpdateError,
    comment_issue_milestone,
    completed_details,
    default_issue_client,
    qa_failure_details,
)
from .models import GenerationRecord, IN_PROGRESS_STATUSES, QaFailedError, QaResult, TERMINAL_STATUSES, Task, WorkerError
from .qa import AgentWorkspaceQaRunner, QaRunner
from .state import StateStore
from .task_store import JsonlTaskReader


class Worker:
    def __init__(
        self,
        settings: WorkerSettings | None = None,
        task_reader: JsonlTaskReader | None = None,
        state_store: StateStore | None = None,
        issue_client: IssueClient | None = None,
        codex_runner: CodexRunner | None = None,
        qa_runner: QaRunner | None = None,
        archiver: Archiver | None = None,
        git_publisher: GitPublisher | None = None,
    ) -> None:
        self.settings = settings or WorkerSettings.from_env()
        self.task_reader = task_reader or JsonlTaskReader(self.settings.task_store_path)
        self.state_store = state_store or StateStore(self.settings.state_dir)
        self.issue_client = issue_client or default_issue_client(self.settings.github_token)
        self.codex_runner = codex_runner or CodexRunner()
        self.qa_runner = qa_runner or AgentWorkspaceQaRunner(self.settings.agent_workspace)
        self.archiver = archiver or Archiver(repo_root())
        self.git_publisher = git_publisher or LocalGitPublisher(
            repo_root(), self.settings.git_remote, self.settings.base_branch
        )

    def run_once(self, request_id: str | None = None) -> bool:
        task = self._next_task(request_id)
        if not task:
            return False
        self.process(task)
        return True

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.settings.poll_interval_seconds)

    def process(self, task: Task) -> None:
        generation: GenerationRecord | None = None
        qa: QaResult | None = None
        try:
            self.state_store.claim_task(task)
            self._comment_issue_milestone(task, "running", "claim-task")

            runtime_dir = self._runtime_dir(task)
            self.state_store.update(task.request_id, "generating", runtime_dir=str(runtime_dir))
            self._comment_issue_milestone(task, "generating", "codex-start")
            generation = self.codex_runner.run(
                task,
                self.settings.agent_workspace,
                runtime_dir,
                self.settings.codex_model,
                self.settings.codex_timeout_seconds,
            )
            self.state_store.update(
                task.request_id,
                "generation_completed",
                candidate_pptx=str(generation.candidate_pptx),
                qa_summary=str(generation.qa_summary),
                qa_json=str(generation.qa_json) if generation.qa_json else None,
                slides_dir=str(generation.slides_dir) if generation.slides_dir else None,
                deck_workspace=str(generation.deck_workspace) if generation.deck_workspace else None,
            )
            self._comment_issue_milestone(
                task,
                "generation_completed",
                "record-generation",
                f"Candidate PPTX: `{generation.candidate_pptx}`",
            )

            qa = self.qa_runner.run(generation)
            self.state_store.update(
                task.request_id,
                "qa_passed",
                qa_summary=str(qa.summary_path),
                qa_report=str(qa.report_path) if qa.report_path else None,
            )
            self._comment_issue_milestone(
                task,
                "qa_passed",
                "record-qa-result",
                f"QA summary: `{qa.summary_path}`",
            )

            branch_name = branch_name_for(task)
            self.state_store.update(task.request_id, "publishing", branch=branch_name)
            self._comment_issue_milestone(task, "publishing", "publish-branch")
            archive = self.archiver.archive(
                task,
                generation,
                qa,
                branch_name,
                task.issue.repository or self.settings.repository,
            )
            self.git_publisher.publish(task, archive)
            self.state_store.update(
                task.request_id,
                "completed",
                branch=branch_name,
                archive_path=archive.archive_dir.as_posix(),
                links=archive.links,
            )
            self._comment_issue_milestone(
                task,
                "completed",
                "complete-issue",
                completed_details(archive),
            )
        except QaFailedError as exc:
            summary = qa or QaResult(
                passed=False,
                summary_path=(generation.qa_summary if generation else Path("")),
                report_path=(generation.qa_json if generation else None),
                reason=str(exc),
            )
            self.state_store.update(
                task.request_id,
                "qa_failed",
                reason=str(exc),
                qa_summary=str(summary.summary_path) if summary.summary_path else None,
                qa_report=str(summary.report_path) if summary.report_path else None,
            )
            self._comment_issue_milestone(
                task,
                "qa_failed",
                "record-qa-result",
                qa_failure_details(summary),
            )
        except WorkerError as exc:
            self.state_store.update(task.request_id, "failed", reason=str(exc))
            details = "\n".join(
                [
                    f"Failure stage: `{getattr(exc, 'stage', 'worker')}`",
                    f"Reason: {exc}",
                    "Retryability: retryable after fixing the worker environment or rerunning the task.",
                    "Suggested next action: inspect the worker state/logs, fix the reported environment or artifact issue, then rerun the task manually.",
                ]
            )
            self._comment_issue_milestone(task, "failed", "worker", details)
        except Exception as exc:
            self.state_store.update(task.request_id, "failed", reason=str(exc))
            details = "\n".join(
                [
                    "Failure stage: `worker`",
                    f"Reason: {exc}",
                    "Retryability: retryable after fixing the unexpected worker error.",
                    "Suggested next action: inspect the worker logs and rerun the task manually after the root cause is fixed.",
                ]
            )
            self._comment_issue_milestone(task, "failed", "worker", details)

    def _next_task(self, request_id: str | None) -> Task | None:
        if request_id:
            return self.task_reader.find(request_id)
        for task in self.task_reader.queued_tasks():
            state = self.state_store.read(task.request_id)
            if state and state.get("status") in TERMINAL_STATUSES | IN_PROGRESS_STATUSES:
                continue
            return task
        return None

    def _runtime_dir(self, task: Task) -> Path:
        return self.settings.agent_workspace / ".tmp" / "mozhi-service" / task.request_id

    def _comment_issue_milestone(
        self,
        task: Task,
        status: str,
        stage: str,
        details: str = "",
    ) -> None:
        try:
            comment_issue_milestone(self.issue_client, task, status, stage, details)
        except IssueUpdateError:
            current = self.state_store.read(task.request_id) or {}
            self.state_store.update(
                task.request_id,
                str(current.get("status") or status),
                issue_update_status=status,
                issue_update_stage=stage,
                issue_update_failed=True,
            )
