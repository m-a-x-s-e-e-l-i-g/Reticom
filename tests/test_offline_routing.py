import hashlib
import json
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from retium.offline_routing import OfflineRouting, RoutingError, engine_config, offline_routing_router


class Engine:
    def __init__(self):
        self.calls = []
        self.enabled = True
        self.result = {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": [[4, 51], [4.1, 51.1]]}}]}

    def available(self): return self.enabled
    def close(self): pass
    def route(self, config, request):
        self.calls.append((config, request))
        return self.result


def pack(tmp_path, **overrides):
    data = b"test graph for validation tests only"
    files = {"tiles/2/000/001.gph": hashlib.sha256(data).hexdigest()}
    manifest = {"format": "reticom-routing-v1", "valhalla_version": "3.8.3", "id": "test-region",
                "name": "Test region", "source": "unit test", "created_at": "2026-09-07T00:00:00Z",
                "bounds": [3, 50, 5, 52], "profiles": ["walking", "driving"], "files": files, **overrides}
    archive = tmp_path / "test.retiroute"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        for name in manifest["files"]:
            bundle.writestr(name, data)
    return archive


@pytest.fixture
def store(tmp_path):
    return OfflineRouting(tmp_path / "routing", Engine())


def test_pack_import_replace_delete(tmp_path, store):
    result = store.install(pack(tmp_path))
    assert result["packs"][0]["name"] == "Test region"
    first = store._directory("test-region")
    store.install(pack(tmp_path, name="Updated"))
    assert not first.exists()
    assert len(store.listing()["packs"]) == 1
    assert store.listing()["packs"][0]["name"] == "Updated"
    assert OfflineRouting(store.root, Engine()).listing() == store.listing()
    assert store.delete("test-region")["packs"] == []


@pytest.mark.parametrize("overrides", [
    {"id": "../escape"}, {"bounds": [5, 50, 3, 52]}, {"bounds": [3, 50, 500, 52]},
    {"bounds": [True, 50, 5, 52]}, {"bounds": [3, 50, float("nan"), 52]},
    {"profiles": ["driving"]}, {"valhalla_version": "3.0.0"},
    {"files": {"../../escape": "0" * 64}}, {"files": {}}, {"name": []},
])
def test_reject_invalid_packs(tmp_path, store, overrides):
    with pytest.raises(RoutingError): store.install(pack(tmp_path, **overrides))
    assert store.listing()["packs"] == []


def test_failed_replacement_keeps_previous(tmp_path, store):
    store.install(pack(tmp_path))
    original = store._directory("test-region")
    with pytest.raises(RoutingError, match="checksum"):
        store.install(pack(tmp_path, files={"tiles/2/000/001.gph": "0" * 64}))
    assert store._directory("test-region") == original
    assert not list(store.root.glob(".import-*"))


def test_no_config_or_symlinks_can_be_injected(tmp_path, store):
    archive = pack(tmp_path)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("valhalla.json", '{"mjolnir":{"tile_url":"https://example.com"}}')
    with pytest.raises(RoutingError, match="unexpected"):
        store.install(archive)
    archive = pack(tmp_path)
    # Duplicate names cannot shadow validated entries.
    with zipfile.ZipFile(archive, "a") as bundle:
        with pytest.warns(UserWarning): bundle.writestr("manifest.json", "{}")
    with pytest.raises(RoutingError, match="duplicate"):
        store.install(archive)


def test_profiles_and_geometry_bounds(tmp_path, store):
    store.install(pack(tmp_path))
    for profile, costing in [("walking", "pedestrian"), ("driving", "auto")]:
        result = store.route([4, 51], [4.1, 51.1], profile)
        assert result["source"] == "offline"
        assert store.engine.calls[-1][1]["costing"] == costing
        assert store.engine.calls[-1][1]["shape_format"] == "geojson"
    store.engine.result["routes"][0]["geometry"]["coordinates"] = [[4, 51], [20, 51], [4.1, 51.1]]
    with pytest.raises(RoutingError) as exc: store.route([4, 51], [4.1, 51.1], "walking")
    assert exc.value.code == "no_route"


def test_local_alternatives_are_requested_and_stay_inside_downloaded_pack(tmp_path, store):
    store.install(pack(tmp_path))
    store.engine.result["routes"].append({"geometry":{"type":"LineString","coordinates":[[4,51],[8,51],[4.1,51.1]]}})
    result=store.route([4,51],[4.1,51.1],"driving",True)
    assert store.engine.calls[-1][1]["alternates"] == 2
    assert len(result["routes"]) == 1


def test_coverage_and_engine_failures_never_invoke_engine(store):
    with pytest.raises(RoutingError) as exc: store.route([4, 51], [4.1, 51.1], "walking")
    assert exc.value.code == "outside_coverage"
    store.engine.enabled = False
    with pytest.raises(RoutingError) as exc: store.route([4, 51], [4.1, 51.1], "walking")
    assert exc.value.code == "engine_unavailable"
    assert store.engine.calls == []


@pytest.mark.parametrize("origin,profile", [(None, "walking"), ([float("inf"), 1], "walking"), (["4", 51], "walking"), ([4, 51], "truck"), ([False, 1], "driving")])
def test_bad_route_inputs(store, origin, profile):
    with pytest.raises(RoutingError): store.route(origin, [4.1, 51.1], profile)


def test_no_remote_tile_config(tmp_path):
    config = engine_config(tmp_path)
    assert config["mjolnir"]["tile_dir"] == str(tmp_path.resolve())
    assert "tile_url" not in json.dumps(config)
    assert "http" not in json.dumps(config)
    assert config["loki"]["actions"] == ["route"]


def test_api_works_without_team_or_reticulum(tmp_path):
    app = FastAPI()
    router = offline_routing_router(tmp_path / "device")
    router.store.engine = Engine()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/offline-routing").json()["packs"] == []
        assert client.post("/api/offline-routing/import", content=pack(tmp_path).read_bytes(), headers={"content-type": "text/plain"}).status_code == 415
        result = client.post("/api/offline-routing/import", content=pack(tmp_path).read_bytes(), headers={"content-type": "application/octet-stream"})
        assert result.status_code == 200
        request = {"origin": [4, 51], "target": [4.1, 51.1], "profile": "walking"}
        assert client.post("/api/offline-routing/route", json=request).json()["source"] == "offline"
        request["target"] = [9, 55]
        result = client.post("/api/offline-routing/route", json=request)
        assert result.status_code == 409 and result.json()["code"] == "outside_coverage"
        assert client.post("/api/offline-routing/route", content="[]").status_code == 422
        assert client.delete("/api/offline-routing/test-region").status_code == 200


def test_bounded_import(tmp_path, store, monkeypatch):
    monkeypatch.setattr("retium.offline_routing.MAX_EXPANDED", 1)
    with pytest.raises(RoutingError, match="storage"): store.install(pack(tmp_path))


def test_native_adapter_is_offline_and_keeps_jni_symbols():
    source = Path("android/app/src/main/java/com/valhalla/valhalla/ValhallaKotlin.java").read_text()
    assert "createActor(configPath, null)" in source
    assert "synchronized String routeJson" in source
    assert "deleteActor(handle)" in source
