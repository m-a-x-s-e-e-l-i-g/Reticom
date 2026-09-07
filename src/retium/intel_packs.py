"""Device-local public map layers; deliberately outside the signed team feed.

Provider secrets and caches never enter mission packs or Reticulum replication.
All network work is opt-in, bounded and performed off the ASGI event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .intel_sources import PACKS, ProviderError, MAX_FEATURES, OSM_AREA_LIMIT_KM2, OSM_SPAN_LIMIT_DEGREES, GDACS_DETAIL_MAX_SPAN, load_pack
from .osm_cache import buffered_bounds, intersects, merge_entries
from .elevation import ElevationStore, ELEVATION_MAX_ZOOM
from .traffic import TRAFFIC_PACKS, TRAFFIC_IDS, LiveTraffic, validate_traffic_filters


ELEVATION_PACK = {
    "id": "elevation", "title": "Contour lines", "description": "Height lines labelled in metres. Spacing adjusts with zoom; thicker lines show major elevations. Estimated terrain, not survey accuracy.",
    "source": "AWS / Mapzen terrain", "source_url": "https://registry.opendata.aws/terrain-tiles/",
    "attribution": "Terrain: Mapzen / data providers", "attribution_url": "https://github.com/tilezen/joerd/blob/master/docs/attribution.md", "min_zoom": 9,
    "ttl_seconds": 2592000, "credential_fields": [],
}
CATALOGUE = {p["id"]: p for p in [*PACKS, ELEVATION_PACK, *TRAFFIC_PACKS]}
GDACS_TYPES = tuple(item["id"] for item in CATALOGUE["gdacs"]["disaster_types"])
DEFAULT_GDACS_TYPES = tuple(code for code in GDACS_TYPES if code != "DR")
DEFAULT_ENABLED = ["gdacs", "military"]  # Trails now come from always-on basemap tiles.
GDACS_CACHE_VERSION = 2  # Earlier caches contain point locations, not event outlines.
SECRET_FIELDS = {"firms_key", "acled_token", "aisstream_key"}
OSM_PACKS = {"trails", "military"}
OSM_DAILY_LIMITS = {"trails": {"requests": 500, "bytes": 200_000_000}, "military": {"requests": 500, "bytes": 100_000_000}}
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_FILES = 128
STALE_LIMIT = {"trails": 30 * 86400, "military": 30 * 86400, "gdacs": 86400, "firms": 21600, "acled": 7 * 86400}


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
        self.usage_path = self.directory / "osm-usage.json"
        self.loader, self.clock = loader, clock
        self.lock = threading.RLock()
        self.fetch_locks = {key: threading.Lock() for key in CATALOGUE}
        self.osm_lock = threading.Lock()
        self.next_fetch = {}
        stored = self._read(self.settings_path) or {}
        enabled = stored.get("enabled", DEFAULT_ENABLED)
        credentials = stored.get("credentials", {})
        self.enabled = {key for key in enabled if isinstance(key, str) and key in CATALOGUE} if isinstance(enabled, list) else set()
        self.credentials = {key: value for key, value in credentials.items() if key in SECRET_FIELDS and isinstance(value, str)} if isinstance(credentials, dict) else {}
        try:
            self.gdacs_types = validate_gdacs_types(stored.get("gdacs_types", list(DEFAULT_GDACS_TYPES)))
        except ValueError:
            self.gdacs_types = DEFAULT_GDACS_TYPES
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
                "settings": {"enabled": sorted(self.enabled), "gdacs_types": list(self.gdacs_types), "traffic_filters": self.traffic_filters},
                "credentials": {key: bool(self.credentials.get(key)) for key in sorted(SECRET_FIELDS)},
            }

    def update(self, body):
        if not isinstance(body, dict) or set(body) - {"enabled", "credentials", "gdacs_types", "traffic_filters"}:
            raise ValueError("Use enabled packs, disaster filters and provider credentials only")
        with self.lock:
            enabled = body.get("enabled", sorted(self.enabled))
            if not isinstance(enabled, list) or any(not isinstance(key, str) or key not in CATALOGUE for key in enabled):
                raise ValueError("Choose an available intel pack")
            supplied = body.get("credentials", {})
            if not isinstance(supplied, dict) or set(supplied) - SECRET_FIELDS:
                raise ValueError("Unknown provider credential")
            if any(not isinstance(value, str) or len(value) > 8192 or any(ord(char) < 32 for char in value) for value in supplied.values()):
                raise ValueError("Enter a valid provider key or token")
            gdacs_types = validate_gdacs_types(body.get("gdacs_types", list(self.gdacs_types)))
            traffic_filters = validate_traffic_filters(body.get("traffic_filters", self.traffic_filters))
            credentials = {**self.credentials, **{key: value.strip() for key, value in supplied.items()}}
            self._write(self.settings_path, {"enabled": sorted(set(enabled)), "credentials": credentials, "gdacs_types": list(gdacs_types), "traffic_filters": traffic_filters})
            if gdacs_types != self.gdacs_types:
                self.next_fetch = {key: until for key, until in self.next_fetch.items()
                                   if not (key == "gdacs" or isinstance(key, tuple) and key[0] == "gdacs")}
            self.enabled, self.credentials = set(enabled), credentials
            self.gdacs_types = gdacs_types
            self.traffic_filters = traffic_filters
            if supplied:
                self.next_fetch.clear()
            return self.catalogue()

    def is_enabled(self, pack_id):
        with self.lock:
            return pack_id in self.enabled

    def _credential_hash(self, pack_id):
        selected = {key: self.credentials.get(key, "") for key in CATALOGUE[pack_id].get("credential_fields", [])}
        if pack_id == "gdacs":
            selected.update(gdacs_types=self.gdacs_types, cache_version=GDACS_CACHE_VERSION)
        return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()

    def _cache_hash(self, pack_id, bbox):
        credential_hash = self._credential_hash(pack_id)
        if pack_id != "gdacs":
            return credential_hash
        detail = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) <= GDACS_DETAIL_MAX_SPAN
        # A global overview has no per-event enrichment. Nor can a containing
        # detail view promise coverage of another view when enrichment is capped.
        scope = list(bbox) if detail else "overview"
        return hashlib.sha256(json.dumps([credential_hash, scope]).encode()).hexdigest()

    def _cached(self, pack_id, bbox, credential_hash):
        candidates = []
        for path in self.cache_dir.glob(f"{pack_id}-*.json"):
            entry = self._read(path)
            if not entry or entry.get("credential_hash") != credential_hash:
                continue
            if not isinstance(entry.get("bounds"), list) or len(entry["bounds"]) != 4 or not isinstance(entry.get("data"), dict):
                continue
            try:
                age = self.clock() - float(entry["fetched_at"])
                if (entry.get("pinned") or 0 <= age <= STALE_LIMIT.get(pack_id, 86400)) and (intersects(entry["bounds"], bbox) if pack_id in OSM_PACKS else contains(entry["bounds"], bbox)):
                    candidates.append(entry)
            except (TypeError, ValueError, KeyError):
                continue
        if pack_id not in OSM_PACKS or not candidates:
            return max(candidates, key=lambda entry: entry["fetched_at"], default=None)
        candidates.sort(key=lambda entry: entry["fetched_at"], reverse=True)
        fresh = [entry for entry in candidates if entry.get("pinned") or self.clock() < entry["fetched_at"] + CATALOGUE[pack_id]["ttl_seconds"]]
        # A limited result may be reused for the exact original request, with
        # its warning intact. It cannot claim completeness for a different view.
        exact = next((entry for entry in fresh if tuple(entry.get("request_bounds", entry["bounds"])) == tuple(bbox)), None)
        merged = merge_entries(fresh or candidates, bbox, credential_hash, feature_intersects, MAX_FEATURES)
        if merged["data"]["coverage_complete"]:
            return merged
        if exact and exact["data"].get("truncated") and tuple(exact["bounds"]) == tuple(bbox):
            return {**exact, "data": {**exact["data"], "coverage_complete": False}, "reuse_limited": True}
        # On provider failure include older overlapping areas, explicitly stale.
        if len(fresh) != len(candidates):
            merged = merge_entries(candidates, bbox, credential_hash, feature_intersects, MAX_FEATURES)
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
            raise ValueError("Only Military areas can be saved for offline use")
        result = self.load(pack_id, bbox, zoom)
        if result.get("status") not in {"fresh", "cached", "stale"}:
            raise ValueError(result.get("error") or "Military areas are not available for this view")
        if not result.get("coverage_complete", True) or result.get("truncated"):
            raise ValueError("This view is incomplete. Zoom in slightly, then save it again.")
        bbox = validate_bounds(bbox)
        with self.lock:
            if pack_id not in self.enabled:
                raise ValueError("Enable Military areas before saving a view")
            credential_hash = self._cache_hash(pack_id, bbox)
            data = {key: value for key, value in result.items() if key not in {"status", "fetched_at", "expires_at", "error"}}
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

    def _response(self, pack_id, entry, bbox, status, error=None):
        with self.lock:
            if pack_id not in self.enabled or entry.get("credential_hash") != self._cache_hash(pack_id, bbox):
                return self._empty(pack_id, "disabled")
            features = [feature for feature in entry["data"].get("features", []) if feature_intersects(feature, bbox)]
            if pack_id == "gdacs":
                features = [feature for feature in features if
                            (feature.get("properties", {}).get("disaster_code") or feature.get("properties", {}).get("event_type")) in self.gdacs_types]
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
            return "Public OSM service cooldown. Retry after 30 seconds."
        bucket, limit = usage["packs"][pack_id], OSM_DAILY_LIMITS[pack_id]
        if bucket["requests"] >= limit["requests"] or bucket["bytes"] >= limit["bytes"]:
            return f"{CATALOGUE[pack_id]['title']} daily download budget reached ({limit['requests']} requests / {limit['bytes']//1_000_000} MB). Other OSM packs have separate budgets. Saved areas remain available; resets at 00:00 UTC."
        bucket["requests"] += 1
        usage["last_request"] = now
        self._write(self.usage_path, usage)
        return None

    def _record_osm_bytes(self, pack_id, data):
        usage = self._osm_usage()
        usage["packs"][pack_id]["bytes"] += max(0, int(data.get("source_bytes", len(json.dumps(data).encode()))))
        self._write(self.usage_path, usage)

    def load(self, pack_id, bbox, zoom):
        if pack_id not in CATALOGUE or pack_id == "elevation" or pack_id in TRAFFIC_IDS:
            raise ValueError("Choose a public feature pack")
        bbox = validate_bounds(bbox)
        if not math.isfinite(zoom) or not 0 <= zoom <= 24:
            raise ValueError("Enter a valid map zoom")
        pack = CATALOGUE[pack_id]
        with self.fetch_locks[pack_id]:
            with self.lock:
                if pack_id not in self.enabled:
                    return self._empty(pack_id, "disabled")
                if pack_id == "gdacs" and not self.gdacs_types:
                    return {**self._empty(pack_id, "filtered"), "note": "No disaster types selected. Choose types in Intel packs."}
                if not self._configured(pack):
                    return self._empty(pack_id, "needs_key", "Add provider access in Intel packs settings.")
                if zoom < pack["min_zoom"]:
                    return self._empty(pack_id, "zoom_in", f"Zoom in to level {pack['min_zoom']} to load this pack.")
                if pack_id in OSM_PACKS:
                    west, south, east, north = bbox
                    area = (east - west) * (north - south) * 111.32**2 * max(.01, math.cos(math.radians((south + north) / 2)))
                    area_limit, span_limit = OSM_AREA_LIMIT_KM2[pack_id], OSM_SPAN_LIMIT_DEGREES[pack_id]
                    if area > area_limit or east - west > span_limit or north - south > span_limit:
                        result = self._empty(pack_id, "zoom_in")
                        result["note"] = f"Zoom in to an area smaller than {area_limit:,} km² for OpenStreetMap intel."
                        return result
                credential_hash = self._cache_hash(pack_id, bbox)
                credentials = dict(self.credentials)
                gdacs_types = self.gdacs_types
                cached = self._cached(pack_id, bbox, credential_hash)
                if cached and (cached.get("pinned") or self.clock() < cached["fetched_at"] + pack["ttl_seconds"]) and (cached["data"].get("coverage_complete", True) or cached.get("reuse_limited")):
                    return self._response(pack_id, cached, bbox, "cached")
                cooldown_key = (pack_id, credential_hash, bbox) if pack_id == "gdacs" else pack_id
                self.next_fetch = {key: until for key, until in self.next_fetch.items() if until > self.clock()}
                next_fetch = self.next_fetch.get(cooldown_key, 0)
            if self.clock() < next_fetch:
                error = "Provider cooldown. Saved data is shown when available; retry shortly."
                return self._response(pack_id, cached, bbox, "stale", error) if cached else self._empty(pack_id, "error", error)
            fetch_bounds = buffered_bounds(bbox, OSM_AREA_LIMIT_KM2[pack_id], OSM_SPAN_LIMIT_DEGREES[pack_id]) if pack_id in OSM_PACKS else bbox
            if pack_id in OSM_PACKS and cached and cached["data"].get("truncated"):
                # Dense areas can overflow when padded: refine to the actual
                # view on the next permitted request, rather than retaining an
                # unnecessarily truncated buffered result for the whole day.
                fetch_bounds = bbox
            gate = self.osm_lock if pack_id in OSM_PACKS else threading.Lock()
            with gate:
                with self.lock:
                    error = self._reserve_osm(pack_id) if pack_id in OSM_PACKS else None
                if error:
                    return self._response(pack_id, cached, bbox, "stale", error) if cached else self._empty(pack_id, "error", error)
                try:
                    options = {"gdacs_types": list(gdacs_types)} if pack_id == "gdacs" else {}
                    data = self.loader(pack_id, fetch_bounds, credentials, **options)
                    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
                        raise ValueError("Invalid provider response")
                    entry = {"bounds": list(fetch_bounds), "request_bounds": list(bbox), "credential_hash": credential_hash, "fetched_at": self.clock(), "data": data}
                    with self.lock:
                        if pack_id in OSM_PACKS:
                            self._record_osm_bytes(pack_id, data)
                        # A toggle or credential change while fetching must not reveal stale access.
                        if pack_id not in self.enabled or credential_hash != self._cache_hash(pack_id, bbox):
                            return self._empty(pack_id, "disabled")
                        identity = hashlib.sha256(json.dumps([fetch_bounds, credential_hash]).encode()).hexdigest()[:24]
                        self._write(self.cache_dir / f"{pack_id}-{identity}.json", entry)
                        self._prune()
                        self.next_fetch[cooldown_key] = self.clock() + 30
                    return self._response(pack_id, entry, bbox, "fresh")
                except Exception as exc:
                    # Never echo upstream exceptions: URLs may contain a NASA key.
                    with self.lock:
                        if pack_id not in self.enabled or credential_hash != self._cache_hash(pack_id, bbox):
                            return self._empty(pack_id, "disabled")
                        self.next_fetch[cooldown_key] = self.clock() + 60
                    error = str(exc) if isinstance(exc, ProviderError) else "Provider unavailable or access rejected. Check Internet and provider credentials, then retry."
                    return self._response(pack_id, cached, bbox, "stale", error) if cached else self._empty(pack_id, "error", error)


def intel_router(data_dir: Path, *, store=None, elevation=None, traffic=None):
    store = store or IntelPackStore(data_dir)
    elevation = elevation or ElevationStore(data_dir)
    router = APIRouter(prefix="/api/intel-packs")
    traffic = traffic or LiveTraffic(store)
    router.traffic = traffic

    @router.get("")
    async def catalogue():
        return JSONResponse(store.catalogue(), headers={"Cache-Control": "no-store"})

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
            result = await asyncio.to_thread(store.update, json.loads(raw))
            await traffic.settings_changed()
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
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

    @router.get("/elevation/tiles/{zoom}/{x}/{y}.png")
    async def terrain_tile(zoom: int, x: int, y: int):
        if not store.is_enabled("elevation"):
            raise HTTPException(404, "Enable Contour lines in Map layers")
        if not 0 <= zoom <= ELEVATION_MAX_ZOOM or not 0 <= x < (1 << zoom) or not 0 <= y < (1 << zoom):
            raise HTTPException(404, "Elevation tile outside map bounds")
        try:
            result = await asyncio.to_thread(elevation.tile_result, zoom, x, y)
            if not store.is_enabled("elevation"):
                raise HTTPException(404, "Elevation disabled")
            return Response(result["data"], media_type=result["content_type"], headers={
                "Cache-Control": "private, max-age=60" if result["stale"] else "private, max-age=86400",
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

    @router.get("/live/{pack_id}")
    async def live_features(pack_id: str, west: float, south: float, east: float, north: float, zoom: float, client: str):
        try:
            if pack_id not in TRAFFIC_IDS:
                raise ValueError("Choose aircraft or vessels")
            result = await traffic.load(pack_id, validate_bounds((west, south, east, north)), zoom, client)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/live/vessels/lease/{client}")
    async def release_vessels(client: str, request: Request):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Use this Reticom app")
        traffic.release(client)
        return {"released": True}

    @router.get("/{pack_id}")
    async def features(pack_id: str, west: float, south: float, east: float, north: float, zoom: float):
        try:
            result = await asyncio.to_thread(store.load, pack_id, (west, south, east, north), zoom)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
