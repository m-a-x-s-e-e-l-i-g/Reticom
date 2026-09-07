from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import RNS

EVENT_TYPES = {
    "chat.message",
    "drawing.created",
    "drawing.deleted",
    "position.updated",
    "marker.created",
    "marker.status",
    "marker.deleted",
    "message.deleted",
    "report.dismissed",
    "navigation.updated",
    "navigation.stopped",
    "profile.updated",
    "private.message",
    "private.ptt",
    "ptt.broadcast",
    "task.created",
    "task.completed",
    "team.joined",
    "waypoint.arrived",
}
OPERATOR_ICONS = {
    "arrow",
    "beacon",
    "bolt",
    "chevron",
    "cross",
    "diamond",
    "dot",
    "hex",
    "square",
    "star",
    "target",
    "triangle",
}
OPERATOR_COLORS = {"amber", "moss", "plum", "rust", "steel", "teal"}
MAX_CALLSIGN_LENGTH = 24
MAX_MESSAGE_LENGTH = 160
MAX_LABEL_LENGTH = 80
MAX_DRAWING_POINTS = 48
MARKER_TYPES = {
    "note",
    "other",
    "flooding",
    "electrical-hazard",
    "waypoint",
    "warning",
    "observation",
    "obstacle",
    "text",
    "car",
    "tank",
    "helicopter",
    "airplane",
    "casualty",
    "medevac",
    "evac-point",
    "medical-point",
    "rally-point",
    "checkpoint",
    "landing-zone",
    "search-area",
    "hold-line",
    "contact",
    "possible-movement",
    "drone-spotted",
    "fire-smoke",
    "road-blocked",
    "route-compromised",
    "bridge-damaged",
    "unit-moving",
    "vehicle-disabled",
    "radio-dead-zone",
    "supply-cache",
    "water-point",
    "last-seen",
    "command-post",
    "base-camp",
    "objective",
    "assembly-point",
}
DRAWING_TYPES = {"trace", "arrow", "area"}
DRAWING_COLORS = {"amber", "red", "green", "blue", "purple", "white"}
DRAWING_FILLS = {"none", "solid", "lines", "crosses"}


class ProtocolError(ValueError):
    pass


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a number")
    value = float(value)
    if not minimum <= value <= maximum:
        raise ProtocolError(f"{name} must be between {minimum} and {maximum}")
    return value


def validate_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("event must be an object")

    event_type = value.get("type")
    if event_type not in EVENT_TYPES:
        raise ProtocolError("unsupported event type")

    event_id = value.get("id")
    try:
        uuid.UUID(str(event_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProtocolError("event id must be a UUID") from exc

    callsign = str(value.get("callsign", "")).strip()
    if not callsign or len(callsign) > MAX_CALLSIGN_LENGTH:
        raise ProtocolError(f"callsign must be 1-{MAX_CALLSIGN_LENGTH} characters")

    created_at = value.get("created_at")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise ProtocolError("created_at must be a Unix timestamp")

    event: dict[str, Any] = {
        "id": str(event_id),
        "type": event_type,
        "callsign": callsign,
        "created_at": created_at,
    }

    icon = value.get("icon")
    if icon is not None:
        if icon not in OPERATOR_ICONS:
            raise ProtocolError("unsupported operator icon")
        event["icon"] = str(icon)

    color = value.get("color")
    if color is not None:
        if color not in OPERATOR_COLORS:
            raise ProtocolError("unsupported operator color")
        event["color"] = str(color)

    if event_type == "chat.message":
        message = str(value.get("message", "")).strip()
        if not message or len(message) > MAX_MESSAGE_LENGTH:
            raise ProtocolError(f"message must be 1-{MAX_MESSAGE_LENGTH} characters")
        event["message"] = message

    if event_type in {"private.message", "private.ptt"}:
        recipient_hash = str(value.get("recipient_hash", "")).strip().lower()
        try:
            recipient_bytes = bytes.fromhex(recipient_hash)
        except ValueError as exc:
            raise ProtocolError("private message recipient must be a Reticulum identity") from exc
        if len(recipient_bytes) != RNS.Reticulum.TRUNCATED_HASHLENGTH // 8:
            raise ProtocolError("private message recipient must be a Reticulum identity")
        event["recipient_hash"] = recipient_hash

    if event_type == "private.message":
        message = str(value.get("message", "")).strip()
        if not message or len(message) > MAX_MESSAGE_LENGTH:
            raise ProtocolError(f"message must be 1-{MAX_MESSAGE_LENGTH} characters")
        event["message"] = message

    if event_type in {"position.updated", "marker.created"}:
        event["lat"] = round(_number(value.get("lat"), "lat", -90, 90), 6)
        event["lon"] = round(_number(value.get("lon"), "lon", -180, 180), 6)

    if event_type == "position.updated":
        accuracy = value.get("accuracy")
        if accuracy is not None:
            event["accuracy"] = round(
                _number(accuracy, "accuracy", 0, 100000), 1
            )

    if event_type == "marker.created":
        label = str(value.get("label", "")).strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise ProtocolError(f"label must be 1-{MAX_LABEL_LENGTH} characters")
        marker_type = str(value.get("marker_type", "waypoint")).strip().lower()
        if marker_type not in MARKER_TYPES:
            raise ProtocolError("unsupported marker type")
        event["label"] = label
        event["marker_type"] = marker_type
        if "description" in value or marker_type in {"note", "other"}:
            description = value.get("description", "")
            if not isinstance(description, str) or len(description.strip()) > 160:
                raise ProtocolError("report description must be at most 160 characters")
            description = description.strip()
            if marker_type in {"note", "other"} and not description:
                raise ProtocolError("Describe what you are reporting")
            event["description"] = description
        if "urgent" in value:
            if type(value["urgent"]) is not bool:
                raise ProtocolError("urgent must be a boolean")
            event["urgent"] = value["urgent"]

    if event_type == "marker.status" or (event_type == "marker.created" and "report_status" in value):
        status = value.get("report_status")
        if not isinstance(status, str) or status not in {"reported", "confirmed", "cleared"}:
            raise ProtocolError("report status must be reported, confirmed or cleared")
        event["report_status"] = status
        if event_type == "marker.status" and "report_revision" in value:
            revision = value["report_revision"]
            if type(revision) is not int or not 1 <= revision <= 1_000_000_000:
                raise ProtocolError("report revision must be a positive integer")
            event["report_revision"] = revision

    if event_type == "drawing.created":
        drawing_type = str(value.get("drawing_type", "trace")).strip().lower()
        if drawing_type not in DRAWING_TYPES:
            raise ProtocolError("unsupported drawing type")
        points = value.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_DRAWING_POINTS:
            raise ProtocolError(f"drawing must contain 2-{MAX_DRAWING_POINTS} points")
        clean_points: list[list[float]] = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ProtocolError("drawing points must be [longitude, latitude]")
            clean_points.append(
                [
                    round(_number(point[0], "longitude", -180, 180), 5),
                    round(_number(point[1], "latitude", -90, 90), 5),
                ]
            )
        event["points"] = clean_points
        if drawing_type != "trace":
            event["drawing_type"] = drawing_type
        # Optional fields stay absent on legacy traces: preserve signed payloads.
        if "label" in value:
            label = str(value["label"]).strip()
            if len(label) > MAX_LABEL_LENGTH:
                raise ProtocolError(f"drawing name must be at most {MAX_LABEL_LENGTH} characters")
            if label:
                event["label"] = label
        if "drawing_color" in value:
            if not isinstance(value["drawing_color"], str) or value["drawing_color"] not in DRAWING_COLORS:
                raise ProtocolError("unsupported drawing color")
            event["drawing_color"] = value["drawing_color"]
        if "fill_style" in value:
            if not isinstance(value["fill_style"], str) or value["fill_style"] not in DRAWING_FILLS:
                raise ProtocolError("unsupported drawing fill")
            if drawing_type != "area" and value["fill_style"] != "none":
                raise ProtocolError("only a closed area can have a fill")
            event["fill_style"] = value["fill_style"]
        if drawing_type == "area":
            # Reject collapsed areas after wire precision rounding. Unwrap at the
            # dateline before measuring; do not mistake a short crossing for 360°.
            unwrapped = []
            for lon, lat in clean_points:
                if unwrapped:
                    while lon - unwrapped[-1][0] > 180:
                        lon -= 360
                    while lon - unwrapped[-1][0] < -180:
                        lon += 360
                unwrapped.append((lon, lat))
            origin_x, origin_y = unwrapped[0]
            area = sum((x - origin_x) * (unwrapped[(i + 1) % len(unwrapped)][1] - origin_y)
                       - (unwrapped[(i + 1) % len(unwrapped)][0] - origin_x) * (y - origin_y)
                       for i, (x, y) in enumerate(unwrapped))
            if len(set(unwrapped)) < 3 or abs(area) < 1e-12:
                raise ProtocolError("area must enclose space with at least three distinct points")

    if event_type in {"marker.deleted", "marker.status"}:
        try:
            event["marker_id"] = str(uuid.UUID(str(value.get("marker_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("marker_id must be a UUID") from exc

    if event_type == "drawing.deleted":
        try:
            event["drawing_id"] = str(uuid.UUID(str(value.get("drawing_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("drawing_id must be a UUID") from exc

    if event_type == "task.created":
        try:
            event["task_id"] = str(uuid.UUID(str(value.get("task_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("task_id must be a UUID") from exc
        title = str(value.get("title", "")).strip()
        if not title or len(title) > 100:
            raise ProtocolError("task title must be 1-100 characters")
        assignee = str(value.get("assignee") or "").strip()
        if len(assignee) > MAX_CALLSIGN_LENGTH:
            raise ProtocolError(f"assignee must be at most {MAX_CALLSIGN_LENGTH} characters")
        event["title"] = title
        event["assignee"] = assignee or None

    if event_type == "task.completed":
        try:
            event["task_id"] = str(uuid.UUID(str(value.get("task_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("task_id must be a UUID") from exc

    if event_type == "waypoint.arrived":
        try:
            event["waypoint_id"] = str(uuid.UUID(str(value.get("waypoint_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("waypoint_id must be a UUID") from exc
        waypoint_label = str(value.get("waypoint_label", "")).strip()
        if not waypoint_label or len(waypoint_label) > MAX_LABEL_LENGTH:
            raise ProtocolError(f"waypoint label must be 1-{MAX_LABEL_LENGTH} characters")
        operator_callsign = str(value.get("operator_callsign", "")).strip()
        if not operator_callsign or len(operator_callsign) > MAX_CALLSIGN_LENGTH:
            raise ProtocolError(
                f"operator callsign must be 1-{MAX_CALLSIGN_LENGTH} characters"
            )
        event["waypoint_label"] = waypoint_label
        event["operator_callsign"] = operator_callsign
        event["distance_m"] = round(
            _number(value.get("distance_m"), "distance_m", 0, 1000), 1
        )

    if event_type in {"message.deleted", "report.dismissed"}:
        try:
            event["message_id"] = str(uuid.UUID(str(value.get("message_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("message_id must be a UUID") from exc

    if event_type in {"ptt.broadcast", "private.ptt"}:
        try:
            event["clip_id"] = str(uuid.UUID(str(value.get("clip_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("clip_id must be a UUID") from exc
        duration_ms = value.get("duration_ms")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or not 100 <= duration_ms <= 10_000
        ):
            raise ProtocolError("duration_ms must be between 100 and 10000")
        mime_type = str(value.get("mime_type", "")).split(";", 1)[0]
        if mime_type not in {"audio/webm", "audio/ogg", "audio/mp4"}:
            raise ProtocolError("unsupported voice clip format")
        event["duration_ms"] = duration_ms
        event["mime_type"] = mime_type

    if event_type in {"navigation.updated", "navigation.stopped"}:
        from .navigation import MAX_EVENT_BYTES, validate_fields
        try:
            event.update(validate_fields(value))
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        if len(_canonical_json(event)) > MAX_EVENT_BYTES:
            raise ProtocolError("shared route exceeds 12 KB; route was not shared")

    return event


def new_event(event_type: str, callsign: str, **payload: Any) -> dict[str, Any]:
    candidate = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "callsign": callsign,
        "created_at": int(time.time()),
        **payload,
    }
    return validate_event(candidate)


def sign_event(
    event: dict[str, Any], identity: RNS.Identity, *, packet_limit: bool = True
) -> bytes:
    event = validate_event(event)
    message = _canonical_json(event)
    envelope = {
        "event": event,
        "public_key": base64.b64encode(identity.get_public_key()).decode("ascii"),
        "signature": base64.b64encode(identity.sign(message)).decode("ascii"),
    }
    encoded = _canonical_json(envelope)
    if packet_limit and len(encoded) > RNS.Packet.MDU:
        raise ProtocolError(
            f"signed event is {len(encoded)} bytes; Reticulum packet limit is {RNS.Packet.MDU}"
        )
    return encoded


def verify_envelope(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        envelope = json.loads(raw.decode("utf-8"))
        event = validate_event(envelope["event"])
        public_key = base64.b64decode(envelope["public_key"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
    except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid Reticom envelope") from exc

    identity = RNS.Identity(create_keys=False)
    identity.load_public_key(public_key)
    if not identity.validate(signature, _canonical_json(event)):
        raise ProtocolError("invalid event signature")

    return event, identity.hash.hex()
