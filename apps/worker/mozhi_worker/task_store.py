from __future__ import annotations

import json
from pathlib import Path

from .models import TERMINAL_STATUSES, Task


class JsonlTaskReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def queued_tasks(self) -> list[Task]:
        if not self.path.exists():
            return []
        tasks: list[Task] = []
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                task = Task.from_json(json.loads(line))
                if task.status not in TERMINAL_STATUSES:
                    tasks.append(task)
        return tasks

    def find(self, request_id: str) -> Task | None:
        for task in self.queued_tasks():
            if task.request_id == request_id:
                return task
        return None
