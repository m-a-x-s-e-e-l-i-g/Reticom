"""Opt-in road reports from fixed government feeds, separate from team data."""
from __future__ import annotations

from datetime import datetime
import gzip
import io
import json
import math
import re
import threading
import xml.etree.ElementTree as ET

ROAD_PACK = {
    "id": "roads", "title": "Road closures & disruptions", "source": "NDW / NCDOT",
    "source_url": "https://opendata.ndw.nu/", "attribution": "NDW / NCDOT public road reports",
    "description": "Closed roads, lane restrictions and incidents. Coverage: Netherlands (NDW); North Carolina, US (NCDOT work zones). Other regions are not covered.",
    "min_zoom": 0, "ttl_seconds": 120, "credential_fields": [],
}
SOURCES = {
    "ndw": {"name": "NDW · Netherlands", "url": "https://opendata.ndw.nu/actueel_beeld.xml.gz",
            "link": "https://opendata.ndw.nu/", "bounds": (3, 50.6, 7.4, 53.7), "interval": 120},
    "ncdot": {"name": "NCDOT · North Carolina", "url": "https://www.drivenc.gov/api/wzdx",
              "link": "https://www.drivenc.gov/developers/doc", "bounds": (-84.4, 33.7, -75.3, 36.7), "interval": 300},
}
ROAD_URLS = frozenset(source["url"] for source in SOURCES.values())
MAX_BYTES = 16_000_000
MAX_RECORDS, MAX_POSITIONS = 10000, 250000
STALE_SECONDS, EXPIRE_SECONDS = 600, 3600
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def clean(value, limit=1000):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def label(value):
    return clean(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).replace("-", " ")).capitalize()


def nodes(element, name):
    return element.findall(f".//{{*}}{name}")


def value(element, name):
    found = element.find(f".//{{*}}{name}")
    return clean(found.text) if found is not None else ""


def texts(element, name):
    return "; ".join(dict.fromkeys(clean(" ".join(item.itertext())) for item in nodes(element, name)))


def valid_point(point):
    return (isinstance(point, (list, tuple)) and len(point) >= 2
            and all(type(n) in (float, int) and math.isfinite(n) for n in point[:2])
            and abs(point[0]) <= 180 and abs(point[1]) <= 90)


def active_window(start, end, now):
    return start is not None and start <= now and (end is None or now < end)


def datex_window(record, now):
    if value(record, "validityStatus") == "suspended" or value(record, "operatorActionStatus") in {"cancelled", "completed", "rejected"}:
        return None
    start, end = timestamp(value(record, "overallStartTime")), timestamp(value(record, "overallEndTime"))
    if (value(record, "overallEndTime") and end is None) or not active_window(start, end, now):
        return None
    # Unsupported recurring schedules must never masquerade as active closures.
    if any(e.tag.rsplit("}", 1)[-1].startswith("recurring") for e in record.iter()):
        return None
    for period in nodes(record, "exceptionPeriod"):
        if active_window(timestamp(value(period, "startOfPeriod")), timestamp(value(period, "endOfPeriod")), now):
            return None
    periods = nodes(record, "validPeriod")
    if periods:
        if any(value(p, "endOfPeriod") and timestamp(value(p, "endOfPeriod")) is None for p in periods):
            return None
        active = [(timestamp(value(p, "startOfPeriod")), timestamp(value(p, "endOfPeriod"))) for p in periods]
        active = [(s, e) for s, e in active if active_window(s, e, now)]
        if not active:
            return None
        start = max(start, min(s for s, _ in active))
        period_end = None if any(e is None for _, e in active) else max(e for _, e in active)
        end = min(end, period_end) if end is not None and period_end is not None else end or period_end
    return start, end


def datex_geometry(record):
    lines = []
    for line in nodes(record, "gmlLineString"):
        if line.get("srsName", "WGS 84") not in {"WGS 84", "EPSG:4326", "urn:ogc:def:crs:EPSG::4326"}:
            continue
        try:
            pos_list = line.find(".//{*}posList")
            numbers = [float(n) for n in (pos_list.text or "").split()] if pos_list is not None else []
        except ValueError:
            continue
        if len(numbers) < 4 or len(numbers) % 2 or len(numbers) > 20000:
            continue
        # Dutch DATEX GML positions are latitude, longitude (EPSG:4326).
        points = [[numbers[i+1], numbers[i]] for i in range(0, len(numbers), 2)]
        if all(valid_point(p) for p in points) and points not in lines:
            lines.append(points)
    if lines:
        return {"type": "MultiLineString", "coordinates": lines}, sum(map(len, lines))
    point = record.find(".//{*}pointByCoordinates/{*}pointCoordinates")
    if point is not None:
        try:
            coordinates = [float(value(point, "longitude")), float(value(point, "latitude"))]
            if valid_point(coordinates):
                return {"type": "Point", "coordinates": coordinates}, 1
        except ValueError:
            pass
    return None, 0


def report(source, identity, geometry, start, end, updated, impact, reason, title, detail, **extra):
    identity = f"road-{source}-{clean(identity, 180)}"
    return {"type": "Feature", "id": identity, "geometry": geometry,
            "properties": {"road_id": identity, "road_source": source, "title": clean(title, 180), "detail": clean(detail),
                           "road_impact": impact, "reason": clean(reason), "starts_at": start, "ends_at": end,
                           "updated_at": updated, "source": SOURCES[source]["name"],
                           "source_url": SOURCES[source]["link"], "attribution": SOURCES[source]["name"],
                           "geometry_note": "Reported road segment" if geometry["type"] != "Point" else "Reported point; road segment unavailable",
                           **extra}}


def parse_ndw(raw, now):
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Invalid road feed")
    root = ET.fromstring(raw)
    publication = timestamp(value(root, "publicationTime"))
    if publication is None or publication > now + 300:
        raise ValueError("Missing road feed publication time")
    records = nodes(root, "situationRecord")
    if len(records) > MAX_RECORDS:
        raise ValueError("Road feed too large")
    features, used = [], 0
    for record in records:
        window = datex_window(record, now)
        kind = record.get(XSI, "").split(":")[-1]
        management = value(record, "roadOrCarriagewayOrLaneManagementType")
        if not window or not record.get("id"):
            continue
        if kind == "RoadOrCarriagewayOrLaneManagement":
            if management in {"roadClosed", "carriagewayClosures", "entrySlipRoadClosed", "exitSlipRoadClosed"}:
                impact = "closed"
            elif "clos" in management.lower() or management in {"singleAlternateLineTraffic", "contraflow", "narrowLanes", "laneDeviations"}:
                impact = "restricted"
            else:
                continue
        elif kind in {"Accident", "VehicleObstruction", "GeneralObstruction", "EnvironmentalObstruction", "WeatherRelatedRoadConditions", "ConstructionWorks", "MaintenanceWorks"}:
            impact = "incident"
        elif value(record, "generalNetworkManagementType") == "bridgeSwingInOperation":
            impact = "closed"
        else:
            continue
        restrictions = texts(record, "forVehiclesWithCharacteristicsOf")
        if restrictions and impact == "closed":
            impact = "restricted"
        geometry, count = datex_geometry(record)
        if not geometry:
            continue
        used += count
        if used > MAX_POSITIONS:
            raise ValueError("Road geometry too large")
        reason = texts(record, "causeDescription") or label(value(record, "causeType") or management or kind)
        road = value(record, "roadNumber") or texts(record, "roadName") or texts(record, "locationDescription")
        detail = texts(record, "generalPublicComment") or reason
        features.append(report("ndw", record.get("id"), geometry, *window,
                               timestamp(value(record, "situationRecordVersionTime")), impact, reason,
                               road or label(management or kind), detail,
                               restrictions=restrictions, direction=value(record, "directionRelativeOnLinearSection"),
                               authority=texts(record, "sourceName")))
    return features, publication


def parse_wzdx(raw, now):
    payload = json.loads(raw)
    rows = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise ValueError("Invalid road feed")
    info = payload.get("feed_info") or payload.get("road_event_feed_info") or {}
    publication = timestamp(info.get("update_date"))
    if publication is None or publication > now + 300:
        raise ValueError("Missing road feed publication time")
    features, used = [], 0
    for row in rows:
        p = row.get("properties") or {}
        core = p.get("core_details") or p
        if core.get("event_type") != "work-zone" or (p.get("event_status") or core.get("event_status")) in {"planned", "pending", "completed", "cancelled"}:
            continue
        start, end = timestamp(p.get("start_date")), timestamp(p.get("end_date"))
        if (p.get("end_date") and end is None) or not active_window(start, end, now):
            continue
        vehicle = p.get("vehicle_impact", "unknown")
        if vehicle.startswith("all-lanes-open"):
            continue
        impact = "closed" if vehicle == "all-lanes-closed" else "incident" if vehicle == "unknown" else "restricted"
        geometry = row.get("geometry") or {}
        lines = [geometry.get("coordinates")] if geometry.get("type") == "LineString" else geometry.get("coordinates") if geometry.get("type") == "MultiLineString" else None
        if not isinstance(lines, list) or not lines or any(not isinstance(line, list) or not 2 <= len(line) <= 10000 or not all(valid_point(p) for p in line) for line in lines):
            continue
        used += sum(map(len, lines))
        if used > MAX_POSITIONS:
            raise ValueError("Road geometry too large")
        if not row.get("id"):
            continue
        features.append(report("ncdot", row["id"], {"type":"MultiLineString", "coordinates":lines}, start, end,
                               timestamp(core.get("update_date")), impact, "Roadworks · " + label(vehicle),
                               ", ".join(core.get("road_names") or []) or "Roadworks", core.get("description"),
                               direction=core.get("direction"), restrictions=clean(p.get("restrictions") or "")))
    return features, publication


def overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


class RoadDisruptions:
    def __init__(self, store, *, fetch=None, clock=None):
        self.store, self.clock, self.fetch = store, clock or store.clock, fetch
        self.directory = store.directory / "road-feeds"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.locks = {key: threading.Lock() for key in SOURCES}
        self.next_fetch = {}
        self.failed = set()

    def snapshot(self, key):
        source = SOURCES[key]
        path = self.directory / f"{key}.json"
        with self.locks[key]:
            if not self.store.is_enabled("roads"):
                return None
            now = self.clock()
            cached = self.store._read(path)
            if cached and (cached.get("version") != 1 or not 0 <= now - cached.get("received_at", 0) <= EXPIRE_SECONDS):
                cached = None
            if now < self.next_fetch.get(key, 0) or cached and now - cached["received_at"] < source["interval"]:
                return cached
            self.next_fetch[key] = now + source["interval"]
            try:
                from .intel_sources import fetch_bytes
                raw = (self.fetch or fetch_bytes)(source["url"], max_bytes=MAX_BYTES, timeout=20)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Road feed too large")
                features, published = (parse_ndw if key == "ndw" else parse_wzdx)(raw, now)
                if cached and published < cached["published_at"]:
                    self.failed.add(key)
                    return cached
                if now - published > EXPIRE_SECONDS:
                    self.failed.add(key)
                    return cached
                if not self.store.is_enabled("roads"):
                    return None
                cached = {"version":1, "received_at":now, "published_at":published, "features":features}
                self.store._write(path, cached)
                self.failed.discard(key)
            except Exception:
                self.failed.add(key)  # Never expose upstream response details.
            return cached

    def load(self, bounds, zoom):
        from .intel_packs import validate_bounds
        bounds = validate_bounds(bounds)
        if not math.isfinite(zoom) or not 0 <= zoom <= 24:
            raise ValueError("Enter a valid map zoom")
        empty = {"type":"FeatureCollection", "features":[], "replaced_sources":[], "status":"disabled"}
        if not self.store.is_enabled("roads"):
            return empty
        selected = [key for key, source in SOURCES.items() if overlaps(bounds, source["bounds"])]
        if not selected:
            return {**empty, "status":"uncovered", "note":"No connected road feed for this area. Coverage: Netherlands and North Carolina, US."}
        features, replaced, missing = [], [], []
        for key in selected:
            snapshot = self.snapshot(key)
            if not snapshot:
                missing.append(SOURCES[key]["name"])
                continue
            now = self.clock()
            confirmed = min(snapshot["received_at"], snapshot["published_at"])
            if now - confirmed > EXPIRE_SECONDS:
                missing.append(SOURCES[key]["name"])
                continue
            if key in self.failed:
                missing.append(SOURCES[key]["name"])
            replaced.append(key)
            for feature in snapshot["features"]:
                p = feature["properties"]
                if not active_window(p["starts_at"], p.get("ends_at"), now) or now - confirmed > EXPIRE_SECONDS:
                    continue
                features.append({**feature, "properties":{**p, "confirmed_at":confirmed,
                    "stale_at":confirmed + STALE_SECONDS, "expires_at":confirmed + EXPIRE_SECONDS,
                    "stale":key in self.failed or now - confirmed > STALE_SECONDS}})
        if not self.store.is_enabled("roads"):
            return empty
        stale = missing or any(f["properties"]["stale"] for f in features)
        return {"type":"FeatureCollection", "features":features, "replaced_sources":replaced,
                "status":"stale" if stale and features else "error" if missing else "fresh", "fetched_at":self.clock(),
                "note":"Partial geographic coverage · NDW Netherlands / NCDOT North Carolina. No report does not mean a road is open.",
                "error":f"Road feed unavailable: {', '.join(missing)}. Retaining recent reports where available." if missing else None}
