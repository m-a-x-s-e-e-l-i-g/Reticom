from __future__ import annotations

import json
import math
import shutil
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable


OPENFREEMAP_TILEJSON = "https://tiles.openfreemap.org/planet"
OPENFREEMAP_FONTS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf"
OFFLINE_MAP_MAX_ZOOM = 14
OFFLINE_MAP_MAX_TILES = 2500
FONT_RANGES = ("0-255", "256-511")
FONT_STACK = "Noto Sans Regular"


class OfflineMapError(ValueError):
    pass


Fetch = Callable[[str], tuple[bytes, str]]


def _default_fetch(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Reticom/0.1 (offline field maps)"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read(), response.headers.get_content_type()


def _tile_x(longitude: float, zoom: int) -> int:
    scale = 1 << zoom
    return min(scale - 1, max(0, int(math.floor((longitude + 180.0) / 360.0 * scale))))


def _tile_y(latitude: float, zoom: int) -> int:
    latitude = min(85.05112878, max(-85.05112878, latitude))
    radians = math.radians(latitude)
    scale = 1 << zoom
    value = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale
    return min(scale - 1, max(0, int(math.floor(value))))


def tile_coordinates(bounds: dict[str, float], max_zoom: int) -> list[tuple[int, int, int]]:
    west = float(bounds["west"])
    south = float(bounds["south"])
    east = float(bounds["east"])
    north = float(bounds["north"])
    if not (-180 <= west < east <= 180 and -85.05112878 <= south < north <= 85.05112878):
        raise OfflineMapError("Map area is outside supported world bounds")
    if not 0 <= max_zoom <= OFFLINE_MAP_MAX_ZOOM:
        raise OfflineMapError(f"Detail must be between zoom 0 and {OFFLINE_MAP_MAX_ZOOM}")

    coordinates: list[tuple[int, int, int]] = []
    for zoom in range(max_zoom + 1):
        west_x = _tile_x(west, zoom)
        east_x = _tile_x(east, zoom)
        north_y = _tile_y(north, zoom)
        south_y = _tile_y(south, zoom)
        for x in range(west_x, east_x + 1):
            for y in range(north_y, south_y + 1):
                coordinates.append((zoom, x, y))
                if len(coordinates) > OFFLINE_MAP_MAX_TILES:
                    raise OfflineMapError(
                        f"Area needs more than {OFFLINE_MAP_MAX_TILES} tiles. Zoom in or choose less detail."
                    )
    return coordinates


class OfflineMapStore:
    def __init__(self, root: Path, fetch: Fetch | None = None):
        self.root = root
        self.packs_root = root / "packs"
        self.assets_root = root / "assets"
        self.packs_root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self._fetch = fetch or _default_fetch
        self._lock = threading.RLock()
        self._tile_template: str | None = None
        self._mark_interrupted_downloads()

    def _manifest_path(self, pack_id: str) -> Path:
        if len(pack_id) != 32 or any(character not in "0123456789abcdef" for character in pack_id):
            raise FileNotFoundError("Offline map area not found")
        return self.packs_root / pack_id / "manifest.json"

    def _write_manifest(self, manifest: dict) -> None:
        path = self._manifest_path(manifest["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    def _read_manifest(self, path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _mark_interrupted_downloads(self) -> None:
        for path in self.packs_root.glob("*/manifest.json"):
            manifest = self._read_manifest(path)
            if manifest and manifest.get("status") == "downloading":
                manifest["status"] = "error"
                manifest["error"] = "Download was interrupted. Delete this area and download it again."
                self._write_manifest(manifest)

    def list(self) -> list[dict]:
        packs = [
            manifest
            for path in self.packs_root.glob("*/manifest.json")
            if (manifest := self._read_manifest(path)) is not None
        ]
        return sorted(packs, key=lambda item: int(item.get("created_at", 0)), reverse=True)

    def create(self, bounds: dict[str, float], max_zoom: int, name: str | None = None) -> dict:
        coordinates = tile_coordinates(bounds, int(max_zoom))
        center_lat = (float(bounds["north"]) + float(bounds["south"])) / 2
        center_lon = (float(bounds["east"]) + float(bounds["west"])) / 2
        label = str(name or "").strip()[:40] or f"{abs(center_lat):.2f}°{'N' if center_lat >= 0 else 'S'} · {abs(center_lon):.2f}°{'E' if center_lon >= 0 else 'W'}"
        manifest = {
            "id": uuid.uuid4().hex,
            "name": label,
            "bounds": {key: round(float(bounds[key]), 6) for key in ("west", "south", "east", "north")},
            "max_zoom": int(max_zoom),
            "tile_count": len(coordinates),
            "downloaded_tiles": 0,
            "bytes": 0,
            "status": "queued",
            "created_at": int(time.time()),
            "error": None,
        }
        with self._lock:
            self._write_manifest(manifest)
        return manifest

    def _resolve_tile_template(self) -> str:
        with self._lock:
            if self._tile_template:
                return self._tile_template
        payload, _ = self._fetch(OPENFREEMAP_TILEJSON)
        try:
            document = json.loads(payload.decode("utf-8"))
            template = str(document["tiles"][0])
        except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise OfflineMapError("Map provider returned an invalid tile address") from exc
        if not template.startswith("https://tiles.openfreemap.org/"):
            raise OfflineMapError("Map provider returned an unexpected tile host")
        with self._lock:
            self._tile_template = template
        return template

    def _tile_url(self, zoom: int, x: int, y: int) -> str:
        return self._resolve_tile_template().replace("{z}", str(zoom)).replace("{x}", str(x)).replace("{y}", str(y))

    def _download_tile(self, pack_id: str, coordinate: tuple[int, int, int]) -> int:
        zoom, x, y = coordinate
        payload, _ = self._fetch(self._tile_url(zoom, x, y))
        if not payload:
            raise OfflineMapError("Map provider returned an empty tile")
        path = self.packs_root / pack_id / "tiles" / str(zoom) / str(x) / f"{y}.pbf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return len(payload)

    def _download_font(self, range_name: str) -> int:
        target = self.assets_root / "fonts" / FONT_STACK / f"{range_name}.pbf"
        if target.exists():
            return target.stat().st_size
        target.parent.mkdir(parents=True, exist_ok=True)
        url = OPENFREEMAP_FONTS.format(
            fontstack=urllib.parse.quote(FONT_STACK, safe=""),
            range=range_name,
        )
        payload, _ = self._fetch(url)
        target.write_bytes(payload)
        return len(payload)

    def download(self, pack_id: str) -> dict:
        with self._lock:
            manifest = self._read_manifest(self._manifest_path(pack_id))
            if manifest is None:
                raise FileNotFoundError("Offline map area not found")
            if manifest.get("status") == "downloading":
                raise OfflineMapError("Offline map area is already downloading")
            manifest["status"] = "downloading"
            manifest["error"] = None
            self._write_manifest(manifest)

        coordinates = tile_coordinates(manifest["bounds"], int(manifest["max_zoom"]))
        downloaded = 0
        byte_count = 0
        try:
            for range_name in FONT_RANGES:
                self._download_font(range_name)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="offline-map") as pool:
                futures = [pool.submit(self._download_tile, pack_id, coordinate) for coordinate in coordinates]
                for future in as_completed(futures):
                    byte_count += future.result()
                    downloaded += 1
                    if downloaded % 10 == 0 or downloaded == len(coordinates):
                        with self._lock:
                            manifest["downloaded_tiles"] = downloaded
                            manifest["bytes"] = byte_count
                            self._write_manifest(manifest)
            with self._lock:
                manifest["status"] = "ready"
                manifest["completed_at"] = int(time.time())
                self._write_manifest(manifest)
            return manifest
        except Exception as exc:
            with self._lock:
                manifest["downloaded_tiles"] = downloaded
                manifest["bytes"] = byte_count
                manifest["status"] = "error"
                manifest["error"] = f"Download stopped: {exc}"
                self._write_manifest(manifest)
            raise

    def tile(self, zoom: int, x: int, y: int) -> tuple[bytes, bool]:
        if not 0 <= zoom <= OFFLINE_MAP_MAX_ZOOM:
            raise FileNotFoundError("Map tile is outside supported detail")
        scale = 1 << zoom
        if not 0 <= x < scale or not 0 <= y < scale:
            raise FileNotFoundError("Map tile is outside world bounds")
        for manifest in self.list():
            path = self.packs_root / manifest["id"] / "tiles" / str(zoom) / str(x) / f"{y}.pbf"
            if path.exists():
                return path.read_bytes(), True
        payload, _ = self._fetch(self._tile_url(zoom, x, y))
        return payload, False

    def font(self, fontstack: str, range_name: str) -> tuple[bytes, bool]:
        if fontstack != FONT_STACK or range_name not in FONT_RANGES:
            raise FileNotFoundError("Map font is not available")
        path = self.assets_root / "fonts" / fontstack / f"{range_name}.pbf"
        if path.exists():
            return path.read_bytes(), True
        self._download_font(range_name)
        return path.read_bytes(), False

    def delete(self, pack_id: str) -> dict:
        path = self._manifest_path(pack_id)
        manifest = self._read_manifest(path)
        if manifest is None:
            raise FileNotFoundError("Offline map area not found")
        if manifest.get("status") == "downloading":
            raise OfflineMapError("Wait for the download to finish before deleting this area")
        shutil.rmtree(path.parent)
        return manifest
