from __future__ import annotations

import os
import threading
from pathlib import Path

import RNS
import uvicorn

from retium.server import create_app
from retium.bootstrap import prepare_reticulum_config

_lock = threading.Lock()
_thread: threading.Thread | None = None

ANDROID_CONFIG = """[reticulum]
  enable_transport = No
  share_instance = No
  discover_interfaces = Yes
  autoconnect_discovered_interfaces = 3
  required_discovery_value = 16

[logging]
  loglevel = 4

[interfaces]
  [[Reticom Nearby WiFi]]
    type = AutoInterface
    enabled = Yes
"""

def _prepare(home: Path, relay_host: str = "", relay_port: int = 4242) -> tuple[Path, Path]:
    os.environ["HOME"] = str(home)
    root = home / "retium"
    config_dir = root / "config"
    data_dir = root / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config"
    current_config = config_path.read_text(encoding="utf-8") if config_path.exists() else ANDROID_CONFIG
    wanted_config = prepare_reticulum_config(current_config, relay_host, relay_port)
    if wanted_config != current_config:
        config_path.write_text(wanted_config, encoding="utf-8")
    return config_dir, data_dir


def _run(config_dir: Path, data_dir: Path) -> None:
    app = create_app("field", config_dir, data_dir)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8781,
        loop="asyncio",
        ws="wsproto",
        log_level="info",
        access_log=False,
    )
    uvicorn.Server(config).run()


def start(files_dir: str, relay_host: str = "", relay_port: int = 4242) -> bool:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        config_dir, data_dir = _prepare(Path(files_dir), relay_host, relay_port)
        if RNS.Reticulum.get_instance() is None:
            # RNS installs process signal handlers during construction. Initialise it
            # on Android's main service thread, then let the web server reuse it.
            RNS.Reticulum(str(config_dir))
        _thread = threading.Thread(
            target=_run,
            args=(config_dir, data_dir),
            name="retium-node",
            daemon=True,
        )
        _thread.start()
        return True
