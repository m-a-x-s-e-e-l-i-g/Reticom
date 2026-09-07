import asyncio
from contextlib import asynccontextmanager
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from retium.intel_packs import IntelPackStore, intel_router
from retium.traffic import LiveTraffic, VesselRegistry, aircraft_profile, flight_feature, parse_flights, flight_query, flight_queries, vessel_kind, vessel_view_allowed, validate_traffic_filters

NOW = 1780000000
BOUNDS = (4.5, 51.4, 5, 51.8)


def aircraft(**overrides):
    return {"hex": "abcdef", "lat": 51.6, "lon": 4.8, "seen_pos": 1, "category": "A3", "flight": "KLM123", "gs": 100, "alt_baro": 10000, "track": 90, **overrides}


def ais(kind="PositionReport", **fields):
    return {"MessageType": kind, "MetaData": {"MMSI": 244123456, "ShipName": "Fixture vessel"},
            "Message": {kind: {"UserID": 244123456, "Valid": True, **fields}}}


def test_aircraft_timestamp_units_classifications_and_no_false_civilian_claim():
    result = flight_feature(aircraft(dbFlags=1), NOW, NOW)
    assert result["properties"]["affiliation"] == "military"
    assert result["properties"]["altitude_m"] == 3048
    assert result["properties"]["position_time"] == NOW-1
    assert flight_feature(aircraft(), NOW, NOW)["properties"]["affiliation"] == "commercial"
    assert "unverified" in flight_feature(aircraft(), NOW, NOW)["properties"]["classification_note"]
    unknown = flight_feature(aircraft(dbFlags=0, flight="PHABC", category="A1"), NOW, NOW)
    assert unknown["properties"]["affiliation"] == "unknown"
    features, _ = parse_flights({"now": NOW*1000, "ac": [aircraft(category="A7")]}, NOW)
    assert features[0]["properties"]["traffic_type"] == "helicopter"


@pytest.mark.parametrize("row", [aircraft(lat=91), aircraft(lon=float("nan")), aircraft(seen_pos=121), aircraft(seen_pos=None), aircraft(hex="bad"), aircraft(lat=True), None])
def test_aircraft_invalid_or_old_positions_are_not_faked(row):
    assert flight_feature(row, NOW, NOW) is None
    assert flight_feature(aircraft(), NOW-200, NOW) is None


def test_regional_aircraft_query_does_not_silently_clip_world():
    assert flight_query((-180, -80, 180, 80)) is None
    assert flight_query(BOUNDS).startswith("https://api.adsb.lol/v2/point/")


def test_wide_traffic_views_use_small_markers_but_keep_hard_provider_caps():
    regional = (-5, 48, 10, 53)
    assert 1 < len(flight_queries(regional)) <= 4
    assert flight_queries((-30, 20, 30, 70)) == ()
    assert vessel_view_allowed(regional)
    assert not vessel_view_allowed((-10, 40, 10, 55))


def test_vessel_static_enrichment_does_not_refresh_position_or_claim_ownership():
    registry = VesselRegistry()
    registry.ingest(ais(Latitude=51.6, Longitude=4.8, Sog=3.5, Cog=91, TrueHeading=511), NOW)
    feature = registry.features(BOUNDS, NOW)[0]
    assert feature["properties"]["traffic_type"] == "unknown"
    assert feature["properties"]["heading"] == 91
    assert feature["properties"]["course"] == 91
    registry.ingest(ais("ShipStaticData", Type=70, Name="Cargo fixture", Destination="Test port"), NOW+20)
    props = registry.features(BOUNDS, NOW+30)[0]["properties"]
    assert props["traffic_type"] == "cargo" and props["affiliation"] == "commercial"
    assert props["position_time"] == NOW
    registry.ingest(ais("ShipStaticData", Type=35), NOW+590)
    assert "restricted" in registry.features(BOUNDS, NOW+591)[0]["properties"]["classification_note"]
    assert registry.features(BOUNDS, NOW+601) == []
    assert vessel_kind(55) == ("service", "unknown"), "law enforcement is not automatically military"


def test_class_b_vessel_data_arrives_in_separate_parts():
    registry = VesselRegistry()
    registry.ingest(ais("StaticDataReport", PartNumber=0, ReportA={"Name": "Sailing fixture"}), NOW)
    registry.ingest(ais("StaticDataReport", PartNumber=1, ReportB={"ShipType": 36, "CallSign": "FIX"}), NOW)
    assert registry.features(BOUNDS, NOW) == [], "static metadata never invents a position"
    registry.ingest(ais("StandardClassBPositionReport", Latitude=51.6, Longitude=4.8, Sog=102.3, Cog=360, TrueHeading=511), NOW)
    props = registry.features(BOUNDS, NOW)[0]["properties"]
    assert props["traffic_type"] == "sailing"
    assert props["speed_knots"] is None and props["heading"] is None
    assert props["course"] is None
    assert props["callsign"] == "FIX"


def test_vessel_course_and_bow_heading_are_separate_for_animation():
    registry = VesselRegistry()
    registry.ingest(ais(Latitude=51.6, Longitude=4.8, Sog=4, Cog=90, TrueHeading=180, NavigationalStatus=5), NOW)
    props = registry.features(BOUNDS, NOW)[0]["properties"]
    assert props["heading"] == 180
    assert props["course"] == 90
    assert props["navigation_status"] == 5


def test_bad_ais_positions_and_unrelated_frames_are_ignored():
    registry = VesselRegistry()
    for frame in [None, {}, ais(Latitude=91, Longitude=181), ais(Valid=False, Latitude=51.6, Longitude=4.8), {"MessageType": "PositionReport", "Message": []}]:
        registry.ingest(frame, NOW)
    assert registry.features(BOUNDS, NOW) == []


def test_settings_preserve_filters_and_never_return_keys(tmp_path):
    store = IntelPackStore(tmp_path)
    result = store.update({"traffic_filters": {"flights": {"affiliation": "military", "type": "helicopter"}}, "credentials": {"aisstream_key": "private-fixture-key"}})
    assert "private-fixture-key" not in json.dumps(result)
    assert result["credentials"]["aisstream_key"] is True
    assert IntelPackStore(tmp_path).traffic_filters["flights"]["type"] == "helicopter"
    assert not store.is_enabled("flights") and not store.is_enabled("vessels")
    with pytest.raises(ValueError):
        validate_traffic_filters({"vessels": {"type": "tank"}})
    with pytest.raises(ValueError):
        validate_traffic_filters({"flights": {"query": "x\n"}})


def test_flights_use_short_cache_expire_stale_and_stop_after_disable(tmp_path):
    async def scenario():
        now, calls = [NOW], []
        def fetch(url):
            calls.append(url)
            if len(calls) > 1:
                raise RuntimeError("sensitive provider error")
            return {"now": NOW*1000, "ac": [aircraft()]}
        store = IntelPackStore(tmp_path)
        traffic = LiveTraffic(store, clock=lambda: now[0], flight_fetch=fetch)
        assert (await traffic.load("flights", BOUNDS, 12, "map"))["status"] == "disabled"
        assert not calls
        store.update({"enabled": ["flights"]})
        assert len((await traffic.load("flights", BOUNDS, 12, "map"))["features"]) == 1
        await traffic.load("flights", BOUNDS, 12, "map")
        assert len(calls) == 1
        now[0] += 11
        failed = await traffic.load("flights", BOUNDS, 12, "map")
        assert failed["status"] == "stale" and "sensitive" not in json.dumps(failed)
        now[0] += 121
        assert (await traffic.load("flights", BOUNDS, 12, "map"))["features"] == []
        store.update({"enabled": []})
        await traffic.settings_changed()
        assert traffic.flight_data == []
        await traffic.close()
    asyncio.run(scenario())


def test_ais_one_compressed_socket_with_leases_and_immediate_key_revocation(tmp_path):
    async def scenario():
        sent, connections, closed = [], [], []
        queue = asyncio.Queue()
        class Socket:
            async def send(self, raw):
                sent.append(json.loads(raw))
            async def recv(self):
                return await queue.get()
        @asynccontextmanager
        async def connector(url, **options):
            connections.append((url, options))
            try:
                yield Socket()
            finally:
                closed.append(True)
        store = IntelPackStore(tmp_path)
        traffic = LiveTraffic(store, clock=lambda: NOW, connector=connector)
        store.update({"enabled": ["vessels"]})
        assert (await traffic.load("vessels", BOUNDS, 12, "one"))["status"] == "needs_key"
        assert traffic.task is None
        store.update({"credentials": {"aisstream_key": "fixture-key"}})
        await traffic.load("vessels", BOUNDS, 12, "one")
        await traffic.load("vessels", BOUNDS, 12, "two")
        queue.put_nowait(json.dumps({"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}))
        queue.put_nowait(json.dumps(ais(Latitude=51.6, Longitude=4.8)).encode())
        for _ in range(20):
            await asyncio.sleep(.005)
            if traffic.registry.records:
                break
        result = await traffic.load("vessels", BOUNDS, 12, "one")
        assert result["status"] == "fresh" and len(result["features"]) == 1
        assert len(connections) == 1 and connections[0][1]["compression"] == "deflate"
        assert sent[0]["BoundingBoxes"] == [[[51.4, 4.5], [51.8, 5]]]
        assert "fixture-key" not in json.dumps(result)
        traffic.release("one")
        assert "two" in traffic.leases
        store.update({"credentials": {"aisstream_key": ""}})
        await traffic.settings_changed()
        assert closed and traffic.task is None and not traffic.registry.records
        await traffic.close()
    asyncio.run(scenario())


def test_traffic_endpoints_are_available_without_reticulum_or_team(tmp_path):
    store = IntelPackStore(tmp_path)
    traffic = LiveTraffic(store, clock=lambda: NOW, flight_fetch=lambda _url: {"now": NOW*1000, "ac": [aircraft()]})
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=store, traffic=traffic))
    with TestClient(app) as client:
        client.patch("/api/intel-packs/settings", json={"enabled": ["flights", "vessels"]})
        query = "?west=4.5&south=51.4&east=5&north=51.8&zoom=12&client=test"
        assert len(client.get("/api/intel-packs/live/flights"+query).json()["features"]) == 1
        assert client.get("/api/intel-packs/live/vessels"+query).json()["status"] == "needs_key"
        assert client.get("/api/intel-packs/live/flights"+query.replace("west=4.5", "west=nan")).status_code == 422
        assert client.delete("/api/intel-packs/live/vessels/lease/test", headers={"origin": "https://evil.example"}).status_code == 403


def test_aircraft_details_are_lazy_bounded_and_photo_is_proxied(tmp_path):
    calls = []
    def metadata(identity):
        calls.append(identity)
        return {"response": {"aircraft": {"type": "A320-232", "manufacturer": "Airbus", "icao_type": "A320", "registration": "PH-TEST", "registered_owner": "Fixture Air", "url_photo": "https://image.airport-data.com/aircraft/photo.jpg", "url_photo_thumbnail": "https://image.airport-data.com/aircraft/thumb.jpg"}}}
    traffic = LiveTraffic(IntelPackStore(tmp_path), clock=lambda: NOW, aircraft_fetch=metadata,
                          photo_fetch=lambda url: {"data": b"fixture-photo", "content_type": "image/jpeg"})
    app = FastAPI()
    app.include_router(intel_router(tmp_path, store=traffic.store, traffic=traffic))
    with TestClient(app) as client:
        profile = client.get("/api/intel-packs/live/flights/abcdef/profile")
        assert profile.status_code == 200
        assert profile.json() == {"model": "A320-232", "manufacturer": "Airbus", "type_code": "A320", "registration": "PH-TEST", "owner": "Fixture Air", "photo_available": True, "photo_source_url": "https://image.airport-data.com/aircraft/photo.jpg", "source": "ADSBDB public aircraft database"}
        image = client.get("/api/intel-packs/live/flights/abcdef/thumbnail")
        assert image.status_code == 200 and image.content == b"fixture-photo" and image.headers["content-type"] == "image/jpeg"
        assert calls == ["abcdef"], "profile metadata is cached across the thumbnail request"
        assert client.get("/api/intel-packs/live/flights/not-hex/profile").status_code == 422
    assert aircraft_profile({"response": {"aircraft": {"url_photo_thumbnail": "https://evil.example/photo.jpg"}}})["photo_url"] is None


def test_flights_share_budget_without_starving_second_view(tmp_path):
    async def scenario():
        now, calls = [NOW], []
        def fetch(url):
            calls.append(url)
            return {"now": now[0]*1000, "ac": []}
        store = IntelPackStore(tmp_path)
        store.update({"enabled": ["flights"]})
        traffic = LiveTraffic(store, clock=lambda: now[0], flight_fetch=fetch)
        second = (5, 51.4, 5.5, 51.8)
        await traffic.load("flights", BOUNDS, 12, "one")
        assert (await traffic.load("flights", second, 12, "two"))["status"] == "waiting"
        now[0] += 11
        await traffic.load("flights", BOUNDS, 12, "one")
        assert (await traffic.load("flights", second, 12, "two"))["status"] == "fresh"
        assert calls == [flight_query(BOUNDS), flight_query(second)]
        now[0] += 11
        await traffic.load("flights", BOUNDS, 12, "one")
        assert calls[-1] == flight_query(BOUNDS)
        await traffic.close()
    asyncio.run(scenario())


def test_tiny_aircraft_viewport_pan_keeps_recent_global_positions(tmp_path):
    async def scenario():
        now = [NOW]
        store = IntelPackStore(tmp_path)
        store.update({"enabled": ["flights"]})
        traffic = LiveTraffic(store, clock=lambda: now[0], flight_fetch=lambda _url: {"now": now[0]*1000, "ac": [aircraft()]})
        first = await traffic.load("flights", BOUNDS, 12, "map")
        assert len(first["features"]) == 1
        # A small pan has a new exact cache key, but still covers the aircraft.
        panned = (4.6, 51.4, 5.1, 51.8)
        snapshot = await traffic.load("flights", panned, 12, "map")
        assert snapshot["status"] == "stale"
        assert len(snapshot["features"]) == 1
        assert "recent aircraft" in snapshot["note"]
        await traffic.close()
    asyncio.run(scenario())


def test_disabled_inflight_fetch_cannot_restore_old_traffic(tmp_path):
    import threading
    async def scenario():
        started, finish = threading.Event(), threading.Event()
        def fetch(_url):
            started.set()
            assert finish.wait(5)
            return {"now": NOW*1000, "ac": [aircraft()]}
        store = IntelPackStore(tmp_path)
        store.update({"enabled": ["flights"]})
        traffic = LiveTraffic(store, clock=lambda: NOW, flight_fetch=fetch)
        task = asyncio.create_task(traffic.load("flights", BOUNDS, 12, "one"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            store.update({"enabled": []})
            await traffic.settings_changed()
            store.update({"enabled": ["flights"]})
        finally:
            finish.set()
        assert (await task)["features"] == []
        assert not traffic.flight_views and not traffic.flight_data
        await traffic.close()
    asyncio.run(scenario())


def test_ais_leases_expire_and_bad_static_parts_do_not_reclassify(tmp_path):
    store = IntelPackStore(tmp_path)
    now = [NOW]
    traffic = LiveTraffic(store, clock=lambda: now[0])
    traffic.leases["map"] = (BOUNDS, NOW+35)
    assert traffic.boxes() == [BOUNDS]
    now[0] += 35
    assert traffic.boxes() == []
    registry = VesselRegistry()
    registry.ingest(ais("StaticDataReport", PartNumber=True, ReportB={"Valid": False, "ShipType": 35}), NOW)
    registry.ingest(ais(Latitude=51.6, Longitude=4.8), NOW)
    assert registry.features(BOUNDS, NOW)[0]["properties"]["traffic_type"] == "unknown"
    old = ais(Latitude=51.7, Longitude=4.9)
    old["MetaData"]["time_utc"] = "2020-01-01 00:00:00 +0000 UTC"
    registry.ingest(old, NOW)
    assert registry.features(BOUNDS, NOW)[0]["geometry"]["coordinates"] == [4.8, 51.6]
