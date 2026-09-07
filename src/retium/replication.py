"""Durable, transactional row replication for explicitly trusted team hosts.

SQLite triggers journal local writes in the same transaction as application data.
Lamport versions give deterministic convergence without trusting wall clocks.
Deletion is permanent for an ID; recreating an object must use a new ID.
The journal is deliberately not compacted until a retention protocol exists.
"""
from __future__ import annotations

import json


class ReplicatedTable:
    def __init__(self, store, table: str, key: str, origin: str):
        # Identifiers come only from the local schema, never from the wire.
        if (table, key) not in {
            ("events", "event_id"), ("tasks", "task_id"),
            ("private_messages", "event_id"), ("team_settings", "key"),
        }:
            raise ValueError("unsupported replicated table")
        self.store, self.table, self.key = store, table, key
        self.db, self.lock = store._connection, store._lock
        with self.lock, self.db:
            self.columns = [row[1] for row in self.db.execute(f'PRAGMA table_info({table})')]
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS replica_meta (id INTEGER PRIMARY KEY CHECK(id=1), clock INTEGER NOT NULL, origin TEXT NOT NULL, applying INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS replica_records (key TEXT PRIMARY KEY, clock INTEGER NOT NULL, origin TEXT NOT NULL, payload TEXT);
                CREATE TABLE IF NOT EXISTS replica_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, clock INTEGER NOT NULL, origin TEXT NOT NULL, payload TEXT);
                CREATE TABLE IF NOT EXISTS replica_cursors (peer TEXT PRIMARY KEY, seq INTEGER NOT NULL);
            ''')
            self.db.execute("INSERT OR IGNORE INTO replica_meta VALUES(1,0,?,0)", (origin,))
            if self.db.execute("SELECT origin FROM replica_meta").fetchone()[0] != origin:
                raise ValueError("replica identity does not match its database")
            encoded = ",".join(f"'{col}',NEW.{col}" for col in self.columns)
            self.db.executescript(f'''
                CREATE TRIGGER IF NOT EXISTS replica_no_resurrection BEFORE INSERT ON {table}
                WHEN (SELECT applying FROM replica_meta WHERE id=1)=0 AND EXISTS
                    (SELECT 1 FROM replica_records WHERE key=NEW.{key} AND payload IS NULL)
                BEGIN SELECT RAISE(IGNORE); END;
            ''')
            for operation in ("INSERT", "UPDATE", "DELETE"):
                ref = "OLD" if operation == "DELETE" else "NEW"
                payload = "NULL" if operation == "DELETE" else f"json_object({encoded})"
                self.db.executescript(f'''
                    CREATE TRIGGER IF NOT EXISTS replica_{operation.lower()} AFTER {operation} ON {table}
                    WHEN (SELECT applying FROM replica_meta WHERE id=1)=0
                    BEGIN
                        UPDATE replica_meta SET clock=clock+1 WHERE id=1;
                        INSERT INTO replica_records
                        SELECT {ref}.{key}, clock, origin, {payload} FROM replica_meta WHERE id=1
                        ON CONFLICT(key) DO UPDATE SET clock=excluded.clock, origin=excluded.origin, payload=excluded.payload;
                        INSERT INTO replica_log(key,clock,origin,payload)
                        SELECT key,clock,origin,payload FROM replica_records WHERE key={ref}.{key};
                    END;
                ''')
            # Upgrade existing hosts: snapshot every existing row once, not just
            # the last 60 events shown in the UI.
            self.db.execute(f"UPDATE {table} SET {key}={key} WHERE {key} NOT IN (SELECT key FROM replica_records)")

    def page(self, after=0, limit=40):
        if type(after) is not int or after < 0:
            raise ValueError("invalid replication cursor")
        with self.lock:
            rows = self.db.execute("SELECT seq,key,clock,origin,payload FROM replica_log WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
            head = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM replica_log").fetchone()[0]
        return {"rows": [dict(row) for row in rows], "head": head, "cursor": rows[-1][0] if rows else after}

    def cursor(self, peer):
        with self.lock:
            row = self.db.execute("SELECT seq FROM replica_cursors WHERE peer=?", (peer,)).fetchone()
        return row[0] if row else 0

    def merge(self, peer, page, allowed_origins):
        rows, cursor = page.get("rows"), page.get("cursor")
        if not isinstance(rows, list) or len(rows) > 40 or type(cursor) is not int:
            raise ValueError("invalid replication page")
        with self.lock, self.db:
            previous = self.db.execute("SELECT seq FROM replica_cursors WHERE peer=?", (peer,)).fetchone()
            last_seq = previous[0] if previous else 0
            if cursor < last_seq:
                raise ValueError("replication cursor rollback")
            self.db.execute("UPDATE replica_meta SET applying=1 WHERE id=1")
            for change in rows:
                seq, clock, origin = change.get("seq"), change.get("clock"), change.get("origin")
                key, payload = change.get("key"), change.get("payload")
                if (type(seq) is not int or seq <= last_seq or seq > cursor
                    or type(clock) is not int or not 0 < clock < 2**60
                    or origin not in allowed_origins or not isinstance(key, str) or len(key) > 80
                    or (payload is not None and (not isinstance(payload, str) or len(payload) > 30000))):
                    raise ValueError("invalid replication change")
                last_seq = seq
                value = json.loads(payload) if payload is not None else None
                if value is not None and (not isinstance(value, dict) or set(value) != set(self.columns) or value[self.key] != key):
                    raise ValueError("replication schema mismatch")
                existing = self.db.execute("SELECT clock,origin,payload FROM replica_records WHERE key=?", (key,)).fetchone()
                self.db.execute("UPDATE replica_meta SET clock=MAX(clock,?) WHERE id=1", (clock,))
                # Remove-wins prevents replay/partition merges from resurrecting
                # cleared messages or deleted tasks, regardless of arrival order.
                wins = existing is None or (
                    existing[2] is not None and (payload is None or (clock, origin) > (existing[0], existing[1]))
                ) or (existing[2] is None and payload is None and (clock, origin) > (existing[0], existing[1]))
                if not wins:
                    continue
                if value is None:
                    self.db.execute(f"DELETE FROM {self.table} WHERE {self.key}=?", (key,))
                else:
                    cols = ",".join(self.columns)
                    placeholders = ",".join("?" for _ in self.columns)
                    self.db.execute(f"INSERT OR REPLACE INTO {self.table} ({cols}) VALUES ({placeholders})", [value[col] for col in self.columns])
                self.db.execute("INSERT OR REPLACE INTO replica_records VALUES(?,?,?,?)", (key, clock, origin, payload))
                self.db.execute("INSERT INTO replica_log(key,clock,origin,payload) VALUES(?,?,?,?)", (key, clock, origin, payload))
            if (rows and last_seq != cursor) or (not rows and cursor != last_seq):
                raise ValueError("non-contiguous replication page")
            self.db.execute("INSERT OR REPLACE INTO replica_cursors VALUES(?,?)", (peer, cursor))
            self.db.execute("UPDATE replica_meta SET applying=0 WHERE id=1")

    def head(self):
        return self.page(0, 0)["head"]
