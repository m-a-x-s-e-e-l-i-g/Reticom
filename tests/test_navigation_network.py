"""Opt-in shared navigation checks over isolated, real Reticulum TCP links.

No test uses the running app, the user's team, or a community transport node.
Run with RETICOM_NETWORK_TESTS=1 and pytest -s tests/test_navigation_network.py.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_shared_route_survives_late_join_and_offline_stop(tmp_path):
    import httpx
    import RNS
    from test_continuity_network import eventually, port

    carrier, host, reporter, late = (port() for _ in range(4))
    script = str(Path(__file__).with_name("test_continuity_network.py"))
    processes, logs = [], []
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}

    def launch(name, role, web):
        log = (tmp_path / f"{name}.log").open("ab")
        logs.append(log)
        # On Windows, kill the actual interpreter, not a venv launcher parent.
        process = subprocess.Popen(
            [sys._base_executable, script, str(tmp_path / name), role, str(web), str(carrier)],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process

    def api(web, path, body=None, method=None):
        with httpx.Client(timeout=30) as client:
            response = client.request(method or ("GET" if body is None else "POST"),
                                      f"http://127.0.0.1:{web}{path}", json=body)
            assert response.is_success, f"{web} {path}: {response.text}"
            return response.json()

    def plan(web, sender):
        return next((p for p in api(web, "/api/navigation")["plans"] if p["sender_hash"] == sender), None)

    def synced(web):
        feed = api(web, "/api/feed")
        return feed if feed.get("mission_sync", {}).get("state") == "synced" and feed.get("network", {}).get("online") else None

    def stop(process):
        process.kill()
        process.wait(10)

    try:
        launch("carrier", "carrier", 0)
        host_process = launch("host", "gateway", host)
        reporter_process = launch("reporter", "field", reporter)
        launch("late", "field", late)
        for web in (host, reporter, late):
            eventually(lambda: api(web, "/api/health"))
        team = api(host, "/api/team/create", {"name": "Shared navigation test", "modules": []})
        api(reporter, "/api/team/join", {"join_code": team["join_code"], "callsign": "Reporter"})
        api(host, "/api/team/members", {"identity": api(reporter, "/api/state")["network"]["identity"], "status": "approved", "callsign": "Reporter"})
        eventually(lambda: synced(reporter))
        sender = api(reporter, "/api/navigation")["sender_hash"]

        # Many bends, not a fake two-point substitute: the exact geometry must
        # survive encoding, a Link resource transfer, and the mission snapshot.
        coordinates = [[round(4 + index * .00003, 5), round(51 + (index % 7) * .00002, 5)] for index in range(1000)]
        body = {"label": "Rally Alpha", "mode": "route", "origin": coordinates[0],
                "target": coordinates[-1], "target_kind": "waypoint", "target_id": "test-waypoint",
                "coordinates": coordinates, "distance_m": 3500, "duration_s": 500,
                "directions": [{"text": "Follow the trail to Rally Alpha", "distance_m": 3500}]}
        sent = api(reporter, "/api/navigation", body, "PUT")
        assert len(json.dumps(sent["event"]).encode()) > RNS.Packet.MDU

        def host_received():
            current = plan(host, sender)
            return current if current and current["id"] == sent["event"]["id"] else None

        host_plan = eventually(host_received)
        assert host_plan["active"] is True
        assert host_plan["coordinates"] == coordinates
        assert host_plan["directions"] == body["directions"]
        assert host_plan["network"]["verified"] is True
        assert host_plan["network"]["interface"] == "Authenticated Reticulum Link"
        assert host_plan["network"]["packet_hash"]

        # Route state is mission data, not merely one of the last 100 messages.
        for index in range(110):
            api(host, "/api/send", {"type": "chat.message", "message": f"Later intel {index}"})
        api(late, "/api/team/join", {"join_code": team["join_code"], "callsign": "Late joiner"})
        api(host, "/api/team/members", {"identity": api(late, "/api/state")["network"]["identity"], "status": "approved", "callsign": "Late joiner"})
        snapshot = eventually(lambda: synced(late))
        assert any(event["id"] == sent["event"]["id"] for event in snapshot["events"])
        copied = plan(late, sender)
        assert copied["coordinates"] == coordinates
        assert copied["directions"] == body["directions"]
        assert copied["network"]["verified"] is True

        # A caller cannot impersonate the reporter or stop their route through
        # supplied owner/revision fields. DELETE is always device-local identity.
        late_sender = api(late, "/api/navigation")["sender_hash"]
        assert late_sender != sender
        other = api(late, "/api/navigation", {**body, "sender_hash": sender,
            "route_id": sent["event"]["route_id"], "revision": 999_999_999,
            "callsign": "Reporter", "network": {"sender_hash": sender}}, "PUT")
        assert other["plan"]["sender_hash"] == late_sender
        assert other["event"]["revision"] < 999_999_999
        assert other["event"]["callsign"] == "Late joiner"
        assert other["event"]["route_id"] != sent["event"]["route_id"]
        api(late, "/api/navigation", method="DELETE")
        eventually(lambda: (value := plan(host, late_sender)) and not value["active"])
        assert plan(host, sender)["active"] is True

        # Queue while the team host is really dead; pending changes and the
        # sender's monotonic revision must survive an actual process restart.
        stop(host_process)
        with pytest.raises(httpx.TransportError):
            api(host, "/api/health")
        queued = api(reporter, "/api/navigation", {**body, "label": "Rally Bravo"}, "PUT")
        assert queued["delivery"]["status"] == "queued"
        assert queued["event"]["revision"] > sent["event"]["revision"]
        stop(reporter_process)
        reporter_process = launch("reporter", "field", reporter)
        eventually(lambda: api(reporter, "/api/health"))
        assert plan(reporter, sender)["label"] == "Rally Bravo"
        assert plan(reporter, sender)["network"]["queued"] is True
        queued_again = api(reporter, "/api/navigation", {**body, "label": "Rally Charlie"}, "PUT")
        assert queued_again["event"]["revision"] > queued["event"]["revision"]
        stopped = api(reporter, "/api/navigation", method="DELETE")
        assert stopped["event"]["revision"] > queued_again["event"]["revision"]
        assert stopped["plan"]["active"] is False
        assert stopped["delivery"]["status"] == "queued"

        host_process = launch("host", "gateway", host)
        eventually(lambda: api(host, "/api/health"))

        def host_stopped():
            current = plan(host, sender)
            return current if current and not current["active"] and current["id"] == stopped["event"]["id"] else None

        # Only poll the host here. Recovery must not depend on refreshing the
        # field's feed or manually pressing send after connectivity returns.
        eventually(host_stopped)

        def late_stopped():
            synced(late)
            current = plan(late, sender)
            return current if current and not current["active"] and current["id"] == stopped["event"]["id"] else None

        eventually(late_stopped)
        assert plan(reporter, sender)["network"]["queued"] is False
        print("Real RNS navigation: 1000 exact route points over an authenticated Link; late join after 110 newer messages; identity isolation; durable revisions; queued stop delivered after host restart without a field refresh.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(10)
        for log in logs:
            log.close()
        for path in tmp_path.glob("*.log"):
            content = path.read_text(errors="replace")
            if content.strip():
                print(f"{path.name}:\n{content[-4000:]}")
