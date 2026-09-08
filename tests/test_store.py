from retium.protocol import new_event
from retium.store import EventStore
import time


def test_expired_marker_is_hidden_without_deleting_history(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "expiry.sqlite3")
    now = int(time.time())
    event = new_event("marker.created", "Command", lat=52, lon=5,
                      marker_type="observation", label="Heat signature", expires_at=now + 30)
    store.insert(event, "command")
    assert any(item["id"] == event["id"] for item in store.mission_events())
    monkeypatch.setattr("retium.store.time.time", lambda: now + 31)
    assert not any(item["id"] == event["id"] for item in store.mission_events())
    assert store.event_exists(event["id"], "marker.created")


def test_store_is_idempotent_and_preserves_network_evidence(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    event = new_event("chat.message", "BRAVO", message="Network check")

    assert store.insert(event, "abcd", packet_hash="1234", interface_name="TCP")
    assert not store.insert(event, "abcd", packet_hash="1234", interface_name="TCP")

    received = store.recent()
    assert len(received) == 1
    assert received[0]["message"] == "Network check"
    assert received[0]["network"]["verified"] is True
    assert received[0]["network"]["packet_hash"] == "1234"

    operators = store.operators()
    assert operators == [
        {
            "sender_hash": "abcd",
            "callsign": "BRAVO",
            "icon": "dot",
            "color": "moss",
            "last_seen": received[0]["network"]["received_at"],
            "event_count": 1,
            "position": None,
        }
    ]
    assert store.count(since=int(time.time()) + 1) == 0
    assert store.recent(since=int(time.time()) + 1) == []


def test_local_event_is_visible_while_queued_and_can_be_marked_verified(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    event = new_event(
        "marker.created",
        "ALPHA",
        lat=51.5,
        lon=4.3,
        marker_type="warning",
        label="Local warning",
    )
    assert store.insert(
        event,
        "alpha",
        interface_name="Local device · Reticulum outbox",
        delivery_status="queued",
    )

    queued = store.recent()[0]
    assert queued["network"]["verified"] is False
    assert queued["network"]["queued"] is True

    store.mark_verified(event["id"])
    delivered = store.recent()[0]
    assert delivered["network"]["verified"] is True
    assert delivered["network"]["queued"] is False


def test_operator_summary_includes_latest_position(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    position = new_event(
        "position.updated", "BRAVO", lat=51.5, lon=4.3, accuracy=7
    )
    assert store.insert(position, "abcd", packet_hash="5678", interface_name="TCP")

    operator = store.operators()[0]
    assert operator["sender_hash"] == "abcd"
    assert operator["icon"] == "dot"
    assert operator["color"] == "moss"
    assert operator["position"]["lat"] == 51.5
    assert operator["position"]["accuracy"] == 7.0


def test_position_marks_activity_only_after_more_than_one_hour_without_fix(
    tmp_path, monkeypatch
):
    store = EventStore(tmp_path / "events.sqlite3")
    first = new_event("position.updated", "BRAVO", lat=51.5, lon=4.3)
    exactly_one_hour = new_event("position.updated", "BRAVO", lat=51.6, lon=4.4)
    resumed = new_event("position.updated", "BRAVO", lat=51.7, lon=4.5)
    another_operator = new_event("position.updated", "CHARLIE", lat=51.8, lon=4.6)
    received_times = iter([1_000, 4_600, 8_201, 8_202])
    monkeypatch.setattr("retium.store.time.time", lambda: next(received_times))
    for event, sender in (
        (first, "bravo"),
        (exactly_one_hour, "bravo"),
        (resumed, "bravo"),
        (another_operator, "charlie"),
    ):
        assert store.insert(event, sender)

    events = {event["id"]: event for event in store.recent()}
    assert events[first["id"]]["network"]["became_active"] is False
    assert events[exactly_one_hour["id"]]["network"]["became_active"] is False
    assert events[resumed["id"]]["network"]["became_active"] is True
    assert events[resumed["id"]]["network"]["position_silence_seconds"] == 3_601
    assert events[another_operator["id"]]["network"]["became_active"] is False


def test_signed_tombstone_hides_only_the_owners_message(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    own = new_event("chat.message", "ALPHA", message="Remove me")
    other = new_event("chat.message", "BRAVO", message="Keep me")
    assert store.insert(own, "alpha")
    assert store.insert(other, "bravo")
    assert store.message_owned_by(own["id"], "alpha")
    assert not store.message_owned_by(own["id"], "bravo")

    tombstone = new_event("message.deleted", "ALPHA", message_id=own["id"])
    assert store.insert(tombstone, "alpha")

    assert [event["id"] for event in store.recent()] == [other["id"]]
    assert store.count() == 1


def test_forged_tombstone_does_not_hide_another_operators_message(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    message = new_event("chat.message", "BRAVO", message="Keep me")
    assert store.insert(message, "bravo")
    forged = new_event("message.deleted", "ALPHA", message_id=message["id"])
    assert store.insert(forged, "alpha")

    assert [event["id"] for event in store.recent()] == [message["id"]]


def test_authenticated_team_admin_tombstone_hides_another_operators_message(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    message = new_event("chat.message", "BRAVO", message="Admin may remove me")
    deletion = new_event("message.deleted", "ALPHA", message_id=message["id"])
    assert store.insert(message, "bravo")
    assert store.insert(
        deletion,
        "alpha",
        interface_name="Team admin · Authenticated Reticulum Link",
    )

    assert store.recent() == []


def test_map_tombstones_enforce_field_ownership_and_command_moderation(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    own_marker = new_event(
        "marker.created", "ALPHA", lat=51.5, lon=4.3,
        marker_type="warning", label="Remove me",
    )
    other_marker = new_event(
        "marker.created", "BRAVO", lat=51.6, lon=4.4,
        marker_type="waypoint", label="Command removes me",
    )
    assert store.insert(own_marker, "alpha")
    assert store.insert(other_marker, "bravo")
    assert store.map_event_owned_by(own_marker["id"], "marker.created", "alpha")
    assert not store.map_event_owned_by(own_marker["id"], "marker.created", "bravo")

    forged = new_event("marker.deleted", "BRAVO", marker_id=own_marker["id"])
    assert store.insert(forged, "bravo")
    assert own_marker["id"] in {event["id"] for event in store.recent()}

    own_delete = new_event("marker.deleted", "ALPHA", marker_id=own_marker["id"])
    assert store.insert(own_delete, "alpha")
    command_delete = new_event(
        "marker.deleted", "COMMAND", marker_id=other_marker["id"]
    )
    assert store.insert(
        command_delete,
        "command",
        interface_name="Command origin · Reticulum feed",
    )

    assert store.recent() == []


def test_clear_communications_preserves_operational_events(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    chat = new_event("chat.message", "ALPHA", message="Clear me")
    tombstone = new_event("message.deleted", "ALPHA", message_id=chat["id"])
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"
    voice = new_event(
        "ptt.broadcast",
        "BRAVO",
        clip_id=clip_id,
        duration_ms=1200,
        mime_type="audio/webm",
    )
    position = new_event(
        "position.updated", "BRAVO", lat=51.5, lon=4.3, accuracy=7
    )
    task = new_event(
        "task.created",
        "COMMAND",
        task_id="11112233-4455-6677-8899-aabbccddeeff",
        title="Keep assignment",
        assignee=None,
    )
    for event, sender in (
        (chat, "alpha"),
        (tombstone, "alpha"),
        (voice, "bravo"),
        (position, "bravo"),
        (task, "command"),
    ):
        assert store.insert(event, sender)

    cleared = store.clear_communications(since=int(time.time()) - 1)

    assert cleared == {"events": 3, "clip_ids": [clip_id]}
    assert {event["type"] for event in store.recent()} == {
        "position.updated",
        "task.created",
    }
