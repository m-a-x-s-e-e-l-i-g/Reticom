import json
import struct
from collections import OrderedDict
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from retium.intel_packs import IntelPackStore, intel_router
from retium.intel_sources import TRANSITOUS_TRIP_URL, TRANSITOUS_URL, TRANSIT_POSITION_URLS
from retium.navigation import encode_coordinates
from retium.transit import PublicTransit, _merge, _transitous_segments, decode_vehicles


NOW = 1_800_000_000
BOSTON = (-71.2, 42.2, -71.0, 42.4)
MALTA = (14.2, 35.8, 14.6, 36.1)


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def field(tag, value):
    if isinstance(value, int):
        return varint(tag << 3) + varint(value)
    if isinstance(value, float):
        return varint(tag << 3 | 5) + struct.pack("<f", value)
    if isinstance(value, str):
        value = value.encode()
    return varint(tag << 3 | 2) + varint(len(value)) + value


def entity(identity, lon, timestamp):
    trip = field(1, "trip_1") + field(5, "64")
    location = field(1, 42.3) + field(2, lon)
    vehicle = field(1, identity)
    return field(2, field(1, f"e-{identity}") + field(4, field(1, trip) + field(2, location) + field(5, timestamp) + field(8, vehicle)))


def transport_snapshot():
    return (field(1, field(1, "2.0") + field(3, NOW)) + entity("one", -71.10, NOW)
            + entity("one", -71.12, NOW - 10) + entity("stale", -71.13, NOW - 200))


def segment(dataset, trip, *, realtime=True, mode="BUS"):
    return {"realTime": realtime, "mode": mode,
            "departure": datetime.fromtimestamp(NOW - 30, timezone.utc).isoformat(),
            "arrival": datetime.fromtimestamp(NOW + 30, timezone.utc).isoformat(),
            "polyline": encode_coordinates([[-71.18, 42.3], [-71.06, 42.3]]),
            "trips": [{"tripId": f"20270115_12:00_{dataset}_{trip}", "displayName": "Route 64"}]}


def test_gtfs_snapshot_keeps_newest_position_and_drops_stale_reports():
    (report,) = decode_vehicles(transport_snapshot(), "mbta", NOW)
    assert report["point"] == pytest.approx([-71.10, 42.3])
    assert report["mode"] == "bus"
    assert report["trip"] == "trip_1"
    with pytest.raises(ValueError, match="Truncated"):
        decode_vehicles(transport_snapshot()[:-1], "mbta", NOW)


def test_combines_live_feeds_and_falls_back_without_schedule_only_trips():
    rows = [segment("us-ma-mbta", "trip_1"), segment("us-flixbus", "other"),
            segment("us-flixbus", "other"), segment("us-ma-mbta", "scheduled", realtime=False)]
    calls = []
    down = set()

    def fetch(url, **options):
        provider = "mbta" if url == TRANSIT_POSITION_URLS["mbta"] else "transitous"
        assert provider == "mbta" or url.startswith(f"{TRANSITOUS_URL}?")
        calls.append(provider)
        if provider in down:
            raise ValueError("Provider unavailable")
        return transport_snapshot() if provider == "mbta" else json.dumps(rows).encode()

    transit = PublicTransit(fetch=fetch, clock=lambda: NOW)
    result = transit.load(BOSTON, 12)
    gps, estimate = result["features"]
    assert result["status"] == "fresh"
    assert gps["properties"]["title"] == "Route 64"
    assert gps["properties"]["estimated"] is False
    assert gps["properties"]["trip_ref"] == rows[0]["trips"][0]["tripId"]
    assert estimate["properties"]["estimated"] is True
    assert estimate["geometry"]["coordinates"] == pytest.approx([-71.12, 42.3])
    assert transit.load(BOSTON, 12) == result
    assert calls == ["mbta", "transitous"]

    for provider, expected in [("mbta", [True, True]), ("transitous", [False])]:
        down.clear()
        down.add(provider)
        fallback = PublicTransit(fetch=fetch, clock=lambda: NOW).load(BOSTON, 12)
        assert fallback["status"] == "stale"
        assert [f["properties"]["estimated"] for f in fallback["features"]] == expected


def test_positions_advance_along_cached_timing_and_trip_route_has_precision_six():
    row = segment("us-flixbus", "vehicle")
    reference = row["trips"][0]["tripId"]
    points = [[-71.180001, 42.3], [-71.060001, 42.3]]
    calls = []
    now = NOW

    def fetch(url, **options):
        calls.append(url.partition("?")[0])
        if url.startswith(TRANSITOUS_TRIP_URL):
            return json.dumps({"legs": [{"tripId": reference, "legGeometry": {
                "points": encode_coordinates(points, precision=6)}}]}).encode()
        if url == TRANSIT_POSITION_URLS["mbta"]:
            return transport_snapshot()
        return json.dumps([row]).encode()

    transit = PublicTransit(fetch=fetch, clock=lambda: now)
    initial = transit.load(BOSTON, 12)["features"][-1]
    now += 10
    next_fix = transit.load(BOSTON, 12)["features"][-1]
    assert initial["geometry"]["coordinates"][0] == pytest.approx(-71.12)
    assert next_fix["geometry"]["coordinates"][0] == pytest.approx(-71.10)
    assert calls.count(TRANSITOUS_URL) == 1
    assert calls.count(TRANSIT_POSITION_URLS["mbta"]) == 2
    assert transit.route(reference)["points"] == points
    assert transit.route(reference)["points"] == points
    assert calls.count(TRANSITOUS_TRIP_URL) == 1
    with pytest.raises(ValueError, match="Invalid"):
        transit.route("invalid")


def test_known_route_mode_survives_a_view_without_matching_trip_metadata():
    (report,) = decode_vehicles(transport_snapshot(), "openov", NOW)
    segments = _transitous_segments([segment("nl-openov", "trip_1")])
    modes = OrderedDict()
    (first,) = _merge([report], segments, BOSTON, NOW, modes)
    assert first["mode"] == "bus"
    (next_fix,) = _merge([{**report, "trip": "trip_2"}], [], BOSTON, NOW, modes)
    assert next_fix["mode"] == "bus"
    assert "trip_ref" not in next_fix


def test_unrelated_trip_geometry_falls_back_to_matching_route_segment():
    row = segment("us-flixbus", "vehicle")
    reference = row["trips"][0]["tripId"]

    def fetch(url, **options):
        if url.startswith(TRANSITOUS_TRIP_URL):
            return b'{"legs":[{"tripId":"differentTrip"}]}'
        if url == TRANSIT_POSITION_URLS["mbta"]:
            return transport_snapshot()
        return json.dumps([row]).encode()

    transit = PublicTransit(fetch=fetch, clock=lambda: NOW)
    transit.load(BOSTON, 12)
    assert transit.route(reference) == {"points": [[-71.18, 42.3], [-71.06, 42.3]], "label": "Route segment"}


def test_shared_map_endpoint_is_opt_in_and_uses_standard_empty_message_in_malta(tmp_path, monkeypatch):
    transit = PublicTransit(fetch=lambda *args, **kwargs: b"[]", clock=lambda: NOW)
    assert transit.load(MALTA, 5)["status"] == "zoom_in"
    monkeypatch.setattr("retium.intel_packs.PublicTransit", lambda: transit)
    store = IntelPackStore(tmp_path)
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store))
    with TestClient(app) as client:
        url = "/api/intel-packs/transit?west=14.2&south=35.8&east=14.6&north=36.1&zoom=12"
        assert client.get(url).json()["status"] == "disabled"
        assert client.get("/api/intel-packs/transit/route?trip=invalid").status_code == 404
        store.update({"enabled": ["transit"]})
        assert client.get("/api/intel-packs/transit/route?trip=invalid").status_code == 422
        result = client.get(url).json()
    assert result["status"] == "uncovered"
    assert result["features"] == []
    assert result["note"] == "No live public transport reports in this view."
