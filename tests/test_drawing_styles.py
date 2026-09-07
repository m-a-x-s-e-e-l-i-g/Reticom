import json

import pytest
import RNS

from retium.protocol import ProtocolError, new_event, sign_event, verify_envelope


def drawing(**options):
    return new_event("drawing.created", "ALPHA",
                     **({"drawing_type": "area", "points": [[4, 51], [4.01, 51], [4.01, 51.01]], **options}))


@pytest.mark.parametrize("fill", ["none", "solid", "lines", "crosses"])
def test_area_style_is_signed_and_preserved(fill):
    event = drawing(label="Search Alpha", drawing_color="red", fill_style=fill, color="moss")
    identity = RNS.Identity()
    wire = sign_event(event, identity, packet_limit=False)
    assert verify_envelope(wire)[0] == event
    assert event["color"] == "moss"  # Operator identity color is independent.
    envelope = json.loads(wire)
    envelope["event"]["drawing_color"] = "blue"
    with pytest.raises(ProtocolError, match="signature"):
        verify_envelope(json.dumps(envelope).encode())


@pytest.mark.parametrize("options", [
    {"drawing_color": "pink"}, {"drawing_color": []}, {"fill_style": "gradient"},
    {"fill_style": []}, {"drawing_type": "trace", "fill_style": "solid"},
    {"drawing_type": "arrow", "fill_style": "lines"}, {"label": "x" * 81},
    {"points": [[0, 0], [1, 1]]}, {"points": [[0, 0], [1, 1], [2, 2]]},
    {"points": [[0, 0], [0.000001, 0], [0, 0.000001]]},
])
def test_invalid_area_styles_and_collapsed_geometry_rejected(options):
    with pytest.raises(ProtocolError):
        drawing(**options)


def test_dateline_area_and_legacy_trace():
    assert drawing(points=[[179.99, 1], [-179.99, 1], [-179.99, 2]])["drawing_type"] == "area"
    event = drawing(drawing_type="trace")
    assert not {"label", "drawing_type", "drawing_color", "fill_style"} & event.keys()
    assert verify_envelope(sign_event(event, RNS.Identity(), packet_limit=False))[0] == event


@pytest.mark.parametrize("kind", ["command-post", "base-camp", "objective", "assembly-point"])
def test_operational_marker_signed_roundtrip(kind):
    event = new_event("marker.created", "ALPHA", marker_type=kind, label=kind, lat=51, lon=4)
    assert verify_envelope(sign_event(event, RNS.Identity(), packet_limit=False))[0] == event
