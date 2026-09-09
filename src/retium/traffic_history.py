"""Bounded public traffic history, independent of the browser viewport."""
import json
import gzip
import io
import math
import re
import sqlite3
import threading
import time
from urllib.request import Request, build_opener

HISTORY_SECONDS = 86400
MAX_POINTS = 12000


def valid_identity(pack, identity):
    return isinstance(identity, str) and bool(re.fullmatch(r"[0-9a-fA-F]{6}" if pack == "flights" else r"[0-9]{9}", identity))


def aircraft_trace(payload, identity, now):
    if not isinstance(payload, dict) or str(payload.get("icao", "")).lower() != identity.lower():
        raise ValueError("Aircraft history identity mismatch")
    base = payload.get("timestamp")
    if type(base) not in (int, float) or not math.isfinite(base):
        raise ValueError("Invalid aircraft history time")
    points = []
    for row in payload.get("trace", [])[:50000]:
        if not isinstance(row, list) or len(row) < 3:
            continue
        offset, lat, lon = row[:3]
        if not all(type(n) in (int, float) and math.isfinite(n) for n in (offset, lat, lon)):
            continue
        timestamp = base + offset
        if -90 <= lat <= 90 and -180 <= lon <= 180 and now - HISTORY_SECONDS <= timestamp <= now + 30:
            points.append({"point": [lon, lat], "time": timestamp,
                           "breakBefore": len(row) > 6 and type(row[6]) is int and bool(row[6] & 2)})
    return sorted(points, key=lambda p: p["time"])[-MAX_POINTS:]


def fetch_trace(identity):
    from .traffic import _NoRedirect
    if not valid_identity("flights", identity):
        raise ValueError("Invalid aircraft identity")
    url = f"https://adsb.lol/data/traces/{identity[-2:]}/trace_full_{identity}.json"
    with build_opener(_NoRedirect()).open(Request(url, headers={"User-Agent": "Reticom/0.2 public-traffic-history", "Accept-Encoding": "identity"}), timeout=15) as response:
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise ValueError("Aircraft history is too large")
    # The provider serves gzip trace files even with Accept-Encoding: identity.
    if body.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            body = stream.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError("Decompressed aircraft history is too large")
    return json.loads(body)


class TrafficHistory:
    def __init__(self, path, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS fixes (pack TEXT, identity TEXT, time REAL, lon REAL, lat REAL, PRIMARY KEY(pack,identity,time))")
        self.db.execute("CREATE INDEX IF NOT EXISTS fixes_time ON fixes(time)")
        self.clock, self.lock, self.pruned_at = clock, threading.Lock(), 0

    def add(self, pack, identity, timestamp, lon, lat):
        now = self.clock()
        if not valid_identity(pack, identity) or not all(type(n) in (int, float) and math.isfinite(n) for n in (timestamp, lon, lat)):
            return
        if not (-180 <= lon <= 180 and -90 <= lat <= 90 and now - HISTORY_SECONDS <= timestamp <= now + 30):
            return
        with self.lock, self.db:
            last = self.db.execute("SELECT MAX(time) FROM fixes WHERE pack=? AND identity=?", (pack, identity)).fetchone()[0]
            if last is not None and timestamp - last < 30:
                return
            self.db.execute("INSERT OR IGNORE INTO fixes VALUES (?,?,?,?,?)", (pack, identity, timestamp, lon, lat))
            if now - self.pruned_at >= 60:
                self.db.execute("DELETE FROM fixes WHERE time < ?", (now - HISTORY_SECONDS,))
                self.db.execute("DELETE FROM fixes WHERE rowid IN (SELECT rowid FROM fixes ORDER BY time DESC LIMIT -1 OFFSET 200000)")
                self.pruned_at = now

    def points(self, pack, identity):
        with self.lock:
            rows = self.db.execute("SELECT time,lon,lat FROM fixes WHERE pack=? AND identity=? AND time>=? ORDER BY time DESC LIMIT ?",
                                   (pack, identity, self.clock() - HISTORY_SECONDS, MAX_POINTS)).fetchall()
        return [{"point": [lon, lat], "time": timestamp} for timestamp, lon, lat in reversed(rows)]

    def close(self):
        self.db.close()
