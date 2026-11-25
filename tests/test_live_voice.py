from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

import pytest
import RNS

from retium.live_voice import (
    LiveVoiceAssembler,
    LiveVoiceError,
    LiveVoiceMessage,
    validate_live_metadata,
)
from retium.transport import GatewayReceiver
from retium.store import EventStore


STREAM_ID = "336f067d-541e-49af-aa9d-a283146f97f5"


def test_live_voice_control_round_trip() -> None:
    original = LiveVoiceMessage.control(
        LiveVoiceMessage.START,
        STREAM_ID,
        {"mime_type": "audio/webm;codecs=opus", "callsign": "FIELD-1"},
    )
    unpacked = LiveVoiceMessage()
    unpacked.unpack(original.pack())

    assert unpacked.kind == LiveVoiceMessage.START
    assert unpacked.stream_uuid == STREAM_ID
    assert unpacked.metadata()["callsign"] == "FIELD-1"


def test_live_voice_chunks_fragment_and_reassemble() -> None:
    audio = bytes(index % 251 for index in range(2400))
    messages = LiveVoiceMessage.chunk_messages(STREAM_ID, 7, audio, message_mdu=360)

    assert len(messages) > 1
    assembler = LiveVoiceAssembler()
    completed = None
    for message in messages:
        packed = message.pack()
        assert len(packed) <= 360
        unpacked = LiveVoiceMessage()
        unpacked.unpack(packed)
        completed = assembler.add(unpacked)

    assert completed == audio


def test_live_voice_rejects_oversized_chunk() -> None:
    with pytest.raises(LiveVoiceError, match="invalid live voice audio chunk"):
        LiveVoiceMessage.chunk_messages(STREAM_ID, 0, b"x" * 64_001, 360)


def test_live_voice_metadata_is_constrained() -> None:
    metadata = validate_live_metadata(
        {
            "mime_type": "audio/webm; codecs=opus",
            "callsign": " Max ",
            "icon": "cross",
            "color": "moss",
        }
    )

    assert metadata["mime_type"] == "audio/webm;codecs=opus"
    assert metadata["callsign"] == "Max"

    with pytest.raises(LiveVoiceError, match="unsupported live voice format"):
        validate_live_metadata({"mime_type": "audio/wav", "callsign": "Max"})


def test_completed_field_live_stream_is_archived_as_verified_ptt() -> None:
    saved = []
    inserted = []
    published = []

    class Store:
        def insert(self, event, sender_hash, **network):
            inserted.append((event, sender_hash, network))
            return True

    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.save_ptt = lambda clip_id, audio, mime_type: saved.append(
        (clip_id, audio, mime_type)
    )
    receiver.store = Store()
    receiver.event_callback = published.append
    receiver._archive_live_stream(
        STREAM_ID,
        {
            "link": object(),
            "sender_hash": "ab" * 16,
            "metadata": {
                "mime_type": "audio/webm;codecs=opus",
                "callsign": "Max",
                "icon": "cross",
                "color": "moss",
            },
            "chunks": [b"webm", b"-opus"],
            "started_at": 0,
        },
    )

    assert saved == [(STREAM_ID, b"webm-opus", "audio/webm")]
    assert inserted[0][0]["type"] == "ptt.broadcast"
    assert inserted[0][1] == "ab" * 16
    assert inserted[0][2]["interface_name"] == "Authenticated Reticulum live channel"
    assert published[0]["network"]["verified"] is True


def test_completed_command_live_stream_uses_normal_ptt_archive() -> None:
    archived = []
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.save_ptt = object()
    receiver.publish_ptt = lambda event, audio: archived.append((event, audio))
    receiver._archive_live_stream(
        STREAM_ID,
        {
            "link": None,
            "sender_hash": "cd" * 16,
            "metadata": {
                "mime_type": "audio/ogg;codecs=opus",
                "callsign": "COMMAND",
                "icon": "beacon",
                "color": "amber",
            },
            "chunks": [b"ogg-opus"],
            "started_at": 0,
        },
    )

    assert archived[0][0]["mime_type"] == "audio/ogg"
    assert archived[0][1] == b"ogg-opus"


def test_hosted_field_live_stream_keeps_field_identity_in_feed(tmp_path) -> None:
    field_identity = RNS.Identity()
    receiver = GatewayReceiver.__new__(GatewayReceiver)
    receiver.identity = RNS.Identity()
    receiver.destination = SimpleNamespace(hash=b"\x00" * 16)
    receiver._live_lock = Lock()
    receiver._live_streams = {}
    receiver._live_links_except = lambda: [object()]
    receiver._broadcast_live_chunk = lambda *_args: 1
    frames, saved, published = [], [], []
    receiver._broadcast_live_control = lambda *frame: frames.append(frame) or 1
    receiver.save_ptt = lambda *clip: saved.append(clip)
    receiver.event_callback = published.append
    receiver.store = EventStore(tmp_path / "events.sqlite3")
    try:
        receiver.send_live_start(
            STREAM_ID,
            {"mime_type": "audio/webm;codecs=opus", "callsign": "FIELD-1"},
            origin_identity=field_identity,
        )
        receiver.send_live_chunk(STREAM_ID, 0, b"recorded audio")
        receiver.send_live_end(STREAM_ID)

        sender = field_identity.hash.hex()
        assert frames[0][2]["sender_hash"] == sender
        assert saved == [(STREAM_ID, b"recorded audio", "audio/webm")]
        assert published[0]["network"]["sender_hash"] == sender
        [event] = receiver.store.mission_events()
        assert event["clip_id"] == STREAM_ID
        assert event["network"]["sender_hash"] == sender
    finally:
        receiver.store.close()
