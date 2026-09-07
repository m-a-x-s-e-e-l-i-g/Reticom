"""Read-only, bounded local cache of public Mapzen Terrarium elevation tiles."""

from __future__ import annotations

import io
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

ELEVATION_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ELEVATION_MAX_ZOOM = 14
ELEVATION_MAX_TILE_BYTES = 512 * 1024
ELEVATION_CACHE_BYTES = 128 * 1024 * 1024
ELEVATION_CACHE_TILES = 1500
ELEVATION_CACHE_TTL = 30 * 24 * 60 * 60


class ElevationError(ValueError):
    """A request is invalid, unavailable, or contains no usable terrain data."""


def _image_decoder():
    # Elevation is optional: a missing/broken native wheel must not prevent the
    # web server, Reticulum or all the other Intel packs from starting.
    try:
        from PIL import Image
    except (ImportError, OSError) as error:
        raise ElevationError("Elevation decoder is unavailable; install a complete Reticom build with Pillow") from error
    return Image


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ElevationError("The terrain provider returned an unexpected redirect")


def _default_fetch(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Reticom/0.1 (terrain map layer)"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
        data = response.read(ELEVATION_MAX_TILE_BYTES + 1)
        return data, response.headers.get_content_type()


def _coordinates(z: int, x: int, y: int) -> tuple[int, int, int]:
    if any(type(value) is not int for value in (z, x, y)):
        raise ElevationError("Terrain tile coordinates must be integers")
    if not 0 <= z <= ELEVATION_MAX_ZOOM or not 0 <= x < 1 << z or not 0 <= y < 1 << z:
        raise ElevationError("Terrain tile is outside supported world bounds")
    return z, x, y


def _validate_png(data: bytes, content_type: str) -> None:
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ElevationError("The terrain provider did not return a PNG tile")
    if len(data) > ELEVATION_MAX_TILE_BYTES:
        raise ElevationError("The terrain tile exceeds the download limit")
    if content_type.split(";", 1)[0].strip().lower() not in {
        "image/png", "application/octet-stream", "binary/octet-stream",
    }:
        raise ElevationError("The terrain provider returned an unexpected content type")
    Image = _image_decoder()
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (256, 256) or image.mode not in {"RGB", "RGBA"}:
                raise ElevationError("The terrain provider returned an unsupported tile format")
            image.load()
            extrema = image.getextrema()
            if all(maximum == 0 for _minimum, maximum in extrema[:3]):
                raise ElevationError("Elevation is unavailable in this tile")
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ElevationError("The terrain provider returned a damaged PNG tile") from error


class ElevationStore:
    """Terrain cache independent from team data and downloadable basemap packs.

    TTL controls refresh, not deletion: old cached tiles remain usable offline.
    LRU eviction caps both payload bytes and tile count. SQLite auto-vacuum keeps
    deleted cache pages from retaining an unbounded on-disk high-water mark.
    """

    def __init__(
        self,
        data_dir: Path,
        fetch: Callable[[str], tuple[bytes, str]] | None = None,
        *,
        now: Callable[[], float] = time.time,
        max_bytes: int = ELEVATION_CACHE_BYTES,
        max_tiles: int = ELEVATION_CACHE_TILES,
        ttl: int = ELEVATION_CACHE_TTL,
    ):
        self.root = Path(data_dir) / "elevation-cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "tiles.sqlite3"
        self._fetch = fetch or _default_fetch
        self._now = now
        self._max_bytes = max(1, int(max_bytes))
        self._max_tiles = max(1, int(max_tiles))
        self._ttl = max(0, int(ttl))
        self._locks = [threading.Lock() for _ in range(32)]
        self._downloads = threading.BoundedSemaphore(4)
        self._retry_after = 0.0
        with self._connect() as connection:
            connection.execute("PRAGMA auto_vacuum=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tiles ("
                "z INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, "
                "payload BLOB NOT NULL, fetched_at REAL NOT NULL, accessed_at REAL NOT NULL, "
                "PRIMARY KEY (z, x, y))"
            )
            self._evict(connection)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _cached(self, coordinates: tuple[int, int, int]) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, fetched_at FROM tiles WHERE z=? AND x=? AND y=?", coordinates
            ).fetchone()
            if row is None:
                return None
            try:
                _validate_png(row["payload"], "image/png")
            except ElevationError:
                connection.execute("DELETE FROM tiles WHERE z=? AND x=? AND y=?", coordinates)
                return None
            now = self._now()
            connection.execute(
                "UPDATE tiles SET accessed_at=? WHERE z=? AND x=? AND y=?", (now, *coordinates)
            )
            return {
                "data": row["payload"], "content_type": "image/png", "cached": True,
                "stale": now - row["fetched_at"] >= self._ttl, "fetched_at": row["fetched_at"],
            }

    def _evict(self, connection: sqlite3.Connection) -> None:
        count, size = connection.execute("SELECT count(*), coalesce(sum(length(payload)), 0) FROM tiles").fetchone()
        if count <= self._max_tiles and size <= self._max_bytes:
            return
        for row in connection.execute("SELECT z, x, y, length(payload) AS size FROM tiles ORDER BY accessed_at, fetched_at, z, x, y").fetchall():
            connection.execute("DELETE FROM tiles WHERE z=? AND x=? AND y=?", (row["z"], row["x"], row["y"]))
            count -= 1
            size -= row["size"]
            if count <= self._max_tiles and size <= self._max_bytes:
                break

    def tile_result(self, z: int, x: int, y: int) -> dict:
        coordinates = _coordinates(z, x, y)
        # Check before network access or cache validation: an unavailable
        # decoder is not evidence that the user's cached tile is corrupt.
        _image_decoder()
        # Bounded lock striping prevents duplicate simultaneous requests without
        # retaining one lock per tile for every place ever viewed.
        with self._locks[hash(coordinates) % len(self._locks)]:
            cached = self._cached(coordinates)
            if cached is not None and not cached["stale"]:
                return cached
            with self._downloads:
                if self._now() < self._retry_after:
                    if cached is not None:
                        return cached
                    raise ElevationError("Terrain is unavailable offline; this area has not been cached")
                try:
                    data, content_type = self._fetch(ELEVATION_URL.format(z=z, x=x, y=y))
                    _validate_png(data, content_type)
                except (OSError, ValueError) as error:
                    # Avoid making every map tile repeat a long failed network
                    # request while the phone has no Internet connection.
                    if isinstance(error, OSError) and not (
                        isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429
                    ):
                        self._retry_after = self._now() + 30
                    if cached is not None:
                        return cached
                    raise ElevationError("Terrain is currently unavailable; no cached tile for this area") from error
            fetched_at = self._now()
            if len(data) <= self._max_bytes:
                with self._connect() as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?, ?, ?)",
                        (*coordinates, data, fetched_at, fetched_at),
                    )
                    self._evict(connection)
            return {
                "data": data, "content_type": "image/png", "cached": False,
                "stale": False, "fetched_at": fetched_at,
            }

    def tile(self, z: int, x: int, y: int) -> tuple[bytes, str]:
        result = self.tile_result(z, x, y)
        return result["data"], result["content_type"]
