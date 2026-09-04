import json

import pytest

from retium.team import (
    TeamCodeError,
    TeamMembership,
    TeamProfile,
    decode_join_code,
    encode_join_code,
)
from retium.transport import TeamAnnounceHandler


def test_join_code_round_trip_and_human_formatting():
    destination = bytes.fromhex("00112233445566778899aabbccddeeff")
    code = encode_join_code(destination)

    assert code.startswith("RTM1-")
    assert decode_join_code(code.lower().replace("-", " ")) == destination


def test_join_code_detects_typo():
    code = encode_join_code(bytes.fromhex("00112233445566778899aabbccddeeff"))
    replacement = "A" if code[-1] != "A" else "B"

    with pytest.raises(TeamCodeError, match="checksum"):
        decode_join_code(code[:-1] + replacement)


def test_team_profile_persists_only_valid_names(tmp_path):
    path = tmp_path / "team.json"
    profile = TeamProfile(path)

    with pytest.raises(TeamCodeError, match="team name"):
        profile.set_name(None)

    assert profile.set_name("  North Team  ", ["tasks"]) == "North Team"
    assert TeamProfile(path).name == "North Team"
    assert TeamProfile(path).created_at is not None
    assert TeamProfile(path).modules == ["tasks"]
    assert TeamProfile(path).everyone_admin is False

    profile.set_everyone_admin(True)
    assert TeamProfile(path).everyone_admin is True

    profile.set_modules([])
    assert TeamProfile(path).modules == []

    with pytest.raises(TeamCodeError, match="unsupported module"):
        profile.set_modules(["inventory"])


def test_field_is_not_joined_until_membership_is_proven_and_saved(tmp_path):
    path = tmp_path / "membership.json"
    destination = "00112233445566778899aabbccddeeff"
    membership = TeamMembership(path)

    assert membership.state()["joined"] is False
    assert membership.state()["destination"] is None

    membership.join(destination, "North Team", ["tasks"], True)
    restored = TeamMembership(path)

    assert restored.state()["joined"] is True
    assert restored.state()["name"] == "North Team"
    assert restored.state()["destination"] == destination
    assert restored.state()["modules"] == ["tasks"]
    assert restored.state()["everyone_admin"] is True

    restored.leave()
    left = TeamMembership(path)
    assert left.state()["joined"] is False
    assert left.state()["destination"] is None


def test_reticulum_announce_becomes_nearby_team(monkeypatch):
    handler = TeamAnnounceHandler()
    destination = bytes.fromhex("00112233445566778899aabbccddeeff")
    monkeypatch.setattr("RNS.Transport.hops_to", lambda _: 1)

    handler.received_announce(
        destination,
        object(),
        json.dumps({"v": 1, "kind": "team", "name": "North Team"}).encode(),
    )

    assert handler.nearby(destination) == [
        {
            "name": "North Team",
            "modules": [],
            "everyone_admin": False,
            "destination": destination.hex(),
            "join_code": encode_join_code(destination),
            "last_seen": handler.nearby(destination)[0]["last_seen"],
            "hops": 1,
            "via": "Reticulum announce",
            "joined": True,
        }
    ]


def test_command_admin_permission_is_published_in_team_announce(monkeypatch):
    handler = TeamAnnounceHandler()
    destination = bytes.fromhex("00112233445566778899aabbccddeeff")
    monkeypatch.setattr("RNS.Transport.hops_to", lambda _: 1)

    handler.received_announce(
        destination,
        object(),
        json.dumps(
            {
                "v": 1,
                "kind": "team",
                "name": "North Team",
                "everyone_admin": True,
            }
        ).encode(),
    )

    assert handler.nearby(destination)[0]["everyone_admin"] is True
