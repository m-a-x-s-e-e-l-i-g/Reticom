from retium.outbox import FieldOutbox
from retium.protocol import new_event


def test_outbox_persists_events_until_removed(tmp_path):
    path = tmp_path / "field-outbox.sqlite3"
    message = new_event("chat.message", "ALPHA", message="Offline check")

    outbox = FieldOutbox(path)
    outbox.add("a" * 32, message, linked=True)
    outbox.close()

    reopened = FieldOutbox(path)
    assert reopened.count("a" * 32) == 1
    assert reopened.pending("a" * 32)[0]["event"] == message
    assert reopened.pending("a" * 32)[0]["linked"] is True
    assert reopened.pending("a" * 32)[0]["transport"] == "event"
    reopened.remove(message["id"])
    assert reopened.count("a" * 32) == 0
    reopened.close()


def test_outbox_keeps_only_latest_unsent_position_per_team(tmp_path):
    outbox = FieldOutbox(tmp_path / "field-outbox.sqlite3")
    first = new_event("position.updated", "ALPHA", lat=51.5, lon=4.3, accuracy=8)
    latest = new_event("position.updated", "ALPHA", lat=51.6, lon=4.4, accuracy=7)
    marker = new_event(
        "marker.created",
        "ALPHA",
        lat=51.55,
        lon=4.35,
        marker_type="warning",
        label="Keep marker",
    )

    outbox.add("a" * 32, first, linked=True)
    outbox.add("a" * 32, marker, linked=True)
    outbox.add("a" * 32, latest, linked=True)

    pending = outbox.pending("a" * 32)
    assert {item["event"]["id"] for item in pending} == {marker["id"], latest["id"]}
    assert first["id"] not in {item["event"]["id"] for item in pending}
    outbox.close()


def test_outbox_records_deferred_voice_transport(tmp_path):
    outbox = FieldOutbox(tmp_path / "field-outbox.sqlite3")
    voice = new_event(
        "ptt.broadcast",
        "ALPHA",
        clip_id="00112233-4455-6677-8899-aabbccddeeff",
        duration_ms=900,
        mime_type="audio/ogg",
    )

    outbox.add("a" * 32, voice, linked=True, transport="ptt")

    assert outbox.pending("a" * 32)[0]["transport"] == "ptt"
    outbox.close()
