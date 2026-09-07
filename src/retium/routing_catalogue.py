"""Verified regional routing downloads; no arbitrary URL/file input from clients."""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request as HTTPRequest, build_opener
from fastapi import HTTPException, Request

from .offline_routing import GRAPH_VERSION, MAX_EXPANDED, MAX_UPLOAD, OfflineRouting, RoutingError, PACK_ID, point

CATALOGUE_URL = "https://github.com/m-a-x-s-e-e-l-i-g/Reticom/releases/download/routing-catalogue-v1/catalogue.json"
ASSET_PREFIX = "/m-a-x-s-e-e-l-i-g/Reticom/releases/download/"
CATALOGUE_LIMIT = 1024 * 1024
ACTIVE = {"downloading", "verifying", "installing"}


def allowed_url(url, redirect=False):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise RoutingError("Untrusted routing download URL")
    if parsed.hostname == "github.com" and unquote(parsed.path).startswith(ASSET_PREFIX):
        parts = unquote(parsed.path[len(ASSET_PREFIX):]).split("/")
        if len(parts) == 2 and all(re.fullmatch(r"[a-zA-Z0-9._-]+", p) and p not in (".", "..") for p in parts):
            return url
    if redirect and parsed.hostname == "release-assets.githubusercontent.com":
        return url
    raise RoutingError("Routing downloads must come from Reticom's published data releases")


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        allowed_url(newurl, redirect=True)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def open_download(url):
    allowed_url(url)
    return build_opener(SafeRedirects()).open(HTTPRequest(url, headers={"User-Agent": "Reticom-routing/1", "Accept-Encoding": "identity"}), timeout=30)


def validate_catalogue(data):
    if not isinstance(data, dict) or data.get("format") != "reticom-routing-catalogue-v1" or data.get("valhalla_version") != GRAPH_VERSION:
        raise RoutingError("Catalogue requires a different routing engine; update Reticom")
    regions = data.get("regions")
    if type(data.get("published", False)) is not bool:
        raise RoutingError("Invalid catalogue publication state")
    if "generated_at" in data:
        try:
            if not datetime.fromisoformat(data["generated_at"]).tzinfo:
                raise ValueError("Missing timezone")
        except (ValueError, TypeError):
            raise RoutingError("Invalid catalogue date") from None
    if not isinstance(regions, list) or len(regions) > 2000:
        raise RoutingError("Invalid routing catalogue")
    ids = set()
    for entry in regions:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not PACK_ID.fullmatch(entry["id"]) or entry["id"] in ids:
            raise RoutingError("Invalid or duplicate catalogue region")
        ids.add(entry["id"])
        for key in ("name", "country", "created_at", "data_date", "source"):
            if not isinstance(entry.get(key), str) or not 1 <= len(entry[key]) <= 300:
                raise RoutingError("Invalid region metadata")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["data_date"]):
            raise RoutingError("Invalid region data date")
        for key, maximum in (("bytes", MAX_UPLOAD), ("expanded_bytes", MAX_EXPANDED)):
            if type(entry.get(key)) is not int or not 0 < entry[key] <= maximum:
                raise RoutingError("Region exceeds this device's routing pack limits")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise RoutingError("Region is missing its checksum")
        bounds = entry.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise RoutingError("Region is missing coverage")
        point(bounds[:2]); point(bounds[2:])
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3] or entry.get("profiles") != ["walking", "driving"]:
            raise RoutingError("Invalid region coverage or routing profiles")
        if not isinstance(entry.get("url"), str):
            raise RoutingError("Region has no download")
        allowed_url(entry["url"])
    return data


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), "utf-8")
    temporary.replace(path)


class RoutingCatalogue:
    def __init__(self, store, *, bundled=None, opener=open_download, development=None):
        self.store = store
        self.bundled = bundled or Path(__file__).with_name("routing-catalogue.json")
        self.cache = store.root / ".catalogue.json"
        self.state = store.root / ".download.json"
        self.partial = store.root / ".catalogue-download.part"
        # Explicit local build directory is a development-only input, never a
        # path supplied by a web client. The phone always uses public HTTPS.
        self.development = development
        self.opener = opener
        self.lock = threading.RLock()
        self.refresh_lock = asyncio.Lock()
        self.cancelled = threading.Event()
        self.task = None
        self.job = None
        self.last_write = 0
        self.refresh_at = 0
        self.catalogue_error = ""
        try:
            self.job = json.loads(self.state.read_text("utf-8"))
            if self.job.get("status") in ACTIVE:
                self.job = {**self.job, "status": "error", "error": "Download interrupted. Retry to download again."}
        except (ValueError, OSError, AttributeError):
            self.job = None

    def _read(self):
        paths = [self.cache, self.bundled]
        candidates = []
        if self.development:
            paths.insert(0, self.development / "catalogue.json")
        for path in paths:
            try:
                if path.stat().st_size > CATALOGUE_LIMIT:
                    continue
                data = validate_catalogue(json.loads(path.read_text("utf-8")))
                if self.development and path == self.development / "catalogue.json":
                    return data
                candidates.append(data)
            except (OSError, ValueError, TypeError):
                continue
        if candidates:
            # App updates must not be shadowed by an older cached catalogue.
            return max(candidates, key=lambda data: datetime.fromisoformat(data["generated_at"]).timestamp() if data.get("generated_at") else 0)
        return {"format": "reticom-routing-catalogue-v1", "valhalla_version": GRAPH_VERSION, "regions": []}

    def snapshot(self):
        with self.lock:
            catalogue = self._read()
            installed = {p["id"]: p for p in self.store.listing()["packs"]}
            entries = []
            for region in catalogue["regions"]:
                pack = installed.get(region["id"])
                state = "available" if pack is None else "installed" if pack.get("catalogue_sha256") == region["sha256"] else "update_available"
                if state != "installed" and not catalogue.get("published", False) and not self.development:
                    state = "unpublished"
                entries.append({**region, "state": state})
            return {"regions": entries, "job": dict(self.job) if self.job else None,
                    "error": self.catalogue_error, "generated_at": catalogue.get("generated_at"),
                    "development": bool(self.development and (self.development / "catalogue.json").exists())}

    def _set(self, **values):
        with self.lock:
            self.job = {**(self.job or {}), **values}
            now = time.monotonic()
            if "status" in values or now - self.last_write >= 1:
                atomic_json(self.state, self.job)
                self.last_write = now

    def refresh(self):
        with self.lock:
            now = time.monotonic()
            if now - self.refresh_at < 60:
                return
            self.refresh_at = now
        try:
            with self.opener(CATALOGUE_URL) as response:
                raw = response.read(CATALOGUE_LIMIT + 1)
            if len(raw) > CATALOGUE_LIMIT:
                raise RoutingError("Catalogue download is too large")
            data = validate_catalogue(json.loads(raw))
            with self.lock:
                atomic_json(self.cache, data)
                self.catalogue_error = ""
        except (OSError, ValueError) as exc:
            with self.lock:
                self.catalogue_error = "Could not refresh the catalogue. Saved regions and the built-in list remain available."

    def start(self, region_id):
        if self.task and not self.task.done():
            raise RoutingError("Finish or cancel the current download first", "download_busy", 409)
        catalogue = self._read()
        entry = next((p for p in catalogue["regions"] if p["id"] == region_id), None)
        if entry is None:
            raise RoutingError("Unknown catalogue region", "not_found", 404)
        if not catalogue.get("published", False) and not self.development:
            raise RoutingError("These packs have not been published yet. Refresh after the data release is available.", "unpublished", 409)
        if not self.store.engine.available():
            raise RoutingError("This build has no offline routing engine", "engine_unavailable", 409)
        if shutil.disk_usage(self.store.root).free < entry["bytes"] + entry["expanded_bytes"] + 128 * 1024**2:
            raise RoutingError("Not enough free space for this region", "storage_full", 409)
        self.cancelled.clear()
        self._set(id=region_id, name=entry["name"], status="downloading", downloaded_bytes=0,
                  total_bytes=entry["bytes"], error="")
        self.task = asyncio.create_task(asyncio.to_thread(self._download, dict(entry)))

    def cancel(self):
        with self.lock:
            if self.job and self.job["status"] in ("downloading", "verifying"):
                self.cancelled.set()
            elif self.job and self.job["status"] == "installing":
                raise RoutingError("Installation is finishing; it cannot be cancelled now", "installing", 409)

    def _check_cancelled(self):
        if self.cancelled.is_set():
            raise InterruptedError("Download cancelled")

    def _download(self, entry):
        try:
            local = self.development / urlparse(entry["url"]).path.rsplit("/", 1)[1] if self.development else None
            source = local.open("rb") if local and local.is_file() else self.opener(entry["url"])
            digest, received = hashlib.sha256(), 0
            with source, self.partial.open("wb") as output:
                while chunk := source.read(256 * 1024):
                    self._check_cancelled()
                    received += len(chunk)
                    if received > entry["bytes"]:
                        raise RoutingError("Downloaded region is larger than its catalogue entry")
                    digest.update(chunk)
                    output.write(chunk)
                    self._set(downloaded_bytes=received)
            self._set(status="verifying")
            self._check_cancelled()
            if received != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                raise RoutingError("Incomplete or damaged download. Retry this region.")
            with self.lock:
                self._check_cancelled()
                self._set(status="installing")
            self.store.install(self.partial, expected=entry)
            self._set(status="ready", error="")
        except InterruptedError:
            self._set(status="cancelled", error="")
        except Exception as exc:
            self._set(status="error", error=str(exc) if isinstance(exc, RoutingError) else "Download failed. Check your connection and retry.")
        finally:
            self.partial.unlink(missing_ok=True)

    async def close(self):
        self.cancelled.set()
        if self.task:
            await self.task


def add_catalogue_routes(router, store, data_dir):
    # A developer's prepared packs can be exercised locally before publishing.
    import os
    development = Path(os.environ["RETICOM_ROUTING_CATALOGUE_DIR"]).resolve() if os.environ.get("RETICOM_ROUTING_CATALOGUE_DIR") else None
    manager = RoutingCatalogue(store, development=development)
    router.downloads = manager

    @router.get("/api/offline-routing/catalogue")
    async def catalogue():
        return await asyncio.to_thread(manager.snapshot)

    @router.post("/api/offline-routing/catalogue/refresh")
    async def refresh(request: Request):
        if request.headers.get("content-type") != "application/json":
            raise HTTPException(415, "Use application/json")
        async with manager.refresh_lock:
            await asyncio.to_thread(manager.refresh)
        return await asyncio.to_thread(manager.snapshot)

    @router.post("/api/offline-routing/catalogue/{region_id}/download")
    async def download(region_id: str, request: Request):
        if request.headers.get("content-type") != "application/json":
            raise HTTPException(415, "Use application/json")
        try:
            manager.start(region_id)
            return {"accepted": True}
        except RoutingError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/api/offline-routing/catalogue/cancel")
    async def cancel(request: Request):
        if request.headers.get("content-type") != "application/json":
            raise HTTPException(415, "Use application/json")
        try:
            manager.cancel()
            return {"accepted": True}
        except RoutingError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
