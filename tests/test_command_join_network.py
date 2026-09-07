"""Command joins another host over isolated real Reticulum TCP, not a UI fixture."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_command_joins_remote_team_and_syncs_in_background(tmp_path):
    import httpx
    from test_continuity_network import eventually, port

    carrier, host, command = (port() for _ in range(3))
    processes, logs = [], []
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    script = str(Path(__file__).with_name("test_continuity_network.py"))

    def launch(name, role, web):
        log = (tmp_path / f"{name}.log").open("ab")
        logs.append(log)
        processes.append(subprocess.Popen([sys._base_executable, script, str(tmp_path / name), role,
            str(web), str(carrier)], env=env, stdout=log, stderr=subprocess.STDOUT))

    def api(web, path, body=None):
        with httpx.Client(timeout=40) as client:
            response = client.request("GET" if body is None else "POST", f"http://127.0.0.1:{web}{path}", json=body)
            assert response.is_success, response.text
            return response.json()

    try:
        launch("carrier", "carrier", 0)
        launch("host", "gateway", host)
        launch("command", "gateway", command)
        for web in (host, command):
            eventually(lambda: api(web, "/api/health"))
        team = api(host, "/api/team/create", {"name": "Remote team", "modules": []})
        marker = api(host, "/api/send", {"type": "marker.created", "marker_type": "waypoint",
            "label": "Remote rally", "lat": 51.0, "lon": 4.0})["event"]
        joined = api(command, "/api/command/join", {"join_code": team["join_code"]})
        query = f'?team={joined["id"]}'
        member = api(command, "/api/state" + query)["network"]["identity"]
        api(host, "/api/team/members", {"identity": member, "status": "approved", "callsign": "Command"})
        # Read-only state calls do not pull the feed: the background worker must sync.
        def ready():
            state = api(command, "/api/state" + query)
            return state if any(event["id"] == marker["id"] for event in state["events"]) else None
        state = eventually(ready)
        assert state["role"] == "field" and not state["team"]["admin"]
        assert state["team"]["name"] == "Remote team"
        assert next(event for event in state["events"] if event["id"] == marker["id"])["network"]["verified"]
        overview = api(command, "/api/command/overview")["teams"]
        assert any(event["id"] == marker["id"] for event in overview[0]["events"])
        outgoing = api(command, "/api/send" + query, {"type": "chat.message", "message": "Command joined remotely"})["event"]
        eventually(lambda: any(event["id"] == outgoing["id"] for event in api(host, "/api/state")["events"]))
        assert api(command, "/api/command/join", {"join_code": team["join_code"]})["id"] == joined["id"]
        api(command, f'/api/command/teams/{joined["id"]}/remove', {"confirm": True})
        assert api(command, "/api/command/teams")["teams"] == []
        assert any(event["id"] == marker["id"] for event in api(host, "/api/state")["events"])
        restored = api(command, "/api/command/join", {"join_code": team["join_code"]})
        assert restored["id"] == joined["id"]
        assert api(command, "/api/state" + query)["network"]["identity"] == state["network"]["identity"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        for log in logs:
            log.close()
