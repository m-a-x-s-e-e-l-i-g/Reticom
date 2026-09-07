"""Opt-in removal delivery test using isolated, real Reticulum nodes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_field_and_command_dismissal_sync_without_deleting_message(tmp_path):
    import httpx
    from test_continuity_network import port, eventually
    carrier, host, field = port(), port(), port()
    processes, logs = [], []
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    script = str(Path(__file__).with_name("test_continuity_network.py"))
    def api(web, method, path, body=None):
        with httpx.Client(timeout=30) as client:
            response = client.request(method, f"http://127.0.0.1:{web}{path}", json=body)
            response.raise_for_status()
            return response.json()
    def source(web, path, event_id):
        return next((e for e in api(web, "GET", path)["events"] if e["id"] == event_id), None)
    try:
        for name, role, web in [("carrier", "carrier", 0), ("host", "gateway", host), ("field", "field", field)]:
            log = (tmp_path / f"{name}.log").open("ab"); logs.append(log)
            processes.append(subprocess.Popen([sys._base_executable, script, str(tmp_path/name), role, str(web), str(carrier)], env=env, stdout=log, stderr=log))
        for web in [host, field]: eventually(lambda: api(web, "GET", "/api/health"))
        team = api(host, "POST", "/api/team/create", {"name": "Removal test"})
        api(field, "POST", "/api/team/join", {"join_code": team["join_code"], "callsign": "Reporter"})
        api(host, "POST", "/api/team/members", {"identity": api(field, "GET", "/api/state")["network"]["identity"], "status": "approved", "callsign": "Reporter"})
        event = api(field, "POST", "/api/send", {"type": "chat.message", "message": "contact north 100 meters"})["event"]
        eventually(lambda: source(host, "/api/state", event["id"]))
        eventually(lambda: source(field, "/api/feed", event["id"]))
        removal = api(field, "DELETE", f'/api/map/automatic-report/{event["id"]}')
        assert removal["event"]["type"] == "report.dismissed"
        eventually(lambda: (source(host, "/api/state", event["id"]) or {}).get("automatic_report_dismissed"))
        eventually(lambda: (source(field, "/api/feed", event["id"]) or {}).get("automatic_report_dismissed"))
        assert source(field, "/api/feed", event["id"])["message"] == event["message"]
        other = api(host, "POST", "/api/send", {"type": "chat.message", "message": "contact east 200 meters"})["event"]
        eventually(lambda: source(field, "/api/feed", other["id"]))
        with httpx.Client(timeout=30) as client:
            rejected = client.delete(f'http://127.0.0.1:{field}/api/map/automatic-report/{other["id"]}')
            assert rejected.status_code in {403, 422}
        api(host, "DELETE", f'/api/map/automatic-report/{other["id"]}')
        eventually(lambda: (source(field, "/api/feed", other["id"]) or {}).get("automatic_report_dismissed"))
        print("Real RNS: Field removal delivered to Command; Command removal synced to Field; original chat retained; unauthorized removal rejected.")
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(10)
        for log in logs: log.close()
