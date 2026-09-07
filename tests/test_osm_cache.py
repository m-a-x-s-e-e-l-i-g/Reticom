import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from retium.intel_packs import IntelPackStore, OSM_DAILY_LIMITS, CATALOGUE, contains
from retium.intel_sources import OSM_AREA_LIMIT_KM2
from retium.osm_cache import covers, buffered_bounds

NOW = 1780000000
VIEW = (4.7, 51.5, 4.8, 51.6)


def data(identifier="way/1", geometry=None, **extra):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": identifier,
            "geometry": geometry or {"type": "Point", "coordinates": [4.75, 51.55]}, "properties": {}}],
            "source_bytes": 500, **extra}


def save_area(store, pack, bounds, value, at=NOW):
    store._write(store.cache_dir / f"{pack}-{len(list(store.cache_dir.glob('*.json')))}.json",
                 {"bounds": list(bounds), "fetched_at": at, "credential_hash": store._credential_hash(pack), "data": value})


def test_pan_margin_reuses_nearby_view_and_survives_restart(tmp_path):
    calls = []
    def loader(*args):
        calls.append(args)
        return data()
    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: NOW)
    assert store.load("military", VIEW, 14)["status"] == "fresh"
    expanded = calls[0][1]
    assert contains(expanded, VIEW) and expanded != VIEW
    moved = (4.71, 51.501, 4.81, 51.601)
    assert store.load("military", moved, 14)["status"] == "cached"
    restarted = IntelPackStore(tmp_path, loader=loader, clock=lambda: NOW)
    assert restarted.load("military", moved, 14)["status"] == "cached"
    assert len(calls) == 1


def test_overlapping_legacy_areas_cover_view_without_fetch_and_deduplicate(tmp_path):
    def unexpected(*args):
        pytest.fail("Already downloaded area must not fetch")
    store = IntelPackStore(tmp_path, loader=unexpected, clock=lambda: NOW)
    save_area(store, "military", (4.7, 51.5, 4.76, 51.6), data())
    save_area(store, "military", (4.74, 51.5, 4.8, 51.6), data())
    result = store.load("military", VIEW, 14)
    assert result["status"] == "cached"
    assert result["coverage_complete"] is True
    assert result["cache_areas"] == 2
    assert len(result["features"]) == 1
    assert not store.usage_path.exists()


def test_union_with_hole_is_not_complete_and_offline_shows_partial_cache(tmp_path):
    def offline(*args):
        raise OSError("offline")
    store = IntelPackStore(tmp_path, loader=offline, clock=lambda: NOW)
    save_area(store, "military", (4.7, 51.5, 4.74, 51.6), data("left", {"type": "Point", "coordinates": [4.72, 51.55]}))
    save_area(store, "military", (4.76, 51.5, 4.8, 51.6), data("right", {"type": "Point", "coordinates": [4.78, 51.55]}))
    result = store.load("military", VIEW, 14)
    assert result["status"] == "stale" and result["coverage_complete"] is False
    assert "Missing areas are not known to be empty" in result["note"]
    assert len(result["features"]) == 2
    assert json.loads(store.usage_path.read_text())["packs"]["military"]["requests"] == 1


def test_clipped_trail_fragments_survive_union_without_connecting_gaps(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda: NOW)
    left = [[4.7, 51.55], [4.74, 51.55]]
    right = [[4.76, 51.55], [4.8, 51.55]]
    save_area(store, "trails", (4.7, 51.5, 4.76, 51.6), data("route/1", {"type": "LineString", "coordinates": left}))
    save_area(store, "trails", (4.74, 51.5, 4.8, 51.6), data("route/1", {"type": "LineString", "coordinates": right}))
    result = store.load("trails", VIEW, 14)
    assert result["status"] == "cached"
    assert len(result["features"]) == 1
    assert result["features"][0]["geometry"] == {"type": "MultiLineString", "coordinates": [left, right]}


def test_newer_complete_area_does_not_resurrect_deleted_feature(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda: NOW)
    save_area(store, "military", VIEW, data(), at=NOW-60)
    save_area(store, "military", VIEW, {"type": "FeatureCollection", "features": []}, at=NOW-1)
    assert store.load("military", VIEW, 14)["features"] == []


def test_deleted_point_inside_newer_partial_area_is_not_resurrected_by_union(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda: NOW)
    save_area(store, "military", (4.7, 51.5, 4.77, 51.6), data(), at=NOW-60)
    save_area(store, "military", (4.74, 51.5, 4.8, 51.6), {"type": "FeatureCollection", "features": []}, at=NOW-1)
    result = store.load("military", VIEW, 14)
    assert result["coverage_complete"]
    assert result["features"] == []


def test_stale_half_of_union_cannot_become_fresh(tmp_path):
    store = IntelPackStore(tmp_path, loader=lambda *a: (_ for _ in ()).throw(OSError()), clock=lambda: NOW)
    old = NOW-CATALOGUE["military"]["ttl_seconds"]-1
    save_area(store, "military", (4.7, 51.5, 4.76, 51.6), data(), at=old)
    save_area(store, "military", (4.74, 51.5, 4.8, 51.6), data())
    result = store.load("military", VIEW, 14)
    assert result["status"] == "stale"
    assert result["fetched_at"] == old


def test_truncated_area_does_not_claim_coverage_and_is_refined_without_margin(tmp_path):
    calls, now = [], [NOW]
    def loader(pack, bounds, credentials):
        calls.append(bounds)
        return data(truncated=len(calls) == 1)
    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: now[0])
    assert store.load("trails", VIEW, 14)["truncated"]
    now[0] += 31
    assert store.load("trails", VIEW, 14)["status"] == "fresh"
    assert calls[1] == VIEW
    assert store.load("trails", VIEW, 14)["status"] == "cached"
    assert len(calls) == 2


def test_exact_truncated_result_reuses_with_warning_but_not_for_new_view(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda: NOW)
    save_area(store, "trails", VIEW, data(truncated=True))
    result = store.load("trails", VIEW, 14)
    assert result["status"] == "cached" and result["truncated"]
    assert not result["coverage_complete"]
    assert not store.usage_path.exists()


@pytest.mark.parametrize("bounds", [(179.8, 70, 180, 70.1), (-180, -80, -179.9, -79.9), (0, 0, .44, .44), (0, 89.8, 1.99, 90)])
def test_padding_respects_world_span_area_and_original_view(bounds):
    result = buffered_bounds(bounds, OSM_AREA_LIMIT_KM2["trails"])
    assert contains(result, bounds)
    assert -180 <= result[0] < result[2] <= 180
    assert -90 <= result[1] < result[3] <= 90
    assert result[2]-result[0] <= 2 and result[3]-result[1] <= 2


def test_rectangle_union_handles_corner_holes_and_multiple_rows():
    assert covers((0, 0, 2, 2), [(0, 0, 1, 2), (1, 0, 2, 1), (1, 1, 2, 2)])
    assert not covers((0, 0, 2, 2), [(0, 0, 1, 2), (1, 0, 2, 1)])
    assert not covers((0, 0, 2, 2), [])


@pytest.mark.parametrize("field", ["requests", "bytes"])
def test_hiking_limit_cannot_exhaust_military_budget(tmp_path, field):
    now = [NOW]
    store = IntelPackStore(tmp_path, loader=lambda *a: data(), clock=lambda: now[0])
    usage = store._osm_usage()
    usage["packs"]["trails"][field] = OSM_DAILY_LIMITS["trails"][field]
    store._write(store.usage_path, usage)
    denied = store.load("trails", VIEW, 14)
    assert denied["status"] == "error" and "separate budgets" in denied["error"]
    assert store.load("military", VIEW, 14)["status"] == "fresh"
    saved = json.loads(store.usage_path.read_text())
    assert saved["packs"]["military"] == {"requests": 1, "bytes": 500}
    assert saved["packs"]["trails"][field] == OSM_DAILY_LIMITS["trails"][field]


def test_legacy_shared_usage_migrates_without_reset_and_new_day_resets(tmp_path):
    now = [NOW]
    store = IntelPackStore(tmp_path, clock=lambda: now[0])
    save_area(store, "trails", VIEW, data(source_bytes=10_000_000))
    save_area(store, "military", VIEW, data(source_bytes=100_000))
    store._write(store.usage_path, {"day": int(NOW//86400), "requests": 4, "bytes": 10_100_000, "last_request": NOW-31})
    assert store._reserve_osm("military") is None
    usage = json.loads(store.usage_path.read_text())
    assert usage["version"] == 2
    assert usage["packs"]["trails"] == {"requests": 3, "bytes": 10_000_000}
    assert usage["packs"]["military"] == {"requests": 4, "bytes": 100_000}
    restarted = IntelPackStore(tmp_path, clock=lambda: now[0])
    assert restarted._reserve_osm("trails") is not None  # persisted shared throttle
    now[0] += 86400
    assert restarted._reserve_osm("trails") is None
    assert restarted._osm_usage()["packs"]["military"]["requests"] == 0


def test_concurrent_overlapping_pans_spend_one_download(tmp_path):
    calls = []
    def loader(*args):
        calls.append(args)
        return data()
    store = IntelPackStore(tmp_path, loader=loader, clock=lambda: NOW)
    views = [VIEW, (4.701, 51.501, 4.801, 51.601), (4.702, 51.502, 4.802, 51.602)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda view: store.load("military", view, 14), views))
    assert len(calls) == 1
    assert all(result["status"] in {"cached", "fresh"} for result in results)
