"""Bounded public transit snapshots from agency GTFS-RT and Transitous."""

from __future__ import annotations

import math
import re
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlencode

from .intel_sources import TRANSITOUS_TRIP_URL, TRANSITOUS_URL, TRANSIT_POSITION_URLS, _coord, _inside, _json, _text, fetch_bytes
from .navigation import decode_coordinates
from .osm_cache import intersects


TRANSIT_PACK = {
    "id": "transit", "title": "Public transport", "source": "Transitous sources",
    "source_url": "https://transitous.org/sources/", "attribution": "Transitous and transport operators",
    "description": "Reported bus, rail and ferry positions plus estimates based on realtime trip updates. Coverage varies. Transitous data is for non-commercial use.",
    "credential_fields": [], "min_zoom": 10, "ttl_seconds": 60,
}
FEEDS = {
    "openov": ("OpenOV", "https://gtfs.ovapi.nl/", (3.15, 50.65, 7.30, 53.65), ("nl-openov", "nl-ovapi")),
    "hsl": ("HSL", "https://www.hsl.fi/en/opendata", (23.75, 59.75, 25.95, 60.75), ("fi-hsl",)),
    "mbta": ("MBTA", "https://github.com/mbta/gtfs-documentation", (-72.75, 41.55, -70.45, 43), ("us-ma-mbta", "us-mbta")),
    "entur": ("Entur", "https://developer.entur.org/pages-real-time-api/", (4, 57.7, 32, 72), ("no-entur", "no-entur-")),
}
MODES = {
    "BUS": "bus", "COACH": "bus", "RAIL": "train", "HIGHSPEED_RAIL": "train", "LONG_DISTANCE": "train",
    "REGIONAL_RAIL": "train", "INTERCITY": "train", "SUBURBAN": "train", "METRO": "metro",
    "SUBWAY": "metro", "TRAM": "tram", "FERRY": "ferry", "CABLE_CAR": "tram", "TROLLEYBUS": "bus",
}
MODE_LABELS = {"bus": "Bus", "train": "Train", "metro": "Metro", "tram": "Tram", "ferry": "Ferry", "vehicle": "Transit vehicle"}
POSITION_MAX_AGE = 120
ESTIMATE_MAX_AGE = 90
TRIP_REFERENCE = re.compile(r"\d{8}_\d{2}:\d{2}(?::\d{2})?_([^_\s]{1,64})_([^\s]{1,170})")
DATASET_FEED = {tag: key for key, (_, _, _, tags) in FEEDS.items() for tag in tags}


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        part = data[offset]
        offset += 1
        value |= (part & 127) << shift
        if part < 128:
            if shift == 63 and part > 1:
                break
            return value, offset
    raise ValueError("Invalid GTFS-RT varint")


def _fields(data):
    # Only read the wire types used in the official GTFS-RT vehicle schema.
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        tag, wire = key >> 3, key & 7
        if not tag or wire not in (0, 1, 2, 5):
            raise ValueError("Invalid GTFS-RT field")
        if wire == 0:
            value, offset = _varint(data, offset)
        else:
            length, offset = _varint(data, offset) if wire == 2 else (8 if wire == 1 else 4, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("Truncated GTFS-RT field")
            value = data[offset:end]
            offset = end
        yield tag, value


def _message(value):
    return dict(_fields(value)) if isinstance(value, bytes) else {}


def _wire_text(fields, tag):
    value = fields.get(tag)
    return _text(value.decode("utf-8", errors="replace"), 170) if isinstance(value, bytes) else ""


def _wire_float(fields, tag):
    value = fields.get(tag)
    return struct.unpack("<f", value)[0] if isinstance(value, bytes) and len(value) == 4 else None


def _direct_mode(key, route):
    if key == "mbta":
        if route.startswith("CR-"):
            return "train"
        if route.startswith("Green") or route == "Mattapan":
            return "tram"
        if route in {"Red", "Orange", "Blue"}:
            return "metro"
        if route.startswith("Boat-"):
            return "ferry"
        if route.isdigit():
            return "bus"
    return "vehicle"


def decode_vehicles(payload, key, now):
    agency, url, _, _ = FEEDS[key]
    header_time = None
    header_seen = False
    vehicles = {}
    for tag, raw in _fields(payload):
        if tag == 1:
            header = _message(raw)
            if not _wire_text(header, 1) or header.get(2, 0) != 0:
                raise ValueError("Expected a full GTFS-RT snapshot")
            header_seen = True
            header_time = header.get(3)
        if tag != 2:
            continue
        entity = _message(raw)
        if entity.get(2) or 4 not in entity:
            continue
        row = _message(entity[4])
        position, vehicle, trip = map(_message, (row.get(2), row.get(8), row.get(1)))
        point = _coord([_wire_float(position, 2), _wire_float(position, 1)])
        identity = _wire_text(vehicle, 1) or _wire_text(entity, 1)
        if not point or not identity:
            continue
        observed = row.get(5) if isinstance(row.get(5), int) else None
        route = _wire_text(trip, 5)
        label = _wire_text(vehicle, 2)
        mode = _direct_mode(key, route)
        report = {"id": f"{key}:{identity}", "point": point, "measured_at": observed,
                  "trip": _wire_text(trip, 1), "route": route, "vehicle": label or identity,
                  "mode": mode, "estimated": False,
                  "source": agency, "source_url": url, "feed": key}
        if key == "hsl":
            report["hsl_run"] = (_wire_text(trip, 3), _wire_text(trip, 2)[:5], route)
        previous = vehicles.get(identity)
        if previous is None or (observed or 0) >= (previous["measured_at"] or 0):
            vehicles[identity] = report
    if not header_seen:
        raise ValueError("Missing GTFS-RT header")
    result = []
    for row in vehicles.values():
        timestamp = row["measured_at"] if row["measured_at"] is not None else header_time
        if isinstance(timestamp, int) and -30 <= now - timestamp <= POSITION_MAX_AGE:
            result.append({**row, "at": timestamp})
    return result


def _epoch(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _route_position(points, ratio):
    if not points:
        return None
    legs = []
    for (x, y), (lon, lat) in zip(points, points[1:]):
        dx = (lon - x + 180) % 360 - 180
        legs.append((math.hypot(dx * math.cos(math.radians((y + lat) / 2)), lat - y), dx))
    remaining = sum(length for length, _ in legs) * ratio
    for (x, y), (_, lat), (length, dx) in zip(points, points[1:], legs):
        if length and remaining <= length:
            step = remaining / length
            return [(x + dx * step + 180) % 360 - 180, y + (lat - y) * step]
        remaining -= length
    return points[-1]


def _transitous_segments(rows):
    if not isinstance(rows, list):
        raise ValueError("Invalid Transitous response")
    segments = []
    for row in rows[:4000]:
        if not isinstance(row, dict) or not isinstance(row.get("trips"), list):
            continue
        start, end = _epoch(row.get("departure")), _epoch(row.get("arrival"))
        if start is None or end is None or start >= end:
            continue
        polyline = row.get("polyline")
        points = []
        if row.get("realTime") is True:
            try:
                points = decode_coordinates(polyline)
            except ValueError:
                stops = [row.get("from"), row.get("to")]
                if all(isinstance(place, dict) for place in stops):
                    fallback = [_coord([place.get("lon"), place.get("lat")]) for place in stops]
                    if all(fallback):
                        points = fallback
        mode = MODES.get(str(row.get("mode")), "vehicle")
        trips = []
        for trip in row["trips"][:20]:
            if not isinstance(trip, dict) or not isinstance(trip.get("tripId"), str):
                continue
            identity = trip["tripId"]
            parts = TRIP_REFERENCE.fullmatch(identity)
            if not parts:
                continue
            dataset, trip_id = parts.groups()
            dataset = dataset.lower()
            feed = DATASET_FEED.get(dataset) or ("entur" if dataset.startswith("no-entur-") else None)
            trips.append({"id": f"transitous:{identity}", "trip_ref": identity, "feed": feed, "trip": trip_id,
                          "mode": mode, "route": _text(trip.get("displayName"), 88)})
        if trips:
            segments.append((start, end, points, trips, polyline))
    return segments


def _merge(direct, segments, bounds, now, route_modes, *, estimate_positions=True):
    metadata, estimates, hsl_runs, hsl_lines = {}, {}, {}, {}
    for start, end, points, trips, _polyline in segments:
        point = _route_position(points, (now - start) / (end - start)) if estimate_positions and start <= now < end else None
        for trip in trips:
            metadata[(trip["feed"], trip["trip"])] = trip
            if trip["feed"] == "hsl":
                # HSL publishes the service date, departure time and route instead of a trip ID.
                day, departure, *_ = trip["trip_ref"].split("_", 3)
                route = trip["trip"].split("_", 1)[0]
                key = (day, departure[:5], route)
                hsl_runs.setdefault(key, {})[trip["trip_ref"]] = trip
                hsl_lines[route] = trip
            if point and _inside(point, bounds) and (trip["id"] not in estimates or start > estimates[trip["id"]][0]):
                estimates[trip["id"]] = start, {**trip, "point": point, "vehicle": "", "at": now,
                                                "estimated": True, "source": "Transitous", "source_url": TRANSIT_PACK["source_url"]}
    reports, by_trip, covered = [], set(), set()
    for row in direct:
        if not -30 <= now - row["at"] <= POSITION_MAX_AGE:
            continue
        covered.add(row["feed"])
        if row["trip"]:
            by_trip.add((row["feed"], row["trip"]))
        if _inside(row["point"], bounds):
            route_key = (row["feed"], row["route"])
            trip = metadata.get((row["feed"], row["trip"]))
            if row["feed"] == "hsl":
                runs = hsl_runs.get(row.get("hsl_run"), {})
                if len(runs) == 1:
                    trip = next(iter(runs.values()))
                elif line := hsl_lines.get(row["route"]):
                    row = {**row, "route": line["route"] or row["route"], "mode": line["mode"]}
            if trip:
                row = {**row, "trip_ref": trip["trip_ref"], "route": trip["route"] or row["route"],
                       "mode": trip["mode"] if row["mode"] == "vehicle" else row["mode"]}
            if route_key[1]:
                if row["mode"] != "vehicle":
                    route_modes[route_key] = row["mode"]
                    route_modes.move_to_end(route_key)
                    if len(route_modes) > 2048:
                        route_modes.popitem(last=False)
                elif mode := route_modes.get(route_key):
                    row = {**row, "mode": mode}
            reports.append(row)
    for _, estimate in estimates.values():
        feed = estimate["feed"]
        if (feed, estimate["trip"]) in by_trip:
            continue
        # HSL publishes positions without trip IDs. Its own live snapshot is the
        # safest source for HSL vehicles when it is active, even on busy routes.
        if feed == "hsl" and feed in covered:
            continue
        reports.append(estimate)
    return reports


def _feature(row):
    mode = MODE_LABELS[row["mode"]]
    return {"type": "Feature", "id": row["id"], "geometry": {"type": "Point", "coordinates": row["point"]},
            "properties": {"id": row["id"], "title": row["route"] or mode, "kind": row["mode"], "mode_label": mode,
                           "vehicle": row["vehicle"], "estimated": row["estimated"], "observed_at": row["at"],
                           "position_time": row["at"], "traffic_pack": "transit", "identity": row["id"],
                           "trip_ref": row.get("trip_ref", ""),
                           "vehicle_trip": "/".join(row["hsl_run"]) if "hsl_run" in row else row["trip"],
                           "feed_timestamp": not row["estimated"] and row.get("measured_at") is None,
                           "source": row["source"], "source_url": row["source_url"]}}


class PublicTransit:
    def __init__(self, *, fetch=fetch_bytes, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.route_modes = OrderedDict()
        self.next_request = {}

    def _get(self, key, provider, ttl, load, *, interval=15):
        entry = self.cache.get(key)
        now = self.clock()
        if entry and now - entry[0] < ttl:
            return entry[1], True
        retry, healthy = self.next_request.get(provider, (0, True))
        if now < retry:
            return (entry[1] if entry else []), None if healthy else False
        try:
            rows = load(now)
        except (ValueError, OSError):
            self.next_request[provider] = (now + 60, False)
            return (entry[1] if entry else []), False
        self.next_request[provider] = (now + interval, True)
        self.cache[key] = (now, rows)
        self.cache.move_to_end(key)
        while len(self.cache) > 36:
            self.cache.popitem(last=False)
        return rows, True

    def load(self, bounds, zoom):
        result = {"type": "FeatureCollection", "features": [], "status": "zoom_in", "source": TRANSIT_PACK["source"],
                  "source_url": TRANSIT_PACK["source_url"], "note": "Zoom in to see public transport."}
        if zoom < TRANSIT_PACK["min_zoom"] or max(bounds[2]-bounds[0], bounds[3]-bounds[1]) > 2:
            return result
        with self.lock:
            direct, healthy = [], []
            for key, (_, _, coverage, _) in FEEDS.items():
                if not intersects(coverage, bounds):
                    continue
                interval = 20 if key == "openov" else 10
                rows, ok = self._get(key, key, interval, lambda now: decode_vehicles(
                    self.fetch(TRANSIT_POSITION_URLS[key], headers={"ET-Client-Name": "reticom-reticom"} if key == "entur" else None, timeout=6, max_bytes=4_000_000),
                    key, self.clock()), interval=interval)
                direct.extend(rows)
                healthy.append(ok)
            w, s, e, n = bounds
            region = (max(-180, math.floor(w*20)/20), max(-90, math.floor(s*20)/20),
                      min(180, math.ceil(e*20)/20), min(90, math.ceil(n*20)/20))
            segments, ok = self._get(region, "transitous", 60, lambda now: self._segments(region, now))
            healthy.append(ok)
            now = self.clock()
            recent_timing = now - self.cache.get(region, (0,))[0] <= ESTIMATE_MAX_AGE
            reports = _merge(direct, segments, bounds, now, self.route_modes, estimate_positions=recent_timing)
        count = len(reports)
        failed = False in healthy
        status = "fresh" if reports else "uncovered"
        if failed:
            status = "stale" if reports else "error"
        elif not reports and None in healthy:
            status = "deferred"
        note = "One or more providers are unavailable. Available feeds are still shown." if reports and failed else ""
        if not reports and status != "deferred":
            note = "No live public transport reports in this view."
            if status == "error" and not any(healthy):
                note = "Public transport feeds are unavailable. Try again shortly."
        return {**result, "features": [_feature(row) for row in reports[:2000]], "status": status,
                "fetched_at": now, "truncated": count > 2000, "note": note,
                "error": note if status in {"error", "stale"} else None}

    def _segments(self, bounds, now):
        w, s, e, n = bounds
        stamp = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urlencode({"min": f"{s},{w}", "max": f"{n},{e}", "zoom": 11,
                           "startTime": stamp(now - 30), "endTime": stamp(now + ESTIMATE_MAX_AGE)})
        rows = _json(self.fetch(f"{TRANSITOUS_URL}?{query}", timeout=10, max_bytes=8_000_000))
        return _transitous_segments(rows)

    def route(self, reference):
        if not TRIP_REFERENCE.fullmatch(reference):
            raise ValueError("Invalid public transport trip")
        with self.lock:
            route, _ = self._get(("route", reference), "transitous-trip", 3600,
                                 lambda _now: self._trip_geometry(reference), interval=2)
            if route:
                return route
            now = self.clock()
            candidates = [(abs((start + end) / 2 - now), polyline)
                          for key, (cached_at, segments) in self.cache.items()
                          if isinstance(key, tuple) and len(key) == 4 and now - cached_at < ESTIMATE_MAX_AGE
                          for start, end, _, trips, polyline in segments
                          if any(trip["trip_ref"] == reference for trip in trips)]
        for _, polyline in sorted(candidates, key=lambda candidate: candidate[0])[:4]:
            try:
                return {"points": decode_coordinates(polyline), "label": "Route segment"}
            except ValueError:
                continue
        return {"points": [], "label": "Route unavailable"}

    def _trip_geometry(self, reference):
        query = urlencode({"tripId": reference})
        payload = _json(self.fetch(f"{TRANSITOUS_TRIP_URL}?{query}", timeout=6, max_bytes=750_000))
        for leg in payload.get("legs", []) if isinstance(payload, dict) else []:
            if not isinstance(leg, dict) or leg.get("tripId") != reference or leg.get("cancelled"):
                continue
            geometry = leg.get("legGeometry")
            if isinstance(geometry, dict):
                try:
                    return {"points": decode_coordinates(geometry.get("points"), precision=6), "label": "Trip route"}
                except ValueError:
                    continue
        return {}
