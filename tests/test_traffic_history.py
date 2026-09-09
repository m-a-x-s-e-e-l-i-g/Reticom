import asyncio
import gzip
import io
import json
import pytest
from retium.traffic_history import TrafficHistory, aircraft_trace
from retium.traffic import LiveTraffic
from retium.intel_packs import IntelPackStore


def test_provider_gzip_is_decoded_and_decompressed_size_is_bounded(monkeypatch):
    from retium.traffic_history import fetch_trace
    payload = {"icao":"abcdef","timestamp":100000,"trace":[]}
    body = gzip.compress(json.dumps(payload).encode())
    class Opener:
        def open(self,*args,**kwargs):
            return io.BytesIO(body)
    monkeypatch.setattr("retium.traffic_history.build_opener",lambda *args:Opener())
    assert fetch_trace("abcdef") == payload
    body = gzip.compress(b" " * 4_000_001)
    with pytest.raises(ValueError,match="Decompressed"):
        fetch_trace("abcdef")


def test_history_survives_restart_deduplicates_and_excludes_expired_positions(tmp_path):
    now = [100000]
    path = tmp_path / "history.sqlite3"
    history = TrafficHistory(path, clock=lambda:now[0])
    history.add("vessels","123456789",now[0],5,52)
    history.add("vessels","123456789",now[0]+1,5,52)
    history.add("vessels","123456789",now[0]+30,5.01,52)
    history.close()
    history = TrafficHistory(path, clock=lambda:now[0])
    assert len(history.points("vessels","123456789")) == 2
    assert history.points("vessels","987654321") == []
    now[0] += 86500
    assert history.points("vessels","123456789") == []
    history.close()


def test_trace_validates_identity_coordinates_and_flight_leg_breaks():
    payload = {"icao":"abcdef","timestamp":100000,"trace":[[0,52,5,0,0,0,2],[10,52.01,5.01],[20,99,5],[30,52,float('nan')]]}
    points = aircraft_trace(payload,"abcdef",100100)
    assert len(points) == 2 and points[0]["breakBefore"]
    with pytest.raises(ValueError):
        aircraft_trace(payload,"123456",100100)


def test_route_caches_aircraft_fetch_and_keeps_vessel_history_local(tmp_path,monkeypatch):
    store = IntelPackStore(tmp_path)
    store.enabled.update(["flights","vessels"])
    calls = []
    def fetch(identity):
        calls.append(identity)
        return {"icao":identity,"timestamp":100000,"trace":[[0,52,5],[10,52.01,5.01]]}
    monkeypatch.setattr("retium.traffic_history.fetch_trace",fetch)
    async def exercise():
        traffic = LiveTraffic(store,clock=lambda:100100)
        assert len((await traffic.route("flights","abcdef"))["points"]) == 2
        await traffic.route("flights","abcdef")
        assert calls == ["abcdef"]
        traffic.history.add("vessels","123456789",100090,5,52)
        result = await traffic.route("vessels","123456789")
        assert len(result["points"]) == 1 and not result["complete"]
        assert calls == ["abcdef"]
        with pytest.raises(ValueError):
            await traffic.route("flights","../secret")
        await traffic.close()
    asyncio.run(exercise())
