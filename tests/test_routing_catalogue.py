import asyncio
import hashlib
import io
import json
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from retium.offline_routing import OfflineRouting, RoutingError, offline_routing_router
from retium.routing_catalogue import RoutingCatalogue, allowed_url, validate_catalogue, CATALOGUE_URL
from test_offline_routing import Engine, pack


def setup(tmp_path, opener=None):
    archive = pack(tmp_path)
    raw = archive.read_bytes()
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("manifest.json"))
    entry = {k: manifest[k] for k in ("id", "name", "bounds", "profiles", "created_at", "source")}
    entry.update(country="Netherlands", bytes=len(raw), expanded_bytes=4096, data_date="2026-09-06",
                 sha256=hashlib.sha256(raw).hexdigest(),
                 url="https://github.com/m-a-x-s-e-e-l-i-g/Reticom/releases/download/routing-data-test/test.retiroute")
    data = {"format": "reticom-routing-catalogue-v1", "valhalla_version": "3.8.3", "published": True, "regions": [entry]}
    bundled = tmp_path / "catalogue.json"
    bundled.write_text(json.dumps(data))
    store = OfflineRouting(tmp_path / "device", Engine())
    return RoutingCatalogue(store, bundled=bundled, opener=opener or (lambda url: io.BytesIO(raw))), entry, raw


def test_download_verifies_installs_and_survives_restart(tmp_path):
    manager, entry, _ = setup(tmp_path)
    assert manager.snapshot()["regions"][0]["state"] == "available"
    async def run():
        manager.start(entry["id"])
        await manager.task
    asyncio.run(run())
    assert manager.job["status"] == "ready"
    assert manager.snapshot()["regions"][0]["state"] == "installed"
    assert manager.store.listing()["packs"][0]["data_date"] == "2026-09-06"
    assert not manager.partial.exists()
    reloaded = RoutingCatalogue(manager.store, bundled=manager.bundled)
    assert reloaded.snapshot()["regions"][0]["state"] == "installed"


@pytest.mark.parametrize("transform", [lambda b: b[:-2], lambda b: b + b"extra", lambda b: b[:-1] + bytes([b[-1] ^ 1])])
def test_bad_download_preserves_working_pack(tmp_path, transform):
    manager, entry, raw = setup(tmp_path)
    manager.store.install(pack(tmp_path))
    original = manager.store._directory(entry["id"])
    manager.opener = lambda url: io.BytesIO(transform(raw))
    async def run():
        manager.start(entry["id"])
        await manager.task
    asyncio.run(run())
    assert manager.job["status"] == "error"
    assert manager.store._directory(entry["id"]) == original
    assert not manager.partial.exists()


def test_cancellation_and_single_download(tmp_path):
    manager, entry, raw = setup(tmp_path)
    class CancelReader(io.BytesIO):
        def read(self, count=-1):
            manager.cancel()
            return super().read(count)
    manager.opener = lambda url: CancelReader(raw)
    async def run():
        manager.start(entry["id"])
        with pytest.raises(RoutingError, match="current download"):
            manager.start(entry["id"])
        await manager.task
    asyncio.run(run())
    assert manager.job["status"] == "cancelled"
    assert manager.store.listing()["packs"] == []


def test_wrong_region_even_with_valid_archive_hash_is_rejected(tmp_path):
    manager, entry, _ = setup(tmp_path)
    data = json.loads(manager.bundled.read_text())
    data["regions"][0]["id"] = "different-region"
    manager.bundled.write_text(json.dumps(data))
    async def run():
        manager.start("different-region")
        await manager.task
    asyncio.run(run())
    assert manager.job["status"] == "error"
    assert manager.store.listing()["packs"] == []


@pytest.mark.parametrize("url", [
    "http://github.com/m-a-x-s-e-e-l-i-g/Reticom/releases/download/x/a.retiroute",
    "https://127.0.0.1/data", "file:///etc/passwd", "https://github.com.evil.test/x",
    "https://github.com/another/repo/releases/download/x/a", "https://user@github.com/m-a-x-s-e-e-l-i-g/Reticom/releases/download/x/a",
    "https://github.com/m-a-x-s-e-e-l-i-g/Reticom/releases/download/%2e%2e/a", "https://github.com:1234/m-a-x-s-e-e-l-i-g/Reticom/releases/download/x/a",
])
def test_rejects_untrusted_urls(url):
    with pytest.raises((RoutingError, ValueError)): allowed_url(url)


def test_redirects_only_to_github_release_storage():
    assert allowed_url(CATALOGUE_URL) == CATALOGUE_URL
    assert allowed_url("https://release-assets.githubusercontent.com/github-production-release-asset/x?token=signature", redirect=True)
    with pytest.raises(RoutingError): allowed_url("https://127.0.0.1/private", redirect=True)
    with pytest.raises(RoutingError): allowed_url("https://release-assets.githubusercontent.com/asset")


def test_offline_catalogue_and_bad_refresh_keep_known_list(tmp_path):
    manager, entry, _ = setup(tmp_path, opener=lambda url: io.BytesIO(b"not json"))
    manager.refresh()
    assert manager.snapshot()["regions"][0]["id"] == entry["id"]
    assert "Could not refresh" in manager.snapshot()["error"]


def test_catalogue_validation_and_update_state(tmp_path):
    manager, entry, _ = setup(tmp_path)
    manager.store.install(pack(tmp_path))
    assert manager.snapshot()["regions"][0]["state"] == "update_available"
    data = json.loads(manager.bundled.read_text())
    data["regions"].append(dict(entry))
    with pytest.raises(RoutingError): validate_catalogue(data)
    data["regions"] = [{**entry, "profiles": ["driving"]}]
    with pytest.raises(RoutingError): validate_catalogue(data)


def test_interrupted_state_and_unknown_region(tmp_path):
    manager, entry, _ = setup(tmp_path)
    manager.state.write_text(json.dumps({"status": "downloading", "id": entry["id"]}))
    loaded = RoutingCatalogue(manager.store, bundled=manager.bundled)
    assert loaded.job["status"] == "error"
    with pytest.raises(RoutingError, match="Unknown"): manager.start("../../data")


def test_unpublished_catalogue_cannot_start_public_download(tmp_path):
    manager, entry, _ = setup(tmp_path)
    data = json.loads(manager.bundled.read_text())
    data["published"] = False
    manager.bundled.write_text(json.dumps(data))
    assert manager.snapshot()["regions"][0]["state"] == "unpublished"
    with pytest.raises(RoutingError, match="not been published"):
        manager.start(entry["id"])
    data["published"] = "false"
    with pytest.raises(RoutingError, match="publication state"):
        validate_catalogue(data)


def test_new_bundle_is_not_shadowed_by_old_cache(tmp_path):
    manager, _, _ = setup(tmp_path)
    data = json.loads(manager.bundled.read_text())
    data["generated_at"] = "2026-09-01T00:00:00+00:00"
    manager.cache.write_text(json.dumps(data))
    data["generated_at"] = "2026-09-07T00:00:00+00:00"
    manager.bundled.write_text(json.dumps(data))
    assert manager.snapshot()["generated_at"] == data["generated_at"]
    data["generated_at"] = "invalid"
    with pytest.raises(RoutingError, match="catalogue date"):
        validate_catalogue(data)


def test_update_quota_counts_replacement_once(tmp_path, monkeypatch):
    manager, entry, _ = setup(tmp_path)
    manager.store.install(pack(tmp_path))
    size = manager.store.listing()["bytes"]
    monkeypatch.setattr("retium.offline_routing.MAX_STORAGE", size + 1)
    manager.store.install(pack(tmp_path))
    assert len(manager.store.listing()["packs"]) == 1


def test_http_endpoints_are_device_scoped_and_csrf_guarded(tmp_path):
    manager, entry, raw = setup(tmp_path)
    app = FastAPI()
    router = offline_routing_router(tmp_path / "api")
    router.downloads.bundled = manager.bundled
    router.downloads.opener = lambda url: io.BytesIO(raw)
    router.downloads.store.engine = Engine()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/offline-routing/catalogue").json()["regions"][0]["id"] == entry["id"]
        path = f"/api/offline-routing/catalogue/{entry['id']}/download"
        assert client.post(path, content="{}").status_code == 415
        assert client.post(path, json={}).status_code == 200
        client.portal.call(router.downloads.close)
        assert client.post("/api/offline-routing/catalogue/cancel", content="{}").status_code == 415
