"""Opt-in public moving traffic. No team identity, messages or Reticulum writes.

Aircraft use bounded regional requests. Vessel providers serve leased visible
maps through the local Python backend.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

FLIGHT_TYPES = ("light", "small", "medium", "heavy", "helicopter", "glider", "balloon", "drone", "ground", "other", "unknown")
VESSEL_TYPES = ("cargo", "tanker", "passenger", "fishing", "sailing", "pleasure", "tug", "service", "high_speed", "military_restricted", "other", "unknown")
AFFILIATIONS = ("all", "military", "commercial", "unknown")
TRAFFIC_IDS = ("flights", "vessels")


def personal_flight_fallback_enabled():
    setting = "RETICOM_ADSB_FI_PERSONAL"
    value = os.environ.get(setting)
    if value is not None or os.name != "nt":
        return value == "1"
    # Desktop launchers can inherit an environment older than the user's opt-in.
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, setting)
    except OSError:
        return False
    return value == "1"


PERSONAL_FLIGHT_FALLBACK = personal_flight_fallback_enabled()
TRAFFIC_PACKS = [
    {"id": "flights", "title": "Live aircraft", "source": "ADSB.lol", "source_url": "https://www.adsb.lol/docs/open-data/api/",
     "attribution": "ADSB.lol contributors · ODbL", "description": "Public aircraft positions, heading and altitude. Incomplete coverage; not an air-traffic safety service.",
     "min_zoom": 6, "ttl_seconds": 10, "credential_fields": [],
     "personal_fallback": PERSONAL_FLIGHT_FALLBACK},
    {"id": "vessels", "title": "Live boats & ships", "source": "Open Waters AIS", "source_url": "https://openwaters.io/ais/",
     "description": "Public AIS positions. Uses AISStream when configured, with Open Waters AIS and optional AISHub fallback.",
     "min_zoom": 6, "ttl_seconds": 5, "credential_fields": []},
]
MAX_TARGETS = 1500
MAX_FLIGHT_QUERIES = 4
MAX_VESSEL_AREA_KM2 = 1_000_000
MAX_VESSEL_SPAN_DEGREES = 16
MAX_LEASES = 6
LEASE_SECONDS = 35
FLIGHT_MAX_AGE = 120
VESSEL_MAX_AGE = 600
TRAFFIC_CACHE_SECONDS = 1800
MAX_CACHED_TARGETS = 6000
AIS_URL = "wss://stream.aisstream.io/v0/stream"
AIS_POSITION = ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport")
AIS_TYPES = (*AIS_POSITION, "ShipStaticData", "StaticDataReport")
AIRCRAFT_PROFILE_TTL = 7 * 24 * 60 * 60
AIRCRAFT_PROFILE_MAX = 512
AIRCRAFT_PHOTO_MAX_BYTES = 1_500_000
AIRCRAFT_PHOTO_HOSTS = {"airport-data.com", "www.airport-data.com", "image.airport-data.com"}


def validate_traffic_filters(value):
    if not isinstance(value, dict) or set(value) - set(TRAFFIC_IDS):
        raise ValueError("Choose aircraft or vessel filters")
    result = {}
    for key, types in (("flights", FLIGHT_TYPES), ("vessels", VESSEL_TYPES)):
        item = value.get(key, {})
        if not isinstance(item, dict) or set(item) - {"affiliation", "type", "query"}:
            raise ValueError("Use classification, type and callsign/name filters")
        affiliation, kind, query = item.get("affiliation", "all"), item.get("type", "all"), item.get("query", "")
        if not isinstance(affiliation, str) or not isinstance(kind, str) or affiliation not in AFFILIATIONS or kind not in ("all", *types):
            raise ValueError("Choose an available traffic classification and type")
        if not isinstance(query, str) or len(query) > 64 or any(ord(char) < 32 for char in query):
            raise ValueError("Search traffic with at most 64 printable characters")
        result[key] = {"affiliation": affiliation, "type": kind, "query": query.strip()}
    return result


def number(value, low=-math.inf, high=math.inf):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and low <= value <= high else None
    except (TypeError, ValueError, OverflowError):
        return None


def clean(value, size=80):
    return "".join(c for c in str(value or "") if ord(c) >= 32).strip()[:size]


def ais_length(dimensions):
    if not isinstance(dimensions, dict):
        return None
    bow, stern = (number(dimensions.get(axis), 0, 511) for axis in ("A", "B"))
    if bow is None or stern is None:
        return None
    return round(bow + stern) or None


def point_feature(key, lon, lat, props):
    return {"type": "Feature", "id": key, "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def inside(feature, bounds):
    lon, lat = feature["geometry"]["coordinates"]
    return bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]


def flight_feature(row, generated_at, now):
    if not isinstance(row, dict):
        return None
    identity = clean(row.get("hex")).lower()
    lat, lon = number(row.get("lat"), -90, 90), number(row.get("lon"), -180, 180)
    age = number(row.get("seen_pos"), 0, FLIGHT_MAX_AGE)
    if not re.fullmatch(r"~?[0-9a-f]{6}", identity) or lat is None or lon is None or age is None:
        return None
    observed = generated_at - age
    if now - observed > FLIGHT_MAX_AGE or observed > now + 30:
        return None
    category = clean(row.get("category"))
    kind = {"A1": "light", "A2": "small", "A3": "medium", "A4": "heavy", "A5": "heavy", "A6": "other", "A7": "helicopter",
            "B1": "glider", "B2": "balloon", "B3": "other", "B4": "other", "B6": "drone", "C1": "ground", "C2": "ground"}.get(category, "unknown")
    callsign, registration = clean(row.get("flight"), 16), clean(row.get("r"), 20)
    flags = number(row.get("dbFlags"), 0, 255)
    military = flags is not None and int(flags) & 1
    # This is deliberately an estimate, NOT a database claim of ownership. A
    # three-letter flight designator can also belong to government/charter users.
    airline_style = kind in {"medium", "heavy"} and bool(re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9]{0,4}", callsign))
    affiliation = "military" if military else "commercial" if airline_style else "unknown"
    classification = "Provider military flag" if military else "Commercial / airline estimate: aircraft category + callsign pattern; unverified" if airline_style else "Ownership unknown; not assumed civilian"
    altitude_ft = number(row.get("alt_baro"), -2000, 100000)
    heading = number(row.get("track"), 0, 359.999)
    return point_feature(f"flight-{identity}", lon, lat, {
        "title": callsign or registration or identity.upper(), "callsign": callsign, "registration": registration, "identity": identity,
        "traffic_pack": "flights", "traffic_type": kind, "affiliation": affiliation, "classification_note": classification,
        "type_code": clean(row.get("t"), 12), "category": category, "heading": heading,
        "altitude_m": round(altitude_ft * .3048) if altitude_ft is not None else None,
        "altitude_reference": "Barometric altitude" if altitude_ft is not None else "Ground" if row.get("alt_baro") == "ground" else "Unknown",
        "speed_knots": number(row.get("gs"), 0, 2000), "observed_at": observed, "position_time": observed,
        "source_url": "https://www.adsb.lol/", "attribution": "ADSB.lol contributors · ODbL",
    })


def parse_flights(payload, now):
    if not isinstance(payload, dict) or not isinstance(payload.get("ac"), list):
        raise ValueError("Invalid aircraft feed")
    generated = number(payload.get("now"), 1)
    if generated is None:
        raise ValueError("Aircraft feed has no timestamp")
    if generated > 100_000_000_000:
        generated /= 1000
    features = []
    for row in payload["ac"][:MAX_TARGETS * 2]:
        feature = flight_feature(row, generated, now)
        if feature:
            features.append(feature)
    return features[:MAX_TARGETS], len(payload["ac"]) > MAX_TARGETS


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Aircraft provider redirected")


def fetch_traffic_json(url, user_agent):
    with build_opener(_NoRedirect()).open(Request(url, headers={"User-Agent": user_agent}), timeout=12) as response:
        data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError("Traffic response too large")
        return json.loads(data)


def fetch_flights(url):
    return fetch_traffic_json(url, "Reticom/0.1 public-aircraft-layer")


def flight_retry_at(error, now, default):
    retry = error.headers.get("Retry-After", "") if error.headers else ""
    if retry.isdecimal():
        return now + max(1, int(retry))
    if retry:
        try:
            return max(now + 1, parsedate_to_datetime(retry).timestamp())
        except (ValueError, OverflowError):
            pass
    return default


class FlightFeedCooldown(Exception):
    pass


def vessel_snapshot(bounds, aishub_key=""):
    west, south, east, north = bounds
    agent = "Reticom/0.2 (https://github.com/m-a-x-s-e-e-l-i-g/Reticom)"
    if aishub_key:
        query = urlencode(dict(username=aishub_key, format=1, output="json", interval=10,
                               lonmin=west, latmin=south, lonmax=east, latmax=north))
        payload = fetch_traffic_json(f"https://data.aishub.net/ws.php?{query}", agent)
        if (not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[0], dict)
                or payload[0].get("ERROR") is not False or not isinstance(payload[1], list)):
            raise ValueError("AISHub snapshot unavailable")
        return [dict(mmsi=row.get("MMSI"), name=row.get("NAME"), type=row.get("TYPE"),
                     callsign=row.get("CALLSIGN"), destination=row.get("DEST"), seen=row.get("TIME"),
                     length_m=ais_length(row),
                     cog=row.get("COG"), sog=row.get("SOG"), heading=row.get("HEADING"), nav_status=row.get("NAVSTAT"),
                     coordinates=[row.get("LONGITUDE"), row.get("LATITUDE")], source="aishub", credit="Data: AISHub contributors")
                for row in payload[1][:MAX_CACHED_TARGETS] if isinstance(row, dict)]

    # Anonymous Open Waters queries allow 100 square degrees per request.
    middle_lon, middle_lat = (west + east) / 2, (south + north) / 2
    boxes = [bounds] if (east-west)*(north-south) <= 100 else [
        (w, s, e, n) for w, e in ((west, middle_lon), (middle_lon, east))
        for s, n in ((south, middle_lat), (middle_lat, north))]
    rows = []
    for w, s, e, n in boxes:
        query = urlencode(dict(bbox=f"{s},{w},{n},{e}", max_age="10m", kind="vessel"))
        payload = fetch_traffic_json(f"https://ais.openwaters.io/v1/vessels?{query}", agent)
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            raise ValueError("Open Waters AIS snapshot unavailable")
        credits = payload.get("attribution")
        credits = credits if isinstance(credits, dict) else {}
        for feature in payload["features"][:MAX_TARGETS]:
            if not isinstance(feature, dict):
                continue
            props, geometry = feature.get("properties"), feature.get("geometry")
            if isinstance(props, dict) and isinstance(geometry, dict) and geometry.get("type") == "Point":
                source = clean(props.get("source")).split(":", 1)[0]
                credit = clean(credits.get(source), 500) or "Open Waters AIS"
                if source == "aishub":
                    credit = "Open Waters AIS · Data: AISHub contributors"
                rows.append({**props, "coordinates": geometry.get("coordinates"), "credit": credit})
    return rows


def fetch_aircraft_profile(identity):
    """Fetch a bounded, keyless metadata record only after an operator selects it."""
    url = f"https://api.adsbdb.com/v0/aircraft/{identity}"
    with build_opener(_NoRedirect()).open(Request(url, headers={"User-Agent": "Reticom/0.1 aircraft-details"}), timeout=8) as response:
        data = response.read(256_001)
        if len(data) > 256_000:
            raise ValueError("Aircraft metadata response too large")
        return json.loads(data)


def safe_aircraft_photo_url(value):
    value = clean(value, 500)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in AIRCRAFT_PHOTO_HOSTS:
        return None
    return value


def aircraft_profile(payload):
    aircraft = payload.get("response", {}).get("aircraft") if isinstance(payload, dict) else None
    if not isinstance(aircraft, dict):
        return None
    return {
        "aircraft_model": clean(aircraft.get("type"), 100),
        "aircraft_manufacturer": clean(aircraft.get("manufacturer"), 80),
        "aircraft_type_code": clean(aircraft.get("icao_type"), 12),
        "aircraft_registration": clean(aircraft.get("registration"), 20),
        "aircraft_owner": clean(aircraft.get("registered_owner"), 100),
        "photo_url": safe_aircraft_photo_url(aircraft.get("url_photo_thumbnail")),
        "photo_source_url": safe_aircraft_photo_url(aircraft.get("url_photo")),
    }


def fetch_aircraft_photo(url):
    if not safe_aircraft_photo_url(url):
        raise ValueError("Unsafe aircraft photo URL")
    return _fetch_traffic_photo(url)


def _fetch_traffic_photo(url):
    with build_opener(_NoRedirect()).open(Request(url, headers={"User-Agent": "Reticom/0.2 public-traffic-photo"}), timeout=8) as response:
        content_type = clean(response.headers.get("Content-Type")).lower().split(";", 1)[0]
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Traffic photo is not a supported image")
        data = response.read(AIRCRAFT_PHOTO_MAX_BYTES + 1)
        if not data or len(data) > AIRCRAFT_PHOTO_MAX_BYTES:
            raise ValueError("Invalid traffic photo size")
        return {"data": data, "content_type": content_type}


def vessel_photo_info(identity):
    url = f"https://openvesselphotos.org/v1/mmsi/{identity}"
    payload = fetch_traffic_json(url, "Reticom/0.2 public-vessel-photo (+https://github.com/m-a-x-s-e-e-l-i-g/Reticom)")
    if not isinstance(payload, dict) or str(payload.get("mmsi")) != identity or not isinstance(payload.get("photos"), list):
        return None
    for photo in payload["photos"]:
        if not isinstance(photo, dict):
            continue
        photo_number = photo.get("n")
        credit = clean(photo.get("attribution"), 350)
        if type(photo_number) is int and 1 <= photo_number <= 9999 and credit:
            return {"number": photo_number, "credit": credit,
                    "source_url": clean(photo.get("source"), 800) or f"{url}/photos/{photo_number}.jpg"}
    return None


def fetch_vessel_photo(identity, photo_number):
    return _fetch_traffic_photo(f"https://openvesselphotos.org/v1/mmsi/{identity}/photos/{photo_number}.jpg?size=320")


def flight_query(bounds):
    west, south, east, north = bounds
    lat, lon = (south + north) / 2, (west + east) / 2
    # Conservative covering circle; reject rather than silently truncate views.
    radius = math.ceil(math.hypot((east-west)*111.32/2, (north-south)*111.32/2) / 1.852) + 1
    if radius > 250:
        return None
    return f"https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{max(1, radius)}"


def flight_queries(bounds):
    """Cover a wider map with a small, explicit number of provider-safe circles."""
    direct = flight_query(bounds)
    if direct:
        return (direct,)
    west, south, east, north = bounds
    candidates = []
    for columns in range(1, MAX_FLIGHT_QUERIES + 1):
        for rows in range(1, MAX_FLIGHT_QUERIES + 1):
            if columns * rows > MAX_FLIGHT_QUERIES:
                continue
            queries = []
            for column in range(columns):
                for row in range(rows):
                    box = (west + (east-west) * column / columns, south + (north-south) * row / rows,
                           west + (east-west) * (column+1) / columns, south + (north-south) * (row+1) / rows)
                    query = flight_query(box)
                    if not query:
                        queries = []
                        break
                    queries.append(query)
                if not queries:
                    break
            if queries:
                candidates.append(tuple(queries))
    return min(candidates, key=len, default=())


def vessel_view_allowed(bounds):
    west, south, east, north = bounds
    area = (east-west) * (north-south) * 111.32**2 * max(.01, math.cos(math.radians((south+north)/2)))
    return area <= MAX_VESSEL_AREA_KM2 and east-west <= MAX_VESSEL_SPAN_DEGREES and north-south <= MAX_VESSEL_SPAN_DEGREES


def vessel_kind(code):
    if code == 35:
        return "military_restricted", "military"
    if 60 <= code <= 69:
        return "passenger", "commercial"
    if 70 <= code <= 79:
        return "cargo", "commercial"
    if 80 <= code <= 89:
        return "tanker", "commercial"
    kinds = {30: "fishing", 31: "tug", 32: "tug", 36: "sailing", 37: "pleasure", 52: "tug"}
    if code in kinds:
        return kinds[code], "commercial" if code in {30, 31, 32, 52} else "unknown"
    if 40 <= code <= 49:
        return "high_speed", "unknown"
    if code in {33, 34, 50, 51, 53, 54, 55, 58, 59}:
        return "service", "unknown"
    return ("unknown" if code == 0 else "other"), "unknown"


def ais_timestamp(metadata, now):
    value = str(metadata.get("time_utc", ""))
    try:
        observed = datetime.fromisoformat(value[:19].replace(" ", "T")).replace(tzinfo=timezone.utc).timestamp()
        return observed if observed <= now + 30 else None
    except ValueError:
        return now  # explicitly labelled stream-received time, not GNSS time


class VesselRegistry:
    def __init__(self, on_position=None):
        self.records = OrderedDict()
        self.on_position = on_position

    def ingest_snapshot(self, row, now, provider):
        coordinates, seen = row.get("coordinates"), row.get("seen")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2 or not isinstance(seen, str):
            return False
        try:
            observed = datetime.fromisoformat(seen.replace(" GMT", "+00:00").replace("Z", "+00:00"))
            if observed.tzinfo is None:
                return False
            report_time = observed.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            return False
        lon, lat = coordinates
        position = dict(UserID=row.get("mmsi"), Latitude=lat, Longitude=lon, TrueHeading=row.get("heading"),
                        Cog=row.get("cog"), Sog=row.get("sog"), NavigationalStatus=row.get("nav_status"))
        return self.ingest({"MessageType": "PositionReport", "Message": {"PositionReport": position},
                            "MetaData": {"time_utc": report_time}}, now, provider, row)

    def ingest(self, envelope, now, provider="AISStream", snapshot=None):
        if not isinstance(envelope, dict) or envelope.get("MessageType") not in AIS_TYPES:
            return
        kind = envelope["MessageType"]
        messages = envelope.get("Message", {})
        if not isinstance(messages, dict):
            return
        message = messages.get(kind, {})
        metadata = envelope.get("MetaData", {})
        if not isinstance(message, dict) or not isinstance(metadata, dict) or message.get("Valid") is False:
            return
        mmsi = clean(message.get("UserID") or metadata.get("MMSI"), 12)
        if not re.fullmatch(r"[0-9]{9}", mmsi):
            return
        record = self.records.setdefault(mmsi, {"identity": mmsi, "ship_type_code": 0, "metadata_time": now})
        self.records.move_to_end(mmsi)
        if len(self.records) > MAX_TARGETS:
            self.records.popitem(last=False)
        if metadata.get("ShipName"):
            record["title"] = clean(metadata["ShipName"])
        length = None
        if kind == "ShipStaticData":
            record.update(title=clean(message.get("Name")) or record.get("title", mmsi), callsign=clean(message.get("CallSign")),
                          ship_type_code=int(number(message.get("Type"), 0, 99) or 0), destination=clean(message.get("Destination")))
            length = ais_length(message.get("Dimension"))
        elif kind == "StaticDataReport":
            part_a, part_b = message.get("ReportA", {}), message.get("ReportB", {})
            if message.get("PartNumber") == 0 and isinstance(part_a, dict) and part_a.get("Valid") is not False:
                record["title"] = clean(part_a.get("Name")) or record.get("title", mmsi)
            elif message.get("PartNumber") == 1 and isinstance(part_b, dict) and part_b.get("Valid") is not False:
                record.update(callsign=clean(part_b.get("CallSign")), ship_type_code=int(number(part_b.get("ShipType"), 0, 99) or 0))
                length = ais_length(part_b.get("Dimension"))
        else:
            lat = number(message.get("Latitude", metadata.get("latitude", metadata.get("Latitude"))), -90, 90)
            lon = number(message.get("Longitude", metadata.get("longitude", metadata.get("Longitude"))), -180, 180)
            observed = ais_timestamp(metadata, now)
            if lat is None or lon is None or observed is None or now-observed > VESSEL_MAX_AGE or observed < record.get("position_time", 0):
                return
            heading = number(message.get("TrueHeading"), 0, 359)
            if heading is None:
                heading = number(message.get("Cog"), 0, 359.999)
            record.update(lon=lon, lat=lat, position_time=observed, heading=heading,
                          course=number(message.get("Cog"), 0, 359.999),
                          navigation_status=number(message.get("NavigationalStatus"), 0, 15),
                          speed_knots=number(message.get("Sog"), 0, 102.2), source=provider,
                          attribution="AISStream · AIS reports", source_url="https://aisstream.io/")
            if kind == "ExtendedClassBPositionReport":
                length = ais_length(message.get("Dimension"))
                if ship_type := number(message.get("Type"), 1, 99):
                    record["ship_type_code"] = int(ship_type)
            if snapshot is not None:
                length = number(snapshot.get("length_m"), 1, 1022)
                source_url = "https://www.aishub.net/" if snapshot.get("source") == "aishub" else "https://openwaters.io/ais/"
                record.update(attribution=snapshot["credit"], source_url=source_url)
                for field, value in (("title", clean(snapshot.get("name"))), ("callsign", clean(snapshot.get("callsign"))),
                                     ("destination", clean(snapshot.get("destination"))), ("ship_type_code", int(number(snapshot.get("type"), 0, 99) or 0))):
                    if value:
                        record[field] = value
        if length:
            record["length_m"] = round(length)
        # Static reports enrich a target, never refresh its position or resurrect it.
        record["metadata_time"] = now
        if kind in AIS_POSITION and self.on_position and "position_time" in record:
            self.on_position(record)
        return True

    def features(self, bounds, now):
        result = []
        for mmsi, record in list(self.records.items()):
            if now-record.get("position_time", record["metadata_time"]) > TRAFFIC_CACHE_SECONDS:
                self.records.pop(mmsi, None)
                continue
            if "position_time" not in record:
                continue
            kind, affiliation = vessel_kind(record["ship_type_code"])
            props = {key: value for key, value in record.items() if key not in {"lat", "lon", "metadata_time"}}
            props.update(title=record.get("title") or mmsi, traffic_pack="vessels", traffic_type=kind, affiliation=affiliation,
                         observed_at=record["position_time"],
                         classification_note="AIS type 35: military OR other restricted operations, not confirmed ownership" if affiliation == "military" else "Commercial use inferred from self-reported AIS vessel type" if affiliation == "commercial" else "Ownership unknown; AIS vessel type may be missing",
                         position_time_note="Provider report time, not guaranteed GNSS fix time")
            feature = point_feature(f"vessel-{mmsi}", record["lon"], record["lat"], props)
            if inside(feature, bounds):
                result.append(feature)
        return result


class LiveTraffic:
    def __init__(self, store, *, clock=time.time, flight_fetch=fetch_flights, aircraft_fetch=fetch_aircraft_profile,
                 photo_fetch=fetch_aircraft_photo, connector=None, snapshot_fetch=vessel_snapshot):
        self.store, self.clock, self.flight_fetch, self.aircraft_fetch, self.photo_fetch, self.connector = store, clock, flight_fetch, aircraft_fetch, photo_fetch, connector
        self.flight_lock = asyncio.Lock()
        self.flight_data, self.flight_at, self.flight_next = [], 0, 0
        self.primary_flight_next = self.fallback_flight_next = 0
        self.flight_views = OrderedDict()
        self.flight_clients = OrderedDict()
        self.flight_epoch = 0
        self.flight_capped = False
        self.flight_error = None
        self.aircraft_profiles = OrderedDict()
        from .traffic_history import TrafficHistory
        self.history = TrafficHistory(store.directory / "traffic-history.sqlite3", clock=clock)
        self.route_cache = OrderedDict()
        self.route_lock = asyncio.Lock()
        self.leases = OrderedDict()
        self.registry = VesselRegistry(self.record_vessel)
        self.task = None
        self.closed = False
        self.ais_state = "waiting"
        self.ais_keys = None
        self.ais_provider = None
        self.snapshot_fetch = snapshot_fetch
        self.aishub_next = 0

    def access(self):
        with self.store.lock:
            return "vessels" in self.store.enabled, (self.store.credentials.get("aisstream_key", ""), self.store.credentials.get("aishub_key", ""))

    async def settings_changed(self):
        enabled, keys = self.access()
        if not enabled or keys != self.ais_keys:
            await self.stop_vessels()
        if not self.store.is_enabled("flights"):
            self.flight_data = []
            self.flight_views.clear()
            self.flight_clients.clear()
            self.flight_epoch += 1

    async def stop_vessels(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.leases.clear()
        self.registry = VesselRegistry(self.record_vessel)
        self.ais_state, self.ais_keys, self.ais_provider = "waiting", None, None

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.stop_vessels()
        self.history.close()

    def record_vessel(self, record):
        self.history.add("vessels", record["identity"], record["position_time"], record.get("lon"), record.get("lat"))

    async def route(self, pack, identity):
        from .traffic_history import valid_identity, fetch_trace, aircraft_trace
        if pack not in TRAFFIC_IDS or not valid_identity(pack, identity):
            raise ValueError("Choose a valid aircraft or vessel")
        if self.closed or not self.store.is_enabled(pack):
            raise ValueError("Enable this traffic layer to view its route")
        identity = identity.lower()
        points = self.history.points(pack, identity)
        note = "Recorded AIS track · last 24 hours; earlier voyage unavailable" if pack == "vessels" else "Local fixes · ADSB.lol history unavailable"
        if pack == "flights":
            async with self.route_lock:
                cached = self.route_cache.get(identity)
                if not cached or self.clock() - cached["at"] > 60:
                    try:
                        historical = aircraft_trace(await asyncio.to_thread(fetch_trace, identity), identity, self.clock())
                    except Exception:
                        historical = []
                    cached = {"at": self.clock(), "points": historical}
                    self.route_cache[identity] = cached
                    while len(self.route_cache) > 64:
                        self.route_cache.popitem(last=False)
                if cached["points"]:
                    merged = {p["time"]: p for p in [*points, *cached["points"]]}
                    points = sorted(merged.values(), key=lambda p: p["time"])[-12000:]
                    note = "ADSB.lol history · coverage may have gaps"
        return {"points": points, "note": note, "complete": False}

    @staticmethod
    def valid_aircraft_identity(identity):
        return isinstance(identity, str) and bool(re.fullmatch(r"[0-9a-fA-F]{6}", identity))

    async def aircraft_profile(self, identity):
        if not self.valid_aircraft_identity(identity):
            raise ValueError("Choose a valid aircraft address")
        identity = identity.lower()
        now = self.clock()
        cached = self.aircraft_profiles.get(identity)
        if cached and now - cached["fetched_at"] < (AIRCRAFT_PROFILE_TTL if cached["profile"] else 300):
            self.aircraft_profiles.move_to_end(identity)
            return cached["profile"]
        try:
            profile = await asyncio.to_thread(self.aircraft_fetch, identity)
            profile = aircraft_profile(profile)
        except Exception:
            profile = None
        # A failed lookup is intentionally short-lived: public databases can
        # gain an airframe record without waiting a week to retry.
        self.aircraft_profiles[identity] = {"profile": profile, "fetched_at": now}
        self.aircraft_profiles.move_to_end(identity)
        while len(self.aircraft_profiles) > AIRCRAFT_PROFILE_MAX:
            self.aircraft_profiles.popitem(last=False)
        return profile

    async def aircraft_photo(self, identity):
        profile = await self.aircraft_profile(identity)
        if not profile or not profile.get("photo_url"):
            return None
        try:
            return await asyncio.to_thread(self.photo_fetch, profile["photo_url"])
        except Exception:
            return None

    def release(self, client):
        self.leases.pop(client, None)

    def boxes(self):
        now = self.clock()
        for key, (_bounds, until) in list(self.leases.items()):
            if until <= now:
                self.leases.pop(key, None)
        return sorted(set(bounds for bounds, _until in self.leases.values()))

    async def load(self, pack_id, bounds, zoom, client):
        empty = {"type": "FeatureCollection", "features": [], "status": "disabled", "traffic": True, "fetched_at": self.clock()}
        if self.closed or not self.store.is_enabled(pack_id):
            return empty
        if pack_id not in TRAFFIC_IDS:
            raise ValueError("Choose a live traffic pack")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", client):
            raise ValueError("Invalid map session")
        if not math.isfinite(zoom) or not 0 <= zoom <= 24:
            raise ValueError("Invalid zoom")
        if zoom < 6:
            self.release(client)
            return {**empty, "status": "zoom_in", "note": "Zoom in to level 6 for live traffic."}
        if pack_id == "flights":
            queries = flight_queries(bounds)
            if not queries:
                return {**empty, "status": "zoom_in", "note": f"Zoom in: aircraft coverage is limited to {MAX_FLIGHT_QUERIES} regional requests."}
            return await self.flights(bounds, empty, queries, client)
        if not vessel_view_allowed(bounds):
            self.release(client)
            return {**empty, "status": "zoom_in", "note": "Zoom in: vessel coverage is limited to a large regional view."}
        _, keys = self.access()
        if keys != self.ais_keys:
            await self.stop_vessels()
            self.ais_keys = keys
        self.leases[client] = (tuple(bounds), self.clock() + LEASE_SECONDS)
        self.leases.move_to_end(client)
        while len(self.leases) > MAX_LEASES:
            self.leases.popitem(last=False)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run_vessels(keys))
        features = self.registry.features(bounds, self.clock())
        capped = len(features) > MAX_TARGETS
        features = sorted(features, key=lambda feature: feature["properties"].get("position_time", 0), reverse=True)[:MAX_TARGETS]
        note = f"{self.ais_provider} · {len(features)} vessels in view" if self.ais_state == "connected" else "Connecting to AIS providers…" if self.ais_state == "waiting" else "AIS providers unavailable · retrying"
        return {**empty, "status": "fresh" if self.ais_state == "connected" else "stale" if features else "waiting" if self.ais_state == "waiting" else "error",
                "features": features, "capped": capped, "note": note, "error": note if self.ais_state == "error" else None}

    async def flight_batches(self, queries):
        now = self.clock()
        if now >= self.primary_flight_next:
            try:
                payloads = await asyncio.gather(*(asyncio.to_thread(self.flight_fetch, query) for query in queries))
                return [parse_flights(payload, self.clock()) for payload in payloads]
            except (OSError, ValueError) as error:
                now = self.clock()
                if isinstance(error, HTTPError) and error.code == 429:
                    self.primary_flight_next = flight_retry_at(error, now, (int(now) // 86400 + 1) * 86400)
                elif not PERSONAL_FLIGHT_FALLBACK:
                    raise
                else:
                    self.primary_flight_next = now + 30
        if not PERSONAL_FLIGHT_FALLBACK or now < self.fallback_flight_next:
            raise FlightFeedCooldown
        batches = []
        for index, query in enumerate(queries):
            if index:
                await asyncio.sleep(1)
            _, lat, lon, radius = query.rsplit("/", 3)
            url = f"https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{radius}"
            try:
                payload = await asyncio.to_thread(self.flight_fetch, url)
                features, capped = parse_flights(payload, self.clock())
            except (OSError, ValueError) as error:
                now = self.clock()
                self.fallback_flight_next = flight_retry_at(error, now, now + 30) if isinstance(error, HTTPError) else now + 30
                if batches:
                    return batches
                if isinstance(error, HTTPError) and error.code == 429:
                    raise FlightFeedCooldown from error
                raise
            for feature in features:
                feature["properties"].update(source_url="https://adsb.fi/", attribution="adsb.fi")
            batches.append((features, capped))
        return batches

    async def flights(self, bounds, empty, queries, client):
        async with self.flight_lock:
            now = self.clock()
            bounds = tuple(bounds)
            self.flight_clients[client] = (bounds, now)
            self.flight_clients.move_to_end(client)
            for key, (_view, at) in list(self.flight_clients.items()):
                if now - at > LEASE_SECONDS:
                    self.flight_clients.pop(key)
            while len(self.flight_clients) > MAX_LEASES:
                self.flight_clients.popitem(last=False)
            active_views = {view for view, _at in self.flight_clients.values()}
            for view in list(self.flight_views):
                if view not in active_views:
                    self.flight_views.pop(view)
            current = self.flight_views.setdefault(bounds, {"demand_at": now, "attempt_at": 0, "features": [], "fetched_at": None, "error": None, "capped": False})
            current["demand_at"] = now
            self.flight_views.move_to_end(bounds)
            while len(self.flight_views) > MAX_LEASES:
                self.flight_views.popitem(last=False)
            if now >= self.flight_next:
                # Round-robin least recently attempted view: date-line halves and
                # concurrent maps cannot permanently starve behind the first box.
                chosen_bounds, chosen = min(self.flight_views.items(), key=lambda item: item[1]["attempt_at"])
                chosen["attempt_at"] = now
                self.flight_next = now + 10
                epoch = self.flight_epoch
                try:
                    chosen_queries = flight_queries(chosen_bounds)
                    batches = await self.flight_batches(chosen_queries)
                    merged = {}
                    for features, _capped in batches:
                        for feature in features:
                            merged.setdefault(feature["id"], feature)
                    features = list(merged.values())[:MAX_TARGETS]
                    capped = len(merged) > MAX_TARGETS or any(_capped for _features, _capped in batches)
                    if not self.store.is_enabled("flights") or epoch != self.flight_epoch:
                        return empty
                    # Merge observations across regions, never replace the whole
                    # cache with the most recently fetched viewport or empty batch.
                    cached = {feature["id"]: feature for feature in self.flight_data
                              if self.clock() - feature["properties"]["position_time"] <= TRAFFIC_CACHE_SECONDS}
                    for feature in features:
                        old = cached.get(feature["id"])
                        if not old or feature["properties"]["position_time"] >= old["properties"]["position_time"]:
                            cached[feature["id"]] = feature
                    self.flight_data = sorted(cached.values(), key=lambda f: f["properties"]["position_time"], reverse=True)[:MAX_CACHED_TARGETS]
                    self.flight_capped = capped
                    for feature in features:
                        props = feature["properties"]
                        lon, lat = feature["geometry"]["coordinates"][:2]
                        self.history.add("flights", props["identity"], props["position_time"], lon, lat)
                    self.flight_at, self.flight_error = self.clock(), None
                    chosen.update(features=features, capped=capped, fetched_at=self.clock(), error=None)
                except FlightFeedCooldown:
                    self.flight_next = self.clock() + 30
                    current["error"] = chosen["error"] = "Aircraft provider is cooling down. Last-seen positions stay visible until they expire."
                except Exception:
                    self.flight_next = self.clock() + 30
                    chosen["error"] = "Aircraft feed unavailable; retrying shortly. Coverage is not guaranteed."
            if not self.store.is_enabled("flights"):
                return empty
            self.flight_data = [feature for feature in self.flight_data if self.clock()-feature["properties"]["position_time"] <= TRAFFIC_CACHE_SECONDS]
            features = [feature for feature in self.flight_data if inside(feature, bounds)][:MAX_TARGETS]
            if not current["error"] and current["fetched_at"] is None:
                # Exact viewport cache keys should not make a tiny pan blank the
                # map. Return a recent compatible global snapshot while this
                # viewport waits for its own round-robin refresh.
                recent = features
                if recent:
                    return {**empty, "status": "stale", "features": recent, "fetched_at": self.flight_at,
                            "capped": self.flight_capped,
                            "note": "Showing recent aircraft positions while this view refreshes"}
                return {**empty, "status": "waiting", "features": [], "note": "Map moved · waiting for the next aircraft refresh"}
            return {**empty, "status": "stale" if current["error"] and features else "error" if current["error"] else "fresh",
                    "features": features, "fetched_at": current["fetched_at"], "capped": current["capped"], "error": current["error"],
                    "note": current["error"] or "Refreshes about every 10 seconds · last-seen positions cached for 30 minutes"}

    async def stream_vessels(self, keys):
        if self.connector is None:
            from websockets.asyncio.client import connect
        else:
            connect = self.connector
        loop = asyncio.get_running_loop()
        async with connect(AIS_URL, compression="deflate", open_timeout=10, close_timeout=2, max_size=65536,
                           max_queue=32, ping_interval=20, ping_timeout=20, proxy=None) as socket:
            sent, sent_at = None, 0
            deadline = loop.time() + 15
            while (boxes := self.boxes()) and self.access() == (True, keys):
                if loop.time() >= deadline:
                    raise TimeoutError("AISStream sent no recent positions")
                if boxes != sent and loop.time()-sent_at >= 1:
                    await socket.send(json.dumps({"APIKey": keys[0], "BoundingBoxes": [[[s, w], [n, e]] for w, s, e, n in boxes], "FilterMessageTypes": list(AIS_TYPES)}))
                    sent, sent_at = boxes, loop.time()
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1)
                    data = json.loads(raw)
                except (TimeoutError, ValueError, UnicodeDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("MessageType")
                if data.get("error") or data.get("Error") or kind in ("Error", "SubscriptionError"):
                    raise ValueError("AISStream subscription rejected")
                if kind == "SubscriptionConfirmation" and self.ais_provider != "AISStream":
                    deadline = loop.time() + 45
                    self.ais_state, self.ais_provider = "connected", "AISStream"
                if self.registry.ingest(data, self.clock()) and kind in AIS_POSITION:
                    deadline = loop.time() + 45
                    self.ais_state, self.ais_provider = "connected", "AISStream"

    async def run_vessels(self, keys):
        loop = asyncio.get_running_loop()
        primary_next = 0
        open_view = hub_view = 0
        try:
            while (boxes := self.boxes()) and not self.closed and self.access() == (True, keys):
                if keys[0] and loop.time() >= primary_next:
                    try:
                        await self.stream_vessels(keys)
                        return
                    except Exception:
                        primary_next = loop.time() + 120
                        continue
                bounds = boxes[open_view % len(boxes)]
                open_view += 1
                available = await self.poll_vessels(bounds, keys, "Open Waters AIS")
                if not available and keys[1] and loop.time() >= self.aishub_next and self.access() == (True, keys):
                    self.aishub_next = loop.time() + 60
                    bounds = boxes[hub_view % len(boxes)]
                    hub_view += 1
                    await self.poll_vessels(bounds, keys, "AISHub")
                await asyncio.sleep(8)
        finally:
            if self.ais_state != "error":
                self.ais_state = "waiting"

    async def poll_vessels(self, bounds, keys, provider):
        try:
            rows = await asyncio.to_thread(self.snapshot_fetch, bounds, keys[1] if provider == "AISHub" else "")
        except Exception:
            # Provider errors can contain the contributor key in the request URL.
            self.ais_state = "error"
            return False
        if self.closed or not self.boxes() or self.access() != (True, keys):
            return False
        now = self.clock()
        received = sum(bool(self.registry.ingest_snapshot(row, now, provider)) for row in rows)
        if received or self.ais_state != "connected":
            self.ais_state, self.ais_provider = "connected", provider
        return bool(received)
