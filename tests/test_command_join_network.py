"""Command joins another host over isolated real Reticulum TCP, not a UI fixture."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_command_joins_remote_team_and_syncs_in_background(tmp_path):
    import httpx
    from retium.intel_packs import IntelPackStore
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
        eventually(lambda: any(item["identity"] == member and item.get("command")
                               for item in api(host, "/api/team/members")["requests"]))
        prefix = "/api/intel-packs"
        settings_url = f"http://127.0.0.1:{command}{prefix}/settings{query}"
        key_payload = {"credentials": {"host_bravoecho_key": "isolated-notam-fixture-key"}}
        assert httpx.patch(settings_url, json=key_payload).status_code == 403
        assert httpx.patch(settings_url, json={"enabled": ["notams"]}).is_success
        notam_view = prefix + "/notams" + query + "&west=4&south=51&east=5&north=52&zoom=10"
        assert api(command, notam_view)["status"] == "error"
        api(host, "/api/team/members", {"identity": member, "status": "approved", "callsign": "Command", "admin": True})
        # Read-only state calls do not pull the feed: the background worker must sync.
        def ready():
            state = api(command, "/api/state" + query)
            return state if state["team"]["admin"] and any(event["id"] == marker["id"] for event in state["events"]) else None
        state = eventually(ready)
        assert state["role"] == "field" and not state["team"]["everyone_admin"]
        assert state["team"]["name"] == "Remote team"
        assert api(command, notam_view)["status"] == "needs_key"
        remote_setup = httpx.patch(settings_url, json=key_payload, timeout=12)
        assert remote_setup.is_success, remote_setup.text
        assert "isolated-notam-fixture-key" not in remote_setup.text
        assert remote_setup.json()["credentials"]["bravoecho_key"] is False
        firms_key = "isolated_nasa_firms_fixture"
        assert httpx.patch(settings_url, json={"credentials": {"host_firms_key": firms_key}}, timeout=12).is_success
        preferences = {"enabled": ["notams", "firms"], "firms_days": 7}
        assert httpx.patch(settings_url, json=preferences).json()["credentials"]["firms_key"] is False
        second_team = api(host, "/api/command/teams", {"name": "Second hosted team", "modules": []})
        assert api(host, prefix + f'?team={second_team["id"]}')["credentials"]["bravoecho_key"] is True
        assert httpx.patch(settings_url, json=key_payload, timeout=12).status_code == 403
        fixture = {"type": "Feature", "id": "notams:fixture", "geometry": {"type": "Point", "coordinates": [4.5, 51.5]},
                   "properties": {"id": "notams:fixture", "title": "Shared runway notice", "kind": "runway"}}
        detection = {**fixture, "id": "firms:fixture", "properties": {"title": "Shared heat detection"}}

        def load_fixture(pack_id, *args, **options):
            if pack_id == "firms":
                assert options["firms_days"] == 7
            return {"type": "FeatureCollection", "features": [detection if pack_id == "firms" else fixture]}

        store = IntelPackStore(tmp_path / "host" / "data", loader=load_fixture)
        store._load_regional("notams", (4, 51, 5, 52), 10, shared=True)
        store._load_regional("firms", (4, 51, 5, 52), 10, shared=True, firms_days=7)
        assert api(command, notam_view)["features"] == [fixture]
        firms_view = notam_view.replace("/notams?", "/firms?")
        assert api(command, firms_view)["features"] == [detection]
        assert next(event for event in state["events"] if event["id"] == marker["id"])["network"]["verified"]
        overview = api(command, "/api/command/overview")["teams"]
        assert any(event["id"] == marker["id"] for event in overview[0]["events"])
        casualty = api(host, "/api/send", {"type": "marker.created", "marker_type": "casualty",
            "label": "Isolated test casualty", "lat": 51.0, "lon": 4.0})["event"]
        def casualty_arrived():
            state = api(command, "/api/state" + query)
            return state["team"]["admin"] and any(event["id"] == casualty["id"] for event in state["events"])
        eventually(casualty_arrived)
        response = httpx.delete(f'http://127.0.0.1:{command}/api/map/marker/{casualty["id"]}{query}', timeout=10)
        assert response.is_success, response.text
        assert response.json()["delivery"]["status"] == "queued"
        assert casualty["id"] not in {event["id"] for event in api(command, "/api/state" + query)["events"]}
        eventually(lambda: casualty["id"] not in {event["id"] for event in api(host, "/api/state")["events"]})
        eventually(lambda: api(command, "/api/state" + query)["network"]["queued_events"] == 0)
        outgoing = api(command, "/api/send" + query, {"type": "chat.message", "message": "Command joined remotely"})["event"]
        eventually(lambda: any(event["id"] == outgoing["id"] for event in api(host, "/api/state")["events"]))
        assert api(command, "/api/command/join", {"join_code": team["join_code"]})["id"] == joined["id"]
        api(command, f'/api/command/teams/{joined["id"]}/remove', {"confirm": True})
        assert api(command, "/api/command/teams")["teams"] == []
        assert any(event["id"] == marker["id"] for event in api(host, "/api/state")["events"])
        restored = api(command, "/api/command/join", {"join_code": team["join_code"]})
        assert restored["id"] == joined["id"]
        assert api(command, "/api/state" + query)["network"]["identity"] == state["network"]["identity"]
        second_query = f'?team={second_team["id"]}'
        second_state = api(host, "/api/state" + second_query)
        second_join = api(command, "/api/command/join", {"join_code": second_state["team"]["join_code"]})
        member_query = f'?team={second_join["id"]}'
        second_member = api(command, "/api/state" + member_query)["network"]["identity"]
        eventually(lambda: any(item["identity"] == second_member
                               for item in api(host, "/api/team/members" + second_query)["requests"]))
        api(host, "/api/team/members" + second_query, {"identity": second_member, "status": "approved", "callsign": "Second member"})
        assert eventually(lambda: api(command, notam_view.replace(query, member_query)).get("features")) == [fixture]
        assert api(command, firms_view.replace(query, member_query))["features"] == [detection]
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
