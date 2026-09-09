import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from retium.intel_packs import IntelPackStore, intel_router
from retium.road_disruptions import RoadDisruptions
from retium.road_navigation import follows_segment, route_closures, assess_routes, validate_routes
from test_road_disruptions import NOW, ndw

MAIN = [[4.1, 52.1], [4.2, 52.2]]
DETOUR = [[4.1, 52.1], [4.2, 52.1], [4.2, 52.2]]


def test_geometry_matching_uses_aligned_overlap_not_bbox_or_overpass():
    assert follows_segment([4,52], [4.01,52], [4.002,52], [4.008,52])
    assert follows_segment([4,52], [4.01,52], [4.008,52], [4.002,52])
    assert follows_segment([4,52], [4.01,52], [4.002,52.0001], [4.008,52.0001])
    assert not follows_segment([4,52], [4.01,52], [4.005,51.999], [4.005,52.001])
    assert not follows_segment([4,52], [4.01,52], [4.002,52.001], [4.008,52.001])
    assert not follows_segment([4,52], [4.01,52], [4.011,52], [4.015,52])


def test_alternative_avoids_closure_even_when_map_filters_hide_all(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda:NOW)
    store.update({"enabled":["roads"], "road_filters":{"types":[], "impacts":[]}})
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args)
        return ndw()
    roads = RoadDisruptions(store, fetch=fetch)
    result = assess_routes(roads, {"routes":[MAIN, DETOUR]})
    assert result["selected"] == 1 and result["counts"] == [1,0]
    assert "Alternative chosen" in result["note"]
    blocked = assess_routes(roads, {"routes":[MAIN]})
    assert blocked["status"] == "closures" and "No supplied alternative" in blocked["warning"]
    assert len(calls) == 1


def test_offline_check_never_fetches_and_expires_old_cache(tmp_path):
    now = [NOW]
    store = IntelPackStore(tmp_path, clock=lambda:now[0]); store.update({"enabled":["roads"]})
    calls = []
    roads = RoadDisruptions(store, fetch=lambda *a, **k: calls.append(a) or ndw())
    body = {"routes":[MAIN], "cached_only":True}
    assert assess_routes(roads, body)["status"] == "error" and calls == []
    assess_routes(roads, {"routes":[MAIN]})
    now[0] += 601
    assert "stale" in assess_routes(roads, body)["warning"]
    now[0] += 3600
    assert assess_routes(roads, body)["status"] == "error" and len(calls) == 1


def test_disabled_and_uncovered_are_not_successful_checks(tmp_path):
    store = IntelPackStore(tmp_path)
    roads = RoadDisruptions(store, fetch=lambda *a, **k: pytest.fail("Unexpected provider request"))
    assert assess_routes(roads,{"routes":[MAIN]})["status"] == "disabled"
    store.update({"enabled":["roads"]})
    assert assess_routes(roads,{"routes":[[[20,40],[21,41]]]})["status"] == "uncovered"


def test_restrictions_are_not_assumed_full_road_closures(tmp_path):
    store = IntelPackStore(tmp_path, clock=lambda:NOW); store.update({"enabled":["roads"]})
    roads = RoadDisruptions(store, fetch=lambda *a, **k: ndw(management="laneClosures"))
    assert assess_routes(roads,{"routes":[MAIN]})["counts"] == [0]


@pytest.mark.parametrize("body", [{}, {"routes":[]}, {"routes":[MAIN]*5}, {"routes":[[[True,52],[4,52]]]}, {"routes":[MAIN],"cached_only":"true"}, {"routes":[[[float('nan'),52],[4,52]]]}])
def test_bad_geometry_is_rejected(body):
    with pytest.raises(ValueError): validate_routes(body)


def test_route_check_endpoint_validation_and_disabled_state(tmp_path):
    app=FastAPI(); app.include_router(intel_router(tmp_path))
    with TestClient(app) as client:
        url="/api/intel-packs/roads/check-route"
        assert client.post(url,json={"routes":[MAIN]}).json()["status"] == "disabled"
        assert client.post(url,json={"routes":[]}).status_code == 422
        assert client.post(url,content=b'x'*4_000_001).status_code == 413
