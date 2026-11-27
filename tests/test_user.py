import json

import pytest

from retium.protocol import ProtocolError
from retium.user import UserProfile


def test_user_settings_persist_with_the_field_identity(tmp_path):
    path = tmp_path / "user.json"
    profile = UserProfile(path)

    assert profile.state() == {"callsign": "FIELD-1", "icon": "dot", "color": "moss"}
    assert profile.update("  RaVeN  ", "triangle", "steel")["callsign"] == "RAVEN"
    assert json.loads(path.read_text(encoding="utf-8"))["callsign"] == "RAVEN"

    assert UserProfile(path).state() == {"callsign": "RAVEN", "icon": "triangle", "color": "steel"}


def test_existing_mixed_case_callsign_is_normalized_on_load(tmp_path):
    path = tmp_path / "user.json"
    path.write_text('{"callsign": "Eagle-2"}', encoding="utf-8")

    assert UserProfile(path).callsign == "EAGLE-2"


def test_user_settings_validate_callsign_and_icon(tmp_path):
    profile = UserProfile(tmp_path / "user.json")

    with pytest.raises(ProtocolError, match="callsign"):
        profile.update("", "dot")
    with pytest.raises(ProtocolError, match="operator icon"):
        profile.update("RAVEN", "unknown")
    with pytest.raises(ProtocolError, match="operator color"):
        profile.update("RAVEN", "dot", "neon")
