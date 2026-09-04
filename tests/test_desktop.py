from __future__ import annotations

from pathlib import Path

from retium.desktop import (
    DEFAULT_GATEWAY_CONFIG,
    SMOKE_TEST_CONFIG,
    desktop_data_root,
    ensure_gateway_config,
)


def test_desktop_data_root_uses_local_app_data() -> None:
    root = desktop_data_root({"LOCALAPPDATA": r"C:\Users\Field\AppData\Local"})
    assert root == Path(r"C:\Users\Field\AppData\Local") / "Reticom"


def test_gateway_config_is_created_once(tmp_path: Path) -> None:
    config_dir = ensure_gateway_config(tmp_path)
    config_path = config_dir / "config"
    assert config_path.read_text(encoding="utf-8") == DEFAULT_GATEWAY_CONFIG
    assert "AutoInterface" in DEFAULT_GATEWAY_CONFIG
    assert "TCPClientInterface" in DEFAULT_GATEWAY_CONFIG

    config_path.write_text("custom operator config", encoding="utf-8")
    ensure_gateway_config(tmp_path)
    assert config_path.read_text(encoding="utf-8") == "custom operator config"


def test_smoke_config_has_no_external_interfaces(tmp_path: Path) -> None:
    config_dir = ensure_gateway_config(tmp_path, SMOKE_TEST_CONFIG)
    config = (config_dir / "config").read_text(encoding="utf-8")
    assert "[interfaces]" in config
    assert "TCPServerInterface" not in config
    assert "TCPClientInterface" not in config
