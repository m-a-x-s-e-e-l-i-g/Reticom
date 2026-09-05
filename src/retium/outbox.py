from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class FieldOutbox:
    """Persistent local-first queue for team events awaiting Reticulum delivery."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_events (
                    event_id TEXT PRIMARY KEY,
                    team_destination TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    linked INTEGER NOT NULL,
                    transport TEXT NOT NULL DEFAULT 'event',
                    queued_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(pending_events)"
                ).fetchall()
            }
            if "transport" not in columns:
                self._connection.execute(
                    "ALTER TABLE pending_events ADD COLUMN transport TEXT NOT NULL DEFAULT 'event'"
                )

    def add(
        self,
        team_destination: str,
        event: dict[str, Any],
        *,
        linked: bool,
        transport: str = "event",
    ) -> None:
        destination = str(team_destination).strip().lower()
        if not destination:
            raise ValueError("team destination is required")
        if transport not in {"event", "ptt"}:
            raise ValueError("unsupported outbox transport")
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection:
            if event.get("type") == "position.updated":
                self._connection.execute(
                    "DELETE FROM pending_events WHERE team_destination = ? AND event_type = ?",
                    (destination, "position.updated"),
                )
            self._connection.execute(
                """
                INSERT OR REPLACE INTO pending_events (
                    event_id, team_destination, event_type, event_json, linked,
                    transport, queued_at, attempts, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    event["id"],
                    destination,
                    event["type"],
                    encoded,
                    int(linked),
                    transport,
                    int(time.time()),
                ),
            )

    def pending(self, team_destination: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM pending_events
                WHERE team_destination = ?
                ORDER BY queued_at ASC, rowid ASC
                LIMIT ?
                """,
                (str(team_destination).lower(), max(1, min(limit, 200))),
            ).fetchall()
        return [
            {
                "event": json.loads(row["event_json"]),
                "linked": bool(row["linked"]),
                "transport": row["transport"],
                "attempts": row["attempts"],
                "last_error": row["last_error"],
            }
            for row in rows
        ]

    def failed(self, event_id: str, error: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE pending_events
                SET attempts = attempts + 1, last_error = ?
                WHERE event_id = ?
                """,
                (str(error)[:500], event_id),
            )

    def remove(self, event_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM pending_events WHERE event_id = ?", (event_id,)
            )

    def count(self, team_destination: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM pending_events WHERE team_destination = ?",
                (str(team_destination).lower(),),
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
