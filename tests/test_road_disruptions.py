import gzip
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from retium.intel_packs import IntelPackStore, intel_router
from retium.road_disruptions import RoadDisruptions, parse_ndw, parse_wzdx, timestamp, EXPIRE_SECONDS, SOURCES

NOW = timestamp("2026-09-09T09:30:00Z")
NL = (4, 51, 5, 52)
NC = (-80, 35, -79, 36)


def ndw(*, end="2026-09-10T10:00:00Z", extra="", management="roadClosed", coordinates="52.1 4.1 52.2 4.2", published="2026-09-09T09:30:00Z"):
    return f'''<message xmlns="http://datex2.eu/schema/3/situation" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <publicationTime>{published}</publicationTime><situationRecord id="test" xsi:type="sit:RoadOrCarriagewayOrLaneManagement">
      <situationRecordVersionTime>2026-09-01T00:00:00Z</situationRecordVersionTime>
      <validity><validityStatus>definedByValidityTimeSpec</validityStatus><validityTimeSpecification>
      <overallStartTime>2026-09-08T00:00:00Z</overallStartTime><overallEndTime>{end}</overallEndTime>
      </validityTimeSpecification></validity><locationReference><gmlLineString srsName="WGS 84"><posList>{coordinates}</posList></gmlLineString></locationReference>
      <roadOrCarriagewayOrLaneManagementType>{management}</roadOrCarriagewayOrLaneManagementType>{extra}
      </situationRecord></message>'''.encode()


def wzdx(*, impact="all-lanes-closed", end="2026-09-10T00:00:00Z", status=None, rows=None):
    feature = {"type":"Feature", "id":"one", "geometry":{"type":"LineString","coordinates":[[-80,35],[-79.99,35.01]]},
      "properties":{"core_details":{"event_type":"work-zone","road_names":["I-40"],"update_date":"2026-09-01T00:00:00Z"},
      "start_date":"2026-09-08T00:00:00Z","end_date":end,"vehicle_impact":impact,"event_status":status}}
    return json.dumps({"type":"FeatureCollection", "feed_info":{"update_date":"2026-09-09T09:30:00Z"}, "features":[feature] if rows is None else rows}).encode()


def test_ndw_gml_order_full_geometry_and_report_age_are_preserved():
    coordinates=" ".join(f"52.{i:04d} 4.{i:04d}" for i in range(200))
    features,published=parse_ndw(gzip.compress(ndw(coordinates=coordinates)), NOW)
    assert published == NOW
    line=features[0]["geometry"]["coordinates"][0]
    assert len(line)==200 and line[-1]==[4.0199,52.0199]
    assert features[0]["properties"]["road_impact"]=="closed"
    assert features[0]["properties"]["updated_at"] < NOW-86400


@pytest.mark.parametrize("extra", [
    "<validPeriod><startOfPeriod>2026-09-10T00:00:00Z</startOfPeriod></validPeriod>",
    "<operatorActionStatus>cancelled</operatorActionStatus>",
    "<recurringTimePeriodOfDay><startTimeOfPeriod>22:00:00</startTimeOfPeriod></recurringTimePeriodOfDay>",
    "<exceptionPeriod><startOfPeriod>2026-09-09T09:00:00Z</startOfPeriod><endOfPeriod>2026-09-09T10:00:00Z</endOfPeriod></exceptionPeriod>",
    "<validPeriod><startOfPeriod>2026-09-08T00:00:00Z</startOfPeriod><endOfPeriod>invalid</endOfPeriod></validPeriod>",
])
def test_ndw_inactive_or_uninterpretable_schedules_are_not_current_closures(extra):
    assert parse_ndw(ndw(extra=extra),NOW)[0]==[]


def test_ndw_expired_conditional_lane_and_missing_geometry():
    assert parse_ndw(ndw(end="2026-09-09T09:30:00Z"),NOW)[0]==[]
    assert parse_ndw(ndw(extra="<forVehiclesWithCharacteristicsOf><vehicleType>lorry</vehicleType></forVehiclesWithCharacteristicsOf>"),NOW)[0][0]["properties"]["road_impact"]=="restricted"
    assert parse_ndw(ndw(management="laneClosures"),NOW)[0][0]["properties"]["road_impact"]=="restricted"
    assert parse_ndw(ndw(coordinates="invalid"),NOW)[0]==[]
    assert parse_ndw(ndw(coordinates="52.1 4.1 52.2"),NOW)[0]==[]


def test_xml_external_entities_are_rejected():
    with pytest.raises(ValueError):
        parse_ndw(b'<!DOCTYPE x [<!ENTITY test SYSTEM "file:///secret">]><x/>',NOW)


def test_compressed_feed_cannot_exceed_decompressed_budget(monkeypatch):
    monkeypatch.setattr("retium.road_disruptions.MAX_BYTES", 100)
    with pytest.raises(ValueError):
        parse_ndw(gzip.compress(ndw()), NOW)


def test_old_publication_cannot_extend_report_lifetime(tmp_path):
    now = [NOW]
    store = IntelPackStore(tmp_path, clock=lambda: now[0])
    store.update({"enabled": ["roads"]})
    roads = RoadDisruptions(store, fetch=lambda *a, **kw: ndw())
    assert roads.load(NL, 4)["status"] == "fresh"
    now[0] += 601
    stale = roads.load(NL, 4)
    assert stale["status"] == "stale"
    assert stale["features"][0]["properties"]["expires_at"] == NOW + EXPIRE_SECONDS
    now[0] = NOW + EXPIRE_SECONDS + 1
    assert roads.load(NL, 4)["features"] == []


def test_north_carolina_uses_official_direct_endpoint_and_region_cache(tmp_path):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return wzdx()
    store = IntelPackStore(tmp_path, clock=lambda: NOW)
    store.update({"enabled": ["roads"]})
    roads = RoadDisruptions(store, fetch=fetch)
    assert len(roads.load(NC, 3)["features"]) == 1
    assert len(roads.load(NC, 12)["features"]) == 1
    assert calls == ["https://www.drivenc.gov/api/wzdx"]


@pytest.mark.parametrize("impact,expected",[("all-lanes-closed","closed"),("some-lanes-closed","restricted"),("unknown","incident"),("all-lanes-open",None)])
def test_wzdx_distinguishes_closed_from_restricted_or_unknown(impact, expected):
    features,_=parse_wzdx(wzdx(impact=impact),NOW)
    assert (features[0]["properties"]["road_impact"] if features else None)==expected


def test_wzdx_ended_cancelled_future_and_invalid_geometry_are_excluded():
    assert parse_wzdx(wzdx(end="2026-09-08T00:00:00Z"),NOW)[0]==[]
    assert parse_wzdx(wzdx(status="cancelled"),NOW)[0]==[]
    row=json.loads(wzdx())["features"][0]
    row["properties"]["start_date"]="2027-01-01T00:00:00Z"
    assert parse_wzdx(wzdx(rows=[row]),NOW)[0]==[]
    row["properties"]["start_date"]="2026-09-08T00:00:00Z"
    row["geometry"]["coordinates"][0][0]=float("nan")
    assert parse_wzdx(wzdx(rows=[row]),NOW)[0]==[]


def test_provider_cache_clearing_staleness_restart_and_geographic_selection(tmp_path):
    now,calls,payload=[NOW],[],[ndw()]
    def fetch(url,**kwargs):
        calls.append(url)
        if payload[0] is None: raise OSError("sensitive upstream message")
        return payload[0]
    store=IntelPackStore(tmp_path,clock=lambda:now[0]); roads=RoadDisruptions(store,fetch=fetch)
    assert roads.load(NL,12)["status"]=="disabled" and not calls
    store.update({"enabled":["roads"]})
    assert roads.load((10,40,11,41),12)["status"]=="uncovered" and not calls
    first=roads.load(NL,12); assert len(first["features"])==1 and first["status"]=="fresh"
    roads.load((3,50.6,7.4,53.7),3);assert len(calls)==1
    assert calls[0]==SOURCES["ndw"]["url"]
    restarted=RoadDisruptions(store,fetch=fetch)
    assert restarted.load(NL,12)["features"] and len(calls)==1
    payload[0]=None;now[0]+=121
    failed=restarted.load(NL,12)
    assert failed["status"]=="stale" and failed["features"][0]["properties"]["stale"]
    assert "sensitive" not in json.dumps(failed)
    now[0]+=121;payload[0]=b'<message><publicationTime>2026-09-09T09:34:02Z</publicationTime></message>'
    cleared=restarted.load(NL,12);assert cleared["features"]==[] and cleared["replaced_sources"]==["ndw"]
    now[0]+=EXPIRE_SECONDS+1;payload[0]=None
    assert restarted.load(NL,12)["features"]==[]


def test_disabled_inflight_load_cannot_restore_reports(tmp_path):
    started,finish=threading.Event(),threading.Event()
    def fetch(*args,**kwargs): started.set();assert finish.wait(5);return ndw()
    store=IntelPackStore(tmp_path,clock=lambda:NOW);store.update({"enabled":["roads"]})
    roads=RoadDisruptions(store,fetch=fetch)
    with ThreadPoolExecutor() as pool:
        result=pool.submit(roads.load,NL,12)
        assert started.wait(5)
        store.update({"enabled":[]});finish.set()
        assert result.result()["status"]=="disabled"
    assert not list(roads.directory.glob('*.json'))


def test_endpoint_toggle_and_no_coverage_without_reticulum(tmp_path):
    app=FastAPI();app.include_router(intel_router(tmp_path))
    with TestClient(app) as client:
        url="/api/intel-packs/roads?west=10&south=40&east=11&north=41&zoom=3"
        assert client.get(url).json()["status"]=="disabled"
        assert client.patch("/api/intel-packs/settings",json={"enabled":["roads"]}).status_code==200
        assert client.get(url).json()["status"]=="uncovered"
        assert client.get(url.replace("west=10","west=nan")).status_code==422
