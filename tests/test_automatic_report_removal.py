import json
import uuid

import pytest
import RNS

from retium.mission import MissionInbox, MissionPublisher
from retium.protocol import ProtocolError, new_event, sign_event, verify_envelope
from retium.store import EventStore
from test_map_reports import receiver_for
from membership_helpers import approve_test_members


@pytest.mark.parametrize("voice", [False, True])
def test_removal_is_authorized_signed_and_keeps_source_through_mission_sync(tmp_path, voice):
    store = EventStore(tmp_path / "host.sqlite")
    cache = EventStore(tmp_path / "field.sqlite")
    owner, other = RNS.Identity(), RNS.Identity()
    source = new_event("ptt.broadcast", "ALPHA", clip_id=str(uuid.uuid4()), duration_ms=1000, mime_type="audio/ogg", size_bytes=100) if voice else new_event("chat.message", "ALPHA", message="contact north 100 meters")
    store.insert(source, owner.hash.hex())
    receiver = receiver_for(store)
    approve_test_members(receiver, tmp_path, owner, other)
    publisher, inbox = MissionPublisher(), MissionInbox(cache)
    def sync():
        content = {"events": store.mission_events(), "tasks": [], "private_events": [], "team": {}}
        inbox.accept(publisher.page(owner.hash.hex(), inbox.request(), lambda: content))
    sync()
    assert not cache.recent()[0].get("automatic_report_dismissed")
    removal = new_event("report.dismissed", "BRAVO", message_id=source["id"])
    def send(identity):
        wire = sign_event(removal, identity, packet_limit=False)
        assert verify_envelope(wire)[0] == removal
        return json.loads(receiver._event_request("/event", {"envelope": wire}, b"request", b"link", identity, 0))
    assert "error" in send(other)
    assert not store.recent()[0].get("automatic_report_dismissed")
    receiver.everyone_admin = True
    assert send(other)["accepted"]
    sync()
    for result in [store.recent(), cache.recent()]:
        assert len(result) == 1
        assert result[0]["id"] == source["id"]
        assert result[0]["automatic_report_dismissed"] is True
        for key, value in source.items(): assert result[0][key] == value
    store.close(); cache.close()


def test_owner_hosted_admin_and_unknown_target(tmp_path):
    store = EventStore(tmp_path / "host.sqlite")
    receiver = receiver_for(store)
    owner, other = RNS.Identity(), RNS.Identity()
    for identity, admin in [(owner, False), (other, True)]:
        source = new_event("chat.message", "ALPHA", message="contact east 100m")
        store.insert(source, owner.hash.hex())
        removal = new_event("report.dismissed", "ALPHA", message_id=source["id"])
        if admin:
            with pytest.raises(ProtocolError): receiver.publish_local_field_event(removal, identity)
        receiver.publish_local_field_event(removal, identity, admin=admin)
        assert next(e for e in store.recent() if e["id"] == source["id"])["automatic_report_dismissed"]
    with pytest.raises(ProtocolError):
        receiver.publish_local_field_event(new_event("report.dismissed", "A", message_id=str(uuid.uuid4())), owner, admin=True)
    with pytest.raises(ProtocolError): new_event("report.dismissed", "A", message_id="invalid")
    store.close()
