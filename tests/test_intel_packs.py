import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from retium.intel_packs import CATALOGUE, GDACS_TYPES, DEFAULT_GDACS_TYPES, DEFAULT_ENABLED, IntelPackStore, feature_intersects, intel_router, validate_bounds


BBOX = (4.7, 51.5, 4.8, 51.6)


def test_new_defaults_and_explicit_preferences_survive_restart(tmp_path):
    store = IntelPackStore(tmp_path)
    assert store.enabled == set(DEFAULT_ENABLED)
    assert "trails" not in store.enabled  # Always-on basemap, not another provider request.
    assert "DR" not in store.gdacs_types
    assert set(store.gdacs_types) == set(GDACS_TYPES) - {"DR"}
    store.update({"enabled": [], "gdacs_types": ["DR"]})
    restarted = IntelPackStore(tmp_path)
    assert restarted.enabled == set()
    assert restarted.gdacs_types == ("DR",)
    assert restarted.ahn_mode == "dtm"


def test_ahn_mode_is_validated_and_persisted(tmp_path):
    store = IntelPackStore(tmp_path)
    assert store.update({"ahn_mode": "dsm"})["settings"]["ahn_mode"] == "dsm"
    assert IntelPackStore(tmp_path).ahn_mode == "dsm"
    with pytest.raises(ValueError, match="AHN"):
        store.update({"ahn_mode": "point-cloud"})
    assert store.ahn_mode == "dsm"


def test_malta_lidar_tiles_reproject_all_rows_and_only_fetch_inside_coverage(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from urllib.parse import parse_qs, urlsplit

    image = Image.linear_gradient("L").convert("RGBA")
    raw = BytesIO()
    image.save(raw, format="PNG")
    requests = []

    def fetch(url, **options):
        requests.append(url)
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        assert parts.netloc == "malta.coverage.wetransform.eu"
        assert params["crs"] == ["CRS:84"] and options["max_bytes"] == 1_000_000
        return raw.getvalue()

    monkeypatch.setattr("retium.lidar_tiles.fetch_bytes", fetch)
    store = IntelPackStore(tmp_path)
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store))
    base = "/api/intel-packs/lidar/malta"
    with TestClient(app) as client:
        assert client.get(f"{base}/dtm/12/2212/1610.png").status_code == 404
        store.update({"enabled": ["ahn"]})
        assert client.get(f"{base}/dtm/12/0/0.png").status_code == 404
        assert not requests
        for mode in ("dtm", "dsm"):
            response = client.get(f"{base}/{mode}/12/2212/1610.png")
            assert response.status_code == 200
            with Image.open(BytesIO(response.content)) as tile:
                assert tile.size == (256, 256) and tile.getbbox() == (0, 0, 256, 256)
                assert tile.getpixel((120, 240))[0] > tile.getpixel((120, 16))[0]
        assert [urlsplit(url).path for url in requests] == ["/wms/dtm_1m_2018/ows", "/wms/dsm_1m_2018/ows"]


def test_malta_contours_use_numeric_elevation_and_fall_back_to_global_tiles(tmp_path, monkeypatch):
    from io import BytesIO
    from urllib.parse import parse_qs, urlsplit
    from PIL import Image
    from retium.lidar_tiles import malta_terrain_tile

    def coverage(url, **options):
        parts = urlsplit(url)
        assert parts.path == "/dtm_1m_2018/ows"
        subset = parse_qs(parts.query)["subset"]
        (west, east), (south, north) = [map(float, axis[2:-1].split(",")) for axis in subset]
        assert west < 454992.5 < east and south < 3972477.5 < north
        raw = BytesIO()
        Image.new("L", (288, 288), 37).save(raw, format="TIFF", tiffinfo={
            33550: ((east - west) / 288, (north - south) / 288, 0.0),
            33922: (0.0, 0.0, 0.0, west, north, 0.0),
        })
        return raw.getvalue()

    class Terrain:
        def tile_result(self, z, x, y):
            assert (z, x, y) == (14, 8851, 6439)
            image = BytesIO()
            Image.new("RGB", (256, 256), (128, 19, 0)).save(image, format="PNG")
            return {"data": image.getvalue(), "content_type": "image/png", "stale": False, "fetched_at": 1}

    monkeypatch.setattr("retium.lidar_tiles.fetch_bytes", coverage)
    store = IntelPackStore(tmp_path)
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store, elevation=Terrain()))
    url = "/api/intel-packs/elevation/tiles/17/70815/51517.png?malta=true"
    with TestClient(app) as client:
        assert client.get(url).status_code == 404
        store.update({"enabled": ["elevation"]})
        try:
            regional = client.get(url)
            assert regional.status_code == 200
            with Image.open(BytesIO(regional.content)) as image:
                assert image.getpixel((128, 128)) == (128, 37, 0)
            def offline(*args):
                raise OSError("Malta WCS unavailable")
            monkeypatch.setattr("retium.intel_packs.malta_terrain_tile", offline)
            fallback = client.get(url)
            assert fallback.status_code == 200
            assert fallback.headers["cache-control"] == "private, max-age=60"
            with Image.open(BytesIO(fallback.content)) as image:
                assert image.getpixel((128, 128)) == (128, 19, 0)
        finally:
            malta_terrain_tile.cache_clear()


def collection(*coordinates):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": index, "geometry": {"type": "Point", "coordinates": list(point)}, "properties": {"title": "Public source fixture", "event_type": "WF"}}
        for index, point in enumerate(coordinates or [(4.75, 51.55)])
    ]}


@pytest.fixture
def setup_store(tmp_path):
    now = [1_780_000_000]
    calls = []

    def loader(*args, **kwargs):
        calls.append(args)
        return collection()

    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: now[0])
    return store, now, calls


def test_disabled_and_unconfigured_packs_never_fetch(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": []})
    assert store.load("trails", BBOX, 14)["status"] == "disabled"
    store.update({"enabled": ["firms"]})
    assert store.load("firms", BBOX, 14)["status"] == "needs_key"
    assert calls == []


def test_credentials_are_local_and_never_returned(setup_store, tmp_path):
    store, _, _ = setup_store
    result = store.update({"enabled": ["firms"], "credentials": {"firms_key": "secret-test-key"}})
    assert "secret-test-key" not in json.dumps(result)
    assert result["credentials"]["firms_key"] is True
    assert IntelPackStore(tmp_path).catalogue() == result
    store.update({"credentials": {"firms_key": ""}})
    assert store.catalogue()["credentials"]["firms_key"] is False


def test_notams_use_one_local_cache_across_category_changes_and_drop_expired_notices(tmp_path):
    now, calls = [1_780_000_000], []

    def loader(pack, bounds, credentials):
        calls.append((pack, bounds, credentials))
        data = collection()
        data["features"][0]["properties"]["valid_until"] = now[0] + 30
        return data

    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: now[0])
    assert store.load("notams", BBOX, 10)["status"] == "disabled"
    store.update({"enabled": ["notams"]})
    assert store.load("notams", BBOX, 10)["status"] == "needs_key"
    settings = store.update({"credentials": {"bravoecho_key": "secret-fixture-token"}})
    assert "secret-fixture-token" not in json.dumps(settings)
    assert "runway" in settings["settings"]["notam_categories"]
    assert settings["settings"]["notam_view"] == "local"
    assert store.load("notams", BBOX, 5)["status"] == "zoom_in"
    assert store.load("notams", BBOX, 10)["status"] == "fresh"
    with pytest.raises(ValueError, match="NOTAM categories"):
        store.update({"notam_categories": [None]})
    store.update({"notam_categories": ["airspace", "airspace"], "notam_view": "all"})
    assert IntelPackStore(tmp_path).catalogue()["settings"]["notam_categories"] == ["airspace"]
    assert IntelPackStore(tmp_path).catalogue()["settings"]["notam_view"] == "all"
    assert store.load("notams", BBOX, 10)["status"] == "cached"
    now[0] += 30
    assert store.load("notams", BBOX, 10)["features"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("pack,key,days", [("notams", "fixture-host-token", None), ("firms", "fixture_nasa_firms_key", 7)])
def test_shared_layers_use_host_cache_even_when_host_map_layer_is_off(tmp_path, pack, key, days):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()
    calls = []

    def loader(pack_id, bounds, credentials, **kwargs):
        calls.append((pack_id, kwargs))
        entered.set()
        assert release.wait(5)
        return collection()

    store = IntelPackStore(tmp_path, loader=loader)
    assert store.shared_view(pack, BBOX, 10, days)["status"] == "needs_key"
    store.provide_shared_key(pack, key)
    with pytest.raises(ValueError, match="already configured"):
        store.provide_shared_key(pack, "replacement")
    assert store.load(pack, BBOX, 10)["status"] == "disabled"
    assert store.shared_view(pack, BBOX, 10, days)["status"] == "waiting"
    try:
        assert entered.wait(5)
        assert store.shared_view(pack, BBOX, 10, days)["status"] == "waiting"
    finally:
        release.set()
    # Wait for the bounded provider fetch to write its cache before reading it.
    with ThreadPoolExecutor() as executor:
        result = executor.submit(store._load_regional, pack, BBOX, 10, shared=True, firms_days=days).result(timeout=5)
    assert result["status"] == "cached"
    assert store.shared_view(pack, BBOX, 10, days)["features"] == result["features"]
    assert key not in json.dumps(result)
    assert calls == [(pack, {"firms_days": days} if days else {})]


@pytest.mark.parametrize("pack,field", [("notams", "bravoecho_key"), ("firms", "firms_key")])
def test_field_layers_use_team_host_without_saving_a_local_key(tmp_path, monkeypatch, pack, field):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from retium import intel_packs

    host = (object(), "team-destination", False)
    calls = []

    def request(remote, pack_id, data):
        calls.append((remote, pack_id, data))
        return {"configured": True} if "key" in data else {pack_id: {**collection(), "status": "cached"}}

    monkeypatch.setattr(intel_packs, "request_host_intel", request)
    app = FastAPI()
    router = intel_packs.intel_router(tmp_path, intel_host=lambda request: host, local_request=lambda request: request.client.host == "testclient")
    app.include_router(router)
    with TestClient(app) as client:
        root = "/api/intel-packs"
        saved = client.patch(root + "/settings", json={"enabled": [pack], "firms_days": 7}).json()
        assert next(item for item in saved["packs"] if item["id"] == pack)["host_access"] is True
        assert saved["credentials"][field] is False
        payload = {"credentials": {f"host_{field}": "fixture-host-token"}}
        assert client.patch(root + "/settings", json=payload).status_code == 403
        assert not calls
        view = client.get(root + "/" + pack, params=dict(zip(("west", "south", "east", "north", "zoom"), [*BBOX, 10])))
        assert view.json()["status"] == "cached"
        assert calls[0] == (host, pack, {"bounds": list(BBOX), "zoom": 10, **({"firms_days": 7} if pack == "firms" else {})})
        host = (*host[:2], True)
        response = client.patch(root + "/settings", json=payload)
        assert response.status_code == 200
        assert calls[-1] == (host, pack, {"key": "fixture-host-token"})
        assert "fixture-host-token" not in response.text
        assert router.store.catalogue()["credentials"][field] is False


def test_gdacs_filter_defaults_are_backward_compatible_and_persist(tmp_path):
    directory = tmp_path / "intel-packs"
    directory.mkdir()
    (directory / "settings.json").write_text(json.dumps({"enabled": ["gdacs"], "credentials": {}}))
    store = IntelPackStore(tmp_path)
    assert store.catalogue()["settings"]["gdacs_types"] == list(DEFAULT_GDACS_TYPES)
    gdacs = next(pack for pack in store.catalogue()["packs"] if pack["id"] == "gdacs")
    assert [item["id"] for item in gdacs["disaster_types"]] == list(GDACS_TYPES)
    assert all(item["label"] for item in gdacs["disaster_types"])
    changed = store.update({"gdacs_types": ["WF", "EQ", "WF"]})
    assert changed["settings"]["gdacs_types"] == [code for code in GDACS_TYPES if code in {"WF", "EQ"}]
    store.update({"enabled": ["gdacs", "firms"]})
    store.update({"credentials": {"firms_key": "test-key"}})
    restarted = IntelPackStore(tmp_path)
    assert restarted.catalogue()["settings"] == store.catalogue()["settings"]
    assert restarted.catalogue()["settings"]["gdacs_types"] == changed["settings"]["gdacs_types"]
    store.update({"gdacs_types": []})
    assert IntelPackStore(tmp_path).catalogue()["settings"]["gdacs_types"] == []


@pytest.mark.parametrize("selection", [None, "WF", {}, [True], [False], [1], [None], ["wf"], ["UNKNOWN"]])
def test_invalid_gdacs_filters_reject_other_changes_atomically(setup_store, selection):
    store, _, _ = setup_store
    previous = store.catalogue()
    with pytest.raises(ValueError, match="GDACS"):
        store.update({"enabled": ["gdacs"], "credentials": {"firms_key": "test-key"}, "gdacs_types": selection})
    assert store.catalogue() == previous
    assert not store.settings_path.exists()


def gdacs_collection():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "fire-point", "properties": {"disaster_code": "WF"},
         "geometry": {"type": "Point", "coordinates": [4.75, 51.55]}},
        {"type": "Feature", "id": "fire-area", "properties": {"event_type": "WF"},
         "geometry": {"type": "Polygon", "coordinates": [[[4.7, 51.5], [4.8, 51.5], [4.8, 51.6], [4.7, 51.5]]]}},
        {"type": "Feature", "id": "cyclone-track", "properties": {"disaster_code": "TC"},
         "geometry": {"type": "LineString", "coordinates": [[4.6, 51.55], [4.9, 51.55]]}},
        {"type": "Feature", "id": "cyclone-area", "properties": {"disaster_code": "TC"},
         "geometry": {"type": "MultiPolygon", "coordinates": [[[[4.7, 51.5], [4.8, 51.5], [4.8, 51.6], [4.7, 51.5]]]]}},
    ]}


def test_gdacs_filters_points_areas_and_tracks_with_separate_reusable_caches(setup_store):
    store, _, _ = setup_store
    selected_requests = []

    def loader(pack, bounds, credentials, *, gdacs_types):
        selected_requests.append(gdacs_types)
        assert credentials == {}
        return gdacs_collection()

    store.loader = loader
    store.update({"enabled": ["gdacs"], "gdacs_types": ["WF"]})
    first = store.load("gdacs", BBOX, 14)
    assert {feature["id"] for feature in first["features"]} == {"fire-point", "fire-area"}
    assert first["gdacs_types"] == ["WF"]
    store.update({"gdacs_types": ["TC"]})
    second = store.load("gdacs", BBOX, 14)
    assert {feature["id"] for feature in second["features"]} == {"cyclone-track", "cyclone-area"}
    assert second["status"] == "fresh"
    store.update({"gdacs_types": ["WF"]})
    assert store.load("gdacs", BBOX, 14)["status"] == "cached"
    assert selected_requests == [["WF"], ["TC"]]


def test_empty_gdacs_selection_does_not_fetch_or_show_cached_features(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["gdacs"]})
    store.load("gdacs", BBOX, 14)
    store.update({"gdacs_types": []})
    result = store.load("gdacs", BBOX, 14)
    assert result["status"] == "filtered"
    assert result["features"] == []
    assert result["gdacs_types"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("fails", [False, True])
def test_filter_change_during_fetch_never_returns_old_selection(setup_store, fails):
    store, now, _ = setup_store
    store.update({"enabled": ["gdacs"], "gdacs_types": ["WF"]})
    store.load("gdacs", BBOX, 14)
    now[0] += CATALOGUE["gdacs"]["ttl_seconds"] + 1

    def changing_loader(*args, **kwargs):
        store.update({"gdacs_types": ["TC"]})
        if fails:
            raise OSError("offline")
        return gdacs_collection()

    store.loader = changing_loader
    assert store.load("gdacs", BBOX, 14)["features"] == []
    # An old failed in-flight request must not put the new selection on cooldown.
    store.loader = lambda *args, **kwargs: gdacs_collection()
    next_result = store.load("gdacs", BBOX, 14)
    assert next_result["status"] == "fresh"
    assert {feature["id"] for feature in next_result["features"]} == {"cyclone-track", "cyclone-area"}


def test_legacy_point_only_gdacs_cache_is_not_reused(setup_store):
    store, now, calls = setup_store
    store.update({"enabled": ["gdacs"]})
    old_hash = hashlib.sha256(json.dumps({}, sort_keys=True).encode()).hexdigest()
    entry = {"bounds": [-180, -90, 180, 90], "credential_hash": old_hash, "fetched_at": now[0], "data": collection()}
    store._write(store.cache_dir / "gdacs-legacy.json", entry)
    assert store.load("gdacs", BBOX, 14)["status"] == "fresh"
    assert len(calls) == 1


def test_gdacs_filters_api_works_without_a_team(setup_store, tmp_path):
    store, _, calls = setup_store
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store))
    with TestClient(app) as client:
        response = client.patch("/api/intel-packs/settings", json={"enabled": ["gdacs"], "gdacs_types": ["WF"]})
        assert response.status_code == 200
        assert response.json()["settings"]["gdacs_types"] == ["WF"]
        assert client.patch("/api/intel-packs/settings", json={"gdacs_types": ["NO"]}).status_code == 422
        assert client.get("/api/intel-packs").json()["settings"]["gdacs_types"] == ["WF"]
        client.patch("/api/intel-packs/settings", json={"gdacs_types": []})
        result = client.get("/api/intel-packs/gdacs?west=4.7&south=51.5&east=4.8&north=51.6&zoom=14").json()
        assert result["status"] == "filtered"
        assert result["features"] == []
        assert calls == []


@pytest.mark.parametrize("body", [[], {"enabled": ["unknown"]}, {"enabled": "trails"}, {"credentials": {"url": "https://example.com"}}, {"credentials": {"firms_key": "bad\nheader"}}, {"credentials": {"firms_key": None}}])
def test_rejects_invalid_settings_atomically(setup_store, body):
    store, _, _ = setup_store
    previous = store.catalogue()
    with pytest.raises(ValueError):
        store.update(body)
    assert store.catalogue() == previous


def test_bounded_query_and_min_zoom(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["trails"]})
    assert store.load("trails", BBOX, 2)["status"] == "zoom_in"
    assert calls == []
    for bbox in [(float("nan"), 0, 1, 1), (170, 0, -170, 1), (0, 10, 1, 0), (-181, 0, 1, 1)]:
        with pytest.raises(ValueError):
            validate_bounds(bbox)


def test_cache_reuses_containing_viewport(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["trails"]})
    first = store.load("trails", BBOX, 14)
    assert first["status"] == "fresh"
    assert store.load("trails", (4.72, 51.52, 4.78, 51.58), 15)["status"] == "cached"
    assert len(calls) == 1
    assert first["expires_at"] > first["fetched_at"]


def test_firms_daily_snapshot_reappears_immediately_after_zooming_back_out(setup_store):
    store, now, calls = setup_store
    netherlands = (3.1, 50.7, 7.4, 53.7)
    store.update({"enabled": ["firms"], "credentials": {"firms_key": "fixture-key"}})
    overview = store.load("firms", netherlands, 6)
    assert overview["status"] == "fresh"
    assert CATALOGUE["firms"]["ttl_seconds"] == 86400
    # Zooming into one detection and out again stays within the same daily
    # snapshot, so no second provider request is made.
    assert store.load("firms", (4.7, 51.5, 4.9, 51.7), 12)["status"] == "cached"
    now[0] += 6 * 3600
    restored = store.load("firms", netherlands, 6)
    assert restored["status"] == "cached"
    assert restored["features"] == overview["features"]
    assert len(calls) == 1


def test_gdacs_overview_cache_is_reused_but_never_prevents_detail_enrichment(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["gdacs"]})
    assert store.load("gdacs", (-180, -90, 180, 90), 2)["status"] == "fresh"
    assert store.load("gdacs", (-20, -20, 20, 60), 5)["status"] == "cached"
    assert len(calls) == 1
    assert len(store.load("gdacs", BBOX, 14)["features"]) == 1
    assert calls[-1][1] == BBOX
    assert len(calls) == 2
    assert store.load("gdacs", BBOX, 14)["status"] == "cached"
    # Per-view detail caps mean a cached larger view may omit a smaller view's
    # fire outlines. Each new detail area therefore has its own cache identity.
    assert store.load("gdacs", (-1, -1, 1, 1), 14)["features"] == []
    assert calls[0][1] == (-180, -90, 180, 90)
    assert len(calls) == 3


def test_offline_cache_is_stale_not_fresh_and_errors_do_not_leak_keys(setup_store):
    store, now, calls = setup_store
    store.update({"enabled": ["gdacs"]})
    first = store.load("gdacs", BBOX, 14)
    now[0] += CATALOGUE["gdacs"]["ttl_seconds"] + 1

    def unavailable(*args, **kwargs):
        raise RuntimeError("https://provider/api/secret-key/failed")

    store.loader = unavailable
    stale = store.load("gdacs", BBOX, 14)
    assert stale["status"] == "stale"
    assert stale["fetched_at"] == first["fetched_at"]
    assert stale["features"] == first["features"]
    assert "secret-key" not in json.dumps(stale)
    now[0] += 2 * 86400
    assert store.load("gdacs", BBOX, 14)["features"] == []


def test_credential_rotation_never_reuses_old_access_cache(setup_store):
    store, now, calls = setup_store
    store.update({"enabled": ["firms"], "credentials": {"firms_key": "first"}})
    store.load("firms", BBOX, 14)
    store.update({"credentials": {"firms_key": "second"}})
    assert store.load("firms", BBOX, 14)["status"] == "fresh"
    assert len(calls) == 2


def test_disabling_during_fetch_does_not_return_in_flight_features(setup_store):
    store, _, _ = setup_store
    store.update({"enabled": ["gdacs"]})

    def disable_during_fetch(*args, **kwargs):
        store.update({"enabled": []})
        return collection()

    store.loader = disable_during_fetch
    assert store.load("gdacs", BBOX, 14)["features"] == []


def test_disabling_during_failed_fetch_does_not_return_stale_cache(setup_store):
    store, now, _ = setup_store
    store.update({"enabled": ["gdacs"]})
    store.load("gdacs", BBOX, 14)
    now[0] += CATALOGUE["gdacs"]["ttl_seconds"] + 1

    def disable_and_fail(*args, **kwargs):
        store.update({"enabled": []})
        raise OSError("offline")

    store.loader = disable_and_fail
    assert store.load("gdacs", BBOX, 14)["features"] == []


def test_oversized_osm_view_does_not_spend_public_service_budget(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["military"]})
    assert store.load("military", (-10, 40, 10, 60), 14)["status"] == "zoom_in"
    assert calls == []
    assert not store.usage_path.exists()


def test_military_loads_one_level_earlier_with_a_wider_bounded_area(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["trails", "military"]})
    # This is deliberately bigger than the hiking/legacy 2,500 km² cap, but
    # still bounded enough for sparse military polygons and airfield data.
    wider_view = (4.0, 50.0, 5.1, 51.0)
    assert CATALOGUE["military"]["min_zoom"] == 9
    assert store.load("military", wider_view, 9)["status"] == "fresh"
    assert calls[-1][1][2] - calls[-1][1][0] <= 4
    assert store.load("trails", wider_view, 14)["status"] == "zoom_in"


def test_saved_military_view_survives_expiry_restart_and_provider_outage(setup_store, tmp_path):
    store, now, calls = setup_store
    store.update({"enabled": ["military"]})
    saved = store.download("military", BBOX, 14)
    assert saved["saved_offline"] is True
    assert len(calls) == 1
    now[0] += 366 * 86400

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    restarted = IntelPackStore(tmp_path, loader=unavailable, clock=lambda: now[0])
    restored = restarted.load("military", BBOX, 14)
    assert restored["status"] == "cached"
    assert restored["features"] == saved["features"]


def test_worldwide_military_snapshot_loads_before_regional_cooldown(setup_store):
    import sqlite3
    from contextlib import closing

    store, now, calls = setup_store
    store.update({"enabled": ["military"]})
    store.next_fetch["military"] = now[0] + 60
    west, south, east, north = BBOX
    feature = {"type": "Feature", "id": "world-fixture", "geometry": {"type": "Point", "coordinates": [west, south]}}
    with closing(sqlite3.connect(store.military_snapshot)) as db, db:
        db.executescript("CREATE TABLE snapshot(release, built_at); CREATE TABLE features(id, west, south, east, north, size, feature);")
        db.execute("INSERT INTO snapshot VALUES (?, ?)", ("2026-11-18.0", now[0]))
        db.execute("INSERT INTO features VALUES (?, ?, ?, ?, ?, ?, ?)", (feature["id"], west, south, east, north, 1, json.dumps(feature)))
    world = store.load("military", (-180, -85, 180, 85), 2)
    assert world["status"] == "cached" and world["worldwide_snapshot"]
    assert world["features"] == [feature]
    assert store.load("military", (100, -30, 110, -20), 10)["features"] == []
    assert store.download("military", BBOX, 14)["saved_offline"]
    assert calls == []
    station = {**feature, "id": "station", "properties": {"kind": "police_station"}}
    store.loader = lambda *args, **kwargs: {"type": "FeatureCollection", "features": [station]}
    store.next_fetch.clear()
    assert store.load("military", BBOX, 14)["features"] == [feature, station]
    assert store.download("military", BBOX, 14)["saved_offline"]
    with closing(sqlite3.connect(store.military_snapshot)) as db, db:
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO features VALUES (?, ?, ?, ?, ?, ?, ?)", (station["id"], west, south, east, north, 0, json.dumps(station)))
    store.loader = lambda *args, **kwargs: pytest.fail("Updated snapshot must work without a provider request")
    assert store.load("military", BBOX, 14)["features"] == [feature, station]


def test_military_download_endpoint_saves_the_visible_view(setup_store, tmp_path):
    store, _, calls = setup_store
    store.update({"enabled": ["military"]})
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store))
    with TestClient(app) as client:
        response = client.post("/api/intel-packs/military/download", json={"bounds": list(BBOX), "zoom": 14})
    assert response.status_code == 200
    assert response.json()["saved_offline"] is True
    assert len(calls) == 1


def test_terrain_endpoints_recheck_disable_and_report_unavailability(setup_store, tmp_path):
    store, _, _ = setup_store

    class Terrain:

        def tile_result(self, *args):
            store.update({"enabled": []})
            return {"data": b"tile", "content_type": "image/png", "stale": False, "fetched_at": 1}

    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store, elevation=Terrain()))
    with TestClient(app) as client:
        store.update({"enabled": ["elevation"]})
        assert client.get("/api/intel-packs/elevation/point?lat=0&lon=0").status_code == 404
        store.update({"enabled": ["elevation"]})
        assert client.get("/api/intel-packs/elevation/tiles/99/0/0.png").status_code == 404
        assert client.get("/api/intel-packs/elevation/tiles/0/0/0.png").status_code == 404
        relief = client.get("/api/intel-packs/elevation/tiles/7/10/10.png?relief=true")
        assert relief.status_code == 200
        assert relief.content == b"tile"


def test_osm_shared_cooldown_survives_restart(setup_store, tmp_path):
    store, now, calls = setup_store
    store.update({"enabled": ["trails", "military"]})
    assert store.load("trails", BBOX, 14)["status"] == "fresh"
    assert store.load("military", BBOX, 14)["status"] == "deferred"
    restarted = IntelPackStore(tmp_path, loader=store.loader, clock=lambda: now[0])
    waiting = restarted.load("military", BBOX, 14)
    assert waiting["status"] == "deferred" and waiting["error"] is None and not waiting["coverage_complete"]
    now[0] += 31
    assert restarted.load("military", BBOX, 14)["status"] == "fresh"
    assert len(calls) == 2


def test_military_zoom_reuses_download_and_wider_pending_view_keeps_features(setup_store):
    store, now, calls = setup_store
    store.update({"enabled": ["military"]})
    west, south, east, north = BBOX
    dx, dy = (east - west) / 2, (north - south) / 2
    one_step_out = (west - dx * .9, south - dy * .9, east + dx * .9, north + dy * .9)
    wider = (west - dx * 1.5, south - dy * 1.5, east + dx * 1.5, north + dy * 1.5)
    original = store.load("military", BBOX, 14)
    assert original["status"] == "fresh" and original["features"]
    assert store.load("military", one_step_out, 13)["status"] == "cached"
    waiting = store.load("military", wider, 12)
    assert waiting["status"] == "deferred" and waiting["error"] is None
    assert waiting["features"] == original["features"] and len(calls) == 1
    now[0] += 31
    assert store.load("military", wider, 12)["status"] == "fresh"
    assert len(calls) == 2


def test_concurrent_requests_share_one_fetch(setup_store):
    store, _, calls = setup_store
    store.update({"enabled": ["gdacs"]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.load("gdacs", BBOX, 14), range(4)))
    assert len(calls) == 1
    assert all(result["features"] for result in results)


def test_geometry_crossing_bounds_is_not_lost():
    line = {"geometry": {"type": "LineString", "coordinates": [[4.6, 51.55], [4.9, 51.55]]}}
    assert feature_intersects(line, BBOX)
    assert not feature_intersects(line, (-1, -1, 1, 1))


def test_endpoints_work_without_any_team_or_rns(setup_store, tmp_path):
    store, _, _ = setup_store
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store))
    with TestClient(app) as client:
        ids = {pack["id"] for pack in client.get("/api/intel-packs").json()["packs"]}
        assert {"nightlights", "bathymetry", "firms"} <= ids
        assert not ids.intersection({"acled", "worldcover", "sentinel1-insar"})
        assert client.patch("/api/intel-packs/settings", json={"enabled": ["gdacs"]}).status_code == 200
        response = client.get("/api/intel-packs/gdacs?west=4.7&south=51.5&east=4.8&north=51.6&zoom=14")
        assert response.status_code == 200
        assert response.json()["status"] == "fresh"
        assert client.get("/api/intel-packs/elevation/tiles/0/0/0.png").status_code == 404
        assert client.patch("/api/intel-packs/settings", json={"enabled": []}, headers={"origin": "https://evil.example"}).status_code == 403


def test_restarting_ignores_retired_layers_and_credentials(tmp_path):
    retired = ["acled", "worldcover", "sentinel1-insar"]
    path = IntelPackStore(tmp_path).settings_path
    path.write_text(json.dumps({"enabled": [*retired, "nightlights"], "credentials": {"acled_token": "old-token"}}), encoding="utf-8")
    restored = IntelPackStore(tmp_path)
    assert restored.enabled == {"nightlights"}
    assert "acled_token" not in restored.catalogue()["credentials"]
    for layer in retired:
        with pytest.raises(ValueError, match="public feature pack"):
            restored.load(layer, BBOX, 14)


def test_firms_window_persists_and_caches_are_separate(tmp_path):
    calls = []
    def loader(pack, bounds, credentials, **options):
        calls.append(options["firms_days"])
        return collection()
    store = IntelPackStore(tmp_path, loader=loader)
    store.update({"enabled": ["firms"], "credentials": {"firms_key": "fixture-key"}})
    for days in (3, 1, 7, 3):
        store.update({"firms_days": days})
        store.load("firms", BBOX, 14)
    assert calls == [3, 1, 7]
    store.update({"firms_days": 7})
    assert IntelPackStore(tmp_path).catalogue()["settings"]["firms_days"] == 7
    for bad in (True, 0, 2, 8, "7", None):
        with pytest.raises(ValueError): store.update({"firms_days": bad})
    assert store.firms_days == 7


def test_cached_firms_detections_age_out_of_selected_window(tmp_path):
    now = [1788868800]
    def loader(*args, **kwargs):
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [4.85, 52.05]}, "properties": {"observed_at": "2026-09-07T12:30:00Z"}}]}
    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: now[0])
    store.update({"enabled": ["firms"], "credentials": {"firms_key": "fixture-key"}, "firms_days": 1})
    assert len(store.load("firms", (4.8, 52, 4.9, 52.1), 14)["features"]) == 1
    now[0] += 3600
    result = store.load("firms", (4.8, 52, 4.9, 52.1), 14)
    assert result["status"] == "cached"
    assert result["features"] == []


def test_credential_previews_only_expose_four_suffix_characters(tmp_path):
    store = IntelPackStore(tmp_path)
    state = store.update({"credentials": {"firms_key": "private-prefix-abcd", "aishub_key": "short", "aisstream_key": "private-vessel-wxyz"}})
    assert state["credential_previews"] == {"firms_key": "••••••••abcd", "aishub_key": "••••••••", "aisstream_key": "••••••••wxyz"}
    assert "private-prefix" not in json.dumps(state)
    assert "private-vessel" not in json.dumps(state)
    assert "short" not in json.dumps(state)
    assert IntelPackStore(tmp_path).catalogue()["credential_previews"] == state["credential_previews"]
    state = store.update({"credentials": {"firms_key": ""}})
    assert "firms_key" not in state["credential_previews"]
    assert state["credentials"]["firms_key"] is False
