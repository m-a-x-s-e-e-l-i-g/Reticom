from __future__ import annotations

import math
from typing import Any


ARRIVAL_RADIUS_METERS = 35.0
MAX_ARRIVAL_ACCURACY_METERS = 75.0
REARM_DISTANCE_METERS = 90.0


def distance_meters(left: tuple[float, float], right: tuple[float, float]) -> float:
    """Return great-circle distance between (longitude, latitude) pairs."""
    lon1, lat1 = left
    lon2, lat2 = right
    radius = 6_371_008.8
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(haversine)))


class WaypointArrivalDetector:
    """Detect an operator entering each currently active waypoint once per visit."""

    def __init__(self) -> None:
        self._inside: set[tuple[str, str]] = set()

    def update(
        self,
        position: dict[str, Any],
        visible_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if position.get("type") != "position.updated":
            return []
        network = position.get("network") or {}
        operator_hash = str(network.get("sender_hash") or "")
        if not operator_hash:
            return []
        raw_accuracy = position.get("accuracy")
        accuracy = (
            float(raw_accuracy)
            if isinstance(raw_accuracy, (int, float)) and not isinstance(raw_accuracy, bool)
            else MAX_ARRIVAL_ACCURACY_METERS + 1
        )
        if not 0 < accuracy <= MAX_ARRIVAL_ACCURACY_METERS:
            return []

        waypoints = [
            event
            for event in visible_events
            if event.get("type") == "marker.created"
            and event.get("marker_type") == "waypoint"
        ]
        active_ids = {str(event.get("id")) for event in waypoints}
        self._inside = {
            key for key in self._inside if key[1] in active_ids
        }
        operator_point = (float(position["lon"]), float(position["lat"]))
        arrivals: list[dict[str, Any]] = []
        for waypoint in waypoints:
            waypoint_id = str(waypoint["id"])
            key = (operator_hash, waypoint_id)
            distance = distance_meters(
                operator_point,
                (float(waypoint["lon"]), float(waypoint["lat"])),
            )
            arrival_radius = max(ARRIVAL_RADIUS_METERS, accuracy)
            if distance <= arrival_radius:
                if key not in self._inside:
                    self._inside.add(key)
                    arrivals.append(
                        {
                            "waypoint_id": waypoint_id,
                            "waypoint_label": str(waypoint["label"]),
                            "operator_callsign": str(position["callsign"]),
                            "operator_hash": operator_hash,
                            "distance_m": distance,
                        }
                    )
            elif distance >= max(REARM_DISTANCE_METERS, arrival_radius * 1.75):
                self._inside.discard(key)
        return arrivals
