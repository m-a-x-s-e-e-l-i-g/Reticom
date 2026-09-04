import json

import RNS

from retium.protocol import new_event
from retium.store import EventStore
from retium.transport import GatewayReceiver


class _FieldIdentity:
    hash = b"field"


def test_gateway_enforces_and_records_command_team_admin_mode(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    message = new_event("chat.message", "BRAVO", message="Team-visible")
    deletion = new_event("message.deleted", "ALPHA", message_id=message["id"])
    assert store.insert(message, "other")

    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.store = store
    receiver.everyone_admin = False
    receiver.event_callback = None
    receiver.last_packet_at = None
    monkeypatch.setattr(
        "retium.transport.verify_envelope",
        lambda _: (deletion, b"field".hex()),
    )

    rejected = json.loads(
        receiver._event_request("/event", {"envelope": b"signed"}, b"request-1", b"link", _FieldIdentity(), 0)
    )
    assert rejected["error"] == "only the original sender can remove this message"
    assert [event["id"] for event in store.recent()] == [message["id"]]

    receiver.everyone_admin = True
    accepted = json.loads(
        receiver._event_request("/event", {"envelope": b"signed"}, b"request-2", b"link", _FieldIdentity(), 0)
    )
    assert accepted == {"accepted": True, "event_id": deletion["id"]}
    assert store.recent() == []


def test_gateway_admin_requests_require_mode_and_known_identified_operator(tmp_path):
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.store = EventStore(tmp_path / "events.sqlite3")
    receiver.everyone_admin = False
    receiver.admin_response = lambda action, payload, sender: {
        "action": action,
        "payload": payload,
        "sender": sender,
    }
    receiver._known_private_identity = lambda _: True

    rejected = json.loads(
        receiver._admin_request(
            "/admin",
            {"action": "modules.set", "payload": {"modules": ["tasks"]}},
            b"request",
            b"link",
            _FieldIdentity(),
            0,
        )
    )
    assert rejected == {"error": "team admin mode is not enabled"}

    receiver.everyone_admin = True
    accepted = json.loads(
        receiver._admin_request(
            "/admin",
            {"action": "modules.set", "payload": {"modules": ["tasks"]}},
            b"request",
            b"link",
            _FieldIdentity(),
            0,
        )
    )
    assert accepted == {
        "accepted": True,
        "action": "modules.set",
        "payload": {"modules": ["tasks"]},
        "sender": b"field".hex(),
    }


def test_hosted_field_event_keeps_the_field_identity(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.store = store
    receiver.event_callback = None
    receiver.last_packet_at = None
    receiver.destination = type("Destination", (), {"hash": b"hosted-destination"})()
    field_identity = RNS.Identity()
    event = new_event("team.joined", "ALPHA", icon="diamond", color="moss")

    delivery = receiver.publish_local_field_event(event, field_identity, admin=True)

    saved = store.recent()[0]
    assert delivery["status"] == "hosted"
    assert delivery["transport"] == "local signed origin · Reticulum feed"
    assert saved["network"]["sender_hash"] == field_identity.hash.hex()
    assert saved["network"]["interface"] == "Hosted Field origin · Reticulum feed"
