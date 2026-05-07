from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive import Archiver
from .codex_runner import load_handoff
from .config import WorkerSettings, repo_root
from .git_publish import LocalGitPublisher, branch_name_for
from .issue import comment_issue_milestone, default_issue_client, qa_failure_details
from .models import ArchiveResult, QaResult
from .qa import AgentWorkspaceQaRunner
from .state import StateStore
from .task_store import JsonlTaskReader
from .worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mozhi briefing worker")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--drain", action="store_true")
    run_parser.add_argument("--request-id")

    claim_parser = subparsers.add_parser("claim-task")
    claim_parser.add_argument("request_id")

    issue_parser = subparsers.add_parser("comment-issue-milestone")
    issue_parser.add_argument("request_id")
    issue_parser.add_argument("status")
    issue_parser.add_argument("stage")
    issue_parser.add_argument("--details", default="")

    branch_parser = subparsers.add_parser("branch-name")
    branch_parser.add_argument("request_id")

    generation_parser = subparsers.add_parser("record-generation")
    generation_parser.add_argument("request_id")
    generation_parser.add_argument("handoff_json")

    qa_parser = subparsers.add_parser("run-qa")
    qa_parser.add_argument("request_id")
    qa_parser.add_argument("handoff_json")

    qa_result_parser = subparsers.add_parser("record-qa-result")
    qa_result_parser.add_argument("request_id")
    qa_result_parser.add_argument("status", choices=["qa_passed", "qa_failed"])
    qa_result_parser.add_argument("--summary")
    qa_result_parser.add_argument("--report")
    qa_result_parser.add_argument("--reason", default="")

    archive_parser = subparsers.add_parser("archive-artifacts")
    archive_parser.add_argument("request_id")
    archive_parser.add_argument("handoff_json")
    archive_parser.add_argument("--qa-summary", required=True)

    publish_parser = subparsers.add_parser("publish-branch")
    publish_parser.add_argument("request_id")
    publish_parser.add_argument("archive_json")

    complete_parser = subparsers.add_parser("complete-issue")
    complete_parser.add_argument("request_id")
    complete_parser.add_argument("archive_json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = WorkerSettings.from_env()
    reader = JsonlTaskReader(settings.task_store_path)
    if args.command in {None, "run"}:
        worker = Worker(settings=settings, task_reader=reader)
        if getattr(args, "drain", False):
            processed = 0
            while worker.run_once(getattr(args, "request_id", None)):
                processed += 1
                if getattr(args, "request_id", None):
                    break
            print(json.dumps({"processed": processed}, ensure_ascii=False))
            return 0 if processed else 1
        if getattr(args, "once", False):
            return 0 if worker.run_once(getattr(args, "request_id", None)) else 1
        worker.run_forever()
        return 0
    if args.command == "claim-task":
        task = reader.find(args.request_id)
        if not task:
            raise SystemExit(f"Task not found: {args.request_id}")
        StateStore(settings.state_dir).claim_task(task)
        return 0
    if args.command == "comment-issue-milestone":
        task = reader.find(args.request_id)
        if not task:
            raise SystemExit(f"Task not found: {args.request_id}")
        comment_issue_milestone(
            default_issue_client(settings.github_token),
            task,
            args.status,
            args.stage,
            args.details,
        )
        return 0
    if args.command == "branch-name":
        task = reader.find(args.request_id)
        if not task:
            raise SystemExit(f"Task not found: {args.request_id}")
        print(branch_name_for(task))
        return 0
    if args.command == "record-generation":
        task = require_task(reader, args.request_id)
        generation = load_handoff(Path(args.handoff_json))
        StateStore(settings.state_dir).update(
            task.request_id,
            "generation_completed",
            candidate_pptx=str(generation.candidate_pptx),
            qa_summary=str(generation.qa_summary),
            qa_json=str(generation.qa_json) if generation.qa_json else None,
            slides_dir=str(generation.slides_dir) if generation.slides_dir else None,
            deck_workspace=str(generation.deck_workspace) if generation.deck_workspace else None,
        )
        return 0
    if args.command == "run-qa":
        generation = load_handoff(Path(args.handoff_json))
        result = AgentWorkspaceQaRunner(settings.agent_workspace).run(generation)
        print(json.dumps({"passed": result.passed, "summary": str(result.summary_path), "report": str(result.report_path) if result.report_path else None}, ensure_ascii=False))
        return 0
    if args.command == "record-qa-result":
        task = require_task(reader, args.request_id)
        state = StateStore(settings.state_dir)
        issue_client = default_issue_client(settings.github_token)
        if args.status == "qa_passed":
            state.update(task.request_id, "qa_passed", qa_summary=args.summary, qa_report=args.report)
            comment_issue_milestone(issue_client, task, "qa_passed", "record-qa-result", f"QA summary: `{args.summary}`")
        else:
            result = QaResult(False, Path(args.summary or ""), Path(args.report) if args.report else None, args.reason)
            state.update(task.request_id, "qa_failed", reason=args.reason, qa_summary=args.summary, qa_report=args.report)
            comment_issue_milestone(issue_client, task, "qa_failed", "record-qa-result", qa_failure_details(result))
        return 0
    if args.command == "archive-artifacts":
        task = require_task(reader, args.request_id)
        generation = load_handoff(Path(args.handoff_json))
        qa = QaResult(True, Path(args.qa_summary), generation.qa_json)
        branch = branch_name_for(task)
        archive = Archiver(repo_root()).archive(task, generation, qa, branch, task.issue.repository or settings.repository)
        print(
            json.dumps(
                {
                    "archive_dir": archive.archive_dir.as_posix(),
                    "branch": archive.branch_name,
                    "manifest_path": str(archive.manifest_path),
                    "pptx_path": str(archive.pptx_path),
                    "qa_summary_path": str(archive.qa_summary_path),
                    "source_path": str(archive.source_path),
                    "links": archive.links,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "publish-branch":
        task = require_task(reader, args.request_id)
        data = json.loads(Path(args.archive_json).read_text(encoding="utf-8"))
        archive = ArchiveResult(
            archive_dir=Path(data["archive_dir"]),
            branch_name=str(data.get("branch") or branch_name_for(task)),
            manifest_path=Path(data["manifest_path"]),
            pptx_path=Path(data["pptx_path"]),
            qa_summary_path=Path(data["qa_summary_path"]),
            source_path=Path(data["source_path"]),
            links=data["links"],
        )
        LocalGitPublisher(repo_root(), settings.git_remote, settings.base_branch).publish(
            task,
            archive,
        )
        return 0
    if args.command == "complete-issue":
        task = require_task(reader, args.request_id)
        data = json.loads(Path(args.archive_json).read_text(encoding="utf-8"))
        details = "\n".join(
            [
                "Final briefing artifacts are available on the delivery branch.",
                "",
                f"- Branch: `{data['branch']}`",
                f"- Archive: `{data['archive_dir']}`",
                f"- PPTX: {data['links']['pptx']}",
                f"- Manifest: {data['links']['manifest']}",
                f"- QA summary: {data['links']['qa_summary']}",
            ]
        )
        StateStore(settings.state_dir).update(task.request_id, "completed", branch=data["branch"], archive_path=data["archive_dir"], links=data["links"])
        comment_issue_milestone(default_issue_client(settings.github_token), task, "completed", "complete-issue", details)
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


def require_task(reader: JsonlTaskReader, request_id: str):
    task = reader.find(request_id)
    if not task:
        raise SystemExit(f"Task not found: {request_id}")
    return task


if __name__ == "__main__":
    raise SystemExit(main())
