"""Device-local Valhalla routing. Routing packs are data, never executable config.

No HTTP client or tile URL is configured for either native engine. Import is an
atomic, bounded operation; a bad replacement cannot destroy a working pack.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

MAX_UPLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_STORAGE = 4 * 1024 * 1024 * 1024
TILE_PATH = re.compile(r"tiles/[012]/(?:[0-9]{3}/)*[0-9]{3}\.gph")
PACK_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
GRAPH_VERSION = "3.8.3"


class RoutingError(ValueError):
    def __init__(self, message, code="invalid_pack", status=422):
        super().__init__(message)
        self.code, self.status = code, status


def point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RoutingError("Enter valid route coordinates", "invalid_coordinates")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in value):
        raise RoutingError("Enter valid route coordinates", "invalid_coordinates")
    if not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90:
        raise RoutingError("Route coordinates are outside the world", "invalid_coordinates")
    return list(value)


def contains(bounds, coordinate):
    return bounds[0] <= coordinate[0] <= bounds[2] and bounds[1] <= coordinate[1] <= bounds[3]


def engine_config(tile_dir):
    # Deliberately constructed here, not accepted from an imported pack.
    defaults = json.loads(Path(__file__).with_name("routing-defaults.json").read_text("utf-8"))
    return {**defaults,
        "mjolnir": {"tile_dir": str(Path(tile_dir).resolve()), "concurrency": 1},
        "logging": {"type": ""},
    }


class NativeEngine:
    def __init__(self):
        self.actor = None
        self.config_path = None
        self.android = importlib.util.find_spec("java") is not None

    def available(self):
        if self.android:
            try:
                from java import jclass
                jclass("com.valhalla.valhalla.ValhallaKotlin")
                return True
            except Exception:
                return False
        try:
            from valhalla import Actor  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def close(self):
        if self.actor is not None and self.android:
            self.actor.close()
        self.actor = None
        self.config_path = None

    def route(self, config_path, request):
        if self.config_path != config_path:
            self.close()
            if self.android:
                from java import jclass
                self.actor = jclass("com.valhalla.valhalla.ValhallaKotlin")(str(config_path))
            else:
                from valhalla import Actor
                self.actor = Actor(str(config_path))
            self.config_path = config_path
        result = self.actor.routeJson(json.dumps(request)) if self.android else self.actor.route(request)
        return json.loads(str(result)) if self.android else result


class OfflineRouting:
    def __init__(self, root: Path, engine=None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = engine or NativeEngine()
        self.lock = threading.RLock()
        self.import_lock = asyncio.Lock()
        self.route_lock = asyncio.Lock()

    def close(self):
        with self.lock:
            self.engine.close()

    def listing(self):
        packs = []
        for entry in sorted(self.root.glob("*/manifest.json")):
            if entry.parent.name.startswith("."):
                continue
            try:
                data = json.loads(entry.read_text("utf-8"))
                if not PACK_ID.fullmatch(data["id"]) or not entry.parent.name.startswith(data["id"] + "--"):
                    continue
                packs.append({**{k: data[k] for k in ("id", "name", "bounds", "profiles", "created_at", "bytes", "source")},
                              "catalogue_sha256": data.get("catalogue_sha256"), "data_date": data.get("data_date")})
            except (ValueError, KeyError, OSError, TypeError):
                continue
        return {"engine_available": self.engine.available(), "packs": packs,
                "max_upload_bytes": MAX_UPLOAD, "bytes": sum(p["bytes"] for p in packs)}

    def _directory(self, pack_id):
        if not isinstance(pack_id, str) or not PACK_ID.fullmatch(pack_id):
            raise RoutingError("Invalid routing pack identifier")
        matches = sorted(self.root.glob(pack_id + "--*/manifest.json"))
        return matches[-1].parent if matches else None

    def install(self, archive: Path, *, expected=None):
        if archive.stat().st_size > MAX_UPLOAD:
            raise RoutingError("Routing pack exceeds the 512 MB import limit")
        with self.lock, tempfile.TemporaryDirectory(prefix=".import-", dir=self.root) as temporary:
            stage = Path(temporary)
            try:
                with zipfile.ZipFile(archive) as bundle:
                    entries = bundle.infolist()
                    names = [e.filename for e in entries]
                    if len(entries) > 100001 or len(set(names)) != len(names):
                        raise RoutingError("Routing pack contains too many or duplicate files")
                    if "manifest.json" not in names or bundle.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
                        raise RoutingError("Routing pack manifest is missing or too large")
                    manifest = json.loads(bundle.read("manifest.json"))
                    self._validate_manifest(manifest)
                    if expected and any(manifest[key] != expected[key] for key in ("id", "bounds", "profiles", "created_at")):
                        raise RoutingError("Downloaded pack does not match its catalogue region")
                    checksums = manifest["files"]
                    if set(names) != set(checksums) | {"manifest.json"}:
                        raise RoutingError("Routing pack has unexpected or missing files")
                    total = sum(e.file_size for e in entries)
                    retained = sum(p["bytes"] for p in self.listing()["packs"] if p["id"] != manifest["id"])
                    if total > MAX_EXPANDED or retained + total > MAX_STORAGE:
                        raise RoutingError("Not enough routing storage (2 GB per pack, 4 GB total)")
                    if shutil.disk_usage(self.root).free < total + 128 * 1024 * 1024:
                        raise RoutingError("Not enough free space to import this routing pack")
                    for entry in entries:
                        name = entry.filename
                        if name == "manifest.json":
                            continue
                        if not TILE_PATH.fullmatch(name) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                            raise RoutingError("Only routing graph tiles can be imported")
                        target = stage / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        with bundle.open(entry) as source, target.open("wb") as output:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                output.write(chunk)
                        if digest.hexdigest() != checksums[name]:
                            raise RoutingError("Routing pack checksum failed; download it again")
            except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RoutingError("This is not a valid Reticom routing pack") from exc
            old = self._directory(manifest["id"])
            destination = self.root / (manifest["id"] + "--" + uuid.uuid4().hex)
            manifest["bytes"] = total
            if expected:
                manifest["catalogue_sha256"] = expected["sha256"]
                manifest["data_date"] = expected["data_date"]
            (stage / "manifest.json").write_text(json.dumps(manifest), "utf-8")
            (stage / "valhalla.json").write_text(json.dumps(engine_config(destination / "tiles")), "utf-8")
            self.engine.close()
            stage.rename(destination)
            if old:
                shutil.rmtree(old)
            return self.listing()

    @staticmethod
    def _validate_manifest(manifest):
        if not isinstance(manifest, dict) or manifest.get("format") != "reticom-routing-v1" or manifest.get("valhalla_version") != GRAPH_VERSION:
            raise RoutingError("Unsupported routing pack version; build it with Valhalla 3.8.3")
        if not isinstance(manifest.get("id"), str) or not PACK_ID.fullmatch(manifest["id"]):
            raise RoutingError("Invalid routing pack identifier")
        for key in ("name", "source", "created_at"):
            if not isinstance(manifest.get(key), str) or not 1 <= len(manifest[key]) <= 300:
                raise RoutingError("Invalid routing pack metadata")
        if manifest.get("profiles") != ["walking", "driving"]:
            raise RoutingError("Routing pack must include walking and driving")
        bounds = manifest.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise RoutingError("Routing pack needs coverage bounds")
        point(bounds[:2]); point(bounds[2:])
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise RoutingError("Invalid routing pack coverage")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or not all(
            isinstance(name, str) and TILE_PATH.fullmatch(name) and isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest) for name, digest in files.items()
        ):
            raise RoutingError("Invalid routing tile checksums")

    def delete(self, pack_id):
        with self.lock:
            directory = self._directory(pack_id)
            if directory is None:
                raise RoutingError("Routing pack not found", "not_found", 404)
            self.engine.close()
            shutil.rmtree(directory)
            return self.listing()

    def route(self, origin, target, profile):
        origin, target = point(origin), point(target)
        if profile not in ("walking", "driving"):
            raise RoutingError("Choose walking or driving", "invalid_profile")
        with self.lock:
            if not self.engine.available():
                raise RoutingError("Offline routing engine is not installed on this device", "engine_unavailable", 503)
            packs = [p for p in self.listing()["packs"] if contains(p["bounds"], origin) and contains(p["bounds"], target)]
            if not packs:
                raise RoutingError("Import a routing pack covering both your position and destination", "outside_coverage", 409)
            request = {"locations": [{"lon": p[0], "lat": p[1], "radius": 100} for p in (origin, target)],
                       "costing": "pedestrian" if profile == "walking" else "auto",
                       "format": "osrm", "shape_format": "geojson", "units": "kilometers"}
            for pack in sorted(packs, key=lambda p: p["created_at"], reverse=True):
                try:
                    result = self.engine.route(self._directory(pack["id"]) / "valhalla.json", request)
                    coords = result.get("routes", [{}])[0].get("geometry", {}).get("coordinates", [])
                    if result.get("code") != "Ok" or len(coords) < 2:
                        continue
                    if not all(contains(pack["bounds"], point(p)) for p in coords):
                        continue
                    return {**result, "source": "offline", "pack": pack["name"],
                            "data_date": pack["created_at"], "profile": profile}
                except RuntimeError as exc:
                    # Configuration/native failures are not a missing road.
                    if "No such node" in str(exc):
                        raise RoutingError("Offline engine configuration error", "engine_error", 503) from exc
                    continue
                except ValueError:
                    continue
            raise RoutingError("No offline route within this pack. Download a larger region or choose another destination.", "no_route", 409)


def offline_routing_router(data_dir: Path):
    router = APIRouter()
    store = OfflineRouting(data_dir / "offline-routing")
    router.store = store
    from .routing_catalogue import add_catalogue_routes
    add_catalogue_routes(router, store, data_dir)

    @router.get("/api/offline-routing")
    async def listing():
        return await asyncio.to_thread(store.listing)

    @router.post("/api/offline-routing/import")
    async def import_pack(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
            raise HTTPException(415, "Upload a routing pack as application/octet-stream")
        async with store.import_lock:
            descriptor, name = tempfile.mkstemp(prefix=".upload-", dir=store.root)
            import os
            try:
                received = 0
                with os.fdopen(descriptor, "wb") as output:
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > MAX_UPLOAD:
                            raise HTTPException(413, "Routing pack exceeds the 512 MB import limit")
                        output.write(chunk)
                # Shield the worker and await it on cancellation: never remove its
                # input or start a replacement while native/file work is running.
                task = asyncio.create_task(asyncio.to_thread(store.install, Path(name)))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
            except RoutingError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            finally:
                Path(name).unlink(missing_ok=True)

    @router.delete("/api/offline-routing/{pack_id}")
    async def delete(pack_id: str):
        try:
            return await asyncio.to_thread(store.delete, pack_id)
        except RoutingError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/api/offline-routing/route")
    async def route(request: Request):
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise RoutingError("Send a JSON route request")
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 2048:
                    raise RoutingError("Route request is too large")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise RoutingError("Invalid route request")
            if store.route_lock.locked():
                raise RoutingError("A route is still being calculated. Try again shortly.", "routing_busy", 409)
            async with store.route_lock:
                task = asyncio.create_task(asyncio.to_thread(store.route, body.get("origin"), body.get("target"), body.get("profile", "driving")))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Invalid route request") from exc
        except RoutingError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)

    return router
