from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

from retium.speech_markers import InterpretationError, SpeechMarkerProcessor, resolve_location, validate_interpretation
from retium.protocol import validate_event

SOURCE = {"id": "00112233-4455-6677-8899-aabbccddeeff", "type": "ptt.broadcast", "callsign": "Scout",
    "created_at": 1000, "network": {"sender_hash": "scout"}}
FIX = {"id": "fix", "type": "position.updated", "lat": 52.0, "lon": 5.0, "created_at": 995,
    "callsign": "Scout", "network": {"sender_hash": "scout"}}
LOCATION = {"reference": "speaker", "operator": "", "landmark": "", "distance_m": 200, "direction": "north"}
RESULT = {"decision": "markers", "reason": "Reported heat signature", "markers": [
    {"marker_type": "observation", "label": "Heat signature", "location": LOCATION}]}


@pytest.fixture(autouse=True)
def current_time(monkeypatch):
    monkeypatch.setattr("retium.speech_markers.time.time", lambda: 1005)


def test_reference_positions_are_grounded_in_sender_and_recent_fixes():
    lat, lon = resolve_location(LOCATION, SOURCE, [FIX], "command")
    assert lat > 52.001 and lon == pytest.approx(5)
    with pytest.raises(InterpretationError, match="No recent"):
        resolve_location({**LOCATION, "reference": "command"}, SOURCE, [FIX], "command")
    with pytest.raises(InterpretationError, match="No recent"):
        resolve_location(LOCATION, SOURCE, [{**FIX, "created_at": 300}], "command")


def test_command_uses_persistent_fixed_position_without_gps():
    source = {**SOURCE, "network": {"sender_hash": "command"}}
    lat, lon = resolve_location(LOCATION, source, [], "command", command_position={"lat": 52, "lon": 5})
    assert lat > 52 and lon == pytest.approx(5)


def test_public_command_your_position_averages_one_fresh_fix_per_field_operator():
    source = {**SOURCE, "network": {"sender_hash": "command"}}
    location = {**LOCATION, "reference": "recipient", "distance_m": None, "direction": None}
    fixes = [FIX, {**FIX, "created_at": 900, "lat": 10},
             {**FIX, "lat": 54, "lon": 7, "network": {"sender_hash": "bravo"}},
             {**FIX, "lat": 20, "network": {"sender_hash": "command"}},
             {**FIX, "lat": 80, "created_at": 1, "network": {"sender_hash": "stale"}}]
    assert resolve_location(location, source, fixes, "command", command_position={"lat": 20, "lon": 20}) == pytest.approx((53, 6))


def test_dm_your_position_uses_only_recipient_and_never_group_fallback():
    source = {**SOURCE, "network": {"sender_hash": "command"}, "recipient_hash": "scout"}
    location = {**LOCATION, "reference": "recipient", "distance_m": None, "direction": None}
    assert resolve_location(location, source, [FIX], "command") == pytest.approx((52, 5))
    with pytest.raises(InterpretationError, match="No recent"):
        resolve_location(location, {**source, "recipient_hash": "missing"}, [FIX], "command")


def test_known_team_landmark_does_not_require_gps_or_network_lookup():
    marker = {"id": "base", "type": "marker.created", "label": "Alpha", "lat": 53, "lon": 6, "created_at": 1}
    location = {**LOCATION, "reference": "landmark", "landmark": "Alpha", "distance_m": None, "direction": None}
    assert resolve_location(location, SOURCE, [marker], "command", lambda *a: pytest.fail("No lookup needed")) == (53, 6)


def test_named_operator_is_rejected_when_callsign_is_ambiguous():
    other = {**FIX, "network": {"sender_hash": "different"}}
    with pytest.raises(InterpretationError, match="More than one"):
        resolve_location({**LOCATION, "reference": "operator", "operator": "Scout"}, SOURCE, [FIX, other], "command")


def test_landmark_lookup_failure_never_places_at_speaker():
    with pytest.raises(InterpretationError, match="No matching landmark"):
        resolve_location({**LOCATION, "reference": "landmark", "landmark": "church"}, SOURCE, [FIX], "command", lambda *a: None)


@pytest.mark.parametrize("change", [{"distance_m": -100}, {"direction": None}, {"direction": "up"}, {"lat": 52}, {"distance_m": True}])
def test_model_cannot_invent_coordinates_or_invalid_offsets(change):
    result = deepcopy(RESULT)
    result["markers"][0]["location"].update(change)
    with pytest.raises(InterpretationError):
        validate_interpretation(result)


def test_interpretation_is_published_once_with_signed_source_reference(tmp_path):
    calls = []
    interpreter = SimpleNamespace(model="test", interpret=lambda *args: deepcopy(RESULT))
    processor = SpeechMarkerProcessor(tmp_path, interpreter)
    first = processor.process(SOURCE, "Heat nearby", [SOURCE, FIX], "command", "Command", calls.append)
    assert first["state"] == "created"
    assert len(calls) == 1
    assert validate_event(calls[0])["source_report_id"] == SOURCE["id"]
    reloaded = SpeechMarkerProcessor(tmp_path, interpreter)
    assert reloaded.process(SOURCE, "Heat nearby", [SOURCE, FIX], "command", "Command", calls.append) == first
    assert len(calls) == 1


def test_partial_publish_retries_only_missing_markers(tmp_path):
    result = deepcopy(RESULT)
    result["markers"].append({**result["markers"][0], "label": "Second report"})
    interpreter = SimpleNamespace(model="test", interpret=lambda *args: result)
    processor = SpeechMarkerProcessor(tmp_path, interpreter)
    calls = []
    def publish(event):
        if calls:
            raise RuntimeError("temporary transport failure")
        calls.append(event)
    state = processor.process(SOURCE, "Two reports", [SOURCE, FIX], "command", "Command", publish)
    assert state["state"] == "error"
    interpreter.interpret = lambda *args: pytest.fail("Must reuse interpretation on delivery retry")
    state = processor.process(SOURCE, "Two reports", [SOURCE, FIX, *calls], "command", "Command", calls.append)
    assert state["state"] == "created" and len(calls) == 2
    assert calls[0]["id"] != calls[1]["id"]


def test_no_marker_and_ambiguous_reports_never_publish(tmp_path):
    for decision in ["no_marker", "needs_clarification"]:
        processor = SpeechMarkerProcessor(tmp_path / decision, SimpleNamespace(model="test",
            interpret=lambda *args: {"decision": decision, "reason": "Not enough location detail", "markers": []}))
        result = processor.process(SOURCE, "Can you see anything?", [SOURCE, FIX], "command", "Command",
            lambda *args: pytest.fail("Must not publish"))
        assert result["state"] == decision


def test_cancel_targets_only_the_speakers_previous_ai_marker(tmp_path):
    marker_id = str(uuid.uuid4())
    marker = {"id": marker_id, "type": "marker.created", "created_at": 1001, "source_report_id": SOURCE["id"]}
    cancel = {**SOURCE, "id": str(uuid.uuid4()), "created_at": 1002}
    interpreter = SimpleNamespace(model="test", interpret=lambda *args: {"decision": "cancel_last", "reason": "Cancel request", "markers": []})
    processor = SpeechMarkerProcessor(tmp_path, interpreter)
    events = []
    result = processor.process(cancel, "Cancel my last marker", [SOURCE, FIX, marker], "command", "Command", events.append)
    assert result["state"] == "cancelled"
    assert events[0]["type"] == "marker.deleted" and events[0]["marker_id"] == marker_id
