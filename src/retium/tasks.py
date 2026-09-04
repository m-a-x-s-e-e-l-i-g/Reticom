from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

TASK_TITLE_MAX_LENGTH = 100
TASK_ASSIGNEE_MAX_LENGTH = 24


class TaskError(ValueError):
    pass


def validate_task_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskError("task id must be a UUID") from exc


class TaskStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    assignee TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    completed_by TEXT,
                    completed_sender_hash TEXT
                )
                """
            )

    def create(self, title: Any, assignee: Any = None) -> dict[str, Any]:
        clean_title = title.strip() if isinstance(title, str) else ""
        if not clean_title or len(clean_title) > TASK_TITLE_MAX_LENGTH:
            raise TaskError(f"task title must be 1-{TASK_TITLE_MAX_LENGTH} characters")
        clean_assignee = assignee.strip() if isinstance(assignee, str) else ""
        if len(clean_assignee) > TASK_ASSIGNEE_MAX_LENGTH:
            raise TaskError(
                f"assignee must be at most {TASK_ASSIGNEE_MAX_LENGTH} characters"
            )
        task = {
            "id": str(uuid.uuid4()),
            "title": clean_title,
            "assignee": clean_assignee or None,
            "created_at": int(time.time()),
            "completed_at": None,
            "completed_by": None,
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO tasks (task_id, title, assignee, created_at) VALUES (?, ?, ?, ?)",
                (task["id"], task["title"], task["assignee"], task["created_at"]),
            )
        return task

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM tasks ORDER BY completed_at IS NOT NULL, created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["task_id"],
                "title": row["title"],
                "assignee": row["assignee"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "completed_by": row["completed_by"],
            }
            for row in rows
        ]

    def complete(
        self, task_id: Any, completed_by: str, sender_hash: str
    ) -> dict[str, Any] | None:
        task_id = validate_task_id(task_id)
        completed_by = completed_by.strip()
        if not completed_by or len(completed_by) > TASK_ASSIGNEE_MAX_LENGTH:
            raise TaskError("completed_by must be a valid callsign")
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE tasks
                SET completed_at = COALESCE(completed_at, ?),
                    completed_by = COALESCE(completed_by, ?),
                    completed_sender_hash = COALESCE(completed_sender_hash, ?)
                WHERE task_id = ?
                """,
                (int(time.time()), completed_by, sender_hash, task_id),
            )
            row = self._connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["task_id"],
            "title": row["title"],
            "assignee": row["assignee"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "completed_by": row["completed_by"],
        }

    def delete(self, task_id: Any) -> dict[str, Any] | None:
        task_id = validate_task_id(task_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "DELETE FROM tasks WHERE task_id = ?", (task_id,)
            )
        return {
            "id": row["task_id"],
            "title": row["title"],
            "assignee": row["assignee"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "completed_by": row["completed_by"],
        }
