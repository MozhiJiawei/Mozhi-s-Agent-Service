from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Task

SERVICE_TIMEZONE = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(SERVICE_TIMEZONE).isoformat()


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def path_for(self, request_id: str) -> Path:
        return self.state_dir / f"{request_id}.json"

    def read(self, request_id: str) -> dict[str, Any] | None:
        path = self.path_for(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, request_id: str, state: dict[str, Any]) -> dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {**state, "updated_at": now_iso()}
        self.path_for(request_id).write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return state

    def claim_task(self, task: Task) -> dict[str, Any]:
        existing = self.read(task.request_id)
        if existing and existing.get("status") in {"running", "generating", "generation_completed", "qa_passed", "publishing", "completed"}:
            return existing
        state = {
            "request_id": task.request_id,
            "status": "running",
            "title": task.title,
            "created_at": task.created_at,
            "claimed_at": now_iso(),
            "issue": {
                "number": task.issue.number,
                "url": task.issue.url,
                "repository": task.issue.repository,
            },
        }
        return self.write(task.request_id, state)

    def update(self, request_id: str, status: str, **fields: Any) -> dict[str, Any]:
        state = self.read(request_id) or {"request_id": request_id}
        state.update(fields)
        state["status"] = status
        return self.write(request_id, state)
