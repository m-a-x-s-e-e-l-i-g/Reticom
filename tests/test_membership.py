import json
import threading
from types import SimpleNamespace

import pytest
import RNS

from retium.membership import Membership, MAX_PENDING
from retium.transport import GatewayReceiver, FieldSender
from retium.protocol import new_event, sign_event
from retium.live_heading import HeadingHub, encode, HELLO, SAMPLE
from retium.live_voice import LiveVoiceMessage


def test_unknown_and_previously_seen_are_not_approved_and_rejection_is_durable(tmp_path):
    owner, member = RNS.Identity(), RNS.Identity()
    access = Membership(tmp_path, owner)
    key = member.hash.hex()
    assert access.approved(owner.hash.hex())
    assert not access.approved(key)
    assert access.request(key, "Phone")["status"] == "pending"
    access.request(key, "Spoofed rename")
    assert access.listing()["requests"][0]["callsign"] == "Phone"
    access.decide(key, "rejected")
    assert access.request(key, "Phone")["status"] == "rejected"
    restored = Membership(tmp_path, owner)
    assert restored.status(key) == "rejected"
    restored.decide(key, "approved")
    assert restored.approved(key)
    restored.decide(key, "revoked")
    assert restored.request(key, "Phone")["status"] == "revoked"
    assert not restored.listing()["requests"]


def test_owner_signature_team_binding_and_monotonic_replica_revocation(tmp_path):
    owner, backup, member = RNS.Identity(), RNS.Identity(), RNS.Identity()
    access = Membership(tmp_path / "owner", owner)
    replica = Membership(tmp_path / "backup", backup, access.root)
    assert not replica.approved(member.hash.hex())
    access.decide(member.hash.hex(), "approved", "Phone")
    older = json.loads(json.dumps(access.envelope))
    assert replica.accept(older)
    assert replica.approved(member.hash.hex())
    with pytest.raises(PermissionError): replica.decide(member.hash.hex(), "approved")
    access.decide(member.hash.hex(), "revoked")
    replica.accept(access.envelope)
    assert not replica.accept(older)
    assert not replica.approved(member.hash.hex())
    forged = json.loads(json.dumps(access.envelope))
    forged["policy"]["members"][member.hash.hex()]["status"] = "approved"
    with pytest.raises(ValueError): replica.accept(forged)
    other = Membership(tmp_path / "other", RNS.Identity())
    with pytest.raises(ValueError): replica.accept(other.envelope)


def receiver(tmp_path):
    gateway = GatewayReceiver.__new__(GatewayReceiver)
    gateway.identity = RNS.Identity()
    gateway.membership = Membership(tmp_path, gateway.identity)
    gateway.continuity = None
    gateway._live_lock = threading.RLock()
    gateway._live_links = set()
    gateway.store = SimpleNamespace(operators=lambda **kw: [{"sender_hash": "a"*32}])
    return gateway


@pytest.mark.parametrize("path,method", [("tasks", "_tasks_request"), ("feed", "_feed_request"), ("event", "_event_request"),
    ("ptt", "_ptt_request"), ("audio", "_audio_request"), ("transcription", "_transcription_request"),
    ("private/messages", "_private_messages_request"), ("private/send", "_private_send_request"),
    ("private/ptt", "_private_ptt_request"), ("admin", "_admin_request")])
def test_all_application_rpcs_fail_closed_even_for_identified_or_historical_operators(tmp_path, path, method):
    gateway = receiver(tmp_path)
    outsider = RNS.Identity()
    gateway.everyone_admin = True
    for status in ("pending", "rejected", "revoked", "removed"):
        if status != "pending": gateway.membership.decide(outsider.hash.hex(), status, "Phone")
        response = json.loads(getattr(gateway, method)("/"+path, {}, b"x", b"l", outsider, 0))
        assert response["membership_status"] == status
        assert "events" not in response and "audio" not in response and "tasks" not in response
    assert not gateway._known_private_identity("a"*32)


def test_only_membership_request_is_available_to_unapproved_identity(tmp_path):
    gateway = receiver(tmp_path); outsider = RNS.Identity()
    response = gateway._membership_request("/membership", {"callsign": "Phone", "status": "approved"}, b"r", b"l", outsider, 0)
    assert "error" in json.loads(response)
    response = gateway._membership_request("/membership", {"callsign": "Phone"}, b"r", b"l", outsider, 0)
    assert json.loads(response)["status"] == "pending"
    assert "members" not in json.loads(response)
    gateway.membership.decide(outsider.hash.hex(), "approved")
    assert gateway._identity_error(outsider) is None


def test_everyone_admin_can_delegate_membership_decisions_to_the_owner(tmp_path):
    gateway = receiver(tmp_path)
    gateway.everyone_admin = True
    gateway.enforce_membership = lambda: None
    admin, joining = RNS.Identity(), RNS.Identity()
    denied = json.loads(gateway._membership_admin_request(
        "/membership/admin", {"action": "list"}, b"r", b"l", admin, 0
    ))
    assert denied["error"] == "Team admin rights are required"
    gateway.membership.decide(admin.hash.hex(), "approved", "Admin")
    listing = json.loads(gateway._membership_admin_request(
        "/membership/admin", {"action": "list"}, b"r", b"l", admin, 0
    ))
    assert listing["owner"] is True
    decision = json.loads(gateway._membership_admin_request(
        "/membership/admin",
        {"action": "decide", "identity": joining.hash.hex(), "status": "approved", "callsign": "Joining"},
        b"r", b"l", admin, 0,
    ))
    assert any(member["identity"] == joining.hash.hex() and member["status"] == "approved" for member in decision["members"])


def test_live_voice_and_headings_recheck_access_after_revocation(tmp_path, monkeypatch):
    gateway = receiver(tmp_path); member = RNS.Identity(); frames=[]; sent=[]
    link=SimpleNamespace(get_remote_identity=lambda: member)
    # Live voice is rejected before subscribing or acknowledging HELLO.
    gateway._drop_live_link = lambda link: None
    assert gateway._receive_live(link, LiveVoiceMessage.hello()) is True
    assert not gateway._live_links
    class Link:
        def get_remote_identity(self): return member
        def get_establishment_rate(self): return 1e6
    link = Link()
    hub = HeadingHub(frames.append, authorized=gateway.member_allowed)
    monkeypatch.setattr("retium.live_heading.transmit", lambda link, raw: sent.append(raw))
    hub.receive(link, encode(HELLO)); assert not sent
    gateway.membership.decide(member.hash.hex(), "approved", "Phone")
    hub.receive(link, encode(HELLO)); assert sent
    gateway.membership.decide(member.hash.hex(), "revoked")
    before=len(sent)
    hub.receive(link, encode(SAMPLE, 25)); assert not frames
    hub.publish(30,gateway.identity.hash)
    assert len(sent)==before


def test_pending_queue_bounded_and_cannot_replace_a_denied_identity(tmp_path):
    access=Membership(tmp_path,RNS.Identity())
    for i in range(MAX_PENDING): access.request(f"{i:032x}","Phone")
    with pytest.raises(ValueError): access.request("f"*32,"Another")
    assert len(access.listing()["requests"])==MAX_PENDING


def test_revocation_closes_existing_connections(tmp_path):
    gateway=receiver(tmp_path); member=RNS.Identity(); torn=[]
    gateway.destination=SimpleNamespace(links=[SimpleNamespace(get_remote_identity=lambda: member,teardown=lambda:torn.append(True))])
    gateway.membership.decide(member.hash.hex(),"approved","Phone")
    gateway.enforce_membership(); assert not torn
    gateway.membership.decide(member.hash.hex(),"revoked")
    gateway.enforce_membership(); assert torn


def test_legacy_packets_cannot_bypass_approval(tmp_path):
    gateway = receiver(tmp_path); member = RNS.Identity(); saved = []
    gateway.store = SimpleNamespace(insert=lambda *args, **kwargs: saved.append(args) or True)
    gateway.event_callback = None
    packet = SimpleNamespace(packet_hash=b"test")
    event = new_event("chat.message", "Phone", message="Legacy packet")
    wire = sign_event(event, member, packet_limit=False)
    gateway._packet_received(wire, packet)
    assert not saved and "approval" in gateway.last_error
    gateway._packet_received(sign_event(new_event("team.joined", "Phone"), member, packet_limit=False), packet)
    assert not saved and gateway.membership.listing()["requests"][0]["identity"] == member.hash.hex()
    gateway.membership.decide(member.hash.hex(), "approved")
    gateway._packet_received(wire, packet)
    assert len(saved) == 1
    gateway.membership.decide(member.hash.hex(), "revoked")
    gateway._packet_received(wire, packet)
    assert len(saved) == 1


def test_leaving_closes_streams_and_clears_the_remote_team():
    sender = FieldSender.__new__(FieldSender); closed = []
    sender._send_lock = threading.Lock()
    sender.close_live_link = lambda: closed.append("voice")
    sender.headings = SimpleNamespace(close=lambda: closed.append("heading"))
    sender.gateway_hash, sender.host_directory = b"team", object()
    sender.membership_status, sender.last_delivery = "approved", {"status": "delivered"}
    sender.clear_gateway()
    assert closed == ["voice", "heading"]
    assert sender.gateway_hash is None and sender.host_directory is None
    assert sender.membership_status == "unknown" and sender.last_delivery is None


def test_sending_an_event_requires_application_acknowledgment():
    sender = FieldSender.__new__(FieldSender)
    def deny(event, timeout):
        raise PermissionError("Team membership approval required")
    sender.send_link_event = deny
    with pytest.raises(PermissionError):
        sender.send_event(new_event("chat.message", "Phone", message="Keep this queued"))


def test_removal_is_signed_durable_hidden_and_cannot_requeue(tmp_path):
    owner, member, backup = RNS.Identity(), RNS.Identity(), RNS.Identity()
    access = Membership(tmp_path / "owner", owner)
    key = member.hash.hex()
    access.request(key, "Phone")
    access.decide(key, "approved")
    old = json.loads(json.dumps(access.envelope))
    access.decide(key, "removed")
    restored = Membership(tmp_path / "owner", owner)
    assert restored.status(key) == "removed"
    assert restored.request(key, "Phone")["status"] == "removed"
    assert restored.listing()["members"] == []
    assert restored.listing()["requests"] == []
    replica = Membership(tmp_path / "backup", backup, access.root)
    assert replica.accept(restored.envelope)
    assert not replica.accept(old)
    assert not replica.approved(key)
    with pytest.raises(ValueError):
        restored.decide(owner.hash.hex(), "removed")


def test_removal_deletes_only_selected_operator_history_and_closes_links(tmp_path):
    from retium.store import EventStore, PrivateMessageStore
    from retium.replication import ReplicatedTable
    from retium.mission import MissionPublisher
    gateway = receiver(tmp_path)
    member, other = RNS.Identity(), RNS.Identity()
    key = member.hash.hex()
    gateway.store = EventStore(tmp_path / "events.sqlite3")
    gateway.private_store = PrivateMessageStore(tmp_path / "private.sqlite3")
    replica = ReplicatedTable(gateway.store, "events", "event_id", "host")
    event = new_event("chat.message", "Phone", message="Remove me")
    kept = new_event("chat.message", "Other", message="Keep me")
    gateway.store.insert(event, key)
    gateway.store.insert(kept, other.hash.hex())
    gateway.private_store.insert({**event, "type": "private.ptt", "recipient_hash": other.hash.hex(), "clip_id": "12345678-1234-1234-1234-123456789abc"}, key)
    media_removed = []
    gateway.remove_operator_media = media_removed.append
    gateway.missions = MissionPublisher()
    from retium.mission import MissionInbox
    cache = EventStore(tmp_path / "cache.sqlite3")
    inbox = MissionInbox(cache)
    build = lambda: {"events": gateway.store.mission_events(), "tasks": [], "private_events": [], "team": {}}
    inbox.accept(gateway.missions.page("viewer", inbox.request(), build))
    assert any(e["id"] == event["id"] for e in cache.recent())
    torn = []
    gateway.destination = SimpleNamespace(links=[SimpleNamespace(get_remote_identity=lambda: member, teardown=lambda: torn.append(True))])
    gateway.membership.decide(key, "removed", "Phone")
    gateway.enforce_membership()
    assert torn
    assert [e["id"] for e in gateway.store.recent()] == [kept["id"]]
    assert not gateway.private_store.recent(other.hash.hex())
    assert media_removed == ["12345678-1234-1234-1234-123456789abc"]
    assert [o["sender_hash"] for o in gateway.store.operators()] == [other.hash.hex()]
    assert not gateway.missions.snapshots
    inbox.accept(gateway.missions.page("viewer", inbox.request(), build))
    assert [e["id"] for e in cache.recent()] == [kept["id"]]
    cache.close()
    gateway.missions.snapshots.clear()
    assert any(row["key"] == event["id"] and row["payload"] is None for row in replica.page()["rows"])
    assert not gateway.store.insert(event, key)  # A stale replay cannot resurrect deleted IDs.
    gateway.missions.snapshots["fresh"] = "current"
    gateway.enforce_membership()
    assert gateway.missions.snapshots == {"fresh": "current"}
    gateway.store.close()
    gateway.private_store.close()
