import json
from types import SimpleNamespace

import pytest
import RNS

from retium.protocol import ProtocolError, new_event, sign_event, verify_envelope
from retium.store import EventStore
from retium.transport import GatewayReceiver
from membership_helpers import approve_test_members


def report(**options):
    return new_event("marker.created", "ALPHA", **dict(
        {"marker_type": "road-blocked", "label": "Road blocked", "lat": 51, "lon": 4}, **options))


@pytest.mark.parametrize("kind", ["note", "other", "flooding", "electrical-hazard"])
def test_report_details_are_signed(kind):
    event = report(marker_type=kind, description="Fallen power line", urgent=True, report_status="reported")
    wire = sign_event(event, RNS.Identity(), packet_limit=False)
    assert verify_envelope(wire)[0] == event
    tampered = json.loads(wire)
    tampered["event"]["report_status"] = "cleared"
    with pytest.raises(ProtocolError, match="signature"):
        verify_envelope(json.dumps(tampered).encode())


@pytest.mark.parametrize("options", [
    {"marker_type": "note"}, {"marker_type": "other", "description": "  "},
    {"description": "x" * 161}, {"description": []}, {"urgent": "true"},
    {"report_status": []}, {"report_status": "maybe"},
])
def test_report_validation(options):
    with pytest.raises(ProtocolError):
        report(**options)


def test_legacy_markers_keep_signed_payload():
    for kind in ["warning", "observation", "obstacle", "waypoint"]:
        event = report(marker_type=kind)
        assert not {"description", "urgent", "report_status", "report_view"} & event.keys()
        assert verify_envelope(sign_event(event, RNS.Identity(), packet_limit=False))[0] == event


def receiver_for(store):
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.store, receiver.everyone_admin = store, False
    receiver.event_callback = receiver.last_packet_at = None
    receiver.destination = SimpleNamespace(hash=b"host")
    return receiver


def test_reporter_or_admin_can_update_one_pin_over_signed_link(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    owner, stranger = RNS.Identity(), RNS.Identity()
    event = report(report_status="reported")
    store.insert(event, owner.hash.hex())
    receiver = receiver_for(store)
    approve_test_members(receiver, tmp_path, owner, stranger)
    update = new_event("marker.status", "BRAVO", marker_id=event["id"], report_status="confirmed")
    def send(identity):
        wire = sign_event(update, identity, packet_limit=False)
        return json.loads(receiver._event_request("/event", {"envelope": wire}, b"request", b"link", identity, 0))
    assert "error" in send(stranger)
    assert send(owner)["accepted"]
    visible = store.recent()
    assert len(visible) == 1
    assert visible[0]["report_status"] == "reported"  # Original signature unchanged.
    assert visible[0]["report_view"]["status"] == "confirmed"
    assert visible[0]["id"] == event["id"]
    receiver.everyone_admin = True
    update = new_event("marker.status", "BRAVO", marker_id=event["id"], report_status="cleared")
    update["created_at"] += 1
    assert send(stranger)["accepted"]
    assert store.recent()[0]["report_view"]["status"] == "cleared"


def test_projection_converges_and_cached_feed_keeps_latest_status(tmp_path):
    event = report()
    confirmed = new_event("marker.status", "ALPHA", marker_id=event["id"], report_status="confirmed")
    cleared = new_event("marker.status", "ALPHA", marker_id=event["id"], report_status="cleared")
    cleared["created_at"] += 1
    views = []
    for number, updates in enumerate([[confirmed, cleared], [cleared, confirmed]]):
        store = EventStore(tmp_path / f"replica-{number}.sqlite3")
        store.insert(event, "owner")
        for update in updates:
            store.insert(update, "owner")
        views.append(store.recent()[0]["report_view"])
    assert views[0] == views[1]
    cache = EventStore(tmp_path / "cache.sqlite3")
    cache.insert(event, "owner")
    cache.cache_report_view(event["id"], views[0])
    cache.cache_report_view(event["id"], {"status": "reported", "updated_at": 1, "revision": 0})
    assert cache.recent()[0]["report_view"]["status"] == "cleared"
    assert cache.map_event(event["id"], "marker.created")["label"] == "Road blocked"


def test_hosted_field_admin_status_is_projected(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    event = report()
    store.insert(event, "someone-else")
    update = new_event("marker.status", "HOST", marker_id=event["id"], report_status="cleared")
    receiver = receiver_for(store)
    with pytest.raises(ProtocolError):
        receiver.publish_local_field_event(update, RNS.Identity())
    receiver.publish_local_field_event(update, RNS.Identity(), admin=True)
    assert store.recent()[0]["report_view"]["status"] == "cleared"


def test_quick_successive_updates_follow_revision_not_uuid_order(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    event = report()
    store.insert(event, "owner")
    for revision, status in [(2, "cleared"), (1, "confirmed")]:
        update = new_event("marker.status", "ALPHA", marker_id=event["id"], report_status=status, report_revision=revision)
        update["created_at"] = event["created_at"]
        store.insert(update, "owner")
    assert store.recent()[0]["report_view"]["status"] == "cleared"
