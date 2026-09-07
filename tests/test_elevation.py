import io
import builtins
import importlib.util
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest

from retium.elevation import (
    ELEVATION_MAX_TILE_BYTES, ELEVATION_URL, ElevationError, ElevationStore, _default_fetch, _validate_png,
)


def png(pixel=(128, 100, 128), *, size=(256, 256), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, pixel).save(buffer, format="PNG")
    return buffer.getvalue()


def test_download_uses_fixed_source_and_reuses_cache_across_instances(tmp_path):
    calls = []
    payload = png()
    store = ElevationStore(tmp_path, fetch=lambda url: (calls.append(url) or payload, "image/png"))

    fresh = store.tile_result(4, 8, 5)
    repeated = ElevationStore(tmp_path, fetch=lambda _: pytest.fail("must reuse disk cache")).tile_result(4, 8, 5)

    assert calls == [ELEVATION_URL.format(z=4, x=8, y=5)]
    assert fresh["data"] == repeated["data"] == payload
    assert fresh["cached"] is False
    assert repeated["cached"] is True
    assert repeated["stale"] is False
    assert store.tile(4, 8, 5) == (payload, "image/png")


@pytest.mark.parametrize("coordinates", [(-1, 0, 0), (15, 0, 0), (2, 4, 0), (2, 0, -1), (2, 0, 4), (True, 0, 0), (2.0, 0, 0), ("../", 0, 0)])
def test_invalid_tile_coordinates_never_make_requests(tmp_path, coordinates):
    store = ElevationStore(tmp_path, fetch=lambda _: pytest.fail("invalid tile must not fetch"))
    with pytest.raises(ElevationError):
        store.tile(*coordinates)


def test_expired_cache_refreshes_and_offline_failure_preserves_age(tmp_path):
    clock = [100.0]
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return png(), "image/png"
        raise OSError("offline")

    store = ElevationStore(tmp_path, fetch=fetch, ttl=10, now=lambda: clock[0])
    store.tile(1, 0, 0)
    clock[0] = 120
    stale = store.tile_result(1, 0, 0)
    assert stale["cached"] and stale["stale"]
    assert stale["fetched_at"] == 100
    assert store.tile_result(1, 0, 0) == stale
    with pytest.raises(ElevationError, match="not been cached"):
        store.tile(1, 1, 0)
    assert len(calls) == 2  # Failure cooldown also applies to uncached tiles.


def test_expired_cache_success_replaces_old_elevation(tmp_path):
    clock = [100.0]
    store = ElevationStore(tmp_path, fetch=lambda _: (png((128, int(clock[0]), 0)), "image/png"), ttl=5, now=lambda: clock[0])
    assert store.tile_result(14, 8192, 8192)["data"] == png((128, 100, 0))
    clock[0] += 10
    refreshed = store.tile_result(14, 8192, 8192)
    assert refreshed["data"] == png((128, 110, 0))
    assert refreshed["fetched_at"] == 110
    assert refreshed["cached"] is False




@pytest.mark.parametrize("payload,content_type", [
    (b"not a png", "image/png"),
    (png(), "text/html"),
    (png(size=(512, 512)), "image/png"),
    (png(mode="L", pixel=128), "image/png"),
    (png()[:50], "image/png"),
    (png() + b"x" * ELEVATION_MAX_TILE_BYTES, "image/png"),
    (png((0, 0, 0)), "image/png"),
], ids=["not-png", "wrong-mime", "oversize-dimensions", "wrong-mode", "truncated", "oversize-bytes", "no-data"])
def test_invalid_provider_images_rejected(payload, content_type):
    with pytest.raises(ElevationError):
        _validate_png(payload, content_type)




def test_cache_lru_caps_tile_count_and_bytes(tmp_path):
    clock = [100.0]
    payload = png()
    store = ElevationStore(tmp_path, fetch=lambda _: (payload, "image/png"), max_tiles=2, max_bytes=len(payload) * 2, now=lambda: clock[0])
    store.tile(2, 0, 0)
    clock[0] += 1
    store.tile(2, 1, 0)
    clock[0] += 1
    store.tile(2, 0, 0)  # Touch first tile so second is evicted.
    clock[0] += 1
    store.tile(2, 2, 0)
    with sqlite3.connect(store.database) as connection:
        rows = connection.execute("SELECT x FROM tiles ORDER BY x").fetchall()
        assert rows == [(0,), (2,)]
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 1
    small = ElevationStore(tmp_path, fetch=lambda _: (payload, "image/png"), max_bytes=len(payload), max_tiles=100)
    with sqlite3.connect(small.database) as connection:
        assert connection.execute("SELECT count(*) FROM tiles").fetchone()[0] == 1


def test_corrupt_disk_tile_is_retrieved_again(tmp_path):
    payload = png()
    store = ElevationStore(tmp_path, fetch=lambda _: (payload, "image/png"))
    store.tile(1, 0, 0)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE tiles SET payload=?", (b"broken",))
    assert store.tile_result(1, 0, 0)["cached"] is False


def test_simultaneous_same_tile_requests_only_download_once(tmp_path):
    calls = []
    barrier = threading.Barrier(4)
    store = ElevationStore(tmp_path, fetch=lambda url: (calls.append(url) or png(), "image/png"))

    def get_tile(_):
        barrier.wait()
        return store.tile(3, 2, 1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(get_tile, range(4)))
    assert len(calls) == 1
    assert len(set(results)) == 1


def test_download_read_is_bounded_and_timeout_is_set(monkeypatch):
    class Response:
        headers = type("Headers", (), {"get_content_type": lambda self: "image/png"})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, length):
            assert length == ELEVATION_MAX_TILE_BYTES + 1
            return png()

    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            assert request.full_url == ELEVATION_URL.format(z=0, x=0, y=0)
            return Response()

    monkeypatch.setattr("retium.elevation.urllib.request.build_opener", lambda *args: Opener())
    assert _default_fetch(ELEVATION_URL.format(z=0, x=0, y=0))[1] == "image/png"


def test_missing_pillow_does_not_break_module_or_store_startup(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("No module named 'PIL'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    spec = importlib.util.spec_from_file_location(
        "elevation_without_pillow", Path(__file__).parents[1] / "src/retium/elevation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = module.ElevationStore(tmp_path, fetch=lambda _: pytest.fail("missing decoder must not fetch"))

    with pytest.raises(module.ElevationError, match="decoder is unavailable"):
        store.tile(0, 0, 0)


def test_missing_decoder_preserves_valid_cached_tile(tmp_path, monkeypatch):
    payload = png()
    store = ElevationStore(tmp_path, fetch=lambda _: (payload, "image/png"))
    store.tile(0, 0, 0)

    def unavailable():
        raise ElevationError("Elevation decoder is unavailable")

    monkeypatch.setattr("retium.elevation._image_decoder", unavailable)
    with pytest.raises(ElevationError, match="decoder is unavailable"):
        store.tile(0, 0, 0)
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT payload FROM tiles").fetchone()[0] == payload
