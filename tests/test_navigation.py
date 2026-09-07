import copy
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import RNS

from retium.command_snapshot import read_team_snapshot
from retium.mission import MissionInbox, MissionPublisher
from retium.navigation import as_plan, decode_coordinates, encode_coordinates, fields_from_request
from retium.outbox import FieldOutbox
from retium.protocol import ProtocolError, new_event, sign_event, verify_envelope
from retium.replication import ReplicatedTable
from retium.store import EventStore
from retium.transport import GatewayReceiver
from membership_helpers import approve_test_members


def plan_request(**values):
    return {"label": "Rally point", "mode": "route", "origin": [4, 51], "target": [4.1, 51.1],
            "target_kind": "waypoint", "target_id": str(uuid.uuid4()),
            "coordinates": [[4, 51], [4.01, 51.08], [4.1, 51.1]],
            "distance_m": 17000, "duration_s": 1400, **values}


def route(revision=1, **values):
    return new_event("navigation.updated", "ALPHA", **fields_from_request(plan_request(**values), str(uuid.uuid4()), revision))


def stop(event, revision=2):
    return new_event("navigation.stopped", "ALPHA", route_id=event["route_id"], revision=revision)


def test_polyline_preserves_every_point_at_five_decimals():
    points = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]
    assert encode_coordinates(points) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_coordinates(encode_coordinates(points)) == points
    points = [[179.99999, -89.123456], [-179.99999, 89.99999]]
    assert decode_coordinates(encode_coordinates(points)) == [[179.99999, -89.12346], [-179.99999, 89.99999]]


@pytest.mark.parametrize("geometry", ["", "?", "????~", "??" + "~" * 25, "a" * 12001, "\u2603??"])
def test_malformed_polyline_rejected(geometry):
    with pytest.raises(ValueError):
        decode_coordinates(geometry)


@pytest.mark.parametrize("changes", [
    {"coordinates": [[0, 0]]}, {"coordinates": [[0, 0]] * 4001},
    {"origin": [True, 1]}, {"target": [float("nan"), 1]}, {"origin": [181, 0]},
    {"mode": []}, {"mode": "teleport"}, {"label": " "}, {"duration_s": -1},
    {"target_kind": []}, {"following": {"sender_hash": "no", "route_id": "no"}},
    {"directions": [{"text": "x" * 161}]}, {"mode": "direct"},
])
def test_invalid_plan_is_not_silently_shortened(changes):
    with pytest.raises(ValueError):
        route(**changes)


def test_event_is_signed_and_only_authentic_sender_owns_navigation(tmp_path):
    owner, other = RNS.Identity(), RNS.Identity()
    store = EventStore(tmp_path / "events.sqlite3")
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    approve_test_members(receiver, tmp_path, owner, other)
    receiver.store, receiver.everyone_admin = store, True
    receiver.event_callback = receiver.last_packet_at = None
    receiver.destination = SimpleNamespace(hash=b"host")
    update = route()
    wire = sign_event(update, owner, packet_limit=False)
    assert len(wire) > RNS.Packet.MDU
    assert verify_envelope(wire)[0] == update
    tampered = json.loads(wire)
    tampered["event"]["geometry"] = encode_coordinates([[4, 51], [5, 52]])
    with pytest.raises(ProtocolError, match="signature"):
        verify_envelope(json.dumps(tampered).encode())
    def receive(event, signer, peer):
        return json.loads(receiver._event_request("/event", {"envelope": sign_event(event, signer, packet_limit=False)}, b"request", b"link", peer, 0))
    assert "error" in receive(update, owner, other)
    assert receive(update, owner, owner)["accepted"]
    # Reusing another person's route id does not stop their route, even as admin.
    assert receive(stop(update, 999), other, other)["accepted"]
    plans = {p["sender_hash"]: p for p in map(as_plan, store.navigation_events())}
    assert plans[owner.hash.hex()]["active"]
    assert not plans[other.hash.hex()]["active"]
    assert receive(stop(update), owner, owner)["accepted"]
    assert not next(as_plan(e) for e in store.navigation_events() if e["network"]["sender_hash"] == owner.hash.hex())["active"]
    store.close()


def test_reordering_old_replay_and_future_timestamps_cannot_resurrect_route(tmp_path):
    update = route()
    stopped = stop(update)
    replay = copy.deepcopy(update)
    replay["id"], replay["created_at"] = str(uuid.uuid4()), 9_999_999_999
    same_version_update = route(2)
    for index, changes in enumerate([[update, stopped, replay, same_version_update], [stopped, replay, update, same_version_update]]):
        store = EventStore(tmp_path / f"{index}.sqlite")
        for event in changes:
            store.insert(event, "a" * 32)
        assert store.navigation_events()[0]["type"] == "navigation.stopped"
        assert len(store.navigation_events()) == 1
        store.close()


def test_revision_counter_survives_restart_pruning_and_received_higher_revision(tmp_path):
    path = tmp_path / "events.sqlite"
    store = EventStore(path)
    assert store.next_navigation_revision("alpha") == 1
    store.close()
    store = EventStore(path)
    assert store.next_navigation_revision("alpha") == 2
    store.insert(route(40), "alpha")
    assert store.next_navigation_revision("alpha") == 41
    assert store.next_navigation_revision("bravo") == 1
    store._connection.execute("DELETE FROM events")
    store._connection.commit()
    assert store.next_navigation_revision("alpha") == 42
    store.close()


def test_offline_outbox_sends_only_latest_plan_or_stop_without_touching_other_teams(tmp_path):
    outbox = FieldOutbox(tmp_path / "outbox.sqlite")
    update, other = route(), route()
    chat = new_event("chat.message", "ALPHA", message="Keep this message")
    outbox.add("team-a", update, linked=True)
    outbox.add("team-b", other, linked=True)
    outbox.add("team-a", chat, linked=True)
    outbox.add("team-a", stop(update), linked=True)
    assert [i["event"]["type"] for i in outbox.pending("team-a")] == ["chat.message", "navigation.stopped"]
    assert outbox.pending("team-b")[0]["event"] == other
    outbox.close()


def transfer(source, cache):
    publisher, inbox = MissionPublisher(), MissionInbox(cache)
    def content():
        return {"events": source.mission_events(), "tasks": [], "private_events": [], "team": {}}
    for _ in range(100):
        page = publisher.page("receiver", inbox.request(force=False), content)
        inbox.accept(page)
        if page["mission"].get("complete") or page["mission"].get("unchanged"):
            return
    pytest.fail("mission sync did not finish")


def test_late_join_snapshot_retains_routes_and_stop_beyond_intel_limit_and_stale_host(tmp_path):
    source = EventStore(tmp_path / "host" / "events.sqlite3")
    cache = EventStore(tmp_path / "cache.sqlite3")
    update = route()
    source.insert(update, "a" * 32)
    for index in range(150):
        source.insert(new_event("chat.message", "BRAVO", message=str(index)), "b" * 32)
    assert len(source.mission_events()) == 101
    assert any(e["type"] == "navigation.updated" for e in read_team_snapshot(tmp_path / "host", None, [])["events"])
    transfer(source, cache)
    assert as_plan(cache.navigation_events()[0])["coordinates"] == plan_request()["coordinates"]
    stopped = stop(update)
    cache.insert(stopped, "a" * 32)
    # The source is now a stale backup unaware of the newer stop.
    transfer(source, cache)
    assert cache.navigation_events()[0]["id"] == stopped["id"]
    source.insert(stopped, "a" * 32)
    transfer(source, cache)
    assert len(cache.navigation_events()) == 1
    source.close(); cache.close()


def test_large_geometry_stays_inside_replication_limit_and_is_not_simplified(tmp_path):
    coords = [[4 + i * 0.00001, 51 + i * 0.00001] for i in range(4000)]
    update = route(coordinates=coords)
    source, replica = EventStore(tmp_path / "source.sqlite"), EventStore(tmp_path / "replica.sqlite")
    original = ReplicatedTable(source, "events", "event_id", "host-a")
    mirror = ReplicatedTable(replica, "events", "event_id", "host-b")
    source.insert(update, "a" * 32)
    mirror.merge("host-a", original.page(), {"host-a", "host-b"})
    assert len(as_plan(replica.navigation_events()[0])["coordinates"]) == 4000
    oversized = [[(i % 2) * 100, (i % 2) * 80] for i in range(4000)]
    with pytest.raises(ValueError):
        route(coordinates=oversized)
    source.close(); replica.close()


def test_navigation_api_generates_own_revisions_and_lifecycle(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def exercise_api(root):
    from fastapi.testclient import TestClient
    from retium.server import create_node_app
    from retium.team import TeamProfile
    config, data = root / "config", root / "data"
    config.mkdir()
    (config / "config").write_text("[reticulum]\n  share_instance = No\n[logging]\n  loglevel = 1\n[interfaces]\n")
    RNS.Reticulum(str(config))
    TeamProfile(data / "team.json").set_name("Navigation test")
    with TestClient(create_node_app("gateway", config, data)) as client:
        assert client.get("/api/navigation").json()["plans"] == []
        body = plan_request()
        response = client.put("/api/navigation", json={**body, "revision": 999, "sender_hash": "someone-else"})
        assert response.status_code == 201, response.text
        plan = response.json()["plan"]
        assert plan["active"] and plan["revision"] == 1
        assert plan["sender_hash"] == client.get("/api/navigation").json()["sender_hash"]
        assert plan["coordinates"] == body["coordinates"]
        revised = client.put("/api/navigation", json={**body, "label": "Updated rally"}).json()["plan"]
        assert revised["revision"] == 2 and revised["route_id"] == plan["route_id"]
        assert client.put("/api/navigation", content="{").status_code == 422
        assert client.put("/api/navigation", json={**body, "coordinates": []}).status_code == 422
        assert client.get("/api/navigation").json()["plans"][0]["label"] == "Updated rally"
        assert client.post("/api/send", json=response.json()["event"]).status_code == 422
        stopped = client.delete("/api/navigation").json()["plan"]
        assert stopped["revision"] > revised["revision"] and not stopped["active"]
        assert client.delete("/api/navigation").json() == {"stopped": False}
        restarted = client.put("/api/navigation", json=body).json()["plan"]
        assert restarted["route_id"] != plan["route_id"] and restarted["revision"] > stopped["revision"]
        assert len(client.get("/api/navigation").json()["plans"]) == 1


if __name__ == "__main__":
    exercise_api(Path(sys.argv[1]))
