from __future__ import annotations

import argparse
import ctypes
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Mapping

import uvicorn
import RNS

from retium.server import create_app

APP_NAME = "Reticom"
WINDOW_TITLE = "Reticom Command"
STARTUP_TIMEOUT_SECONDS = 30

DEFAULT_GATEWAY_CONFIG = """[reticulum]
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

  [[Reticom Local TCP Server]]
    type = TCPServerInterface
    enabled = Yes
    listen_ip = 127.0.0.1
    listen_port = 4242

# BEGIN RETICOM COMMUNITY BOOTSTRAPS
  [[Reticom Bootstrap 01]]
    type = TCPClientInterface
    enabled = Yes
    target_host = 193.26.158.230
    target_port = 4965
    connect_timeout = 4
    bootstrap_only = Yes

  [[Reticom Bootstrap 02]]
    type = TCPClientInterface
    enabled = Yes
    target_host = 45.77.109.86
    target_port = 4965
    connect_timeout = 4
    bootstrap_only = Yes

  [[Reticom Bootstrap 03]]
    type = TCPClientInterface
    enabled = Yes
    target_host = sydney.reticulum.au
    target_port = 4242
    connect_timeout = 4
    bootstrap_only = Yes

  [[Reticom Bootstrap 04]]
    type = TCPClientInterface
    enabled = Yes
    target_host = 103.195.4.226
    target_port = 4242
    connect_timeout = 4
    bootstrap_only = Yes
# END RETICOM COMMUNITY BOOTSTRAPS
"""

SMOKE_TEST_CONFIG = """[reticulum]
  enable_transport = No
  share_instance = No

[logging]
  loglevel = 2

[interfaces]
"""


def desktop_data_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    local_app_data = str(values.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def ensure_gateway_config(root: Path, content: str = DEFAULT_GATEWAY_CONFIG) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config"
    if not config_path.exists():
        config_path.write_text(content, encoding="utf-8")
    return config_dir


def configure_logging(root: Path) -> Path:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "desktop.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    return log_path


class DesktopServer:
    def __init__(self, config_dir: Path, data_dir: Path):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(128)
        self.port = int(self._socket.getsockname()[1])
        app = create_app("gateway", config_dir.resolve(), data_dir.resolve())
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_config=None,
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._run,
            name="reticom-command-server",
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._socket])
        except Exception:
            logging.exception("Command server stopped unexpectedly")

    def start(self, timeout: float = STARTUP_TIMEOUT_SECONDS) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        state_url = f"{self.url}api/health"
        while time.monotonic() < deadline:
            if not self._thread.is_alive():
                raise RuntimeError("Reticom Command stopped during startup")
            try:
                with urllib.request.urlopen(state_url, timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError("Reticom Command did not become ready in time")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=8)
        try:
            self._socket.close()
        except OSError:
            pass


def _show_error(message: str, log_path: Path | None = None) -> None:
    detail = str(message)
    if log_path is not None:
        detail += f"\n\nLog: {log_path}"
    try:
        ctypes.windll.user32.MessageBoxW(None, detail, WINDOW_TITLE, 0x10)
    except (AttributeError, OSError):
        logging.error(detail)


def run(smoke_test: bool = False, window_smoke_test: bool = False) -> int:
    test_mode = smoke_test or window_smoke_test
    temporary_root = (
        tempfile.TemporaryDirectory(prefix="reticom-smoke-", ignore_cleanup_errors=True)
        if test_mode
        else None
    )
    root = Path(temporary_root.name) if temporary_root is not None else desktop_data_root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = configure_logging(root)
    server: DesktopServer | None = None
    reticulum_started = False
    try:
        config_dir = ensure_gateway_config(root, SMOKE_TEST_CONFIG if test_mode else DEFAULT_GATEWAY_CONFIG)
        RNS.Reticulum(str(config_dir.resolve()))
        reticulum_started = True
        server = DesktopServer(config_dir, root / "data")
        server.start()
        if smoke_test:
            with urllib.request.urlopen(f"{server.url}api/health", timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError("Command API smoke test failed")
            return 0

        import webview

        window = webview.create_window(
            WINDOW_TITLE,
            server.url,
            width=1440,
            height=900,
            min_size=(960, 640),
            background_color="#020504",
        )
        if window_smoke_test:
            def close_test_window() -> None:
                threading.Timer(2, window.destroy).start()

            window.events.loaded += close_test_window
        webview.start(
            gui="edgechromium", debug=False, private_mode=False,
            icon=str(Path(__file__).with_name("static") / "favicon.ico"),
        )
        return 0
    except Exception as exc:
        logging.exception("Reticom Command failed")
        if test_mode:
            diagnostic = Path(tempfile.gettempdir()) / "reticom-smoke-last-error.log"
            try:
                shutil.copyfile(log_path, diagnostic)
            except OSError:
                pass
            print(f"Reticom Command smoke test failed: {exc}", file=sys.stderr, flush=True)
        else:
            _show_error(str(exc), log_path)
        return 1
    finally:
        if server is not None:
            server.stop()
        if reticulum_started:
            RNS.Reticulum.exit_handler()
        if temporary_root is not None:
            temporary_root.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Reticom Command for Windows")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--window-smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    raise SystemExit(
        run(
            smoke_test=args.smoke_test,
            window_smoke_test=args.window_smoke_test,
        )
    )


if __name__ == "__main__":
    main()
