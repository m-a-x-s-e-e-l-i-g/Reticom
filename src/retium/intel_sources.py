"""Bounded, read-only public map context. Never creates signed team events.

Provider credentials stay in HTTP requests, not feature properties or exceptions.
The caller owns persistent caching, provider rate limits and opt-in preferences.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
import io
import json
import math
import re
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 8_000_000
MAX_FEATURES = 3000
MAX_OSM_AREA_KM2 = 2500
# OSM queries are deliberately tighter for dense hiking geometry. Military
# land-use data is much sparser and often spans a whole training estate, so it
# can safely be useful one zoom level earlier without forcing people to zoom
# into each individual base.
OSM_AREA_LIMIT_KM2 = {"trails": MAX_OSM_AREA_KM2, "military": 15_000}
OSM_SPAN_LIMIT_DEGREES = {"trails": 2, "military": 4}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GDACS_URL = "https://www.gdacs.org/contentdata/xml/gdacsAPP_Home.geojson"
GDACS_NAMES = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "DR": "Drought", "WF": "Wildfire", "VO": "Volcanic activity", "TS": "Tsunami"}
GDACS_TYPES = tuple(GDACS_NAMES)
GDACS_FEEDS = {code: f"https://www.gdacs.org/contentdata/xml/gdacs{code}.geojson" for code in GDACS_TYPES if code != "TS"}
GDACS_GEOMETRY_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
GDACS_DETAIL_ROOT = "https://www.gdacs.org/contentdata/resources/"
GDACS_MAX_BYTES = 16_000_000
GDACS_DOCUMENT_BYTES = 6_000_000
GDACS_MAX_COORDINATES = 200_000
GDACS_MAX_FEATURE_COORDINATES = 100_000
GDACS_DETAIL_LIMIT = 4
GDACS_DETAIL_MAX_SPAN = 10
GDACS_DEADLINE_SECONDS = 20
_GDACS_CACHE = {}
_GDACS_CACHE_LOCK = threading.Lock()
ACLED_URL = "https://acleddata.com/api/acled/read"
FIRMS_ROOT = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"

PACKS = [
    {
        "id": "trails", "title": "Hiking trails", "source": "OpenStreetMap",
        "description": "Mapped walking routes, paths and tracks. Access and conditions are not guaranteed.",
        "source_url": "https://wiki.openstreetmap.org/wiki/Tag:route%3Dhiking",
        "attribution": "© OpenStreetMap contributors · ODbL",
        "min_zoom": 12, "ttl_seconds": 86400, "credential_fields": [],
    },
    {
        "id": "military", "title": "Military areas & airstrips", "source": "OpenStreetMap",
        "description": "Publicly mapped military land, bases, training grounds and airfields; civilian airstrips are labeled separately.",
        "source_url": "https://wiki.openstreetmap.org/wiki/Tag:landuse%3Dmilitary",
        "attribution": "© OpenStreetMap contributors · ODbL",
        "min_zoom": 9, "ttl_seconds": 86400, "credential_fields": [], "offline_downloadable": True,
    },
    {
        "id": "gdacs", "title": "Disaster alerts", "source": "GDACS",
        "description": "Published disaster alerts, affected areas and tracks where GDACS supplies them. Not a complete or real-time incident feed.",
        "source_url": "https://www.gdacs.org/",
        "attribution": "GDACS · European Commission / United Nations",
        "min_zoom": 0, "ttl_seconds": 900, "credential_fields": [],
        "disaster_types": [{"id": code, "label": label} for code, label in GDACS_NAMES.items()],
    },
    {
        "id": "firms", "title": "Satellite heat detections", "source": "NASA FIRMS",
        "description": "VIIRS NOAA-20 thermal anomalies from the last 3 days. A detection is not a confirmed fire or attack.",
        "source_url": "https://firms.modaps.eosdis.nasa.gov/",
        "attribution": "NASA FIRMS · VIIRS NOAA-20 NRT",
        # A regional heat picture is intentionally a daily device-local
        # snapshot. Reusing it makes zooming back out instant and avoids
        # repeatedly spending the user's FIRMS quota on the same view.
        "min_zoom": 4, "ttl_seconds": 86400, "credential_fields": ["firms_key"],
    },
    {
        "id": "acled", "title": "Conflict & protest reports", "source": "ACLED",
        "description": "Published events from the last 30 days, subject to your ACLED access. Reporting dates and locations can be approximate.",
        "source_url": "https://acleddata.com/",
        "attribution": "ACLED · Subject to ACLED terms of use",
        "min_zoom": 4, "ttl_seconds": 21600, "credential_fields": ["acled_token"],
    },
]
_PACK_BY_ID = {pack["id"]: pack for pack in PACKS}


class IntelSourceError(ValueError):
    """A provider failure safe to show in the UI (contains no response or key)."""


ProviderError = IntelSourceError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward an ACLED bearer token to another origin.
        raise IntelSourceError("The provider redirected its API. Try again later.")


def _gdacs_endpoint(url):
    if url == GDACS_URL or url in GDACS_FEEDS.values():
        return True
    parts = urlsplit(url)
    static = re.fullmatch(r"/contentdata/resources/(EQ|TC|FL|DR|WF|VO|TS)/([1-9][0-9]{0,9})/geojson_([1-9][0-9]{0,9})_([1-9][0-9]{0,9})\.geojson", parts.path)
    if (static and static[2] == static[3] and parts.scheme == "https"
            and parts.netloc == "www.gdacs.org" and not parts.query and not parts.fragment):
        return True
    if (parts.scheme != "https" or parts.netloc != "www.gdacs.org"
            or parts.path != "/gdacsapi/api/polygons/getgeometry" or parts.fragment):
        return False
    query = parse_qs(parts.query, keep_blank_values=True)
    return (set(query) == {"eventtype", "eventid", "episodeid"}
            and len(query["eventtype"]) == 1 and query["eventtype"][0] in GDACS_TYPES
            and all(len(query[key]) == 1 and re.fullmatch(r"[1-9][0-9]{0,9}", query[key][0]) for key in ("eventid", "episodeid")))


def fetch_bytes(url: str, *, data: bytes | None = None, headers: dict | None = None,
                timeout: float = 20, max_bytes: int = MAX_BYTES) -> bytes:
    parts = urlsplit(url)
    allowed = (
        url == OVERPASS_URL or _gdacs_endpoint(url)
        or (parts.scheme == "https" and parts.netloc == "acleddata.com" and parts.path == "/api/acled/read")
        or (parts.scheme == "https" and parts.netloc == "firms.modaps.eosdis.nasa.gov" and parts.path.startswith("/api/area/csv/"))
    )
    if not allowed or parts.username or parts.password or parts.fragment:
        raise IntelSourceError("Unsupported public data endpoint.")
    request = Request(url, data=data, headers={
        "User-Agent": "Reticom/0.1 public-intel (+https://github.com/m-a-x-s-e-e-l-i-g/Reticom)",
        "Accept": "application/json,text/csv;q=0.9,*/*;q=0.1",
        **(headers or {}),
    })
    deadline = time.monotonic() + max(0.1, min(20, timeout))
    try:
        with build_opener(_NoRedirect()).open(request, timeout=max(0.1, min(20, timeout))) as response:
            size = response.headers.get("Content-Length")
            if size and size.isdigit() and int(size) > max_bytes:
                raise IntelSourceError("The provider response is too large. Zoom in and retry.")
            # Socket timeouts alone reset for every read. Bound wall time too,
            # so a slow/dribbling geometry response cannot monopolize refresh.
            chunks, received = [], 0
            while received <= max_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                # urllib's HTTPResponse owns this socket. Tighten each blocking
                # read to the remaining wall-clock allowance where available.
                socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                if socket is not None:
                    socket.settimeout(remaining)
                chunk = response.read1(min(65_536, max_bytes + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            payload = b"".join(chunks)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise IntelSourceError("Provider access was denied. Check your API credentials and account access.") from None
        if exc.code == 429:
            raise IntelSourceError("The provider is rate-limiting requests. Please wait before retrying.") from None
        raise IntelSourceError(f"The public data provider is unavailable (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError):
        raise IntelSourceError("The public data provider could not be reached. Check Internet access and retry later.") from None
    if len(payload) > max_bytes:
        raise IntelSourceError("The provider response is too large. Zoom in and retry.")
    return payload


def validate_bbox(bbox) -> tuple[float, float, float, float]:
    try:
        if len(bbox) != 4 or any(isinstance(value, bool) for value in bbox):
            raise ValueError
        west, south, east, north = map(float, bbox)
        if not all(math.isfinite(v) for v in (west, south, east, north)):
            raise ValueError
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError
    except (ValueError, TypeError):
        raise IntelSourceError("Choose a valid map area that does not cross the date line.") from None
    return west, south, east, north


def _text(value, limit=500) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", str(value))).split())[:limit]


def _coord(value):
    if not isinstance(value, (tuple, list)) or len(value) < 2 or any(isinstance(v, bool) for v in value[:2]):
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (ValueError, TypeError):
        return None
    if not math.isfinite(x) or not math.isfinite(y) or not (-180 <= x <= 180 and -90 <= y <= 90):
        return None
    return [x, y]


def _inside(point, bbox):
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _json(payload):
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise IntelSourceError("The provider returned unreadable data. Try again later.") from None


def _feature(pack_id, identifier, geometry, title, detail, kind, observed_at="", source_url=None, **extra):
    pack = _PACK_BY_ID[pack_id]
    identifier = f"{pack_id}:{identifier}"
    return {
        "type": "Feature", "id": identifier, "geometry": geometry,
        "properties": {
            "id": identifier, "title": _text(title, 160), "detail": _text(detail, 1000),
            "kind": _text(kind, 60), "source": pack["source"],
            "source_url": source_url or pack["source_url"], "attribution": pack["attribution"],
            "observed_at": _text(observed_at, 50), **extra,
        },
    }


def _collection(pack_id, features, *, truncated=False, updated_at=None, note=""):
    pack = _PACK_BY_ID[pack_id]
    # Duplicate relation/feature IDs should never cause unstable map interaction.
    unique = list({f["id"]: f for f in features}.values())
    return {
        "type": "FeatureCollection", "features": unique[:MAX_FEATURES],
        "source": pack["source"], "source_url": pack["source_url"],
        "attribution": pack["attribution"], "updated_at": updated_at,
        "truncated": truncated or len(unique) > MAX_FEATURES, "note": note,
    }


def _osm_segments(geometry):
    """Overpass's clipped route geometry uses empty objects outside the bbox."""
    segments, current = [], []
    for raw in geometry if isinstance(geometry, list) else []:
        point = _coord([raw.get("lon"), raw.get("lat")]) if isinstance(raw, dict) else None
        if point is None:
            if len(current) >= 2:
                segments.append(current)
            current = []
        elif not current or point != current[-1]:
            current.append(point)
    if len(current) >= 2:
        segments.append(current)
    return segments


def _join_segments(segments):
    """Join split multipolygon member ways; do not invent missing closing edges."""
    pending = [list(segment) for segment in segments if len(segment) > 1]
    joined = []
    while pending:
        line = pending.pop()
        while line[0] != line[-1]:
            found = False
            for i, candidate in enumerate(pending):
                if line[-1] == candidate[0]:
                    line.extend(candidate[1:])
                elif line[-1] == candidate[-1]:
                    line.extend(candidate[-2::-1])
                elif line[0] == candidate[-1]:
                    line = candidate[:-1] + line
                elif line[0] == candidate[0]:
                    line = list(reversed(candidate[1:])) + line
                else:
                    continue
                pending.pop(i)
                found = True
                break
            if not found:
                break
        joined.append(line)
    return joined


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _orient(ring, *, outer):
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
    return ring if (area > 0) == outer else list(reversed(ring))


def _line_geometry(lines):
    if not lines:
        return None
    return {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else {"type": "MultiLineString", "coordinates": lines}


def _osm_geometry(element, pack_id):
    tags = element.get("tags") or {}
    if element.get("type") == "node":
        point = _coord([element.get("lon"), element.get("lat")])
        return {"type": "Point", "coordinates": point} if point else None
    if element.get("type") == "way":
        lines = _osm_segments(element.get("geometry"))
        if (pack_id == "military" and tags.get("area") != "no" and len(lines) == 1
                and len(lines[0]) >= 4 and lines[0][0] == lines[0][-1]):
            return {"type": "Polygon", "coordinates": [_orient(lines[0], outer=True)]}
        return _line_geometry(lines)
    if element.get("type") != "relation":
        return None
    members = element.get("members") or []
    if not isinstance(members, list):
        return None
    if tags.get("type") != "multipolygon":
        return _line_geometry([line for m in members if isinstance(m, dict) for line in _osm_segments(m.get("geometry"))])
    outer, inner = [], []
    for member in members:
        if not isinstance(member, dict):
            continue
        (inner if member.get("role") == "inner" else outer).extend(_osm_segments(member.get("geometry")))
    outer, inner = _join_segments(outer), _join_segments(inner)
    if not outer or any(len(r) < 4 or r[0] != r[-1] for r in outer + inner):
        return _line_geometry(outer + inner)
    polygons = [[_orient(ring, outer=True)] for ring in outer]
    for hole in inner:
        for polygon in polygons:
            if _point_in_ring(hole[0], polygon[0]):
                polygon.append(_orient(hole, outer=False))
                break
        else:
            # A missing parent ring must not turn an inner boundary into filled land.
            return _line_geometry(outer + inner)
    return {"type": "Polygon", "coordinates": polygons[0]} if len(polygons) == 1 else {"type": "MultiPolygon", "coordinates": polygons}


def _osm_kind(tags, pack_id):
    if pack_id == "trails":
        return ("hiking_route", "Hiking route") if tags.get("route") == "hiking" else ("trail", "Walking route" if tags.get("route") == "foot" else _text(tags.get("highway", "Path")).replace("_", " ").title())
    military = tags.get("military")
    if military or tags.get("landuse") == "military":
        labels = {
            "base": ("military_base", "Military base"), "training_area": ("training_area", "Military training ground"),
            "range": ("training_area", "Military range"), "airfield": ("military_airfield", "Military airfield"),
            "danger_area": ("military_area", "Military danger area"), "barracks": ("military_base", "Military barracks"),
        }
        return labels.get(military, ("military_area", "Military area"))
    labels = {"airstrip": ("airstrip", "Airstrip"), "runway": ("runway", "Runway"), "aerodrome": ("airfield", "Airfield")}
    return labels.get(tags.get("aeroway"), ("military_area", "Military area"))


def _load_osm(pack_id, bbox, fetch):
    w, s, e, n = bbox
    area = (e - w) * (n - s) * 111.32**2 * max(0.01, math.cos(math.radians((s + n) / 2)))
    area_limit = OSM_AREA_LIMIT_KM2[pack_id]
    span_limit = OSM_SPAN_LIMIT_DEGREES[pack_id]
    if area > area_limit or e - w > span_limit or n - s > span_limit:
        raise IntelSourceError(f"Zoom in to an area smaller than {area_limit:,} km² for OpenStreetMap intel.")
    extent = ",".join(f"{v:.6f}" for v in (s, w, n, e))
    if pack_id == "trails":
        query = f'[out:json][timeout:18][maxsize:33554432];(way["highway"~"^(path|footway|track|steps)$"]({extent});relation["route"~"^(hiking|foot)$"]({extent}););out body geom({extent}) {MAX_FEATURES + 1};'
    else:
        query = f'[out:json][timeout:18][maxsize:33554432];(nwr["landuse"="military"]({extent});nwr["military"]({extent});nwr["aeroway"~"^(airstrip|runway|aerodrome)$"]({extent}););out body geom {MAX_FEATURES + 1};'
    payload = _json(fetch(OVERPASS_URL, data=urlencode({"data": query}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise IntelSourceError("OpenStreetMap returned an unexpected response.")
    if payload.get("remark"):
        raise IntelSourceError("OpenStreetMap could not finish this area. Zoom in or try again later.")
    elements = payload["elements"]
    features = []
    for element in elements[:MAX_FEATURES]:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        geometry = _osm_geometry(element, pack_id)
        if not geometry:
            continue
        kind, label = _osm_kind(tags, pack_id)
        identifier = f"{element.get('type')}/{element.get('id')}"
        if not re.fullmatch(r"(?:node|way|relation)/\d+", identifier):
            continue
        details = [label]
        for tag, prefix in (("access", "Access"), ("foot", "Walking access"), ("sac_scale", "Difficulty"), ("surface", "Surface"), ("ref", "Reference")):
            if tags.get(tag):
                details.append(f"{prefix}: {_text(tags[tag], 100).replace('_', ' ')}")
        features.append(_feature(pack_id, identifier, geometry, tags.get("name") or tags.get("ref") or label,
                                 " · ".join(details), kind, source_url=f"https://www.openstreetmap.org/{identifier}",
                                 access=_text(tags.get("access", "unknown"), 50)))
    base = payload.get("osm3s") or {}
    return _collection(pack_id, features, truncated=len(elements) > MAX_FEATURES,
                       updated_at=_text(base.get("timestamp_osm_base"), 50),
                       note="Community mapping can be incomplete or outdated; outlines do not grant access. Route lines may extend to the edge of this view.")


def _gdacs_date(value):
    text = _text(value, 50)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return result.replace(tzinfo=result.tzinfo or timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return ""


def _gdacs_ids(props):
    """Aggregate area features omit eventid; validate their report link as data.

    No provider-supplied link is ever requested. All network URLs are rebuilt
    from these numeric IDs and the closed disaster-type catalogue.
    """
    code = _text(props.get("eventtype"), 10)
    event_id = _text(props.get("eventid"), 30)
    episode = _text(props.get("episodeid"), 30)
    link = props.get("link")
    if isinstance(link, str):
        try:
            parsed = urlsplit(link)
        except ValueError:
            return None
        if (parsed.scheme in ("http", "https") and parsed.netloc == "www.gdacs.org"
                and parsed.path == "/report.aspx" and not parsed.fragment):
            query = parse_qs(parsed.query)
            linked_code, linked_id = query.get("eventtype", [""]), query.get("eventid", [""])
            if len(linked_code) == len(linked_id) == 1 and linked_code[0] == code:
                if event_id and event_id != linked_id[0]:
                    return None
                event_id = linked_id[0]
                linked_episode = query.get("episodeid", [""])
                if len(linked_episode) == 1:
                    episode = episode or linked_episode[0]
    if code not in GDACS_TYPES or not re.fullmatch(r"[1-9][0-9]{0,9}", event_id):
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,9}", episode):
        episode = ""
    if not episode and isinstance(link, list):
        for item in link[:10]:
            if not isinstance(item, dict) or item.get("Key") != "details" or not isinstance(item.get("Value"), str):
                continue
            try:
                parsed = urlsplit(item["Value"])
            except ValueError:
                continue
            match = re.fullmatch(rf"/(?:datareport|contentdata)/resources/{code}/{event_id}/geojson_{event_id}_([1-9][0-9]{{0,9}})\.geojson", parsed.path)
            if (parsed.scheme in ("http", "https") and parsed.netloc == "www.gdacs.org"
                    and not parsed.query and not parsed.fragment and match):
                episode = match[1]
                break
    return code, event_id, episode


def _gdacs_geometry(raw):
    """Strict, bounded GeoJSON validation. Preserve holes and never close a ring."""
    if not isinstance(raw, dict):
        return None
    kind, coordinates = raw.get("type"), raw.get("coordinates")
    count, bounds = 0, [180.0, 90.0, -180.0, -90.0]

    def point(value):
        nonlocal count
        if not isinstance(value, list) or len(value) not in (2, 3):
            raise ValueError
        result = _coord(value)
        if result is None:
            raise ValueError
        count += 1
        if count > GDACS_MAX_FEATURE_COORDINATES:
            raise ValueError
        bounds[0], bounds[1] = min(bounds[0], result[0]), min(bounds[1], result[1])
        bounds[2], bounds[3] = max(bounds[2], result[0]), max(bounds[3], result[1])
        return result

    def line(value, ring=False):
        if not isinstance(value, list) or len(value) < (4 if ring else 2):
            raise ValueError
        result = [point(coordinate) for coordinate in value]
        if ring and (result[0] != result[-1] or len({tuple(p) for p in result}) < 3):
            raise ValueError
        return result

    def polygon(value):
        if not isinstance(value, list) or not value:
            raise ValueError
        rings = []
        for raw_ring in value:
            cleaned = line(raw_ring)
            # Published gdacsWF/FL/DR aggregate files sometimes concatenate all
            # rings into a single array. Recover only explicitly closed loops;
            # only loops inside the first exterior may be treated as holes.
            # Never add a closing edge or discard an open fragment.
            pending = []
            for coordinate in cleaned:
                pending.append(coordinate)
                if len(pending) > 1 and coordinate == pending[0]:
                    if len(pending) < 4 or len({tuple(p) for p in pending}) < 3:
                        raise ValueError
                    if rings and not _point_in_ring(pending[0], rings[0]):
                        raise ValueError
                    rings.append(_orient(pending, outer=not rings))
                    pending = []
            if pending:
                raise ValueError
        return rings

    try:
        if kind == "Point":
            if isinstance(coordinates, list) and len(coordinates) == 1:
                coordinates = coordinates[0]  # Published app feed uses [[lon,lat]].
            cleaned = point(coordinates)
        elif kind == "LineString":
            cleaned = line(coordinates)
        elif kind == "Polygon":
            cleaned = polygon(coordinates)
        elif kind in ("MultiLineString", "MultiPolygon"):
            if not isinstance(coordinates, list) or not coordinates:
                raise ValueError
            cleaned = [(polygon if kind == "MultiPolygon" else line)(item) for item in coordinates]
        else:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return {"type": kind, "coordinates": cleaned}, bounds, count


def _gdacs_document(url, fetch, deadline, *, cache_enabled):
    now = time.monotonic()
    entry = None
    if cache_enabled:
        with _GDACS_CACHE_LOCK:
            entry = _GDACS_CACHE.get(url)
            if entry and (entry["expires"] > now or entry["retry_after"] > now):
                if entry["payload"] is None:
                    raise IntelSourceError("GDACS geometry is temporarily unavailable.")
                return _json(entry["payload"]), entry["expires"] <= now
    remaining = deadline - now
    if remaining <= 0:
        raise IntelSourceError("GDACS geometry time limit reached.")
    try:
        payload = fetch(url, timeout=min(6, remaining), max_bytes=GDACS_DOCUMENT_BYTES)
        if len(payload) > GDACS_DOCUMENT_BYTES:
            raise IntelSourceError("GDACS geometry response is too large.")
        parsed = _json(payload)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("features"), list):
            raise IntelSourceError("GDACS returned an unexpected response.")
    except (IntelSourceError, OSError, TimeoutError) as exc:
        if cache_enabled:
            with _GDACS_CACHE_LOCK:
                _GDACS_CACHE[url] = {"payload": entry["payload"] if entry else None,
                                     "expires": entry["expires"] if entry else 0,
                                     "retry_after": now + 60}
                while len(_GDACS_CACHE) > 24:
                    _GDACS_CACHE.pop(next(iter(_GDACS_CACHE)))
        if entry and entry["payload"] is not None:
            return _json(entry["payload"]), True
        if isinstance(exc, IntelSourceError):
            raise
        raise IntelSourceError("GDACS geometry is temporarily unavailable.") from None
    if cache_enabled:
        with _GDACS_CACHE_LOCK:
            _GDACS_CACHE.pop(url, None)
            _GDACS_CACHE[url] = {"payload": payload, "expires": now + 900, "retry_after": 0}
            while len(_GDACS_CACHE) > 24 or sum(len(e["payload"] or b"") for e in _GDACS_CACHE.values()) > 32_000_000:
                _GDACS_CACHE.pop(next(iter(_GDACS_CACHE)))
    return parsed, False


def _gdacs_shape_label(props, kind, code):
    if kind == "Point":
        return "point", "", "reported"
    label = _text(props.get("polygonlabel"), 100)
    class_name = _text(props.get("Class") or props.get("polygontype"), 100)
    combined = f"{label} {class_name}".lower()
    role = "track" if "LineString" in kind else "area"
    if "circle" in combined:
        return role, f"GDACS {label or 'distance'} reference area (not an impact boundary)", "reference"
    if "fcst" in combined or "forecast" in combined:
        return role, f"GDACS forecast: {label or class_name}", "forecast"
    if code == "TC":
        prefix = "Cyclone track" if role == "track" else "Cyclone model area"
    elif code == "WF":
        prefix = "Published wildfire area"
    elif code == "FL":
        prefix = "Published flood area"
    elif code == "DR":
        prefix = "Published drought area"
    elif code == "VO" and "obs" in combined:
        prefix = "Observed volcanic ash area"
    elif code == "EQ":
        prefix = "Earthquake shaking model"
    else:
        prefix = "Published event geometry"
    return role, f"{prefix}: {label}" if label else prefix, "modeled" if code in ("TC", "EQ") else "reported"


def _load_gdacs(bbox, fetch, selected, *, cache_enabled=False):
    if not selected:
        return _collection("gdacs", [], note="No disaster types selected.")
    deadline = time.monotonic() + GDACS_DEADLINE_SECONDS
    points, vectors, candidates, area_events, notes = {}, [], {}, set(), []
    coordinates_used, dropped, succeeded, failed = 0, False, 0, 0
    first_error = None
    seen_vectors = set()

    def consume(payload, expected=None):
        nonlocal coordinates_used, dropped
        for raw in payload["features"][:MAX_FEATURES * 2]:
            if not isinstance(raw, dict) or not isinstance(raw.get("properties"), dict):
                continue
            props = raw["properties"]
            ids = _gdacs_ids(props)
            if not ids or ids[0] not in selected or (expected and ids[0] != expected):
                continue
            code, event_id, episode = ids
            validated = _gdacs_geometry(raw.get("geometry"))
            if not validated:
                dropped = True
                continue
            geometry, bounds, count = validated
            if bounds[2] < bbox[0] or bounds[0] > bbox[2] or bounds[3] < bbox[1] or bounds[1] > bbox[3]:
                continue
            event_key = f"{code}/{event_id}"
            role, label, basis = _gdacs_shape_label(props, geometry["type"], code)
            if role != "point":
                if coordinates_used + count > GDACS_MAX_COORDINATES or len(vectors) + len(points) >= MAX_FEATURES:
                    dropped = True
                    continue
                digest = hashlib.sha256(json.dumps([event_key, episode, label, geometry], separators=(",", ":")).encode()).hexdigest()[:20]
                if digest in seen_vectors:
                    continue
                seen_vectors.add(digest)
                identifier = f"{event_key}/{digest}"
                coordinates_used += count
                area_events.add(event_key)
            else:
                identifier = event_key
            observed = _gdacs_date(props.get("polygondate") or props.get("datemodified") or props.get("todate") or props.get("fromdate"))
            level = _text(props.get("alertlevel"), 20).lower()
            detail = _text(props.get("htmldescription") or props.get("description") or GDACS_NAMES[code], 800)
            feature = _feature("gdacs", identifier, geometry, props.get("title") or props.get("name") or GDACS_NAMES[code],
                               f"{detail} · {label}" if label else detail, "disaster", observed,
                               source_url=f"https://www.gdacs.org/report.aspx?eventtype={code}&eventid={event_id}",
                               severity=level if level in ("green", "orange", "red") else "unknown",
                               disaster_type=GDACS_NAMES[code], disaster_code=code, event_type=code,
                               geometry_role=role, area_label=label, geometry_basis=basis,
                               event_key=event_key, episode_id=episode)
            if role == "point":
                # The app alert is the canonical current point; aggregate feeds
                # may have a different reporting window and overall alert level.
                points.setdefault(event_key, feature)
                if episode:
                    candidates[event_key] = (feature, episode)
            else:
                vectors.append(feature)
        if len(payload["features"]) > MAX_FEATURES * 2:
            dropped = True

    def document(url, expected=None):
        nonlocal succeeded, failed, first_error
        try:
            payload, stale = _gdacs_document(url, fetch, deadline, cache_enabled=cache_enabled)
            consume(payload, expected)
            succeeded += 1
            if stale:
                notes.append("Some outlines are from an older cached GDACS response.")
        except IntelSourceError as exc:
            failed += 1
            first_error = first_error or exc

    document(GDACS_URL)
    # Prioritize lighter, time-sensitive geometry before the much larger flood
    # and drought boundaries consume the shared mobile rendering budget.
    for code in ("WF", "TC", "EQ", "VO", "FL", "DR"):
        if code in selected and code in GDACS_FEEDS:
            document(GDACS_FEEDS[code], code)
    missing = [(key, feature, episode) for key, (feature, episode) in candidates.items() if key not in area_events]
    local_view = bbox[2] - bbox[0] <= GDACS_DETAIL_MAX_SPAN and bbox[3] - bbox[1] <= GDACS_DETAIL_MAX_SPAN
    if local_view:
        center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
        def priority(item):
            feature = item[1]
            props = feature["properties"]
            point = feature["geometry"]["coordinates"]
            return (props["geometry_role"] != "point", {"red": 0, "orange": 1}.get(props["severity"], 2),
                    props["disaster_code"] not in ("WF", "FL", "TC"),
                    (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2)
        missing.sort(key=priority)
        for key, feature, episode in missing[:GDACS_DETAIL_LIMIT]:
            code, event_id = key.split("/")
            # GDACS publishes the same per-event geometry as a static resource.
            # The app's legacy /datareport link can return 403; /contentdata is
            # the public HTTPS repository. Rebuild, never follow upstream URLs.
            document(f"{GDACS_DETAIL_ROOT}{code}/{event_id}/geojson_{event_id}_{episode}.geojson", code)
        if len(missing) > GDACS_DETAIL_LIMIT:
            dropped = True
            notes.append("Additional event details are limited in this view; zoom closer for nearby outlines.")
    elif missing:
        notes.append("Zoom in for additional individual-event outlines where available.")
    if not succeeded:
        raise first_error or IntelSourceError("GDACS could not be reached or returned unreadable data. Try again later.")
    if failed:
        notes.append("Some GDACS areas could not be loaded; available alert points remain visible.")
    if dropped:
        notes.append("Some geometry was omitted because it was invalid or exceeded the view limits.")
    notes.insert(0, "Published GDACS areas and tracks where available; missing outlines do not mean an area is safe. Model/forecast and reference areas are labeled separately.")
    result = _collection("gdacs", [*points.values(), *vectors], truncated=dropped or failed > 0,
                         note=" ".join(dict.fromkeys(notes)))
    result["geometry_status"] = {"area_features": len(vectors), "point_events": len(points),
                                  "partial": dropped or failed > 0, "failed_sources": failed}
    return result


def _load_firms(bbox, credentials, fetch):
    key = str(credentials.get("firms_key") or "").strip()
    if not key:
        raise IntelSourceError("Add your free NASA FIRMS MAP_KEY in Intel pack settings.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", key):
        raise IntelSourceError("The NASA FIRMS MAP_KEY format is invalid.")
    extent = ",".join(f"{v:.6f}" for v in bbox)
    raw = fetch(f"{FIRMS_ROOT}{key}/VIIRS_NOAA20_NRT/{extent}/3")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if not reader.fieldnames or not {"latitude", "longitude", "acq_date", "acq_time"}.issubset(reader.fieldnames):
            raise IntelSourceError("NASA FIRMS did not return detection data. Check your MAP_KEY and quota.")
        features, truncated = [], False
        for row in reader:
            point = _coord([row.get("longitude"), row.get("latitude")])
            if not point or not _inside(point, bbox):
                continue
            day, minute = row.get("acq_date", ""), row.get("acq_time", "").zfill(4)
            try:
                observed = datetime.strptime(day + minute, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                continue
            if len(features) >= MAX_FEATURES:
                truncated = True
                break
            confidence = {"l": "low", "n": "nominal", "h": "high"}.get(row.get("confidence", "").lower(), "unknown")
            identifier = hashlib.sha256(f"{observed}|{point}|{row.get('satellite', '')}".encode()).hexdigest()[:24]
            detail = f"VIIRS NOAA-20 thermal anomaly · Nominal resolution: 375 m · Confidence: {confidence}. Satellite heat detection, not a confirmed fire or attack."
            features.append(_feature("firms", identifier, {"type": "Point", "coordinates": point}, "Satellite heat detection", detail, "thermal_anomaly", observed, confidence=confidence))
    except (UnicodeDecodeError, csv.Error):
        raise IntelSourceError("NASA FIRMS returned unreadable detection data.") from None
    return _collection("firms", features, truncated=truncated, note="Last 3 days · NOAA-20 NRT only. Overpass timing, clouds and sensor resolution limit coverage.")


def _load_acled(bbox, credentials, fetch):
    token = str(credentials.get("acled_token") or "").strip()
    if not token:
        raise IntelSourceError("Add an ACLED access token in Intel pack settings. An ACLED account is required.")
    if len(token) > 8192 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
        raise IntelSourceError("The ACLED access token format is invalid.")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=29)
    query = {
        "_format": "json", "limit": MAX_FEATURES + 1,
        "latitude": f"{bbox[1]}|{bbox[3]}", "latitude_where": "BETWEEN",
        "longitude": f"{bbox[0]}|{bbox[2]}", "longitude_where": "BETWEEN",
        "event_date": f"{start}|{end}", "event_date_where": "BETWEEN",
        "fields": "event_id_cnty|event_date|event_type|sub_event_type|country|location|latitude|longitude|geo_precision|notes|timestamp",
    }
    payload = _json(fetch(f"{ACLED_URL}?{urlencode(query)}", headers={"Authorization": f"Bearer {token}"}))
    if not isinstance(payload, dict) or str(payload.get("status", 200)) != "200" or not isinstance(payload.get("data"), list):
        raise IntelSourceError("ACLED did not return event data. Check your access token, account access and quota.")
    rows = payload["data"]
    features = []
    for row in rows[:MAX_FEATURES]:
        if not isinstance(row, dict):
            continue
        point = _coord([row.get("longitude"), row.get("latitude")])
        identifier = _text(row.get("event_id_cnty"), 80)
        if not point or not _inside(point, bbox) or not identifier:
            continue
        precision = _text(row.get("geo_precision"), 1)
        precision_text = {"1": "Reported location", "2": "Approximate nearby location", "3": "Approximate administrative area"}.get(precision, "Location precision unknown")
        subtype = _text(row.get("sub_event_type") or row.get("event_type"), 100)
        location = _text(row.get("location"), 100)
        detail = f"{precision_text}. {_text(row.get('notes'), 700)}"
        features.append(_feature("acled", identifier, {"type": "Point", "coordinates": point},
                                 f"{subtype} · {location}" if location else subtype, detail, "acled_event", row.get("event_date"),
                                 precision=int(precision) if precision in ("1", "2", "3") else 0))
    return _collection("acled", features, truncated=len(rows) > MAX_FEATURES,
                       note="Last 30 days · Published reporting, not live tracking. Locations can be approximate; account access may limit coverage. ACLED terms apply.")


def load_pack(pack_id: str, bbox, credentials: dict | None = None, *, fetch: Callable | None = None,
              gdacs_types=None) -> dict:
    """Load a bounded GeoJSON view. ``fetch`` is injectable for offline tests."""
    if pack_id not in _PACK_BY_ID:
        raise IntelSourceError("Unknown Intel pack.")
    bbox = validate_bbox(bbox)
    transport = fetch or fetch_bytes
    source_bytes = 0
    byte_limit = GDACS_MAX_BYTES if pack_id == "gdacs" else MAX_BYTES

    def fetch(url, **kwargs):
        nonlocal source_bytes
        if source_bytes >= byte_limit:
            raise IntelSourceError("The provider response is too large. Zoom in and retry.")
        if pack_id == "gdacs":
            kwargs["max_bytes"] = min(kwargs.get("max_bytes", GDACS_DOCUMENT_BYTES), byte_limit - source_bytes)
        payload = transport(url, **kwargs)
        if not isinstance(payload, bytes):
            raise IntelSourceError("The provider returned an unexpected data format.")
        source_bytes += len(payload)
        if source_bytes > byte_limit:
            raise IntelSourceError("The provider response is too large. Zoom in and retry.")
        return payload

    credentials = credentials or {}
    try:
        if pack_id in ("trails", "military"):
            result = _load_osm(pack_id, bbox, fetch)
        elif pack_id == "gdacs":
            if gdacs_types is None:
                selected = GDACS_TYPES
            elif not isinstance(gdacs_types, (list, tuple)) or any(code not in GDACS_TYPES for code in gdacs_types):
                raise IntelSourceError("Choose valid GDACS disaster types.")
            else:
                selected = tuple(dict.fromkeys(gdacs_types))
            result = _load_gdacs(bbox, fetch, selected, cache_enabled=transport is fetch_bytes)
        elif pack_id == "firms":
            result = _load_firms(bbox, credentials, fetch)
        else:
            result = _load_acled(bbox, credentials, fetch)
        result["source_bytes"] = source_bytes
        return result
    except IntelSourceError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        # Providers can change schema or return arbitrary error bodies. Neither
        # those bodies nor URLs containing credential material reach the UI/logs.
        raise IntelSourceError("The provider returned an unexpected data format. Try again later.") from None
