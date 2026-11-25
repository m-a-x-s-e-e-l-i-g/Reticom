import json
from urllib.parse import parse_qs, urlsplit

import pytest

from retium import intel_sources as source


AREA = (4.8, 52.0, 4.9, 52.1)


def transport(payload, calls=None):
    def fetch(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        return payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return fetch


def geom(points):
    return [{"lon": x, "lat": y} if x is not None else {} for x, y in points]


def osm(*elements):
    return {"elements": list(elements), "osm3s": {"timestamp_osm_base": "2026-09-06T12:00:00Z"}}


def test_catalog_has_real_sources_and_explicit_credentials():
    packs = {pack["id"]: pack for pack in source.PACKS}
    assert set(packs) == {"trails", "military", "gdacs", "firms", "notams"}
    assert packs["firms"]["credential_fields"] == ["firms_key"]
    assert packs["military"]["source"] == "OpenStreetMap"


def test_notams_request_and_geometry_provenance(monkeypatch):
    monkeypatch.setattr(source.time, "time", lambda: 1795608000)  # 2026-11-25 12:00 UTC
    default = {"is_active": True, "number": "A1234/26", "category": "runway", "type": "N",
               "text": "RWY 18 CLSD\nEXC APPROVED ACFT", "effective_end": "2027-01-01T00:00:00Z",
               "effective_start": "2026-11-25T11:00:00Z", "coordinates": "5203N00451E", "icao_location": "EHAM"}
    def notice(index, **fields):
        return {**default, "nms_id": f"{index:016}", **fields}

    polygon = {"type": "Polygon", "coordinates": [[[4.8, 52.0], [4.9, 52.0], [4.9, 52.1], [4.8, 52.0]]]}
    calls = []
    result = source.load_pack("notams", AREA, {"bravoecho_key": "example-key"}, fetch=transport({
        "notams": [notice(1, area=polygon, area_source="faa"),
                   notice(2, category="new-provider-category", area=polygon, area_source="radius", estimated=True),
                   notice(3, type="C"), notice(4, is_active=False), notice(5, coordinates="9999N00450E"),
                   notice(6, effective_end="unreadable"), notice(7, cancelled_at="2026-11-25T00:00:00Z"),
                   notice(8, effective_start="2026-11-25T18:00:00Z"),
                   notice(9, category="warning", selection_code="QWULW", area=polygon, area_source="faa", schedule="DAILY 1230-1300"),
                   notice(10, category="warning", text="FIREWORKS", schedule="SUNSET TO 2300")],
        "meta": {"next_cursor": "fixture"},
    }, calls))
    [(url, options)] = calls
    request_url = urlsplit(url)
    assert request_url.path == "/v1/notams"
    assert parse_qs(request_url.query) == {"bbox": ["4.8,52.0,4.9,52.1"], "include": ["area,geometry"], "limit": ["500"],
                                          "from": ["2026-11-25T12:00:00Z"], "to": ["2026-11-25T13:30:00Z"]}
    assert options["headers"] == {"Authorization": "Bearer example-key"}
    assert "example-key" not in json.dumps(result)
    assert result["truncated"] is True
    area, reference, drones = result["features"]
    assert area["geometry"] == polygon
    assert area["properties"]["kind"] == "runway"
    assert area["properties"]["notice_text"] == default["text"]
    assert area["properties"]["valid_until"] == 1798761600
    assert reference["geometry"]["type"] == "Point"
    assert reference["properties"]["kind"] == "other"
    assert reference["properties"]["valid_until"] is None
    assert "Approximate" in reference["properties"]["geometry_note"]
    assert result["unresolved_schedules"] == 2
    assert drones["properties"]["local_activity"] == "drones"
    assert drones["properties"]["notam_periods"] == [[1795609800, 1795611600]]


def test_local_notam_selection_and_overnight_schedule():
    from retium.notam_activity import activity_periods, local_activity, utc_timestamp

    now = utc_timestamp("2026-11-25T00:30:00Z")
    start = utc_timestamp("2026-11-24T23:00:00Z")
    end = utc_timestamp("2026-11-27T00:00:00Z")
    assert activity_periods("TUE 2300-0100, WED 0800-0930", start, end, now) == [[start, now + 1800]]
    assert activity_periods("NOV 25 0100-0145", start, end, now) == [[now + 1800, now + 4500]]
    assert activity_periods("DAILY SR-SS", start, end, now) is None
    assert local_activity("QWMLW", "warning", "FIREWORK DISPLAY", "Point", None, 2) == "fireworks"
    assert local_activity("QWMLW", "warning", "ARTILLERY", "Point", None, 2) == ""
    assert local_activity("QWPLW", "warning", "", "Point", None, 999) == ""
    assert local_activity("QWPLW", "warning", "", "Polygon", (4, 51, 5, 52), None) == ""


@pytest.mark.parametrize("bbox", [(0, 0, 0, 1), (170, -5, -170, 5), (0, -91, 1, 1), (0, 0, float("nan"), 1), (False, 0, 1, 1), (1, 2, 3)])
def test_rejects_invalid_bounds_without_a_request(bbox):
    calls = []
    with pytest.raises(source.ProviderError, match="valid map area"):
        source.load_pack("gdacs", bbox, fetch=transport({}, calls))
    assert not calls


def test_rejects_huge_osm_queries_before_network():
    calls = []
    with pytest.raises(source.ProviderError, match="Zoom in"):
        source.load_pack("military", (-10, 40, 10, 60), fetch=transport({}, calls))
    assert not calls


def test_osm_preserves_military_and_police_polygons_and_labels_airstrip_and_station():
    square = [(4.81, 52.01), (4.83, 52.01), (4.83, 52.03), (4.81, 52.03), (4.81, 52.01)]
    calls = []
    payload = osm(
        {"type": "way", "id": 10, "tags": {"landuse": "military", "military": "training_area", "name": "Example range", "access": "no"}, "geometry": geom(square)},
        {"type": "node", "id": 11, "lat": 52.02, "lon": 4.85, "tags": {"aeroway": "airstrip", "name": "Civil airstrip"}},
        {"type": "node", "id": 12, "lat": 52.03, "lon": 4.85, "tags": {"amenity": "police"}},
        {"type": "way", "id": 13, "tags": {"landuse": "police", "name": "Example police property"}, "geometry": geom(square)},
    )
    result = source.load_pack("military", AREA, fetch=transport(payload, calls))
    training, airstrip, station, police = result["features"]
    assert training["geometry"]["type"] == "Polygon"
    assert training["properties"]["kind"] == "training_area"
    assert "Access: no" in training["properties"]["detail"]
    assert airstrip["properties"]["kind"] == "airstrip"
    assert "Military" not in airstrip["properties"]["detail"]
    assert station["properties"]["kind"] == "police_station" and station["geometry"]["type"] == "Point"
    assert police["properties"]["kind"] == "police_property" and police["geometry"]["type"] == "Polygon"
    assert training["properties"]["source_url"] == "https://www.openstreetmap.org/way/10"
    assert result["updated_at"] == "2026-09-06T12:00:00Z"
    assert result["source_bytes"] == len(json.dumps(payload).encode())
    query = parse_qs(calls[0][1]["data"].decode())["data"][0]
    assert "52.000000,4.800000,52.100000,4.900000" in query
    assert "[timeout:18]" in query
    assert '["amenity"="police"]' in query and '["landuse"="police"]' in query and '["police"]' in query


def test_osm_multipolygon_joins_reversed_members_and_preserves_holes():
    members = [
        {"role": "outer", "geometry": geom([(4.81, 52.01), (4.83, 52.01), (4.83, 52.03)])},
        {"role": "outer", "geometry": geom([(4.81, 52.01), (4.81, 52.03), (4.83, 52.03)])},
        {"role": "inner", "geometry": geom([(4.815, 52.015), (4.825, 52.015), (4.825, 52.025), (4.815, 52.025), (4.815, 52.015)])},
        {"role": "outer", "geometry": geom([(4.85, 52.05), (4.86, 52.05), (4.86, 52.06), (4.85, 52.06), (4.85, 52.05)])},
    ]
    result = source.load_pack("military", AREA, fetch=transport(osm({"type": "relation", "id": 12, "tags": {"type": "multipolygon", "landuse": "military"}, "members": members})))
    geometry = result["features"][0]["geometry"]
    assert geometry["type"] == "MultiPolygon"
    assert sorted(map(len, geometry["coordinates"])) == [1, 2]
    assert all(ring[0] == ring[-1] for polygon in geometry["coordinates"] for ring in polygon)


def test_incomplete_boundary_is_outline_not_invented_filled_polygon():
    relation = {"type": "relation", "id": 13, "tags": {"type": "multipolygon", "landuse": "military"}, "members": [
        {"role": "outer", "geometry": geom([(4.81, 52.01), (4.83, 52.01), (4.83, 52.03)])},
    ]}
    result = source.load_pack("military", AREA, fetch=transport(osm(relation)))
    assert result["features"][0]["geometry"]["type"] == "LineString"


def test_hiking_routes_keep_segments_separate_across_clipped_gaps():
    route = {"type": "relation", "id": 14, "tags": {"type": "route", "route": "hiking", "name": "Trail"}, "members": [
        {"geometry": geom([(4.81, 52.01), (4.82, 52.02), (None, None), (4.85, 52.05), (4.86, 52.06)])},
    ]}
    result = source.load_pack("trails", AREA, fetch=transport(osm(route)))
    feature = result["features"][0]
    assert feature["geometry"]["type"] == "MultiLineString"
    assert len(feature["geometry"]["coordinates"]) == 2
    assert feature["properties"]["kind"] == "hiking_route"


def test_incomplete_overpass_response_is_error_not_empty_success():
    with pytest.raises(source.ProviderError, match="could not finish"):
        source.load_pack("trails", AREA, fetch=transport({"elements": [], "remark": "runtime error timeout"}))


def test_osm_feature_cap_marks_incomplete_result(monkeypatch):
    monkeypatch.setattr(source, "MAX_FEATURES", 1)
    nodes = [{"type": "node", "id": i, "lon": 4.85, "lat": 52.05, "tags": {"military": "base"}} for i in range(2)]
    result = source.load_pack("military", AREA, fetch=transport(osm(*nodes)))
    assert len(result["features"]) == 1
    assert result["truncated"] is True


def test_gdacs_repairs_official_nested_points_filters_bbox_and_sanitizes():
    def event(eventid, coordinate):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coordinate}, "properties": {
            "eventtype": "EQ", "eventid": eventid, "alertlevel": "Orange", "title": "Earthquake",
            "description": "<b>Public</b> &amp; published", "todate": "2026-09-06 10:20:30",
            "link": [{"Key": "web", "Value": "javascript:alert(1)"}],
        }}
    result = source.load_pack("gdacs", AREA, fetch=transport({"features": [event(1, [[4.85, 52.05]]), event(2, [20, 30]), event(3, [[999, 10]])]}))
    assert len(result["features"]) == 1
    feature = result["features"][0]
    assert feature["geometry"]["coordinates"] == [4.85, 52.05]
    assert feature["properties"]["detail"] == "Public & published"
    assert feature["properties"]["observed_at"] == "2026-09-06T10:20:30Z"
    assert feature["properties"]["severity"] == "orange"
    assert feature["properties"]["source_url"] == "https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1"


def test_firms_is_not_called_without_credentials():
    calls = []
    with pytest.raises(source.ProviderError, match="settings"):
        source.load_pack("firms", AREA, fetch=transport({}, calls))
    assert not calls


def test_firms_csv_dates_confidence_and_no_key_leaks(monkeypatch):
    monkeypatch.setattr(source.time, "time", lambda: 1788868800)
    rows = b"latitude,longitude,acq_date,acq_time,confidence,satellite\n52.05,4.85,2026-09-06,0935,h,N20\n60,4.85,2026-09-06,0100,n,N20\n52.05,4.85,bad,0100,l,N20\n"
    calls = []
    result = source.load_pack("firms", AREA, {"firms_key": "ExampleSecret123"}, fetch=transport(rows, calls))
    assert len(result["features"]) == 1
    props = result["features"][0]["properties"]
    assert props["observed_at"] == "2026-09-06T09:35:00Z"
    assert props["confidence"] == "high"
    assert props["kind"] == "thermal_anomaly"
    assert "not a confirmed fire" in props["detail"]
    assert "ExampleSecret123" not in json.dumps(result)
    assert calls[0][0].endswith("/VIIRS_NOAA20_NRT/4.800000,52.000000,4.900000,52.100000/4/2026-09-05")


def test_firms_bad_key_response_is_safe():
    with pytest.raises(source.ProviderError) as exc:
        source.load_pack("firms", AREA, {"firms_key": "ExampleSecret123"}, fetch=transport(b"Invalid key ExampleSecret123"))
    assert "ExampleSecret123" not in str(exc.value)


def test_oversize_response_is_rejected_before_parse(monkeypatch):
    monkeypatch.setattr(source, "GDACS_MAX_BYTES", 20)
    with pytest.raises(source.ProviderError, match="too large"):
        source.load_pack("gdacs", AREA, fetch=transport(b" " * 21))


def test_transport_refuses_arbitrary_urls_before_network():
    for url in ("http://127.0.0.1/", "https://example.com/", "https://www.gdacs.org@127.0.0.1/contentdata/xml/gdacsAPP_Home.geojson", source.GDACS_URL + "#fragment"):
        with pytest.raises(source.ProviderError, match="Unsupported"):
            source.fetch_bytes(url)


def test_unknown_pack_rejected():
    with pytest.raises(source.ProviderError, match="Unknown"):
        source.load_pack("custom-url", AREA)


@pytest.mark.parametrize("days,expected", [(1, 2), (3, 3), (7, 4)])
def test_firms_rolling_windows_split_requests_and_deduplicate(monkeypatch, days, expected):
    from datetime import datetime, timezone
    monkeypatch.setattr(source.time, "time", lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc).timestamp())
    rows = b"latitude,longitude,acq_date,acq_time,confidence,satellite\n52.05,4.85,2026-09-08,1100,h,N20\n52.05,4.85,2026-09-07,1200,h,N20\n52.05,4.85,2026-09-07,1159,h,N20\n52.05,4.85,2026-09-02,1200,h,N20\n52.05,4.85,2026-09-01,1159,h,N20\n52.05,4.85,2026-09-08,1300,h,N20\n"
    calls = []
    result = source.load_pack("firms", AREA, {"firms_key": "ExampleSecret123"}, fetch=transport(rows, calls), firms_days=days)
    assert len(result["features"]) == expected
    assert result["firms_days"] == days
    assert len(calls) == (2 if days == 7 else 1)
    assert all(int(call[0].split("/")[-2]) <= 5 for call in calls)
    if days == 7:
        assert calls[0][0].endswith("/5/2026-09-01")
        assert calls[1][0].endswith("/3/2026-09-06")
