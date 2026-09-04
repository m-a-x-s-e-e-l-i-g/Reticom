import json

from retium.protocol import new_event
from retium.store import PrivateMessageStore
from retium.transport import GatewayReceiver


ALPHA = "00112233445566778899aabbccddeeff"
BRAVO = "10112233445566778899aabbccddeeff"
CHARLIE = "20112233445566778899aabbccddeeff"


def test_private_store_only_returns_messages_visible_to_identity(tmp_path):
    store = PrivateMessageStore(tmp_path / "private.sqlite3")
    alpha_to_bravo = new_event(
        "private.message", "ALPHA", recipient_hash=BRAVO, message="For Bravo"
    )
    charlie_to_bravo = new_event(
        "private.message", "CHARLIE", recipient_hash=BRAVO, message="Also for Bravo"
    )
    alpha_to_charlie = new_event(
        "private.message", "ALPHA", recipient_hash=CHARLIE, message="Not for Bravo"
    )
    assert store.insert(alpha_to_bravo, ALPHA, packet_hash="one")
    assert store.insert(charlie_to_bravo, CHARLIE, packet_hash="two")
    assert store.insert(alpha_to_charlie, ALPHA, packet_hash="three")

    visible = store.recent(BRAVO)

    assert [message["message"] for message in visible] == [
        "For Bravo",
        "Also for Bravo",
    ]
    assert all(message["network"]["verified"] for message in visible)


def test_private_conversation_filter_and_deduplication(tmp_path):
    store = PrivateMessageStore(tmp_path / "private.sqlite3")
    first = new_event(
        "private.message", "ALPHA", recipient_hash=BRAVO, message="First"
    )
    reply = new_event(
        "private.message", "BRAVO", recipient_hash=ALPHA, message="Reply"
    )
    unrelated = new_event(
        "private.message", "CHARLIE", recipient_hash=ALPHA, message="Separate chat"
    )
    assert store.insert(first, ALPHA)
    assert not store.insert(first, ALPHA)
    assert store.insert(reply, BRAVO)
    assert store.insert(unrelated, CHARLIE)

    conversation = store.recent(ALPHA, peer_hash=BRAVO)

    assert [message["message"] for message in conversation] == ["First", "Reply"]


def test_private_voice_is_visible_only_to_sender_and_recipient(tmp_path):
    store = PrivateMessageStore(tmp_path / "private.sqlite3")
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"
    voice = new_event(
        "private.ptt",
        "ALPHA",
        recipient_hash=BRAVO,
        clip_id=clip_id,
        duration_ms=1200,
        mime_type="audio/webm",
    )
    assert store.insert(voice, ALPHA)

    assert store.clip_visibility(clip_id, ALPHA) is True
    assert store.clip_visibility(clip_id, BRAVO) is True
    assert store.clip_visibility(clip_id, CHARLIE) is False
    assert store.clip_visibility("11112233-4455-6677-8899-aabbccddeeff", CHARLIE) is None


class _Identity:
    def __init__(self, identity_hash: str):
        self.hash = bytes.fromhex(identity_hash)


def test_authenticated_feed_includes_only_the_requesters_private_events(tmp_path):
    store = PrivateMessageStore(tmp_path / "private.sqlite3")
    for sender, recipient, message in (
        (ALPHA, BRAVO, "For Bravo"),
        (ALPHA, CHARLIE, "For Charlie"),
    ):
        assert store.insert(
            new_event(
                "private.message", sender[:5], recipient_hash=recipient, message=message
            ),
            sender,
        )
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.feed_response = lambda: b'{"events":[],"team":{"name":"Test"}}'
    receiver.private_store = store
    receiver.team_created_at = None

    payload = json.loads(
        receiver._feed_request("/feed", b"", b"request", b"link", _Identity(BRAVO), 0)
    )

    assert [event["message"] for event in payload["private_events"]] == ["For Bravo"]


def test_private_voice_request_is_authenticated_and_stored(tmp_path, monkeypatch):
    store = PrivateMessageStore(tmp_path / "private.sqlite3")
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"
    voice = new_event(
        "private.ptt",
        "ALPHA",
        recipient_hash=BRAVO,
        clip_id=clip_id,
        duration_ms=900,
        mime_type="audio/ogg",
    )
    saved = []
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.private_store = store
    receiver.save_ptt = lambda clip, audio, mime: saved.append((clip, audio, mime))
    receiver._known_private_identity = lambda _: True
    monkeypatch.setattr("retium.transport.verify_envelope", lambda _: (voice, ALPHA))

    payload = json.loads(
        receiver._private_ptt_request(
            "/private/ptt",
            {"envelope": b"signed", "audio": b"voice"},
            b"request",
            b"link",
            _Identity(ALPHA),
            0,
        )
    )

    assert payload == {"accepted": True, "event_id": voice["id"]}
    assert saved == [(clip_id, b"voice", "audio/ogg")]
    assert store.recent(BRAVO)[0]["type"] == "private.ptt"
