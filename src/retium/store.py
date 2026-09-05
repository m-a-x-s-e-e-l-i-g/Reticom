from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class EventStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    callsign TEXT NOT NULL,
                    sender_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    packet_hash TEXT,
                    interface_name TEXT,
                    rssi REAL,
                    snr REAL,
                    delivery_status TEXT NOT NULL DEFAULT 'verified'
                )
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(events)").fetchall()
            }
            if "delivery_status" not in columns:
                self._connection.execute(
                    "ALTER TABLE events ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'verified'"
                )

    def insert(
        self,
        event: dict[str, Any],
        sender_hash: str,
        *,
        packet_hash: str | None = None,
        interface_name: str | None = None,
        rssi: float | None = None,
        snr: float | None = None,
        delivery_status: str = "verified",
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, event_type, callsign, sender_hash, created_at,
                    received_at, payload_json, packet_hash, interface_name, rssi, snr,
                    delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["type"],
                    event["callsign"],
                    sender_hash,
                    event["created_at"],
                    int(time.time()),
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    packet_hash,
                    interface_name,
                    rssi,
                    snr,
                    delivery_status,
                ),
            )
            return cursor.rowcount == 1

    def mark_verified(
        self,
        event_id: str,
        *,
        packet_hash: str | None = None,
        interface_name: str = "Authenticated Reticulum delivery",
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE events
                SET delivery_status = 'verified', packet_hash = COALESCE(?, packet_hash),
                    interface_name = ?
                WHERE event_id = ?
                """,
                (packet_hash, interface_name, event_id),
            )

    def message_owned_by(self, message_id: str, sender_hash: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT event_type, sender_hash FROM events WHERE event_id = ?",
                (message_id,),
            ).fetchone()
        return bool(
            row
            and row["event_type"] == "chat.message"
            and row["sender_hash"] == sender_hash
        )

    def event_exists(self, event_id: str, event_type: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM events WHERE event_id = ? AND event_type = ?",
                (event_id, event_type),
            ).fetchone()
        return row is not None

    def map_event(self, event_id: str, event_type: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json, sender_hash FROM events WHERE event_id = ? AND event_type = ?",
                (event_id, event_type),
            ).fetchone()
        if row is None:
            return None
        event = json.loads(row["payload_json"])
        event["sender_hash"] = row["sender_hash"]
        return event

    def map_event_owned_by(
        self, event_id: str, event_type: str, sender_hash: str
    ) -> bool:
        event = self.map_event(event_id, event_type)
        return bool(event and event["sender_hash"] == sender_hash)

    def clear_communications(self, since: int | None = None) -> dict[str, Any]:
        """Delete chat/voice history while preserving operational session data."""
        event_types = ("chat.message", "message.deleted", "ptt.broadcast")
        placeholders = ",".join("?" for _ in event_types)
        where = f"event_type IN ({placeholders})"
        parameters: tuple[Any, ...] = event_types
        if since is not None:
            where += " AND received_at >= ?"
            parameters += (since,)

        with self._lock, self._connection:
            rows = self._connection.execute(
                f"SELECT event_type, payload_json FROM events WHERE {where}",
                parameters,
            ).fetchall()
            clip_ids: list[str] = []
            for row in rows:
                if row["event_type"] != "ptt.broadcast":
                    continue
                try:
                    clip_id = json.loads(row["payload_json"]).get("clip_id")
                except (AttributeError, json.JSONDecodeError):
                    clip_id = None
                if isinstance(clip_id, str):
                    clip_ids.append(clip_id)
            self._connection.execute(
                f"DELETE FROM events WHERE {where}", parameters
            )
        return {"events": len(rows), "clip_ids": list(dict.fromkeys(clip_ids))}

    @staticmethod
    def _visible(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        message_owners: dict[str, str] = {}
        map_owners: dict[str, str] = {}
        for row in rows:
            item = json.loads(row["payload_json"])
            item["network"] = {
                "verified": row["delivery_status"] == "verified",
                "queued": row["delivery_status"] == "queued",
                "sender_hash": row["sender_hash"],
                "packet_hash": row["packet_hash"],
                "interface": row["interface_name"],
                "received_at": row["received_at"],
                "rssi": row["rssi"],
                "snr": row["snr"],
            }
            items.append(item)
            if item["type"] == "chat.message":
                message_owners[item["id"]] = row["sender_hash"]
            if item["type"] in {"marker.created", "drawing.created"}:
                map_owners[item["id"]] = row["sender_hash"]

        previous_position_at: dict[str, int] = {}
        for item in reversed(items):
            if item["type"] != "position.updated":
                continue
            sender_hash = item["network"]["sender_hash"]
            received_at = int(item["network"]["received_at"])
            previous = previous_position_at.get(sender_hash)
            silence_seconds = received_at - previous if previous is not None else None
            item["network"]["became_active"] = bool(
                silence_seconds is not None and silence_seconds > 60 * 60
            )
            if item["network"]["became_active"]:
                item["network"]["position_silence_seconds"] = silence_seconds
            previous_position_at[sender_hash] = received_at

        moderation_interfaces = {
            "Command origin · Reticulum feed",
            "Team admin · Authenticated Reticulum Link",
        }
        deleted = {
            item["message_id"]
            for item in items
            if item["type"] == "message.deleted"
            and (
                message_owners.get(item["message_id"])
                == item["network"]["sender_hash"]
                or item["network"].get("interface") in moderation_interfaces
            )
        }
        deleted_map: set[str] = set()
        for item in items:
            if item["type"] not in {"marker.deleted", "drawing.deleted"}:
                continue
            reference = item.get(
                "marker_id" if item["type"] == "marker.deleted" else "drawing_id"
            )
            moderation_origin = item["network"].get("interface") in moderation_interfaces
            if reference and (
                map_owners.get(reference) == item["network"]["sender_hash"]
                or moderation_origin
            ):
                deleted_map.add(reference)
        return [
            item
            for item in items
            if item["type"]
            not in {"message.deleted", "marker.deleted", "drawing.deleted"}
            and item["id"] not in deleted
            and item["id"] not in deleted_map
        ]

    def recent(self, limit: int = 200, since: int | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._lock:
            if since is None:
                rows = self._connection.execute(
                    "SELECT * FROM events ORDER BY received_at DESC, rowid DESC",
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE received_at >= ? "
                    "ORDER BY received_at DESC, rowid DESC",
                    (since,),
                ).fetchall()
        return self._visible(rows)[:limit]

    def count(self, since: int | None = None) -> int:
        with self._lock:
            if since is None:
                rows = self._connection.execute("SELECT * FROM events").fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE received_at >= ?", (since,)
                ).fetchall()
        return len(self._visible(rows))

    def operators(self, since: int | None = None) -> list[dict[str, Any]]:
        """Return one current, verified summary per sending Reticulum identity."""
        events = self.recent(500, since=since)
        summaries: dict[str, dict[str, Any]] = {}
        for event in events:
            sender_hash = event["network"]["sender_hash"]
            if sender_hash not in summaries:
                summaries[sender_hash] = {
                    "sender_hash": sender_hash,
                    "callsign": event["callsign"],
                    "icon": event.get("icon", "dot"),
                    "color": event.get("color", "moss"),
                    "last_seen": event["network"]["received_at"],
                    "event_count": 0,
                    "position": None,
                }
            summary = summaries[sender_hash]
            summary["event_count"] += 1
            if summary["position"] is None and event["type"] == "position.updated":
                summary["position"] = {
                    "lat": event["lat"],
                    "lon": event["lon"],
                    "accuracy": event.get("accuracy"),
                    "created_at": event["created_at"],
                }
        return list(summaries.values())

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class PrivateMessageStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS private_messages (
                    event_id TEXT PRIMARY KEY,
                    sender_hash TEXT NOT NULL,
                    recipient_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    packet_hash TEXT,
                    interface_name TEXT
                )
                """
            )

    def insert(
        self,
        event: dict[str, Any],
        sender_hash: str,
        *,
        packet_hash: str | None = None,
        interface_name: str | None = None,
    ) -> bool:
        if event.get("type") not in {"private.message", "private.ptt"}:
            raise ValueError("private message store only accepts private communications")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO private_messages (
                    event_id, sender_hash, recipient_hash, created_at,
                    received_at, payload_json, packet_hash, interface_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    sender_hash,
                    event["recipient_hash"],
                    event["created_at"],
                    int(time.time()),
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    packet_hash,
                    interface_name,
                ),
            )
            return cursor.rowcount == 1

    def recent(
        self,
        identity_hash: str,
        *,
        peer_hash: str | None = None,
        limit: int = 300,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        where = "(sender_hash = ? OR recipient_hash = ?)"
        parameters: list[Any] = [identity_hash, identity_hash]
        if peer_hash is not None:
            where += " AND (sender_hash = ? OR recipient_hash = ?)"
            parameters.extend([peer_hash, peer_hash])
        if since is not None:
            where += " AND received_at >= ?"
            parameters.append(since)
        parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM ("
                f"SELECT rowid AS _sequence, * FROM private_messages WHERE {where} "
                "ORDER BY received_at DESC, rowid DESC LIMIT ?"
                ") ORDER BY received_at ASC, _sequence ASC",
                tuple(parameters),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            event = json.loads(row["payload_json"])
            event["network"] = {
                "verified": True,
                "sender_hash": row["sender_hash"],
                "packet_hash": row["packet_hash"],
                "interface": row["interface_name"],
                "received_at": row["received_at"],
            }
            messages.append(event)
        return messages

    def clip_visibility(self, clip_id: str, identity_hash: str) -> bool | None:
        """Return access for a private clip, or None when the clip is not private."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT sender_hash, recipient_hash, payload_json FROM private_messages"
            ).fetchall()
        for row in rows:
            try:
                event = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if event.get("type") != "private.ptt" or event.get("clip_id") != clip_id:
                continue
            return identity_hash in {row["sender_hash"], row["recipient_hash"]}
        return None

    def close(self) -> None:
        with self._lock:
            self._connection.close()
