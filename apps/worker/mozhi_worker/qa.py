from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import GenerationRecord, QaFailedError, QaResult, WorkerError


class QaRunner:
    def run(self, generation: GenerationRecord) -> QaResult:
        raise NotImplementedError


class AgentWorkspaceQaRunner(QaRunner):
    def __init__(self, agent_workspace: Path) -> None:
        self.agent_workspace = agent_workspace

    def run(self, generation: GenerationRecord) -> QaResult:
        if not generation.deck_workspace:
            raise WorkerError("Cannot run QA without deck_workspace from Codex handoff.")
        check_script = self.agent_workspace / "skills" / "hw-ppt-gen" / "scripts" / "qa" / "check_huawei_pptx.js"
        if not check_script.exists():
            raise WorkerError(f"QA checker is missing: {check_script}")
        report = generation.deck_workspace / "worker.qa.json"
        command = ["node", str(check_script), str(generation.candidate_pptx), "--out", str(report)]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.agent_workspace / "skills" / "hw-ppt-gen"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
        except FileNotFoundError as exc:
            raise WorkerError("Node.js is required to run PPT QA but was not found.") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkerError("PPT QA timed out after 600 seconds.") from exc
        except OSError as exc:
            raise WorkerError(f"PPT QA could not start: {exc}") from exc
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "QA failed.").strip()
            raise QaFailedError(reason)
        result = result_from_report(report, generation.qa_summary)
        if not result.passed:
            raise QaFailedError(result.reason)
        return result


class FakeQaRunner(QaRunner):
    def __init__(self, result: QaResult) -> None:
        self.result = result

    def run(self, generation: GenerationRecord) -> QaResult:
        if not self.result.passed:
            raise QaFailedError(self.result.reason)
        return self.result


def result_from_report(report_path: Path, summary_path: Path) -> QaResult:
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError(f"QA report is unreadable: {report_path}: {exc}") from exc
    if "passed" not in data:
        return QaResult(
            passed=False,
            summary_path=summary_path,
            report_path=report_path,
            reason="QA report is ambiguous because it does not include an explicit passed field.",
        )
    issues = data.get("issues", [])
    errors = [
        issue
        for issue in issues
        if str(issue.get("severity", "")).lower() in {"error", "blocker"}
    ]
    passed = bool(data.get("passed", not errors)) and not errors
    reason = ""
    if not passed:
        reason = "; ".join(str(issue.get("message", issue)) for issue in errors[:3])
        if not reason:
            reason = str(data.get("summary", "QA rejected the generated PPT candidate."))
    warnings = [
        str(issue.get("message", issue))
        for issue in issues
        if str(issue.get("severity", "")).lower() == "warning"
    ]
    return QaResult(
        passed=passed,
        summary_path=summary_path,
        report_path=report_path,
        reason=reason,
        warnings=warnings,
    )
