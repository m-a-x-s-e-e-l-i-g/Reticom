"""Device-local public map layers; deliberately outside the signed team feed.

Provider secrets and caches never enter mission packs or Reticulum replication.
All network work is opt-in, bounded and performed off the ASGI event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .intel_sources import PACKS, ProviderError, MAX_FEATURES, NOTAM_CATEGORIES, OSM_AREA_LIMIT_KM2, OSM_SPAN_LIMIT_DEGREES, GDACS_DETAIL_MAX_SPAN, load_pack
from .notam_activity import NOTAM_LOOKAHEAD
from .osm_cache import buffered_bounds, intersects, merge_entries
from .lidar_tiles import MALTA_CONTOUR_MAX_ZOOM, malta_terrain_tile, malta_tile, overzoom_terrain
from .elevation import ElevationStore, ELEVATION_MAX_ZOOM
from .traffic import PERSONAL_FLIGHT_FALLBACK, TRAFFIC_PACKS, TRAFFIC_IDS, LiveTraffic, fetch_vessel_photo, validate_traffic_filters, vessel_photo_info
from .traffic_history import valid_identity
from .road_disruptions import ROAD_PACK, RoadDisruptions, validate_road_filters
from .transit import PublicTransit, TRANSIT_PACK


ELEVATION_PACK = {
    "id": "elevation", "title": "Contour lines", "description": "Height lines labelled in metres. Spacing adjusts with zoom; thicker lines show major elevations. Estimated terrain, not survey accuracy.",
    "source": "AWS / Mapzen terrain", "source_url": "https://registry.opendata.aws/terrain-tiles/",
    "attribution": "Terrain: Mapzen / data providers", "attribution_url": "https://github.com/tilezen/joerd/blob/master/docs/attribution.md", "min_zoom": 9,
    "ttl_seconds": 2592000, "credential_fields": [],
}
AHN_PACK = {
    "id": "ahn", "title": "National LiDAR elevation", "description": "High-resolution airborne-LiDAR raster selected by map area. DTM and DSM are available where the national provider supports them.",
    "source": "National LiDAR providers", "source_url": "https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn",
    "attribution": "National LiDAR providers", "attribution_url": "https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn", "min_zoom": 10,
    "ttl_seconds": 2592000, "credential_fields": [], "client_raster": True,
}
BATHYMETRY_PACK = {
    "id": "bathymetry", "title": "Global bathymetry", "description": "Global GEBCO seafloor and terrain relief. Broad context only; depths are modelled and may be interpolated between measurements.",
    "source": "GEBCO 2026", "source_url": "https://www.gebco.net/data-products/gebco-web-services/web-map-service",
    "attribution": "GEBCO", "attribution_url": "https://www.gebco.net/", "min_zoom": 1,
    "ttl_seconds": 2592000, "credential_fields": [], "client_raster": True,
}
NIGHTLIGHTS_PACK = {
    "id": "nightlights", "title": "Night lights", "description": "NASA VIIRS Black Marble annual night-light composite. Historical human activity and illuminated infrastructure context, not a live activity feed.",
    "source": "NASA Black Marble / VIIRS", "source_url": "https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/science-domain/nighttime-lights",
    "attribution": "NASA Black Marble / VIIRS", "attribution_url": "https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/science-domain/nighttime-lights", "min_zoom": 1,
    "ttl_seconds": 2592000, "credential_fields": [], "client_raster": True,
}
CATALOGUE = {p["id"]: p for p in [*PACKS, ROAD_PACK, TRANSIT_PACK, ELEVATION_PACK, AHN_PACK, BATHYMETRY_PACK, NIGHTLIGHTS_PACK, *TRAFFIC_PACKS]}
GDACS_TYPES = tuple(item["id"] for item in CATALOGUE["gdacs"]["disaster_types"])
DEFAULT_GDACS_TYPES = tuple(code for code in GDACS_TYPES if code != "DR")
DEFAULT_ENABLED = ["gdacs", "military"]  # Trails now come from always-on basemap tiles.
AHN_MODES = ("dtm", "dsm")
GDACS_CACHE_VERSION = 2  # Earlier caches contain point locations, not event outlines.
SECRET_FIELDS = {"firms_key", "aisstream_key", "aishub_key", "bravoecho_key"}
OSM_PACKS = {"trails", "military"}
OSM_COOLDOWN = "Public OSM service cooldown. Retry after 30 seconds."
OSM_DAILY_LIMITS = {"trails": {"requests": 500, "bytes": 200_000_000}, "military": {"requests": 500, "bytes": 100_000_000}}
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_FILES = 128
STALE_LIMIT = {"trails": 30 * 86400, "military": 30 * 86400, "gdacs": 86400, "firms": 21600, "notams": 1800}
SHARED_INTEL_KEYS = {"notams": "bravoecho_key", "firms": "firms_key"}
HOST_CREDENTIALS = {f"host_{field}": pack_id for pack_id, field in SHARED_INTEL_KEYS.items()}
SHARED_INTEL_BYTES = 64_000


def validate_bounds(bbox):
    try:
        west, south, east, north = (float(v) for v in bbox)
    except (ValueError, TypeError) as exc:
        raise ValueError("Enter a valid map area") from exc
    if not all(math.isfinite(v) for v in (west, south, east, north)) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Enter a valid map area; split areas crossing the date line")
    return west, south, east, north


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def validate_gdacs_types(value):
    if not isinstance(value, list) or any(not isinstance(code, str) or code not in GDACS_TYPES for code in value):
        raise ValueError("Choose available GDACS disaster types")
    # A stable catalogue order makes equivalent selections share the same cache.
    return tuple(code for code in GDACS_TYPES if code in value)


def validate_notam_categories(value):
    if not isinstance(value, list) or any(not isinstance(code, str) or code not in NOTAM_CATEGORIES for code in value):
        raise ValueError("Choose available NOTAM categories")
    return [code for code in NOTAM_CATEGORIES if code in value]


def feature_intersects(feature, bbox):
    """Conservative bounds intersection; retain lines crossing the viewport."""
    positions = []

    def walk(value):
        if not isinstance(value, list):
            return
        if len(value) >= 2 and all(isinstance(n, (int, float)) and math.isfinite(n) for n in value[:2]):
            positions.append(value[:2])
        else:
            for item in value:
                walk(item)

    walk(feature.get("geometry", {}).get("coordinates", []))
    if not positions:
        return False
    xs, ys = zip(*positions)
    return max(xs) >= bbox[0] and min(xs) <= bbox[2] and max(ys) >= bbox[1] and min(ys) <= bbox[3]


class IntelPackStore:
    def __init__(self, data_dir: Path, *, loader: Callable = load_pack, clock: Callable = time.time):
        self.directory = Path(data_dir) / "intel-packs"
        self.cache_dir = self.directory / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.directory / "settings.json"
        self.military_snapshot = self.directory / "military-world.sqlite3"
        self.usage_path = self.directory / "osm-usage.json"
        self.loader, self.clock = loader, clock
        self.lock = threading.RLock()
        self.fetch_locks = {key: threading.Lock() for key in CATALOGUE}
        self.osm_lock = threading.Lock()
        self.next_fetch = {}
        self.shared_pending = set()
        self.shared_next_fetch = {}
        stored = self._read(self.settings_path) or {}
        fallback = stored.get("personal_flight_fallback")
        self._personal_fallback_saved = isinstance(fallback, bool)
        self.personal_flight_fallback = fallback if self._personal_fallback_saved else PERSONAL_FLIGHT_FALLBACK
        enabled = stored.get("enabled", DEFAULT_ENABLED)
        credentials = stored.get("credentials", {})
        self.enabled = {key for key in enabled if isinstance(key, str) and key in CATALOGUE} if isinstance(enabled, list) else set()
        self.credentials = {key: value for key, value in credentials.items() if key in SECRET_FIELDS and isinstance(value, str)} if isinstance(credentials, dict) else {}
        self.firms_days = stored.get("firms_days", 3)
        self.ahn_mode = stored.get("ahn_mode", "dtm") if stored.get("ahn_mode", "dtm") in AHN_MODES else "dtm"
        try:
            self.road_filters = validate_road_filters(stored.get("road_filters", {}))
        except ValueError:
            self.road_filters = validate_road_filters({})
        if type(self.firms_days) is not int or self.firms_days not in (1, 3, 7):
            self.firms_days = 3
        try:
            self.gdacs_types = validate_gdacs_types(stored.get("gdacs_types", list(DEFAULT_GDACS_TYPES)))
        except ValueError:
            self.gdacs_types = DEFAULT_GDACS_TYPES
        try:
            self.notam_categories = validate_notam_categories(stored.get("notam_categories", list(NOTAM_CATEGORIES)))
        except ValueError:
            self.notam_categories = list(NOTAM_CATEGORIES)
        self.notam_view = "all" if stored.get("notam_view") == "all" else "local"
        try:
            self.traffic_filters = validate_traffic_filters(stored.get("traffic_filters", {}))
        except (ValueError, TypeError):
            self.traffic_filters = validate_traffic_filters({})

    @staticmethod
    def _read(path):
        try:
            if path.stat().st_size > MAX_CACHE_BYTES:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _write(path, value):
        # Only filenames owned by this store; replace atomically after a full write.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)

    def _configured(self, pack):
        return all(self.credentials.get(key) for key in pack.get("credential_fields", []))

    def catalogue(self):
        with self.lock:
            return {
                "packs": [{**pack, "enabled": key in self.enabled, "configured": self._configured(pack)} for key, pack in CATALOGUE.items()],
                "settings": {"enabled": sorted(self.enabled), "gdacs_types": list(self.gdacs_types), "notam_categories": list(self.notam_categories), "notam_view": self.notam_view, "traffic_filters": self.traffic_filters, "firms_days": self.firms_days, "road_filters": self.road_filters, "ahn_mode": self.ahn_mode, "personal_flight_fallback": self.personal_flight_fallback},
                "credentials": {key: bool(self.credentials.get(key)) for key in sorted(SECRET_FIELDS)},
                "credential_previews": {key: ("••••••••" + value[-4:] if len(value) > 8 else "••••••••")
                                        for key in sorted(SECRET_FIELDS) if (value := self.credentials.get(key))},
            }

    def update(self, body):
        if not isinstance(body, dict) or set(body) - {"enabled", "credentials", "gdacs_types", "notam_categories", "notam_view", "traffic_filters", "firms_days", "road_filters", "ahn_mode", "personal_flight_fallback"}:
            raise ValueError("Use available map layer settings and provider credentials")
        with self.lock:
            enabled = body.get("enabled", sorted(self.enabled))
            if not isinstance(enabled, list) or any(not isinstance(key, str) or key not in CATALOGUE for key in enabled):
                raise ValueError("Choose an available intel pack")
            supplied = body.get("credentials", {})
            if not isinstance(supplied, dict) or set(supplied) - SECRET_FIELDS:
                raise ValueError("Unknown provider credential")
            if any(not isinstance(value, str) or len(value) > 8192 or any(ord(char) < 32 for char in value) for value in supplied.values()):
                raise ValueError("Enter a valid provider key or token")
            firms_days = body.get("firms_days", self.firms_days)
            if type(firms_days) is not int or firms_days not in (1, 3, 7):
                raise ValueError("Choose 24 hours, 3 days or 7 days for NASA FIRMS")
            gdacs_types = validate_gdacs_types(body.get("gdacs_types", list(self.gdacs_types)))
            notam_categories = validate_notam_categories(body.get("notam_categories", self.notam_categories))
            notam_view = body.get("notam_view", self.notam_view)
            if notam_view not in ("local", "all"):
                raise ValueError("Choose Local activity or All notices")
            road_filters = validate_road_filters(body.get("road_filters", self.road_filters))
            traffic_filters = validate_traffic_filters(body.get("traffic_filters", self.traffic_filters))
            ahn_mode = body.get("ahn_mode", self.ahn_mode)
            if ahn_mode not in AHN_MODES:
                raise ValueError("Choose an AHN elevation mode")
            credentials = {**self.credentials, **{key: value.strip() for key, value in supplied.items()}}
            fallback = body.get("personal_flight_fallback", self.personal_flight_fallback)
            if not isinstance(fallback, bool):
                raise ValueError("Choose whether to use the personal aircraft fallback")
            saved_fallback = {"personal_flight_fallback": fallback} if "personal_flight_fallback" in body or self._personal_fallback_saved else {}
            self._write(self.settings_path, {"enabled": sorted(set(enabled)), "credentials": credentials, "gdacs_types": list(gdacs_types), "notam_categories": notam_categories, "notam_view": notam_view, "traffic_filters": traffic_filters, "firms_days": firms_days, "road_filters": road_filters, "ahn_mode": ahn_mode, **saved_fallback})
            self._personal_fallback_saved = bool(saved_fallback)
            self.personal_flight_fallback = fallback
            self.road_filters = road_filters
            if gdacs_types != self.gdacs_types:
                self.next_fetch = {key: until for key, until in self.next_fetch.items()
                                   if not (key == "gdacs" or isinstance(key, tuple) and key[0] == "gdacs")}
            if firms_days != self.firms_days:
                self.next_fetch.pop("firms", None)
            self.firms_days = firms_days
            self.enabled, self.credentials = set(enabled), credentials
            self.gdacs_types = gdacs_types
            self.notam_categories = notam_categories
            self.notam_view = notam_view
            self.traffic_filters = traffic_filters
            self.ahn_mode = ahn_mode
            if supplied:
                self.next_fetch.clear()
            return self.catalogue()

    def is_enabled(self, pack_id):
        with self.lock:
            return pack_id in self.enabled

    def _credential_hash(self, pack_id, firms_days=None):
        selected = {key: self.credentials.get(key, "") for key in CATALOGUE[pack_id].get("credential_fields", [])}
        if pack_id == "firms":
            selected.update(firms_days=self.firms_days if firms_days is None else firms_days, cache_version=2)
        if pack_id == "gdacs":
            selected.update(gdacs_types=self.gdacs_types, cache_version=GDACS_CACHE_VERSION)
        if pack_id == "notams":
            selected["cache_version"] = 2
        return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()

    def _cache_hash(self, pack_id, bbox, firms_days=None):
        credential_hash = self._credential_hash(pack_id, firms_days)
        if pack_id != "gdacs":
            return credential_hash
        detail = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) <= GDACS_DETAIL_MAX_SPAN
        # A global overview has no per-event enrichment. Nor can a containing
        # detail view promise coverage of another view when enrichment is capped.
        scope = list(bbox) if detail else "overview"
        return hashlib.sha256(json.dumps([credential_hash, scope]).encode()).hexdigest()

    def _cached(self, pack_id, bbox, credential_hash, *, partial=False):
        candidates = []
        for path in self.cache_dir.glob(f"{pack_id}-*.json"):
            entry = self._read(path)
            if not entry or entry.get("credential_hash") != credential_hash:
                continue
            if not isinstance(entry.get("bounds"), list) or len(entry["bounds"]) != 4 or not isinstance(entry.get("data"), dict):
                continue
            try:
                age = self.clock() - float(entry["fetched_at"])
                if (entry.get("pinned") or 0 <= age <= STALE_LIMIT.get(pack_id, 86400)) and (intersects(entry["bounds"], bbox) if partial or pack_id in OSM_PACKS else contains(entry["bounds"], bbox)):
                    candidates.append(entry)
            except (TypeError, ValueError, KeyError):
                continue
        if (pack_id not in OSM_PACKS and not partial) or not candidates:
            return max(candidates, key=lambda entry: entry["fetched_at"], default=None)
        candidates.sort(key=lambda entry: entry["fetched_at"], reverse=True)
        def current(entry):
            return pack_id != "military" or entry["data"].get("police_coverage")

        fresh = [entry for entry in candidates if current(entry) and (entry.get("pinned") or self.clock() < entry["fetched_at"] + CATALOGUE[pack_id]["ttl_seconds"])]
        # A limited result may be reused for the exact original request, with
        # its warning intact. It cannot claim completeness for a different view.
        exact = next((entry for entry in fresh if tuple(entry.get("request_bounds", entry["bounds"])) == tuple(bbox)), None)
        merged = merge_entries(fresh or candidates, bbox, credential_hash, feature_intersects, MAX_FEATURES)
        if merged["data"]["coverage_complete"]:
            if not fresh and pack_id == "military":
                merged["data"]["police_coverage"] = all(current(entry) for entry in candidates)
            return merged
        if exact and exact["data"].get("truncated") and tuple(exact["bounds"]) == tuple(bbox):
            return {**exact, "data": {**exact["data"], "coverage_complete": False}, "reuse_limited": True}
        # On provider failure include older overlapping areas, explicitly stale.
        if len(fresh) != len(candidates):
            merged = merge_entries(candidates, bbox, credential_hash, feature_intersects, MAX_FEATURES)
            if pack_id == "military":
                merged["data"]["police_coverage"] = all(current(entry) for entry in candidates)
        return merged

    def _prune(self):
        files = sorted(self.cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        total = 0
        removable = 0
        for path in files:
            entry = self._read(path)
            if entry and entry.get("pinned"):
                continue
            removable += 1
            total += path.stat().st_size
            if removable > MAX_CACHE_FILES or total > MAX_CACHE_BYTES:
                path.unlink(missing_ok=True)

    def download(self, pack_id, bbox, zoom):
        """Save one complete public layer view permanently on this device."""
        if pack_id != "military":
            raise ValueError("Only Military areas, airstrips & police can be saved for offline use")
        result = self.load(pack_id, bbox, zoom)
        if result["status"] == "deferred" and not result.get("worldwide_snapshot"):
            raise ValueError("This view is still waiting to download. Save it when coverage is complete.")
        if result.get("status") not in {"fresh", "cached", "stale", "deferred"}:
            raise ValueError(result.get("error") or "This layer is not available for the current view")
        if result.get("worldwide_snapshot") and (result.get("police_coverage") or not result.get("coverage_complete")):
            return {**result, "saved_offline": True}
        if not result.get("coverage_complete", True) or result.get("truncated"):
            raise ValueError("This view is incomplete. Zoom in slightly, then save it again.")
        bbox = validate_bounds(bbox)
        with self.lock:
            if pack_id not in self.enabled:
                raise ValueError("Enable Military areas, airstrips & police before saving a view")
            credential_hash = self._cache_hash(pack_id, bbox)
            data = {key: value for key, value in result.items() if key not in {"status", "fetched_at", "expires_at", "error", "worldwide_snapshot"}}
            data["police_coverage"] = True
            entry = {"bounds": list(bbox), "request_bounds": list(bbox), "credential_hash": credential_hash,
                     "fetched_at": self.clock(), "pinned": True, "data": data}
            identity = hashlib.sha256(json.dumps(["pinned", bbox, credential_hash]).encode()).hexdigest()[:24]
            self._write(self.cache_dir / f"{pack_id}-pinned-{identity}.json", entry)
        return {**self._response(pack_id, entry, bbox, "cached"), "saved_offline": True}

    def _empty(self, pack_id, status, error=None):
        pack = CATALOGUE[pack_id]
        return {"type": "FeatureCollection", "features": [], "status": status,
                "source": pack["source"], "source_url": pack["source_url"], "attribution": pack["attribution"],
                "fetched_at": None, "expires_at": None, "error": error,
                **({"gdacs_types": list(self.gdacs_types)} if pack_id == "gdacs" else {})}

    def _response(self, pack_id, entry, bbox, status, error=None, *, shared=False, firms_days=None):
        with self.lock:
            if (pack_id not in self.enabled and not shared) or entry.get("credential_hash") != self._cache_hash(pack_id, bbox, firms_days):
                return self._empty(pack_id, "disabled")
            features = [feature for feature in entry["data"].get("features", []) if feature_intersects(feature, bbox)]
            if pack_id == "firms":
                days = self.firms_days if firms_days is None else firms_days
                cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock() - days * 86400))
                features = [feature for feature in features if
                            (feature.get("properties", {}).get("observed_at") or cutoff) >= cutoff]
            if pack_id == "gdacs":
                features = [feature for feature in features if
                            (feature.get("properties", {}).get("disaster_code") or feature.get("properties", {}).get("event_type")) in self.gdacs_types]
            if pack_id == "notams":
                now = self.clock()
                features = [feature for feature in features if
                            any(start <= now + NOTAM_LOOKAHEAD and (end is None or end > now)
                                for start, end in feature["properties"].get("notam_periods", [
                                    (0, feature["properties"].get("valid_until"))
                                ]))]
                # Keep local events within the shared link's response cap when a view is crowded.
                features.sort(key=lambda feature: not feature["properties"].get("local_activity"))
            return {**entry["data"], "features": features,
                    "status": status, "fetched_at": entry["fetched_at"],
                    "expires_at": entry["fetched_at"] + CATALOGUE[pack_id]["ttl_seconds"], "error": error,
                    **({"gdacs_types": list(self.gdacs_types)} if pack_id == "gdacs" else {})}

    def _osm_usage(self):
        now = self.clock()
        usage = self._read(self.usage_path) or {}
        if usage.get("day") != int(now // 86400):
            return {"version": 2, "day": int(now // 86400), "last_request": 0,
                    "packs": {key: {"requests": 0, "bytes": 0} for key in OSM_PACKS}}
        if usage.get("version") == 2:
            return usage
        # Preserve today's old shared usage on upgrade. Attribute successful
        # downloads from their cache records; charge un-attributable usage to
        # both buckets conservatively rather than silently resetting the day.
        packs = {key: {"requests": 0, "bytes": 0} for key in OSM_PACKS}
        for key in OSM_PACKS:
            for path in self.cache_dir.glob(f"{key}-*.json"):
                entry = self._read(path) or {}
                if int(entry.get("fetched_at", 0) // 86400) == usage["day"]:
                    packs[key]["requests"] += 1
                    packs[key]["bytes"] += max(0, int(entry.get("data", {}).get("source_bytes", 0)))
        for field in ("requests", "bytes"):
            unknown = max(0, usage.get(field, 0) - sum(bucket[field] for bucket in packs.values()))
            for bucket in packs.values():
                bucket[field] += unknown
        return {"version": 2, "day": usage["day"], "last_request": usage.get("last_request", 0), "packs": packs}

    def _reserve_osm(self, pack_id):
        now = self.clock()
        usage = self._osm_usage()
        if now - usage.get("last_request", 0) < 30:
            return OSM_COOLDOWN
        bucket, limit = usage["packs"][pack_id], OSM_DAILY_LIMITS[pack_id]
        if bucket["requests"] >= limit["requests"] or bucket["bytes"] >= limit["bytes"]:
            return f"{CATALOGUE[pack_id]['title']} daily download budget reached ({limit['requests']} requests / {limit['bytes']//1_000_000} MB). Other OSM packs have separate budgets. Saved areas remain available; resets at 00:00 UTC."
        bucket["requests"] += 1
        usage["last_request"] = now
        self._write(self.usage_path, usage)
        return None

    def _deferred_military_view(self, bbox, cached):
        result = self._response("military", cached, bbox, "deferred") if cached else self._empty("military", "deferred")
        return {**result, "coverage_complete": False,
                "note": "Additional coverage will be downloaded when the next request can start."}

    def _record_osm_bytes(self, pack_id, data):
        usage = self._osm_usage()
        usage["packs"][pack_id]["bytes"] += max(0, int(data.get("source_bytes", len(json.dumps(data).encode()))))
        self._write(self.usage_path, usage)

    def _wide_cached_view(self, pack_id, bbox, note):
        cached = self._cached(pack_id, bbox, self._cache_hash(pack_id, bbox), partial=True)
        if not cached:
            return {**self._empty(pack_id, "zoom_in"), "note": note}
        fresh = cached.get("pinned") or self.clock() < cached["fetched_at"] + CATALOGUE[pack_id]["ttl_seconds"]
        result = self._response(pack_id, cached, bbox, "cached" if fresh else "stale")
        return {**result, "note": f"Showing saved areas. {note}", "download_limited": True}

    def _military_world_view(self, bbox, zoom):
        if not self.military_snapshot.is_file():
            return None
        limit = 800 if zoom < 9 else MAX_FEATURES
        west, south, east, north = bbox
        features, size, truncated = [], 0, False
        try:
            with closing(sqlite3.connect(self.military_snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                release, built_at = db.execute("SELECT release, built_at FROM snapshot").fetchone()
                (schema_version,) = db.execute("PRAGMA user_version").fetchone()
                rows = db.execute(
                    "SELECT feature FROM features WHERE west<=? AND east>=? AND south<=? AND north>=? "
                    "ORDER BY size DESC, id LIMIT ?", (east, west, north, south, limit + 1)
                )
                for (feature,) in rows:
                    size += len(feature)
                    if len(features) == limit or size > 6_000_000:
                        truncated = True
                        break
                    features.append(json.loads(feature))
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        return {**self._empty("military", "cached"), "features": features, "fetched_at": built_at,
                "updated_at": release, "source": "Overture Maps / OpenStreetMap", "worldwide_snapshot": True,
                "police_coverage": schema_version >= 1,
                "truncated": truncated, "coverage_complete": not truncated,
                "note": f"Worldwide offline snapshot · Overture {release}" + (" · Zoom in for more features" if truncated else "")}

    def load(self, pack_id, bbox, zoom):
        if pack_id not in CATALOGUE or CATALOGUE[pack_id].get("client_raster") or pack_id in TRAFFIC_IDS:
            raise ValueError("Choose a public feature pack")
        bbox = validate_bounds(bbox)
        if not math.isfinite(zoom) or not 0 <= zoom <= 24:
            raise ValueError("Enter a valid map zoom")
        if pack_id == "military":
            with self.lock:
                snapshot = self._military_world_view(bbox, zoom) if pack_id in self.enabled else None
            if snapshot is not None:
                if snapshot["police_coverage"] or zoom < CATALOGUE[pack_id]["min_zoom"]:
                    return snapshot
                regional = self._load_regional(pack_id, bbox, zoom)
                if regional["status"] == "disabled":
                    return regional
                police = [feature for feature in regional["features"]
                          if feature.get("properties", {}).get("kind") in {"police_station", "police_property"}]
                combined = snapshot["features"][:MAX_FEATURES - len(police)] + police
                truncated = snapshot["truncated"] or regional.get("truncated", False) or len(combined) < len(snapshot["features"]) + len(police)
                return {**snapshot, "features": combined, "truncated": truncated,
                        "status": "deferred" if regional["status"] == "deferred" else snapshot["status"],
                        "coverage_complete": snapshot["coverage_complete"] and regional["status"] in {"fresh", "cached"} and regional.get("coverage_complete", True) and not truncated,
                        "note": snapshot["note"] + " · Police locations use regional OpenStreetMap data on this snapshot."}
        return self._load_regional(pack_id, bbox, zoom)

    def provide_shared_key(self, pack_id, key):
        """An admin can initialize the host key without overwriting another team's access."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Enter a valid provider key")
        field = SHARED_INTEL_KEYS[pack_id]
        if pack_id == "firms" and not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", key.strip()):
            raise ValueError("Enter a valid NASA FIRMS MAP_KEY")
        with self.lock:
            if self.credentials.get(field):
                raise ValueError("This provider is already configured. Change the key on the host.")
            self.update({"credentials": {field: key}})

    def shared_view(self, pack_id, bbox, zoom, firms_days=None):
        pack = CATALOGUE[pack_id]
        bbox = validate_bounds(bbox)
        if type(zoom) not in (int, float) or not math.isfinite(zoom) or not 0 <= zoom <= 24:
            raise ValueError("Enter a valid map zoom")
        if pack_id == "firms" and (type(firms_days) is not int or firms_days not in (1, 3, 7)):
            raise ValueError("Choose 24 hours, 3 days or 7 days")
        with self.lock:
            if not self._configured(pack):
                return self._empty(pack_id, "needs_key", f"Ask a team admin to configure {pack['source']} on the host.")
            if zoom < pack["min_zoom"] or max(bbox[2] - bbox[0], bbox[3] - bbox[1]) > 60:
                return self._empty(pack_id, "zoom_in")
            now = self.clock()
            credential_hash = self._cache_hash(pack_id, bbox, firms_days)
            cached = self._cached(pack_id, bbox, credential_hash)
            if cached and now < cached["fetched_at"] + pack["ttl_seconds"] and (
                not cached["data"].get("truncated") or tuple(cached["bounds"]) == bbox
            ):
                return self._response(pack_id, cached, bbox, "cached", shared=True, firms_days=firms_days)
            cooling_down = now < self.next_fetch.get((pack_id, credential_hash, "shared"), 0)
            if not cooling_down and pack_id not in self.shared_pending and now >= self.shared_next_fetch.get(pack_id, 0):
                self.shared_pending.add(pack_id)
                threading.Thread(target=self._refresh_shared, args=(pack_id, bbox, zoom, firms_days), daemon=True).start()
            if cached:
                return {**self._response(pack_id, cached, bbox, "stale", "Showing the team host's cached data.", shared=True, firms_days=firms_days),
                        "waiting_for_host": not cooling_down}
            if cooling_down:
                return self._empty(pack_id, "error", f"The team host could not refresh {pack['source']} data. Try again shortly.")
            return {**self._empty(pack_id, "waiting"), "note": "Waiting for data from the team host.", "waiting_for_host": True}

    def _refresh_shared(self, pack_id, bbox, zoom, firms_days):
        # Keep upstream HTTP off Reticulum's request handler so it can continue handling voice and messages.
        try:
            self._load_regional(pack_id, bbox, zoom, shared=True, firms_days=firms_days)
        finally:
            with self.lock:
                self.shared_next_fetch[pack_id] = self.clock() + 3
                self.shared_pending.discard(pack_id)

    def _load_regional(self, pack_id, bbox, zoom, *, shared=False, firms_days=None):
        pack = CATALOGUE[pack_id]
        def response(entry, status, error=None):
            return self._response(pack_id, entry, bbox, status, error, shared=shared, firms_days=firms_days)

        with self.fetch_locks[pack_id]:
            with self.lock:
                if pack_id not in self.enabled and not shared:
                    return self._empty(pack_id, "disabled")
                if pack_id == "gdacs" and not self.gdacs_types:
                    return {**self._empty(pack_id, "filtered"), "note": "No disaster types selected. Choose types in Intel packs."}
                if not self._configured(pack):
                    return self._empty(pack_id, "needs_key", "Add provider access in Intel packs settings.")
                if zoom < pack["min_zoom"]:
                    return self._wide_cached_view(pack_id, bbox, f"Zoom in to level {pack['min_zoom']} to download additional coverage.")
                if pack_id in OSM_PACKS:
                    west, south, east, north = bbox
                    area = (east - west) * (north - south) * 111.32**2 * max(.01, math.cos(math.radians((south + north) / 2)))
                    area_limit, span_limit = OSM_AREA_LIMIT_KM2[pack_id], OSM_SPAN_LIMIT_DEGREES[pack_id]
                    if area > area_limit or east - west > span_limit or north - south > span_limit:
                        return self._wide_cached_view(pack_id, bbox, f"Zoom in to an area smaller than {area_limit:,} km² to download additional OpenStreetMap coverage.")
                credential_hash = self._cache_hash(pack_id, bbox, firms_days)
                credentials = dict(self.credentials)
                gdacs_types = self.gdacs_types
                selected_days = self.firms_days if firms_days is None else firms_days
                cached = self._cached(pack_id, bbox, credential_hash)
                exact_view = not (pack_id == "notams" or shared) or not cached or not cached["data"].get("truncated") or tuple(cached["bounds"]) == bbox
                if cached and exact_view and (pack_id != "military" or cached["data"].get("police_coverage")) and (cached.get("pinned") or self.clock() < cached["fetched_at"] + pack["ttl_seconds"]) and (cached["data"].get("coverage_complete", True) or cached.get("reuse_limited")):
                    return response(cached, "cached")
                cooldown_key = (pack_id, credential_hash, "shared") if shared else (pack_id, credential_hash, bbox) if pack_id == "gdacs" else pack_id
                self.next_fetch = {key: until for key, until in self.next_fetch.items() if until > self.clock()}
                next_fetch = self.next_fetch.get(cooldown_key, 0)
            if self.clock() < next_fetch:
                if pack_id == "military":
                    return self._deferred_military_view(bbox, cached)
                error = "Provider cooldown. Saved data is shown when available; retry shortly."
                return response(cached, "stale", error) if cached else self._empty(pack_id, "error", error)
            fetch_bounds = buffered_bounds(bbox, OSM_AREA_LIMIT_KM2[pack_id], OSM_SPAN_LIMIT_DEGREES[pack_id],
                                           margin=.5 if pack_id == "military" else .15) if pack_id in OSM_PACKS else bbox
            if pack_id in OSM_PACKS and cached and cached["data"].get("truncated"):
                # Dense areas can overflow when padded: refine to the actual
                # view on the next permitted request, rather than retaining an
                # unnecessarily truncated buffered result for the whole day.
                fetch_bounds = bbox
            if pack_id == "notams" and not (cached and cached["data"].get("truncated")):
                fetch_bounds = buffered_bounds(bbox, 2_000_000, 20, margin=.25)
            gate = self.osm_lock if pack_id in OSM_PACKS else threading.Lock()
            with gate:
                with self.lock:
                    error = self._reserve_osm(pack_id) if pack_id in OSM_PACKS else None
                if pack_id == "military" and error == OSM_COOLDOWN:
                    return self._deferred_military_view(bbox, cached)
                if error:
                    return response(cached, "stale", error) if cached else self._empty(pack_id, "error", error)
                try:
                    options = {"gdacs_types": list(gdacs_types)} if pack_id == "gdacs" else {}
                    if pack_id == "firms":
                        options["firms_days"] = selected_days
                    data = self.loader(pack_id, fetch_bounds, credentials, **options)
                    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
                        raise ValueError("Invalid provider response")
                    if pack_id == "military":
                        data["police_coverage"] = True
                    if pack_id == "notams" and data.get("truncated") and fetch_bounds != bbox:
                        # Retry the actual visible area when padding reaches the provider cap.
                        fetch_bounds = bbox
                        data = self.loader(pack_id, fetch_bounds, credentials)
                    entry = {"bounds": list(fetch_bounds), "request_bounds": list(bbox), "credential_hash": credential_hash, "fetched_at": self.clock(), "data": data}
                    with self.lock:
                        if pack_id in OSM_PACKS:
                            self._record_osm_bytes(pack_id, data)
                        # A toggle or credential change while fetching must not reveal stale access.
                        if (pack_id not in self.enabled and not shared) or credential_hash != self._cache_hash(pack_id, bbox, firms_days):
                            return self._empty(pack_id, "disabled")
                        identity = hashlib.sha256(json.dumps([fetch_bounds, credential_hash]).encode()).hexdigest()[:24]
                        self._write(self.cache_dir / f"{pack_id}-{identity}.json", entry)
                        self._prune()
                        if pack_id != "notams" and not shared:
                            self.next_fetch[cooldown_key] = self.clock() + 30
                    return response(entry, "fresh")
                except Exception as exc:
                    # Never echo upstream exceptions: URLs may contain a NASA key.
                    with self.lock:
                        if (pack_id not in self.enabled and not shared) or credential_hash != self._cache_hash(pack_id, bbox, firms_days):
                            return self._empty(pack_id, "disabled")
                        self.next_fetch[cooldown_key] = self.clock() + 60
                    error = str(exc) if isinstance(exc, ProviderError) else "Provider unavailable or access rejected. Check Internet and provider credentials, then retry."
                    return response(cached, "stale", error) if cached else self._empty(pack_id, "error", error)


def intel_router(data_dir: Path, *, store=None, elevation=None, traffic=None, intel_host=None, local_request=None):
    store = store or IntelPackStore(data_dir)
    elevation = elevation or ElevationStore(data_dir)
    router = APIRouter(prefix="/api/intel-packs")
    traffic = traffic or LiveTraffic(store)
    roads = RoadDisruptions(store)
    transit = PublicTransit()
    router.traffic = traffic
    router.store = store

    def catalogue_for(request):
        result = store.catalogue()
        host = intel_host(request) if intel_host else None
        if host:
            for pack in result["packs"]:
                if pack["id"] in SHARED_INTEL_KEYS:
                    pack["configured"] = True
                    pack["host_access"] = True
                    pack["can_provide_host_key"] = host[2]
        return result

    @router.post("/roads/check-route")
    async def check_road_route(request: Request, driving: bool = False):
        from .road_navigation import assess_routes
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4_000_000:
                raise HTTPException(413, "Route comparison request is too large")
        try:
            body = json.loads(raw)
            return await asyncio.to_thread(assess_routes, roads, body, driving=driving)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    async def catalogue(request: Request):
        return JSONResponse(catalogue_for(request), headers={"Cache-Control": "no-store"})

    @router.patch("/settings")
    async def settings(request: Request):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Change provider settings from this Reticom app")
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 20000:
                    raise ValueError("Provider settings are too large")
            body = json.loads(raw)
            credentials = body.get("credentials") if isinstance(body, dict) else None
            host_keys = HOST_CREDENTIALS.keys() & credentials.keys() if isinstance(credentials, dict) else set()
            if (host_keys or isinstance(credentials, dict) and credentials.keys() & SHARED_INTEL_KEYS.values()
                    or isinstance(body, dict) and "personal_flight_fallback" in body):
                if local_request and not local_request(request):
                    raise HTTPException(403, "Change provider access from this Reticom app")
            if host_keys:
                field = next(iter(host_keys))
                key = credentials[field]
                if body != {"credentials": {field: key}} or not isinstance(key, str) or not key.strip() or len(key) > 8192 or any(ord(char) < 32 for char in key):
                    raise ValueError("Enter a valid provider key")
                host = intel_host(request) if intel_host else None
                if not host or not host[2]:
                    raise HTTPException(403, "Team admin rights are required")
                try:
                    await asyncio.to_thread(request_host_intel, host, HOST_CREDENTIALS[field], {"key": key.strip()})
                except PermissionError as exc:
                    raise HTTPException(403, str(exc)) from None
                except (TimeoutError, OSError, ValueError):
                    raise HTTPException(503, "The team host could not save the key. Try again when connected.") from None
            else:
                await asyncio.to_thread(store.update, body)
                await traffic.settings_changed()
            return JSONResponse(catalogue_for(request), headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/{pack_id}/download")
    async def download(pack_id: str, request: Request):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Save public data from this Reticom app")
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 2048:
                    raise ValueError("Offline map request is too large")
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) - {"bounds", "zoom"}:
                raise ValueError("Choose a map view to save")
            bbox = validate_bounds(body.get("bounds"))
            zoom = float(body.get("zoom"))
            result = await asyncio.to_thread(store.download, pack_id, bbox, zoom)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/lidar/malta/{mode}/{zoom}/{x}/{y}.png")
    async def malta_lidar(mode: str, zoom: int, x: int, y: int):
        if not store.is_enabled("ahn"):
            raise HTTPException(404, "Enable National LiDAR elevation")
        try:
            tile = await asyncio.to_thread(malta_tile, mode, zoom, x, y)
        except (ProviderError, OSError) as exc:
            raise HTTPException(503, "Malta LiDAR tile unavailable") from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(tile, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})

    @router.get("/elevation/tiles/{zoom}/{x}/{y}.png")
    async def terrain_tile(zoom: int, x: int, y: int, relief: bool = False, malta: bool = False):
        if not relief and not store.is_enabled("elevation"):
            raise HTTPException(404, "Enable Contour lines in Map layers")
        max_zoom = MALTA_CONTOUR_MAX_ZOOM if malta and not relief else ELEVATION_MAX_ZOOM
        if not 0 <= zoom <= max_zoom or not 0 <= x < (1 << zoom) or not 0 <= y < (1 << zoom):
            raise HTTPException(404, "Elevation tile outside map bounds")
        try:
            levels = max(0, zoom - ELEVATION_MAX_ZOOM)
            if levels:
                try:
                    regional = await asyncio.to_thread(malta_terrain_tile, zoom, x, y)
                except (ProviderError, OSError, SyntaxError):
                    regional = None
                if regional is not None:
                    if not store.is_enabled("elevation"):
                        raise HTTPException(404, "Elevation disabled")
                    return Response(regional, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
            result = await asyncio.to_thread(elevation.tile_result, zoom - levels, x >> levels, y >> levels)
            data = await asyncio.to_thread(overzoom_terrain, result["data"], levels, x, y) if levels else result["data"]
            if not relief and not store.is_enabled("elevation"):
                raise HTTPException(404, "Elevation disabled")
            return Response(data, media_type=result["content_type"], headers={
                "Cache-Control": "private, max-age=60" if levels or result["stale"] else "private, max-age=86400",
                "X-Reticom-Terrain-Stale": str(result["stale"]).lower(),
                "X-Reticom-Terrain-Fetched-At": str(result["fetched_at"]),
            })
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Elevation not cached and provider unavailable") from exc


    @router.get("/live/flights/{identity}/profile")
    async def aircraft_profile(identity: str):
        try:
            profile = await traffic.aircraft_profile(identity)
            if not profile:
                raise HTTPException(404, "No public aircraft details available")
            return JSONResponse({
                "model": profile.get("aircraft_model"), "manufacturer": profile.get("aircraft_manufacturer"),
                "type_code": profile.get("aircraft_type_code"), "registration": profile.get("aircraft_registration"),
                "owner": profile.get("aircraft_owner"), "photo_available": bool(profile.get("photo_url")),
                "photo_source_url": profile.get("photo_source_url"),
                "source": "ADSBDB public aircraft database",
            }, headers={"Cache-Control": "private, max-age=86400"})
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/live/flights/{identity}/thumbnail")
    async def aircraft_thumbnail(identity: str):
        try:
            image = await traffic.aircraft_photo(identity)
            if not image:
                raise HTTPException(404, "No public aircraft photo available")
            return Response(image["data"], media_type=image["content_type"], headers={"Cache-Control": "private, max-age=86400"})
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/live/vessels/{identity}/photo")
    async def vessel_photo_metadata(identity: str):
        if not valid_identity("vessels", identity):
            raise HTTPException(422, "Choose a valid MMSI")
        try:
            photo = await asyncio.to_thread(vessel_photo_info, identity)
        except HTTPError as exc:
            if exc.code != 404:
                raise HTTPException(503, "Vessel photo provider unavailable") from None
            photo = None
        except (OSError, ValueError):
            raise HTTPException(503, "Vessel photo provider unavailable") from None
        return JSONResponse(photo, headers={"Cache-Control": f"private, max-age={86400 if photo else 300}"})

    @router.get("/live/vessels/{identity}/thumbnail/{number}")
    async def vessel_thumbnail(identity: str, number: int):
        if not valid_identity("vessels", identity) or not 1 <= number <= 9999:
            raise HTTPException(422, "Choose a valid vessel photo")
        try:
            image = await asyncio.to_thread(fetch_vessel_photo, identity, number)
        except (OSError, ValueError):
            raise HTTPException(404, "No public vessel photo available") from None
        return Response(image["data"], media_type=image["content_type"],
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    @router.get("/live/{pack_id}")
    async def live_features(pack_id: str, west: float, south: float, east: float, north: float, zoom: float, client: str):
        try:
            if pack_id not in TRAFFIC_IDS:
                raise ValueError("Choose aircraft or vessels")
            result = await traffic.load(pack_id, validate_bounds((west, south, east, north)), zoom, client)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/live/{pack_id}/{identity}/route")
    async def traffic_route(pack_id: str, identity: str):
        try:
            return JSONResponse(await traffic.route(pack_id, identity), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/live/vessels/lease/{client}")
    async def release_vessels(client: str, request: Request):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Use this Reticom app")
        traffic.release(client)
        return {"released": True}

    @router.get("/transit/route")
    async def transit_route(trip: str):
        if not store.is_enabled("transit"):
            raise HTTPException(404, "Public transport is disabled")
        try:
            return JSONResponse(await asyncio.to_thread(transit.route, trip), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/{pack_id}")
    async def features(request: Request, pack_id: str, west: float, south: float, east: float, north: float, zoom: float, driving: bool = False):
        try:
            bounds = (west, south, east, north)
            if pack_id == "transit":
                bounds = validate_bounds(bounds)
                if not math.isfinite(zoom) or not 0 <= zoom <= 24:
                    raise ValueError("Enter a valid map zoom")
                result = store._empty(pack_id, "disabled")
                if store.is_enabled(pack_id):
                    result = await asyncio.to_thread(transit.load, bounds, zoom)
                if result["status"] != "disabled" and not store.is_enabled(pack_id):
                    result = store._empty(pack_id, "disabled")
            elif pack_id == "roads":
                result = await asyncio.to_thread(roads.load, bounds, zoom, driving=driving)
            else:
                result = await asyncio.to_thread(store.load, pack_id, bounds, zoom)
                if pack_id in SHARED_INTEL_KEYS and result["status"] == "needs_key":
                    host = intel_host(request) if intel_host else None
                    if host and local_request and not local_request(request):
                        raise HTTPException(403, "Access shared map layers from this Reticom app")
                    if host and zoom >= CATALOGUE[pack_id]["min_zoom"]:
                        data = {"bounds": list(bounds), "zoom": zoom}
                        if pack_id == "firms":
                            with store.lock:
                                data["firms_days"] = store.firms_days
                        try:
                            reply = await asyncio.to_thread(request_host_intel, host, pack_id, data)
                            result = reply[pack_id]
                            if result.get("type") != "FeatureCollection" or not isinstance(result.get("features"), list):
                                raise ValueError("Invalid team host response")
                        except PermissionError:
                            result = store._empty(pack_id, "error", "Team approval is needed for shared map layers.")
                        except (TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
                            result = store._empty(pack_id, "error", "The team host is unavailable for this layer.")
                    elif host:
                        result = store._empty(pack_id, "zoom_in")
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router


def request_host_intel(host, pack_id, data):
    from .continuity import json_rpc

    identity, destination, _ = host
    return json_rpc(identity, destination, f"/intel/{pack_id}", data, timeout=8, max_response_size=SHARED_INTEL_BYTES)
