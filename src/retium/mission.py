"""Bounded, resumable operational snapshots over the identified team feed.

No host keys or private mailboxes of other operators are exported. Audio bytes
remain on-demand. Incomplete snapshots never replace the last usable picture.
"""
import hashlib
import json
import threading
import time
from collections import Counter, OrderedDict


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def select_mission_events(visible, intel_limit=100, positions_per_operator=20):
    selected, positions, profiles, intel = [], Counter(), set(), 0
    for event in visible:
        kind = event["type"]
        sender = event["network"]["sender_hash"]
        if kind.startswith("private."):
            continue
        if kind == "position.updated":
            positions[sender] += 1
            if positions[sender] > positions_per_operator:
                continue
        elif kind == "profile.updated":
            if sender in profiles:
                continue
            profiles.add(sender)
        elif kind not in {"marker.created", "drawing.created", "navigation.updated", "navigation.stopped"}:
            intel += 1
            if intel > intel_limit:
                continue
        selected.append(event)
    return selected


class MissionPublisher:
    def __init__(self):
        self.snapshots = OrderedDict()
        self.lock = threading.Lock()

    def page(self, sender, request, build):
        if not isinstance(request, dict):
            raise ValueError("invalid mission request")
        offset = request.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= 20000:
            raise ValueError("invalid mission offset")
        with self.lock:
            now = time.monotonic()
            self.snapshots = OrderedDict((key, value) for key, value in self.snapshots.items() if now - value[0] < 600)
            snapshot = request.get("snapshot")
            if snapshot:
                cached = self.snapshots.get((sender, str(snapshot)))
                if cached is None:
                    return {"mission_restart": True}
                _, payload, records = cached
            else:
                payload = build()
                records = ([{"kind": "event", "value": value} for value in payload.get("events", [])]
                           + [{"kind": "task", "value": value} for value in payload.get("tasks", [])]
                           + [{"kind": "private", "value": value} for value in payload.get("private_events", [])])
                if len(records) > 20000 or len(encoded(payload)) > 8_000_000:
                    raise ValueError("Mission exceeds the current 8 MB sync limit")
                snapshot = hashlib.sha256(encoded(payload)).hexdigest()
                if request.get("known") == snapshot:
                    return {"mission": {"snapshot": snapshot, "unchanged": True}}
                self.snapshots[(sender, snapshot)] = (now, payload, records)
                while len(self.snapshots) > 16:
                    self.snapshots.popitem(last=False)
            if offset > len(records):
                raise ValueError("invalid mission offset")
            page, size = [], 0
            for record in records[offset:]:
                length = len(encoded(record))
                if length > 48000:
                    raise ValueError("Mission object is too large to synchronize")
                if page and size + length > 12000:
                    break
                page.append(record)
                size += length
            return {"mission": {"snapshot": snapshot, "offset": offset,
                "cursor": offset + len(page), "total": len(records),
                "complete": offset + len(page) == len(records),
                "team": payload.get("team", {})}, "records": page}


class MissionInbox:
    def __init__(self, store):
        self.store = store
        with store._lock, store._connection:
            store._connection.execute("CREATE TABLE IF NOT EXISTS mission_sync (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")

    def _load(self):
        row = self.store._connection.execute("SELECT payload FROM mission_sync WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def _save(self, state):
        self.store._connection.execute("INSERT OR REPLACE INTO mission_sync VALUES(1,?)", (encoded(state).decode(),))

    def request(self, force=False):
        with self.store._lock, self.store._connection:
            state = self._load()
            if force:
                state.pop("stage", None)
                state.pop("snapshot", None)
                state["needs_refresh"] = True
                self._save(state)
            stage = state.get("stage")
            return {"snapshot": stage["snapshot"], "offset": len(stage["records"])} if stage else {"known": state.get("snapshot")}

    def status(self):
        with self.store._lock:
            state = self._load()
        stage = state.get("stage")
        return {"state": "syncing" if stage else "synced" if state.get("snapshot") and not state.get("needs_refresh") else "pending",
                "received": len(stage["records"]) if stage else state.get("total", 0),
                "total": stage["total"] if stage else state.get("total", 0),
                "last_synced_at": state.get("last_synced_at"),
                "team": state.get("team", {}), "tasks": state.get("tasks", []),
                "private_events": state.get("private_events", [])}

    def accept(self, payload):
        with self.store._lock, self.store._connection:
            state = self._load()
            if payload.get("mission_restart"):
                state.pop("stage", None)
                state["needs_refresh"] = True
                self._save(state)
                return
            mission = payload.get("mission")
            if not isinstance(mission, dict):
                raise ValueError("Team host does not support mission sync yet")
            snapshot = mission.get("snapshot")
            if not isinstance(snapshot, str) or len(snapshot) != 64 or any(c not in "0123456789abcdef" for c in snapshot):
                raise ValueError("invalid mission snapshot")
            if mission.get("unchanged"):
                if state.get("snapshot") != snapshot or state.get("stage"):
                    raise ValueError("unknown mission snapshot")
                state["last_synced_at"] = int(time.time())
                state.pop("needs_refresh", None)
                self._save(state)
                return
            offset, cursor, total = (mission.get(key) for key in ("offset", "cursor", "total"))
            records = payload.get("records")
            if (any(type(n) is not int for n in (offset, cursor, total)) or not 0 <= offset <= cursor <= total <= 20000
                or not isinstance(records, list) or cursor - offset != len(records)
                or (cursor == offset and cursor != total) or mission.get("complete") != (cursor == total)):
                raise ValueError("invalid mission page")
            stage = state.get("stage")
            if offset == 0:
                stage = {"snapshot": snapshot, "records": [], "total": total,
                    "team": mission.get("team", {}),
                    "watermark": self.store._connection.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0],
                    "protected": [r[0] for r in self.store._connection.execute("SELECT event_id FROM events WHERE delivery_status='queued'")]}
            if not stage or stage["snapshot"] != snapshot or len(stage["records"]) != offset or stage["total"] != total:
                raise ValueError("mission page out of sequence")
            for record in records:
                value = record.get("value") if isinstance(record, dict) else None
                if not isinstance(value, dict) or record.get("kind") not in {"event", "task", "private"}:
                    raise ValueError("invalid mission record")
                if record["kind"] == "event":
                    network = value.get("network", {})
                    if (not all(key in value for key in ("id", "type", "callsign", "created_at"))
                        or not isinstance(network, dict) or not network.get("sender_hash")
                        or value["type"].startswith("private.")):
                        raise ValueError("invalid mission event")
            stage["records"].extend(records)
            if len(encoded(stage)) > 8_500_000:
                raise ValueError("mission cache exceeds size limit")
            state["stage"] = stage
            if cursor == total:
                values = lambda kind: [r["value"] for r in stage["records"] if r["kind"] == kind]
                events, tasks, private = values("event"), values("task"), values("private")
                content = {"events": events, "tasks": tasks, "private_events": private, "team": stage["team"]}
                if hashlib.sha256(encoded(content)).hexdigest() != snapshot:
                    raise ValueError("mission snapshot checksum mismatch")
                keep = {e["id"] for e in events} | set(stage["protected"])
                # A stale backup snapshot must not roll back a newer route or
                # resurrect one that its sender already stopped. Retain each
                # latest signed navigation state until a newer revision wins.
                navigation_rows = self.store._connection.execute(
                    "SELECT * FROM events WHERE event_type IN ('navigation.updated','navigation.stopped')"
                ).fetchall()
                keep.update(e["id"] for e in self.store._visible(navigation_rows))
                # Only prune previously verified cache rows. Never remove queued
                # work or local events arriving while a snapshot is downloading.
                rows = self.store._connection.execute("SELECT event_id FROM events WHERE rowid<=? AND delivery_status='verified'", (stage["watermark"],)).fetchall()
                self.store._connection.executemany("DELETE FROM events WHERE event_id=?", [(r[0],) for r in rows if r[0] not in keep])
                for event in events:
                    network = event["network"]
                    clean = {k: v for k, v in event.items() if k != "network"}
                    self.store._connection.execute("""INSERT INTO events
                        (event_id,event_type,callsign,sender_hash,created_at,received_at,payload_json,interface_name,delivery_status)
                        VALUES(?,?,?,?,?,?,?,?,'verified') ON CONFLICT(event_id) DO UPDATE SET
                        payload_json=excluded.payload_json,interface_name=excluded.interface_name,delivery_status='verified'""",
                        (event["id"], event["type"], event["callsign"], network["sender_hash"], event["created_at"],
                         network.get("received_at") or event["created_at"], encoded(clean).decode(), "Authenticated Reticulum mission snapshot"))
                state = {"snapshot": snapshot, "total": total, "last_synced_at": int(time.time()),
                    "team": stage["team"], "tasks": tasks, "private_events": private}
            self._save(state)
