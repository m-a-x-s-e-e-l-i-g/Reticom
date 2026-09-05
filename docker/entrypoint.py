"""Start one persistent Reticom node inside a container."""

import os
import sys
from pathlib import Path

from retium.bootstrap import prepare_reticulum_config


def initial_config(role: str) -> str:
    base = """[reticulum]
  enable_transport = No
  share_instance = No

[logging]
  loglevel = 4

[interfaces]
"""
    if role == "gateway":
        base += """  [[Reticom Docker TCP Server]]
    type = TCPServerInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
"""
    else:
        base += """  [[Reticom Docker Command]]
    type = TCPClientInterface
    enabled = Yes
    target_host = command
    target_port = 4242
    connect_timeout = 4
"""
    return prepare_reticulum_config(base)


def main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "gateway"
    if role not in {"gateway", "field"}:
        raise SystemExit("Choose gateway or field")
    root = Path("/var/lib/reticom")
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config"
    if not config_path.exists():
        config_path.write_text(initial_config(role), encoding="utf-8")
    os.execv(sys.executable, [
        sys.executable, "-m", "retium.server",
        "--role", role,
        "--config", str(config_dir),
        "--data", str(root / "data"),
        "--host", "0.0.0.0", "--port", "8780",
    ])


if __name__ == "__main__":
    main()
