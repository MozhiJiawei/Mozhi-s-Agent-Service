from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .config import repo_root
from .models import ArchiveResult, GenerationRecord, QaResult, Task, WorkerError


def slugify(value: str, max_length: int = 48) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip()).strip("-")
    if not normalized:
        normalized = "briefing"
    return normalized[:max_length].strip("-") or "briefing"


def archive_relative_dir(task: Task) -> Path:
    created = task.created_at
    year = created[:4]
    month = created[5:7] if len(created) >= 7 and created[4] == "-" else created[4:6]
    return Path("briefings") / year / month / f"issue-{task.issue.number}-{slugify(task.title)}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Archiver:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or repo_root()

    def archive(
        self,
        task: Task,
        generation: GenerationRecord,
        qa: QaResult,
        branch_name: str,
        repository: str,
    ) -> ArchiveResult:
        relative_dir = archive_relative_dir(task)
        archive_dir = self.root / relative_dir
        archive_dir.mkdir(parents=True, exist_ok=True)

        source_path = archive_dir / "source.md"
        pptx_path = archive_dir / "brief.pptx"
        qa_summary_path = archive_dir / "qa-summary.md"
        manifest_path = archive_dir / "manifest.json"

        source_path.write_text(task.source_text, encoding="utf-8")
        shutil.copy2(generation.candidate_pptx, pptx_path)
        shutil.copy2(qa.summary_path, qa_summary_path)

        links = artifact_links(repository, branch_name, relative_dir)
        manifest = {
            "request_id": task.request_id,
            "issue_number": task.issue.number,
            "title": task.title,
            "branch": branch_name,
            "archive_path": relative_dir.as_posix(),
            "artifacts": [
                artifact_metadata("source", "source", source_path, relative_dir / "source.md", "git", links["source"]),
                artifact_metadata("pptx-main", "pptx", pptx_path, relative_dir / "brief.pptx", "git_lfs", links["pptx"]),
                artifact_metadata("qa-summary", "qa_summary", qa_summary_path, relative_dir / "qa-summary.md", "git", links["qa_summary"]),
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ArchiveResult(
            archive_dir=relative_dir,
            branch_name=branch_name,
            manifest_path=manifest_path,
            pptx_path=pptx_path,
            qa_summary_path=qa_summary_path,
            source_path=source_path,
            links=links,
        )


def artifact_metadata(
    artifact_id: str,
    kind: str,
    path: Path,
    logical_path: Path,
    storage_backend: str,
    download_url: str,
) -> dict[str, object]:
    if not path.exists():
        raise WorkerError(f"Archived artifact missing: {path}")
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "logical_path": logical_path.as_posix(),
        "storage_backend": storage_backend,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "download_url": download_url,
    }


def artifact_links(repository: str, branch_name: str, relative_dir: Path) -> dict[str, str]:
    base = f"https://github.com/{repository}/blob/{branch_name}/{relative_dir.as_posix()}"
    return {
        "archive": f"https://github.com/{repository}/tree/{branch_name}/{relative_dir.as_posix()}",
        "source": f"{base}/source.md",
        "pptx": f"{base}/brief.pptx",
        "manifest": f"{base}/manifest.json",
        "qa_summary": f"{base}/qa-summary.md",
    }
