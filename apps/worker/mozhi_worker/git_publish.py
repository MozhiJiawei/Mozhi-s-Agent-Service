from __future__ import annotations

import subprocess
from pathlib import Path

from .archive import archive_relative_dir, slugify
from .models import ArchiveResult, Task, WorkerError


class GitPublisher:
    def publish(self, task: Task, archive: ArchiveResult) -> None:
        raise NotImplementedError


class LocalGitPublisher(GitPublisher):
    def __init__(self, repo_root: Path, remote: str, base_branch: str) -> None:
        self.repo_root = repo_root
        self.remote = remote
        self.base_branch = base_branch

    def publish(self, task: Task, archive: ArchiveResult) -> None:
        run_git(self.repo_root, "fetch", self.remote, self.base_branch)
        run_git(self.repo_root, "checkout", "-B", archive.branch_name, f"{self.remote}/{self.base_branch}")
        ensure_lfs_tracking(self.repo_root)
        rel = archive_relative_dir(task).as_posix()
        run_git(self.repo_root, "add", ".gitattributes", rel)
        run_git(self.repo_root, "commit", "-m", f"feat(briefing): deliver issue {task.issue.number}")
        run_git(self.repo_root, "push", "-u", self.remote, archive.branch_name)


class FakeGitPublisher(GitPublisher):
    def __init__(self) -> None:
        self.published: list[tuple[int, str]] = []

    def publish(self, task: Task, archive: ArchiveResult) -> None:
        self.published.append((task.issue.number, archive.branch_name))


def branch_name_for(task: Task) -> str:
    return f"codex/briefing-issue-{task.issue.number}-{slugify(task.title)}"


def ensure_lfs_tracking(root: Path) -> None:
    attributes = root / ".gitattributes"
    line = "briefings/**/*.pptx filter=lfs diff=lfs merge=lfs -text"
    existing = attributes.read_text(encoding="utf-8") if attributes.exists() else ""
    if line not in existing.splitlines():
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        attributes.write_text(f"{existing}{suffix}{line}\n", encoding="utf-8")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise WorkerError(f"git {' '.join(args)} failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"git {' '.join(args)} timed out.") from exc
