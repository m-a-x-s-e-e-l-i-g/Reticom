import json

import pytest

from retium.offline_maps import OfflineMapError, OfflineMapStore, tile_coordinates


def test_tile_coordinates_limits_large_downloads():
    with pytest.raises(OfflineMapError, match="Zoom in"):
        tile_coordinates({"west": -10, "south": 40, "east": 10, "north": 55}, 14)


def test_downloaded_pack_serves_tiles_without_network(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/planet"):
            return json.dumps({"tiles": ["https://tiles.openfreemap.org/test/{z}/{x}/{y}.pbf"]}).encode(), "application/json"
        return b"tile-or-font", "application/x-protobuf"

    store = OfflineMapStore(tmp_path / "maps", fetch=fetch)
    pack = store.create({"west": -1, "south": -1, "east": 1, "north": 1}, 0)
    result = store.download(pack["id"])

    assert result["status"] == "ready"
    assert result["downloaded_tiles"] == 1
    payload, cached = store.tile(0, 0, 0)
    assert payload == b"tile-or-font"
    assert cached is True
    assert len(calls) == 4  # TileJSON, two font ranges, one vector tile.


def test_delete_ready_pack(tmp_path):
    store = OfflineMapStore(tmp_path / "maps", fetch=lambda _: (b"{}", "application/json"))
    pack = store.create({"west": 4.7, "south": 51.5, "east": 4.8, "north": 51.6}, 1, "Breda")

    deleted = store.delete(pack["id"])

    assert deleted["name"] == "Breda"
    assert store.list() == []


def test_rejects_invalid_tile_and_pack_paths(tmp_path):
    store = OfflineMapStore(tmp_path / "maps", fetch=lambda _: (b"tile", "application/x-protobuf"))

    with pytest.raises(FileNotFoundError):
        store.tile(2, -1, 0)
    with pytest.raises(FileNotFoundError):
        store.delete("..")
