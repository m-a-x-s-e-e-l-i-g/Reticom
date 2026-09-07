"""Read-only operational snapshots, including stopped Command teams.

Never starts a host or reads the private-message store. Tombstones are resolved
before selecting history so an old deleted marker cannot reappear in overview.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from pathlib import Path

from .store import EventStore


def read_team_snapshot(directory: Path, created_at: int | None, modules: list[str]) -> dict:
    events_path = directory / "events.sqlite3"
    visible = []
    if events_path.exists():
        with closing(sqlite3.connect(events_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM events WHERE received_at >= ? ORDER BY received_at DESC, rowid DESC",
                (created_at or 0,),
            ).fetchall()
            visible = EventStore._visible(rows)
    events = []
    positions = Counter()
    recent_intel = 0
    for event in visible:
        kind = event["type"]
        if kind.startswith("private."):
            continue
        if kind == "position.updated":
            sender = event["network"]["sender_hash"]
            positions[sender] += 1
            if positions[sender] > 80:
                continue
        elif kind not in {"marker.created", "drawing.created", "navigation.updated", "navigation.stopped"}:
            recent_intel += 1
            if recent_intel > 100:
                continue
        events.append(event)
        # Only use a finished local transcript; overview never starts STT work.
        if kind == "ptt.broadcast":
            try:
                clip_id = str(uuid.UUID(str(event.get("clip_id"))))
                transcript = json.loads((directory / "transcripts" / f"{clip_id}.json").read_text(encoding="utf-8"))
                if transcript.get("status") == "ready":
                    event["transcript"] = str(transcript.get("text", ""))
            except (ValueError, OSError, TypeError, AttributeError):
                pass
    open_tasks = 0
    task_path = directory / "tasks.sqlite3"
    if "tasks" in modules and task_path.exists():
        with closing(sqlite3.connect(task_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            open_tasks = connection.execute("SELECT count(*) FROM tasks WHERE completed_at IS NULL").fetchone()[0]
    elif "tasks" in modules and events_path.exists():
        # Joined Command teams keep task snapshots in their mission inbox.
        open_tasks = None
        with closing(sqlite3.connect(events_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='mission_sync' AND type='table'").fetchone():
                row = connection.execute("SELECT payload FROM mission_sync WHERE id=1").fetchone()
                saved = json.loads(row[0]) if row else {}
                if saved.get("last_synced_at"):
                    open_tasks = sum(not task.get("completed_at") for task in saved.get("tasks", []))
    return {
        "events": events, "open_tasks": open_tasks,
        "last_event_at": max((e["network"]["received_at"] for e in events), default=None),
    }
