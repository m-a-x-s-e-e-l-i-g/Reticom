"""Late join catch-up on real isolated Reticulum nodes, never a user's team."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_late_join_gets_old_mission_and_reconciles_removals(tmp_path):
    import httpx
    from test_continuity_network import port, eventually
    script = str(Path(__file__).with_name("test_continuity_network.py"))
    carrier, host, field, reporter = port(), port(), port(), port()
    processes, logs = [], []
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    def api(web, path, body=None):
        with httpx.Client(timeout=30) as client:
            response = client.get(f"http://127.0.0.1:{web}{path}") if body is None else client.post(f"http://127.0.0.1:{web}{path}", json=body)
            assert response.is_success, response.text
            response.raise_for_status()
            return response.json()
    def synced():
        feed = api(field, "/api/feed")
        assert feed["team"]["joined"] is True
        return feed if feed.get("mission_sync", {}).get("state") == "synced" and feed.get("network", {}).get("online") else None
    try:
        for name, role, web in [("carrier", "carrier", 0), ("host", "gateway", host), ("field", "field", field), ("reporter", "field", reporter)]:
            log = (tmp_path / f"{name}.log").open("ab"); logs.append(log)
            processes.append(subprocess.Popen([sys._base_executable, script, str(tmp_path/name), role, str(web), str(carrier)], env=env, stdout=log, stderr=log))
        for web in (host, field, reporter): eventually(lambda: api(web, "/api/health"))
        team = api(host, "/api/team/create", {"name":"Late join", "modules":["tasks"]})
        markers = [api(host, "/api/send", {"type":"marker.created", "marker_type":"waypoint", "label":f"Waypoint {n}", "lat":51, "lon":4})["event"]["id"] for n in range(70)]
        task = api(host, "/api/tasks", {"title":"Meet at the first waypoint"})["task"]
        api(reporter, "/api/team/join", {"join_code":team["join_code"], "callsign":"Reporter"})
        api(host, "/api/team/members", {"identity": api(reporter, "/api/state")["network"]["identity"], "status": "approved", "callsign": "Reporter"})
        # The outbox intentionally coalesces unsent GPS fixes. Wait for each
        # delivery so this fixture really creates 85 newer host-side events.
        for n in range(85):
            fix = api(reporter, "/api/send", {"type":"position.updated", "lat":51.1, "lon":4, "accuracy":10})["event"]
            eventually(lambda: any(e["id"] == fix["id"] for e in api(host, "/api/state")["events"]))
        eventually(lambda: api(host, "/api/state")["event_count"] >= 157)
        api(field, "/api/team/join", {"join_code":team["join_code"], "callsign":"Late Field"})
        api(host, "/api/team/members", {"identity": api(field, "/api/state")["network"]["identity"], "status": "approved", "callsign": "Late Field"})
        snapshot = eventually(synced)
        assert set(markers) <= {e["id"] for e in snapshot["events"]}
        assert task["id"] in {t["id"] for t in api(field, "/api/tasks")["tasks"]}
        assert snapshot["team"]["modules"] == ["tasks"]
        assert snapshot["network"]["online"] is True
        # /state and the mission cache must not truncate old pins back to 60.
        assert set(markers) <= {e["id"] for e in api(field, "/api/state")["events"]}
        with httpx.Client(timeout=30) as client:
            response = client.delete(f"http://127.0.0.1:{host}/api/map/marker/{markers[0]}")
            assert response.is_success, response.text
        updated = eventually(synced)
        assert markers[0] not in {e["id"] for e in updated["events"]}
        assert set(markers[1:]) <= {e["id"] for e in updated["events"]}
        print("Real RNS late join: all 70 old waypoints recovered after 85 newer GPS events; tasks/modules synchronized; deletion reconciled.")
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(10)
        for log in logs: log.close()
