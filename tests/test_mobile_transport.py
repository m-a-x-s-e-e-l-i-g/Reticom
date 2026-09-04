import importlib.util
from pathlib import Path

import pytest
from RNS.vendor.configobj import ConfigObj

from retium.bootstrap import (
    AUTOCONNECT_COUNT,
    COMMUNITY_BOOTSTRAPS,
    LEGACY_END,
    LEGACY_START,
    merge_custom_node,
    prepare_reticulum_config,
    read_custom_node,
    validate_node,
)


MODULE_PATH = Path(__file__).parents[1] / "android" / "app" / "src" / "main" / "python" / "mobile_main.py"
MANIFEST_PATH = Path(__file__).parents[1] / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
SPEC = importlib.util.spec_from_file_location("reticom_mobile_main", MODULE_PATH)
assert SPEC and SPEC.loader
mobile_main = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mobile_main)


def test_android_webview_can_select_an_audio_input():
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")

    assert "android.permission.RECORD_AUDIO" in manifest
    assert "android.permission.MODIFY_AUDIO_SETTINGS" in manifest


def test_mobile_config_adds_redundant_bootstraps_and_discovery():
    merged = prepare_reticulum_config(mobile_main.ANDROID_CONFIG)

    assert "[[Reticom Nearby WiFi]]" in merged
    assert f"autoconnect_discovered_interfaces = {AUTOCONNECT_COUNT}" in merged
    assert "required_discovery_value = 16" in merged
    assert merged.count("bootstrap_only = Yes") == len(COMMUNITY_BOOTSTRAPS)
    for node in COMMUNITY_BOOTSTRAPS:
        assert f"target_host = {node['host']}" in merged
        assert f"target_port = {node['port']}" in merged
    parsed = ConfigObj(merged.splitlines())
    assert len(parsed["interfaces"]) == len(COMMUNITY_BOOTSTRAPS) + 1


def test_mobile_config_migration_removes_old_private_relay():
    old = f"""{mobile_main.ANDROID_CONFIG}
{LEGACY_START}
  [[Reticom Cellular Relay]]
    type = TCPClientInterface
    enabled = Yes
    target_host = old.private.example
    target_port = 4242
{LEGACY_END}
"""

    migrated = prepare_reticulum_config(old)

    assert "old.private.example" not in migrated
    assert LEGACY_START not in migrated
    assert len(COMMUNITY_BOOTSTRAPS) == migrated.count("bootstrap_only = Yes")


def test_custom_node_survives_default_config_refresh_and_can_be_cleared():
    configured = merge_custom_node(
        prepare_reticulum_config(mobile_main.ANDROID_CONFIG),
        "chosen.example.net",
        5252,
    )

    refreshed = prepare_reticulum_config(configured)
    assert read_custom_node(refreshed) == {"host": "chosen.example.net", "port": 5252}
    assert read_custom_node(merge_custom_node(refreshed, "")) is None


def test_explicit_packaged_node_becomes_the_chosen_node():
    merged = prepare_reticulum_config(
        mobile_main.ANDROID_CONFIG,
        "relay.example.net",
        4242,
    )

    assert read_custom_node(merged) == {"host": "relay.example.net", "port": 4242}
    assert "[[Reticom Chosen Node]]" in merged


@pytest.mark.parametrize(
    "host,port",
    [
        ("relay.example.net\nenabled = no", 4242),
        ("relay example.net", 4242),
        ("relay.example.net", 0),
        ("relay.example.net", 65536),
    ],
)
def test_custom_node_rejects_invalid_config(host, port):
    with pytest.raises(ValueError):
        validate_node(host, port)


def test_custom_node_accepts_ipv6():
    assert validate_node("2001:db8::1", 4242) == ("2001:db8::1", 4242)
