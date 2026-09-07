"""Shared route snapshots, bounded for encrypted links and mission replication.

The sender comes only from the authenticated envelope, never from route fields.
Geometry is encoded, not simplified: every supplied point survives at 5 decimals.
"""
from __future__ import annotations

import json
import math
import uuid

NAVIGATION_TYPES = {"navigation.updated", "navigation.stopped"}
MAX_POINTS = 4000
MAX_EVENT_BYTES = 12000
MAX_REVISION = 1_000_000_000


def coordinate(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("navigation coordinate must be [longitude, latitude]")
    result = []
    for component, bound in zip(value, (180, 90)):
        if type(component) not in (int, float) or not math.isfinite(component) or not -bound <= component <= bound:
            raise ValueError("invalid navigation coordinate")
        result.append(round(component, 5))
    return result


def encode_coordinates(points):
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise ValueError(f"shared route must contain 2-{MAX_POINTS} points; route was not shared")
    output, previous = [], [0, 0]
    for point in points:
        lon, lat = coordinate(point)
        for axis, value in enumerate((lat, lon)):
            number = round(value * 100000)
            delta = number - previous[axis]
            previous[axis] = number
            encoded = ~(delta << 1) if delta < 0 else delta << 1
            while encoded >= 32:
                output.append(chr((encoded & 31) + 95))
                encoded >>= 5
            output.append(chr(encoded + 63))
    return "".join(output)


def decode_coordinates(polyline):
    if not isinstance(polyline, str) or not 2 <= len(polyline) <= MAX_EVENT_BYTES:
        raise ValueError("invalid shared route geometry")
    offset, previous, points = 0, [0, 0], []
    while offset < len(polyline):
        for axis in (0, 1):
            number, shift = 0, 0
            while True:
                if offset >= len(polyline) or shift > 30:
                    raise ValueError("invalid shared route geometry")
                part = ord(polyline[offset]) - 63
                offset += 1
                if not 0 <= part <= 63:
                    raise ValueError("invalid shared route geometry")
                number |= (part & 31) << shift
                shift += 5
                if part < 32:
                    break
            previous[axis] += ~(number >> 1) if number & 1 else number >> 1
        points.append(coordinate([previous[1] / 100000, previous[0] / 100000]))
        if len(points) > MAX_POINTS:
            raise ValueError("shared route has too many points")
    if len(points) < 2 or encode_coordinates(points) != polyline:
        raise ValueError("invalid shared route geometry")
    return points


def _text(value, name, limit, empty=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (not empty and not value.strip()):
        raise ValueError(f"{name} must be {'0' if empty else '1'}-{limit} characters")
    return value.strip()


def _number(value, name, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"invalid navigation {name}")
    return round(value, 1)


def validate_fields(value):
    try:
        route_id = str(uuid.UUID(str(value.get("route_id"))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("navigation route_id must be a UUID") from exc
    revision = value.get("revision")
    if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
        raise ValueError("navigation revision must be a positive integer")
    clean = {"route_id": route_id, "revision": revision}
    if value["type"] == "navigation.stopped":
        return clean
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in {"direct", "route"}:
        raise ValueError("navigation mode must be direct or route")
    target_kind = value.get("target_kind", "waypoint")
    if not isinstance(target_kind, str) or target_kind not in {"waypoint", "operator", "shared"}:
        raise ValueError("invalid navigation target kind")
    geometry = value.get("geometry")
    points = decode_coordinates(geometry)
    origin, target = coordinate(value.get("origin")), coordinate(value.get("target"))
    if mode == "direct" and (len(points) != 2 or points != [origin, target]):
        raise ValueError("direct navigation must connect origin and target")
    clean.update({"label": _text(value.get("label"), "navigation label", 80),
                  "mode": mode, "origin": origin, "target": target,
                  "target_kind": target_kind,
                  "target_id": _text(value.get("target_id", ""), "navigation target id", 128, empty=True),
                  "geometry": geometry,
                  "distance_m": _number(value.get("distance_m", 0), "distance", 50_000_000),
                  "duration_s": _number(value.get("duration_s", 0), "duration", 31_536_000)})
    following = value.get("following")
    if following is not None:
        if not isinstance(following, dict):
            raise ValueError("invalid followed navigation")
        sender = following.get("sender_hash")
        if not isinstance(sender, str) or len(sender) != 32 or any(c not in "0123456789abcdef" for c in sender):
            raise ValueError("invalid followed operator identity")
        try:
            followed_id = str(uuid.UUID(str(following.get("route_id"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid followed route id") from exc
        clean["following"] = {"sender_hash": sender, "route_id": followed_id}
    if "directions" in value:
        directions = value["directions"]
        if not isinstance(directions, list) or len(directions) > 64:
            raise ValueError("shared route may contain at most 64 directions")
        # Deliberately small, portable instructions; unknown fields do not cross RNS.
        clean["directions"] = []
        for step in directions:
            if not isinstance(step, dict):
                raise ValueError("invalid shared route direction")
            clean["directions"].append({
                "text": _text(step.get("text"), "direction text", 160),
                "distance_m": _number(step.get("distance_m", 0), "step distance", 50_000_000),
            })
        if len(json.dumps(clean["directions"], ensure_ascii=False).encode()) > 4096:
            raise ValueError("shared directions exceed 4 KB; route was not shared")
    return clean


def fields_from_request(body, route_id, revision):
    if not isinstance(body, dict):
        raise ValueError("navigation request must be an object")
    return validate_fields({**body, "type": "navigation.updated", "route_id": route_id,
                            "revision": revision, "geometry": encode_coordinates(body.get("coordinates"))})


def event_version(event):
    # Wall-clock skew must never resurrect a route after a newer stop.
    return (event.get("revision", 0), event.get("type") == "navigation.stopped", event["id"])


def as_plan(event):
    plan = {key: value for key, value in event.items() if key not in {"geometry", "type"}}
    plan["sender_hash"] = event.get("network", {}).get("sender_hash")
    plan["active"] = event["type"] == "navigation.updated"
    plan["coordinates"] = decode_coordinates(event["geometry"]) if plan["active"] else []
    return plan
