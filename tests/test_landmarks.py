import json
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from retium.landmarks import landmark_router, nearest_landmark
from retium.intel_sources import IntelSourceError


def test_nearest_church_uses_distance_not_provider_order():
    queries = []
    def fetch(url, **kwargs):
        queries.append(parse_qs(kwargs["data"].decode())["data"][0])
        return json.dumps({"elements": [
            {"type": "node", "id": 1, "lat": 50.02, "lon": 4, "tags": {"name": "Far"}},
            {"type": "way", "id": 2, "center": {"lat": 50.001, "lon": 4}, "tags": {"name": "Near"}},
            {"type": "way", "id": 3},
            {"type": "node", "id": 4, "lat": 52, "lon": 4},
        ]}).encode()
    result = nearest_landmark("CHURCH", 50, 4, fetch=fetch)
    assert result["name"] == "Near"
    assert result["coordinates"] == [4, 50.001]
    assert result["distance_m"] == 111
    assert '["building"="church"]' in queries[0]
    assert '["religion"="christian"]' in queries[0]
    assert "around:5000,50,4" in queries[0]


def test_missing_or_partial_landmarks_do_not_produce_a_location():
    assert nearest_landmark("church", 50, 4, fetch=lambda *a, **k: b'{"elements": []}') is None
    with pytest.raises(IntelSourceError):
        nearest_landmark("church", 50, 4, fetch=lambda *a, **k: b'{"remark":"timeout", "elements": []}')


@pytest.mark.parametrize("query,lat,lon", [('church];out;', 50, 4), ('church', float('nan'), 4), ('church', 91, 4)])
def test_invalid_lookup_never_contacts_provider(query, lat, lon):
    def fetch(*args, **kwargs):
        pytest.fail("invalid request reached provider")
    with pytest.raises(ValueError):
        nearest_landmark(query, lat, lon, fetch=fetch)


def test_lookup_route_returns_coordinates_and_handles_offline(monkeypatch):
    app = FastAPI()
    app.include_router(landmark_router())
    client = TestClient(app)
    monkeypatch.setattr("retium.landmarks.nearest_landmark", lambda *a: {"coordinates": [4, 50]})
    assert client.get('/api/map/landmark?q=church&lat=50&lon=4').json()["landmark"]["coordinates"] == [4, 50]
    def offline(*args):
        raise IntelSourceError("Offline")
    monkeypatch.setattr("retium.landmarks.nearest_landmark", offline)
    assert client.get('/api/map/landmark?q=church&lat=50&lon=4').status_code == 503
    assert client.get('/api/map/landmark?q=church&lat=500&lon=4').status_code == 422
