from retium.protocol import new_event
from retium.waypoints import WaypointArrivalDetector, distance_meters


WAYPOINT_ID = "00112233-4455-6677-8899-aabbccddeeff"


def position(lon: float, lat: float, accuracy: float = 8) -> dict:
    return {
        **new_event(
            "position.updated", "RAVEN", lon=lon, lat=lat, accuracy=accuracy
        ),
        "network": {"sender_hash": "0123456789abcdef0123456789abcdef"},
    }


def waypoint() -> dict:
    return new_event(
        "marker.created",
        "COMMAND",
        lon=4.300000,
        lat=51.500000,
        marker_type="waypoint",
        label="Waypoint 1",
        id=WAYPOINT_ID,
    )


def test_distance_uses_real_world_metres():
    distance = distance_meters((4.3, 51.5), (4.3, 51.5009))

    assert 99 < distance < 101


def test_arrival_is_announced_once_until_operator_leaves_and_returns():
    detector = WaypointArrivalDetector()
    events = [waypoint()]

    first = detector.update(position(4.3001, 51.5), events)
    still_inside = detector.update(position(4.30012, 51.5), events)
    left = detector.update(position(4.302, 51.5), events)
    returned = detector.update(position(4.3001, 51.5), events)

    assert first[0]["waypoint_label"] == "Waypoint 1"
    assert first[0]["operator_callsign"] == "RAVEN"
    assert still_inside == []
    assert left == []
    assert len(returned) == 1


def test_inaccurate_position_does_not_trigger_arrival():
    detector = WaypointArrivalDetector()

    assert detector.update(position(4.3, 51.5, accuracy=120), [waypoint()]) == []


def test_deleted_waypoint_is_not_considered():
    detector = WaypointArrivalDetector()
    deletion = new_event("marker.deleted", "COMMAND", marker_id=WAYPOINT_ID)

    assert detector.update(position(4.3, 51.5), [deletion]) == []
