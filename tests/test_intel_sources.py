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
    assert set(packs) == {"trails", "military", "gdacs", "firms", "acled"}
    assert packs["firms"]["credential_fields"] == ["firms_key"]
    assert packs["acled"]["credential_fields"] == ["acled_token"]
    assert packs["military"]["source"] == "OpenStreetMap"


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


def test_osm_preserves_military_polygon_and_labels_civilian_airstrip():
    square = [(4.81, 52.01), (4.83, 52.01), (4.83, 52.03), (4.81, 52.03), (4.81, 52.01)]
    calls = []
    payload = osm(
        {"type": "way", "id": 10, "tags": {"landuse": "military", "military": "training_area", "name": "Example range", "access": "no"}, "geometry": geom(square)},
        {"type": "node", "id": 11, "lat": 52.02, "lon": 4.85, "tags": {"aeroway": "airstrip", "name": "Civil airstrip"}},
    )
    result = source.load_pack("military", AREA, fetch=transport(payload, calls))
    training, airstrip = result["features"]
    assert training["geometry"]["type"] == "Polygon"
    assert training["properties"]["kind"] == "training_area"
    assert "Access: no" in training["properties"]["detail"]
    assert airstrip["properties"]["kind"] == "airstrip"
    assert "Military" not in airstrip["properties"]["detail"]
    assert training["properties"]["source_url"] == "https://www.openstreetmap.org/way/10"
    assert result["updated_at"] == "2026-09-06T12:00:00Z"
    assert result["source_bytes"] == len(json.dumps(payload).encode())
    query = parse_qs(calls[0][1]["data"].decode())["data"][0]
    assert "52.000000,4.800000,52.100000,4.900000" in query
    assert "[timeout:18]" in query


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


@pytest.mark.parametrize("pack", ["firms", "acled"])
def test_keyed_providers_are_not_silently_called_without_credentials(pack):
    calls = []
    with pytest.raises(source.ProviderError, match="settings"):
        source.load_pack(pack, AREA, fetch=transport({}, calls))
    assert not calls


def test_firms_csv_dates_confidence_and_no_key_leaks():
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
    assert calls[0][0].endswith("/VIIRS_NOAA20_NRT/4.800000,52.000000,4.900000,52.100000/3")


def test_firms_bad_key_response_is_safe():
    with pytest.raises(source.ProviderError) as exc:
        source.load_pack("firms", AREA, {"firms_key": "ExampleSecret123"}, fetch=transport(b"Invalid key ExampleSecret123"))
    assert "ExampleSecret123" not in str(exc.value)


def test_acled_filters_bbox_and_date_and_preserves_approximation():
    calls = []
    rows = [{"event_id_cnty": "TEST1", "latitude": "52.05", "longitude": "4.85", "event_date": "2026-09-06", "geo_precision": "3", "sub_event_type": "Peaceful protest", "location": "Example", "notes": "Published report"}]
    result = source.load_pack("acled", AREA, {"acled_token": "private-token"}, fetch=transport({"status": 200, "data": rows}, calls))
    props = result["features"][0]["properties"]
    assert props["precision"] == 3
    assert "Approximate administrative area" in props["detail"]
    assert props["title"] == "Peaceful protest · Example"
    assert "private-token" not in json.dumps(result)
    query = parse_qs(urlsplit(calls[0][0]).query)
    assert query["latitude"] == ["52.0|52.1"]
    assert query["longitude_where"] == ["BETWEEN"]
    assert query["event_date_where"] == ["BETWEEN"]
    assert "private-token" not in calls[0][0]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer private-token"


def test_acled_expired_token_response_does_not_echo_secret():
    with pytest.raises(source.ProviderError) as exc:
        source.load_pack("acled", AREA, {"acled_token": "private-token"}, fetch=transport({"status": 401, "error": "expired private-token"}))
    assert "private-token" not in str(exc.value)
    assert "access token" in str(exc.value)


def test_refuses_secret_header_injection():
    calls = []
    with pytest.raises(source.ProviderError, match="format"):
        source.load_pack("acled", AREA, {"acled_token": "foo\r\nBad: header"}, fetch=transport({}, calls))
    assert not calls


def test_oversize_response_is_rejected_before_parse(monkeypatch):
    monkeypatch.setattr(source, "GDACS_MAX_BYTES", 20)
    with pytest.raises(source.ProviderError, match="too large"):
        source.load_pack("gdacs", AREA, fetch=transport(b" " * 21))


def test_transport_refuses_arbitrary_urls_before_network():
    for url in ("http://127.0.0.1/", "https://example.com/", "https://acleddata.com@127.0.0.1/api/acled/read", "https://acleddata.com/api/acled/read#secret"):
        with pytest.raises(source.ProviderError, match="Unsupported"):
            source.fetch_bytes(url)


def test_unknown_pack_rejected():
    with pytest.raises(source.ProviderError, match="Unknown"):
        source.load_pack("custom-url", AREA)
