from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import repo_root
from .models import GenerationRecord, Task, WorkerError


class CodexRunner:
    def __init__(self, executable: str = "codex") -> None:
        self.executable = executable

    def run(
        self,
        task: Task,
        agent_workspace: Path,
        runtime_dir: Path,
        model: str,
        timeout_seconds: int,
    ) -> GenerationRecord:
        if not agent_workspace.exists():
            raise WorkerError(f"AgentWorkspace does not exist: {agent_workspace}")
        command = codex_command(self.executable)
        if command is None:
            raise WorkerError(f"Codex CLI is not installed or not on PATH: {self.executable}")

        runtime_dir.mkdir(parents=True, exist_ok=True)
        source_path = runtime_dir / "source.md"
        contract_path = runtime_dir / "worker_contract.json"
        handoff_path = runtime_dir / "handoff.json"
        status_script_path = runtime_dir / "report-status.ps1"
        stdout_path = runtime_dir / "codex.stdout.log"
        stderr_path = runtime_dir / "codex.stderr.log"
        source_path.write_text(task.source_text, encoding="utf-8")
        status_script_path.write_text(
            status_script(task.request_id),
            encoding="utf-8",
        )
        contract_path.write_text(
            json.dumps(
                handoff_contract(handoff_path, status_script_path),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if handoff_path.exists():
            return load_handoff(handoff_path)

        prompt = build_codex_prompt(task, source_path, contract_path, handoff_path, status_script_path)
        try:
            result = subprocess.run(
                [
                    command,
                    "--search",
                    "--ask-for-approval",
                    "never",
                    "exec",
                    "-m",
                    model,
                    "--sandbox",
                    "danger-full-access",
                    "-C",
                    str(agent_workspace),
                    "-",
                ],
                check=False,
                capture_output=True,
                input=prompt,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerError(f"Codex generation timed out after {timeout_seconds} seconds.") from exc
        except OSError as exc:
            raise WorkerError(f"Codex generation could not start: {exc}") from exc

        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-1200:]
            raise WorkerError(f"Codex generation failed with exit code {result.returncode}: {detail}")
        return load_handoff(handoff_path)


def codex_command(executable: str) -> str | None:
    if os.name == "nt" and Path(executable).name == executable:
        for candidate in (f"{executable}.cmd", f"{executable}.exe", executable):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None
    return shutil.which(executable)


def handoff_contract(handoff_path: Path, status_script_path: Path) -> dict[str, object]:
    return {
        "handoff_path": str(handoff_path),
        "status_script_path": str(status_script_path),
        "required_fields": [
            "candidate_pptx",
            "qa_summary",
            "qa_json",
            "slides_dir",
            "deck_workspace",
            "status",
        ],
        "rules": [
            "Update the GitHub Issue yourself during the AI loop by calling status_script_path.",
            "Report meaningful long-running stages such as research, outline, deck generation, and QA.",
            "Do not modify or commit files in Mozhi-s-Agent-Service.",
            "Place generation artifacts under AgentWorkspace .tmp.",
        ],
    }


def status_script(request_id: str) -> str:
    worker_path = repo_root() / "apps" / "worker"
    return "\n".join(
        [
            "param(",
            "  [Parameter(Mandatory=$true)][string]$Status,",
            "  [Parameter(Mandatory=$true)][string]$Stage,",
            "  [string]$Details = ''",
            ")",
            "",
            "$ErrorActionPreference = 'Stop'",
            f"$env:PYTHONPATH = '{worker_path}'",
            "python -m mozhi_worker.cli comment-issue-milestone "
            f"{request_id} $Status $Stage --details $Details",
            "",
        ]
    )


def build_codex_prompt(
    task: Task,
    source_path: Path,
    contract_path: Path,
    handoff_path: Path,
    status_script_path: Path,
) -> str:
    return "\n".join(
        [
            "You are running inside Mozhi-s-AgentWorkspace.",
            "Read AGENTS.md and use the registered skills it exposes.",
            f"Briefing title: {task.title}",
            f"Request ID: {task.request_id}",
            f"GitHub Issue: {task.issue.url}",
            f"Use the source material at: {source_path}",
            "Create a Chinese, image-rich briefing PPTX based on that source material.",
            "If the source material references a webpage, PDF, paper, or link, use the workspace skills to fetch, parse, analyze, and then generate the deck.",
            f"During the long-running AI loop, update the GitHub Issue by calling: powershell -ExecutionPolicy Bypass -File \"{status_script_path}\" -Status generating -Stage <stage-name> -Details <short markdown progress update>",
            "Post status updates at meaningful milestones, including after research starts, after source analysis, after outline/storyline, before PPT generation, and after PPT QA completes.",
            "Complete the required PPT generation QA described by the relevant skill.",
            f"Follow the worker contract at: {contract_path}",
            f"Write the final machine-readable handoff JSON to: {handoff_path}",
            "Do not archive into Mozhi-s-Agent-Service and do not run git push.",
        ]
    )


def load_handoff(path: Path) -> GenerationRecord:
    if not path.exists():
        raise WorkerError(f"Codex did not write handoff JSON: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"Codex handoff JSON is invalid: {exc}") from exc

    missing = [
        field
        for field in ["candidate_pptx", "qa_summary", "qa_json", "slides_dir", "deck_workspace", "status"]
        if not data.get(field)
    ]
    if missing:
        raise WorkerError(f"Codex handoff JSON missing fields: {', '.join(missing)}")

    candidate = Path(data["candidate_pptx"])
    qa_summary = resolve_qa_summary(data["qa_summary"], path)
    qa_json = Path(data["qa_json"])
    slides_dir = Path(data["slides_dir"])
    deck_workspace = Path(data["deck_workspace"])
    for required_path, label in [(candidate, "candidate_pptx"), (qa_summary, "qa_summary")]:
        if not required_path.exists():
            raise WorkerError(f"Codex handoff path does not exist for {label}: {required_path}")
    return GenerationRecord(
        candidate_pptx=candidate,
        qa_summary=qa_summary,
        qa_json=qa_json,
        slides_dir=slides_dir,
        deck_workspace=deck_workspace,
        status=str(data["status"]),
        log_summary=str(data.get("log_summary", "")),
    )


def resolve_qa_summary(value: object, handoff_path: Path) -> Path:
    if isinstance(value, str):
        return Path(value)
    if isinstance(value, dict):
        summary_path = handoff_path.with_name("qa-summary.json")
        summary_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary_path
    raise WorkerError("Codex handoff qa_summary must be a path string or JSON object.")
