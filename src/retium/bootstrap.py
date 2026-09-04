from __future__ import annotations

import re
from ipaddress import ip_address
from pathlib import Path
from typing import Any


COMMUNITY_BOOTSTRAPS: tuple[dict[str, Any], ...] = (
    {
        "name": "Germany 002",
        "host": "193.26.158.230",
        "port": 4965,
        "region": "DE",
    },
    {
        "name": "US East",
        "host": "45.77.109.86",
        "port": 4965,
        "region": "US",
    },
    {
        "name": "Sydney",
        "host": "sydney.reticulum.au",
        "port": 4242,
        "region": "AU",
    },
    {
        "name": "Hong Kong",
        "host": "103.195.4.226",
        "port": 4242,
        "region": "HK",
    },
)

AUTOCONNECT_COUNT = 3
REQUIRED_DISCOVERY_VALUE = 16
COMMUNITY_START = "# BEGIN RETICOM COMMUNITY BOOTSTRAPS"
COMMUNITY_END = "# END RETICOM COMMUNITY BOOTSTRAPS"
CUSTOM_START = "# BEGIN RETICOM USER NODE"
CUSTOM_END = "# END RETICOM USER NODE"
LEGACY_START = "# BEGIN RETICOM MANAGED CELLULAR RELAY"
LEGACY_END = "# END RETICOM MANAGED CELLULAR RELAY"
_HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", re.IGNORECASE)


def validate_node(host: str, port: int | str) -> tuple[str, int]:
    clean_host = str(host or "").strip().lower().rstrip(".")
    if not clean_host:
        raise ValueError("node host is required")
    try:
        is_ip_address = bool(ip_address(clean_host))
    except ValueError:
        is_ip_address = False
    if not is_ip_address and (not _HOST_PATTERN.fullmatch(clean_host) or ".." in clean_host):
        raise ValueError("node host must be a hostname or IP address")
    try:
        clean_port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("node port must be a number") from exc
    if not 1 <= clean_port <= 65535:
        raise ValueError("node port must be 1-65535")
    return clean_host, clean_port


def _without_block(config: str, start: str, end: str) -> str:
    return re.sub(
        rf"\n?{re.escape(start)}.*?{re.escape(end)}\n?",
        "\n",
        str(config),
        flags=re.DOTALL,
    ).rstrip()


def _replace_block(config: str, start: str, end: str, block: str) -> str:
    clean = _without_block(config, start, end)
    return f"{clean}\n\n{block.strip()}\n" if block.strip() else f"{clean}\n"


def ensure_discovery_settings(config: str) -> str:
    values = {
        "discover_interfaces": "Yes",
        "autoconnect_discovered_interfaces": str(AUTOCONNECT_COUNT),
        "required_discovery_value": str(REQUIRED_DISCOVERY_VALUE),
    }
    match = re.search(
        r"(?ms)^\[reticulum\]\s*\n(?P<body>.*?)(?=^\[[^\[]|\Z)",
        str(config),
    )
    if match is None:
        raise ValueError("Reticulum config is missing its [reticulum] section")
    body = match.group("body").rstrip()
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=.*$")
        line = f"  {key} = {value}"
        if pattern.search(body):
            body = pattern.sub(line, body)
        else:
            body = f"{body}\n{line}"
    replacement = f"[reticulum]\n{body}\n\n"
    return f"{config[:match.start()]}{replacement}{config[match.end():]}"


def community_bootstrap_config() -> str:
    interfaces = []
    for index, node in enumerate(COMMUNITY_BOOTSTRAPS, start=1):
        host, port = validate_node(node["host"], node["port"])
        interfaces.append(
            f"""  [[Reticom Bootstrap {index:02d}]]
    type = TCPClientInterface
    enabled = Yes
    target_host = {host}
    target_port = {port}
    connect_timeout = 4
    bootstrap_only = Yes"""
        )
    return f"{COMMUNITY_START}\n" + "\n\n".join(interfaces) + f"\n{COMMUNITY_END}"


def merge_community_bootstraps(config: str) -> str:
    return _replace_block(
        ensure_discovery_settings(config),
        COMMUNITY_START,
        COMMUNITY_END,
        community_bootstrap_config(),
    )


def custom_node_config(host: str, port: int | str) -> str:
    clean_host, clean_port = validate_node(host, port)
    return f"""{CUSTOM_START}
  [[Reticom Chosen Node]]
    type = TCPClientInterface
    enabled = Yes
    target_host = {clean_host}
    target_port = {clean_port}
    connect_timeout = 4
{CUSTOM_END}"""


def merge_custom_node(config: str, host: str = "", port: int | str = 4242) -> str:
    if not str(host or "").strip():
        return _replace_block(config, CUSTOM_START, CUSTOM_END, "")
    return _replace_block(
        config,
        CUSTOM_START,
        CUSTOM_END,
        custom_node_config(host, port),
    )


def read_custom_node(config: str) -> dict[str, Any] | None:
    match = re.search(
        rf"{re.escape(CUSTOM_START)}(?P<body>.*?){re.escape(CUSTOM_END)}",
        str(config),
        flags=re.DOTALL,
    )
    if match is None:
        return None
    host = re.search(r"(?m)^\s*target_host\s*=\s*(\S+)\s*$", match.group("body"))
    port = re.search(r"(?m)^\s*target_port\s*=\s*(\d+)\s*$", match.group("body"))
    if host is None or port is None:
        return None
    clean_host, clean_port = validate_node(host.group(1), port.group(1))
    return {"host": clean_host, "port": clean_port}


def prepare_reticulum_config(config: str, packaged_host: str = "", packaged_port: int | str = 4242) -> str:
    prepared = merge_community_bootstraps(config)
    prepared = _without_block(prepared, LEGACY_START, LEGACY_END)
    if packaged_host:
        prepared = merge_custom_node(prepared, packaged_host, packaged_port)
    return f"{prepared.rstrip()}\n"


def write_custom_node(config_path: Path, host: str = "", port: int | str = 4242) -> dict[str, Any] | None:
    current = config_path.read_text(encoding="utf-8")
    wanted = merge_custom_node(current, host, port)
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(wanted, encoding="utf-8")
    temporary.replace(config_path)
    return read_custom_node(wanted)


def network_settings(config_path: Path) -> dict[str, Any]:
    current = config_path.read_text(encoding="utf-8")
    return {
        "mode": "automatic",
        "community_bootstraps": [dict(node) for node in COMMUNITY_BOOTSTRAPS],
        "autoconnect_count": AUTOCONNECT_COUNT,
        "required_discovery_value": REQUIRED_DISCOVERY_VALUE,
        "custom_node": read_custom_node(current),
        "restart_required": True,
    }
