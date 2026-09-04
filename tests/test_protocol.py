import json

import pytest
import RNS

from retium.protocol import ProtocolError, new_event, sign_event, verify_envelope


def test_signed_event_round_trip():
    identity = RNS.Identity()
    event = new_event("chat.message", "ALPHA", message="Actual packet payload")
    raw = sign_event(event, identity)

    verified, sender_hash = verify_envelope(raw)

    assert verified == event
    assert sender_hash == identity.hash.hex()
    assert len(raw) <= RNS.Packet.MDU


def test_tampering_breaks_signature():
    identity = RNS.Identity()
    event = new_event("chat.message", "ALPHA", message="Original")
    envelope = json.loads(sign_event(event, identity))
    envelope["event"]["message"] = "Altered"

    with pytest.raises(ProtocolError, match="signature"):
        verify_envelope(json.dumps(envelope).encode())


def test_position_requires_real_coordinates():
    with pytest.raises(ProtocolError, match="lat"):
        new_event("position.updated", "ALPHA", lat=100, lon=4.3)


def test_position_precision_is_compact_and_honest():
    identity = RNS.Identity()
    event = new_event(
        "position.updated",
        "X" * 24,
        icon="chevron",
        color="amber",
        lat=51.49294912345678,
        lon=4.289739123456789,
        accuracy=106.12345678901234,
    )

    assert event["lat"] == 51.492949
    assert event["lon"] == 4.289739
    assert event["accuracy"] == 106.1
    assert len(sign_event(event, identity)) <= RNS.Packet.MDU


def test_authenticated_link_can_carry_a_signed_event_over_packet_mdu():
    identity = RNS.Identity()
    event = new_event("chat.message", "ALPHA", message="X" * 160)

    with pytest.raises(ProtocolError, match="packet limit"):
        sign_event(event, identity)

    verified, _ = verify_envelope(sign_event(event, identity, packet_limit=False))
    assert verified == event


def test_team_join_event_fits_reticulum_packet():
    identity = RNS.Identity()
    event = new_event("team.joined", "ALPHA")

    assert len(sign_event(event, identity)) <= RNS.Packet.MDU


def test_task_completion_is_valid_over_an_authenticated_link():
    identity = RNS.Identity()
    event = new_event(
        "task.completed", "ALPHA", task_id="00112233-4455-6677-8899-aabbccddeeff"
    )

    verified, _ = verify_envelope(sign_event(event, identity, packet_limit=False))

    assert verified["task_id"] == "00112233-4455-6677-8899-aabbccddeeff"


def test_waypoint_arrival_carries_verified_operator_reference():
    identity = RNS.Identity()
    event = new_event(
        "waypoint.arrived",
        "COMMAND",
        waypoint_id="00112233-4455-6677-8899-aabbccddeeff",
        waypoint_label="Waypoint 3",
        operator_callsign="RAVEN",
        distance_m=18.37,
    )

    verified, _ = verify_envelope(sign_event(event, identity))

    assert verified["operator_callsign"] == "RAVEN"
    assert verified["distance_m"] == 18.4
    assert len(sign_event(event, identity)) <= RNS.Packet.MDU


def test_task_creation_carries_assignment_into_the_intel_feed():
    event = new_event(
        "task.created",
        "COMMAND",
        task_id="00112233-4455-6677-8899-aabbccddeeff",
        title="Hold the north gate",
        assignee="RAVEN",
    )

    assert event["title"] == "Hold the north gate"
    assert event["assignee"] == "RAVEN"


def test_drawing_is_compact_and_valid_over_an_authenticated_link():
    identity = RNS.Identity()
    event = new_event(
        "drawing.created",
        "RAVEN",
        points=[[4.289739, 51.492949], [4.290111, 51.493222]],
    )

    verified, _ = verify_envelope(sign_event(event, identity, packet_limit=False))

    assert verified["points"] == [[4.28974, 51.49295], [4.29011, 51.49322]]


def test_arrow_and_tactical_marker_types_are_valid():
    arrow = new_event(
        "drawing.created",
        "RAVEN",
        drawing_type="arrow",
        points=[[4.289739, 51.492949], [4.290111, 51.493222]],
    )

    assert arrow["drawing_type"] == "arrow"
    for marker_type in (
        "text", "car", "tank", "helicopter", "airplane",
        "casualty", "medevac", "evac-point", "medical-point",
        "rally-point", "checkpoint", "landing-zone", "search-area", "hold-line",
        "contact", "possible-movement", "drone-spotted", "fire-smoke",
        "road-blocked", "route-compromised", "bridge-damaged",
        "unit-moving", "vehicle-disabled", "radio-dead-zone",
        "supply-cache", "water-point", "last-seen",
    ):
        marker = new_event(
            "marker.created",
            "RAVEN",
            lat=51.492949,
            lon=4.289739,
            marker_type=marker_type,
            label="Unit note" if marker_type == "text" else marker_type.title(),
        )
        assert marker["marker_type"] == marker_type


def test_unknown_map_shapes_are_rejected():
    with pytest.raises(ProtocolError, match="marker type"):
        new_event(
            "marker.created", "RAVEN", lat=51.5, lon=4.3,
            marker_type="submarine", label="Unknown",
        )
    with pytest.raises(ProtocolError, match="drawing type"):
        new_event(
            "drawing.created", "RAVEN", drawing_type="circle",
            points=[[4.2, 51.4], [4.3, 51.5]],
        )


def test_map_deletions_are_signed_uuid_references():
    identity = RNS.Identity()
    marker = new_event(
        "marker.deleted",
        "RAVEN",
        marker_id="00112233-4455-6677-8899-aabbccddeeff",
    )
    drawing = new_event(
        "drawing.deleted",
        "COMMAND",
        drawing_id="11112233-4455-6677-8899-aabbccddeeff",
    )

    assert marker["marker_id"] == "00112233-4455-6677-8899-aabbccddeeff"
    assert drawing["drawing_id"] == "11112233-4455-6677-8899-aabbccddeeff"
    verified, _ = verify_envelope(sign_event(marker, identity))
    assert verified["marker_id"] == marker["marker_id"]
    assert len(sign_event(marker, identity)) <= RNS.Packet.MDU

    with pytest.raises(ProtocolError, match="marker_id"):
        new_event("marker.deleted", "RAVEN", marker_id="not-a-marker")


def test_message_deletion_is_a_signed_reference():
    identity = RNS.Identity()
    event = new_event(
        "message.deleted",
        "ALPHA",
        message_id="00112233-4455-6677-8899-aabbccddeeff",
    )

    verified, _ = verify_envelope(sign_event(event, identity))

    assert verified["message_id"] == "00112233-4455-6677-8899-aabbccddeeff"


def test_message_deletion_requires_a_uuid():
    with pytest.raises(ProtocolError, match="message_id"):
        new_event("message.deleted", "ALPHA", message_id="not-a-message")


def test_profile_update_carries_selected_operator_identity():
    identity = RNS.Identity()
    event = new_event("profile.updated", "RAVEN", icon="diamond", color="teal")

    verified, _ = verify_envelope(sign_event(event, identity))

    assert verified["callsign"] == "RAVEN"
    assert verified["icon"] == "diamond"
    assert verified["color"] == "teal"
    assert len(sign_event(event, identity)) <= RNS.Packet.MDU


def test_unknown_operator_icon_is_rejected():
    with pytest.raises(ProtocolError, match="operator icon"):
        new_event("profile.updated", "RAVEN", icon="rocket")


def test_unknown_operator_color_is_rejected():
    with pytest.raises(ProtocolError, match="operator color"):
        new_event("profile.updated", "RAVEN", color="neon")


def test_private_message_targets_a_reticulum_identity():
    recipient = "00112233445566778899aabbccddeeff"
    event = new_event(
        "private.message",
        "RAVEN",
        recipient_hash=recipient.upper(),
        message="  Move to checkpoint two  ",
    )

    assert event["recipient_hash"] == recipient
    assert event["message"] == "Move to checkpoint two"


def test_private_message_rejects_invalid_recipient():
    with pytest.raises(ProtocolError, match="Reticulum identity"):
        new_event(
            "private.message",
            "RAVEN",
            recipient_hash="not-an-identity",
            message="Test",
        )


def test_private_ptt_targets_identity_without_embedding_audio():
    identity = RNS.Identity()
    recipient = "00112233445566778899aabbccddeeff"
    event = new_event(
        "private.ptt",
        "RAVEN",
        recipient_hash=recipient,
        clip_id="00112233-4455-6677-8899-aabbccddeeff",
        duration_ms=1800,
        mime_type="audio/ogg",
    )

    verified, _ = verify_envelope(sign_event(event, identity, packet_limit=False))

    assert verified["recipient_hash"] == recipient
    assert verified["clip_id"] == event["clip_id"]
    assert verified["duration_ms"] == 1800


def test_ptt_metadata_is_signed_without_embedding_audio_in_the_packet():
    identity = RNS.Identity()
    event = new_event(
        "ptt.broadcast",
        "RAVEN",
        icon="diamond",
        clip_id="00112233-4455-6677-8899-aabbccddeeff",
        duration_ms=2400,
        mime_type="audio/webm",
    )

    verified, _ = verify_envelope(sign_event(event, identity))

    assert verified["clip_id"] == event["clip_id"]
    assert verified["duration_ms"] == 2400
    assert len(sign_event(event, identity)) <= RNS.Packet.MDU
