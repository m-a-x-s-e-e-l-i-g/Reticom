"""Public GDACS geometry contracts plus deliberately malformed provider fixtures."""

import json

import pytest

from retium import intel_sources as source


WORLD = (-180, -90, 180, 90)
LOCAL = (4, 51, 6, 53)
SQUARE = [[4.1, 51.1], [5.9, 51.1], [5.9, 52.9], [4.1, 52.9], [4.1, 51.1]]
HOLE = [[4.2, 51.2], [4.3, 51.2], [4.3, 51.3], [4.2, 51.3], [4.2, 51.2]]
HOLE2 = [[5.2, 52.2], [5.3, 52.2], [5.3, 52.3], [5.2, 52.3], [5.2, 52.2]]


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def point(code="WF", event=100, coordinate=None, episode=None):
    props = {"eventtype": code, "eventid": event, "title": "Published alert", "alertlevel": "Orange",
             "todate": "2026-09-07 01:02:03"}
    if episode:
        props["link"] = [{"Key": "details", "Value": f"http://www.gdacs.org/datareport/resources/{code}/{event}/geojson_{event}_{episode}.geojson"}]
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": coordinate or [[5, 52]]}}


def area(code="WF", event=100, geometry=None, label="Affected Area", class_name="Poly_area"):
    # The actual aggregate omits eventid and episodeid for polygon/line features.
    return {"type": "Feature", "properties": {
        "eventtype": code, "link": f"https://www.gdacs.org/report.aspx?eventtype={code}&eventid={event}&episodeid=8",
        "name": "Published area", "alertlevel": "Green", "polygonlabel": label, "Class": class_name,
        "todate": "06 Sep 2026 00:00:00",
    }, "geometry": geometry or {"type": "Polygon", "coordinates": [SQUARE, HOLE]}}


def fetcher(documents, calls=None):
    def fetch(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        payload = documents.get(url, collection())
        if isinstance(payload, Exception):
            raise payload
        return payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return fetch


def test_area_and_point_share_event_key_and_keep_holes_and_source_date():
    result = source.load_pack("gdacs", LOCAL, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_URL: collection(point()),
        source.GDACS_FEEDS["WF"]: collection(point(), area()),
    }))
    assert len(result["features"]) == 2
    marker, shape = result["features"]
    assert marker["properties"]["event_key"] == shape["properties"]["event_key"] == "WF/100"
    assert shape["properties"]["disaster_code"] == shape["properties"]["event_type"] == "WF"
    assert shape["properties"]["geometry_role"] == "area"
    assert shape["properties"]["observed_at"] == "2026-09-06T00:00:00Z"
    assert len(shape["geometry"]["coordinates"]) == 2
    assert set(map(tuple, shape["geometry"]["coordinates"][1])) == set(map(tuple, HOLE))
    assert "Published wildfire area" in shape["properties"]["area_label"]


def test_official_krakatau_observed_ash_fixture_not_an_invented_radius():
    # Exact complete OBS polygon from https://www.gdacs.org/contentdata/xml/gdacsVO.geojson
    # retrieved 2026-09-07, VO1000148 episode2. Unneeded HTML/image URLs omitted.
    coordinates = [[105.9, -6.8], [99.5166666666667, -9.81666666666667], [94.6166666666667, -16.95],
                   [85.8333333333333, -10.0833333333333], [94.1, -3.2], [100.933333333333, -1.6],
                   [106.466666666667, -5.0], [105.9, -6.8]]
    raw = area("VO", 1000148, {"type": "Polygon", "coordinates": [coordinates]}, "OBS", "Poly_Cones_0")
    raw["properties"].update(name="Eruption Krakatau", todate="04 Sep 2026 21:00:00")
    result = source.load_pack("gdacs", WORLD, gdacs_types=["VO"], fetch=fetcher({source.GDACS_FEEDS["VO"]: collection(raw)}))
    feature = result["features"][0]
    assert set(map(tuple, feature["geometry"]["coordinates"][0])) == set(map(tuple, coordinates))
    assert feature["properties"]["area_label"] == "Observed volcanic ash area: OBS"
    assert feature["properties"]["observed_at"] == "2026-09-04T21:00:00Z"


@pytest.mark.parametrize("raw_rings", [[SQUARE, HOLE + HOLE2], [SQUARE + HOLE + HOLE2]])
def test_gdacs_concatenated_rings_are_split_only_at_existing_closing_coordinates(raw_rings):
    # GDACS aggregate serializer flattens holes both into the inner ring and
    # into the exterior array of MultiPolygon components. The event API does not.
    result = source._gdacs_geometry({"type": "MultiPolygon", "coordinates": [raw_rings]})
    assert result is not None
    rings = result[0]["coordinates"][0]
    assert len(rings) == 3
    assert all(ring[0] == ring[-1] for ring in rings)
    assert [len(ring) for ring in rings] == [5, 5, 5]


@pytest.mark.parametrize("geometry", [
    {"type": "Polygon", "coordinates": [SQUARE[:-1]]},
    {"type": "Polygon", "coordinates": [SQUARE, HOLE + HOLE2[:-1]]},
    {"type": "Polygon", "coordinates": [SQUARE, [[99, 1], [100, 1], [100, 2], [99, 1]]]},
    {"type": "LineString", "coordinates": [[5, 52], [float("nan"), 52]]},
    {"type": "Polygon", "coordinates": [[[0, 0], [0, 0], [0, 0], [0, 0]]]},
    {"type": "MultiPolygon", "coordinates": []},
])
def test_invalid_geometry_is_dropped_without_creating_closing_edges(geometry):
    assert source._gdacs_geometry(geometry) is None


def test_point_outside_view_does_not_hide_area_inside_view():
    result = source.load_pack("gdacs", LOCAL, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_FEEDS["WF"]: collection(point(coordinate=[20, 20]), area()),
    }))
    assert len(result["features"]) == 1
    assert result["features"][0]["geometry"]["type"] == "Polygon"


def test_selection_only_calls_selected_aggregate_and_empty_selection_calls_nothing():
    calls = []
    result = source.load_pack("gdacs", WORLD, gdacs_types=[], fetch=fetcher({}, calls))
    assert result["features"] == [] and calls == []
    result = source.load_pack("gdacs", WORLD, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_URL: collection(point("WF"), point("EQ")),
        source.GDACS_FEEDS["WF"]: collection(area()),
    }, calls))
    assert [url for url, _ in calls] == [source.GDACS_URL, source.GDACS_FEEDS["WF"]]
    assert all(feature["properties"]["disaster_code"] == "WF" for feature in result["features"])


def test_geometry_failure_keeps_published_point_alerts():
    result = source.load_pack("gdacs", LOCAL, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_URL: collection(point()), source.GDACS_FEEDS["WF"]: source.IntelSourceError("Offline"),
    }))
    assert len(result["features"]) == 1
    assert result["truncated"] is True
    assert "could not be loaded" in result["note"]
    assert result["geometry_status"]["failed_sources"] == 1


def test_app_feed_failure_can_still_return_aggregate_data():
    result = source.load_pack("gdacs", WORLD, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_URL: source.IntelSourceError("Offline"), source.GDACS_FEEDS["WF"]: collection(point(), area()),
    }))
    assert len(result["features"]) == 2
    assert result["truncated"] is True


def test_forecast_reference_and_track_labels_are_honest():
    records = [area("VO", 1, label="FCST 6h", class_name="Poly_Cones_6"),
               area("EQ", 2, label="100km", class_name="Poly_Circle"),
               area("TC", 3, {"type": "LineString", "coordinates": [[4, 51], [5, 52]]}, "HU", "Line_Line_0")]
    data = {source.GDACS_FEEDS[code]: collection(records[i]) for i, code in enumerate(["VO", "EQ", "TC"])}
    result = source.load_pack("gdacs", WORLD, gdacs_types=["VO", "EQ", "TC"], fetch=fetcher(data))
    props = {feature["properties"]["disaster_code"]: feature["properties"] for feature in result["features"]}
    assert props["VO"]["geometry_basis"] == "forecast"
    assert props["EQ"]["geometry_basis"] == "reference"
    assert "not an impact boundary" in props["EQ"]["area_label"]
    assert props["TC"]["geometry_role"] == "track"


def test_local_details_bounded_and_constructed_from_verified_event_episode_ids():
    calls = []
    alerts = [point(event=100 + n, episode=8) for n in range(12)]
    data = {source.GDACS_URL: collection(*alerts)}
    result = source.load_pack("gdacs", LOCAL, gdacs_types=["WF"], fetch=fetcher(data, calls))
    details = [url for url, _ in calls if url.startswith(source.GDACS_DETAIL_ROOT)]
    assert len(details) == source.GDACS_DETAIL_LIMIT
    assert all(source._gdacs_endpoint(url) for url in details)
    assert len(result["features"]) == 12
    assert result["truncated"] and "zoom closer" in result["note"]
    calls.clear()
    source.load_pack("gdacs", WORLD, gdacs_types=["WF"], fetch=fetcher(data, calls))
    assert len(calls) == 2  # No hundreds-of-events fan-out in the world view.


def test_details_do_not_fetch_provider_supplied_urls():
    alert = point(episode=8)
    alert["properties"]["link"][0]["Value"] = "http://127.0.0.1/private"
    calls = []
    source.load_pack("gdacs", LOCAL, gdacs_types=["WF"], fetch=fetcher({source.GDACS_URL: collection(alert)}, calls))
    assert len(calls) == 2
    assert source._gdacs_ids({"eventtype": "WF", "link": "https://evil.example/report.aspx?eventtype=WF&eventid=1"}) is None


@pytest.mark.parametrize("url", [
    source.GDACS_GEOMETRY_URL + "?eventtype=WF&eventid=1&episodeid=2&url=http://127.0.0.1",
    source.GDACS_GEOMETRY_URL + "?eventtype=WF&eventid=1&eventid=2&episodeid=2",
    source.GDACS_GEOMETRY_URL + "?eventtype=bad&eventid=1&episodeid=2",
    source.GDACS_GEOMETRY_URL + "?eventtype=WF&eventid=../1&episodeid=2",
    "https://www.gdacs.org/contentdata/xml/gdacsXX.geojson",
    "https://www.gdacs.org/contentdata/resources/WF/123/geojson_456_7.geojson",
    "https://www.gdacs.org/contentdata/resources/WF/123/geojson_123_7.geojson?redirect=foo",
])
def test_geometry_endpoint_allowlist_rejects_altered_urls_before_network(url):
    with pytest.raises(source.IntelSourceError, match="Unsupported"):
        source.fetch_bytes(url)


def test_coordinate_budget_keeps_point_and_reports_partial(monkeypatch):
    monkeypatch.setattr(source, "GDACS_MAX_COORDINATES", 4)
    result = source.load_pack("gdacs", WORLD, gdacs_types=["WF"], fetch=fetcher({
        source.GDACS_URL: collection(point()), source.GDACS_FEEDS["WF"]: collection(area()),
    }))
    assert len(result["features"]) == 1
    assert result["truncated"]
    assert "view limits" in result["note"]


def test_document_cache_and_failure_cooldown_avoid_repeated_requests(monkeypatch):
    monkeypatch.setattr(source, "_GDACS_CACHE", {})
    now = [100.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: now[0])
    calls = []
    url = source.GDACS_FEEDS["WF"]
    fetch = fetcher({url: collection(area())}, calls)
    first, stale = source._gdacs_document(url, fetch, 120, cache_enabled=True)
    second, stale = source._gdacs_document(url, fetch, 120, cache_enabled=True)
    assert first == second and not stale and len(calls) == 1
    now[0] += 901
    failing = fetcher({url: source.IntelSourceError("Offline")}, calls)
    cached, stale = source._gdacs_document(url, failing, now[0] + 20, cache_enabled=True)
    assert cached == first and stale
    source._gdacs_document(url, failing, now[0] + 20, cache_enabled=True)
    assert len(calls) == 2


def test_wall_time_budget_stops_geometry_requests_and_preserves_alerts(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: now[0])
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        now[0] += 21
        return json.dumps(collection(point())).encode()
    result = source.load_pack("gdacs", WORLD, fetch=fetch)
    assert len(calls) == 1
    assert len(result["features"]) == 1
    assert result["truncated"]
