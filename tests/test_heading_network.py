"""Opt-in real Reticulum stream test, isolated TCP carrier and three app nodes."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack

import pytest


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_real_heading_stream_identity_stop_and_no_event_history(tmp_path):
    import httpx
    from websockets.sync.client import connect
    from test_continuity_network import port, eventually

    processes, logs, sockets = [], [], []
    connections = ExitStack()
    carrier = port()
    ports = [port(), port(), port()]
    script = str(Path(__file__).with_name("test_continuity_network.py"))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    def api(port, path, body=None):
        with httpx.Client(timeout=20) as client:
            response = client.get(f"http://127.0.0.1:{port}{path}") if body is None else client.post(f"http://127.0.0.1:{port}{path}", json=body)
            response.raise_for_status()
            return response.json()
    try:
        for name, role, web_port in [("carrier", "carrier", 0), ("host", "gateway", ports[0]), ("one", "field", ports[1]), ("two", "field", ports[2])]:
            log = (tmp_path / f"{name}.log").open("ab"); logs.append(log)
            processes.append(subprocess.Popen([sys._base_executable, script, str(tmp_path/name), role, str(web_port), str(carrier)], env=env, stdout=log, stderr=log))
        for p in ports: eventually(lambda: api(p, "/api/health"))
        team = api(ports[0], "/api/team/create", {"name":"Heading test"})
        for p in ports[1:]:
            api(p, "/api/team/join", {"join_code":team["join_code"], "callsign":f"Field-{p}"})
            member = api(p, "/api/state")["network"]["identity"]
            api(ports[0], "/api/team/members", {"identity": member, "status": "approved", "callsign": f"Field-{p}"})
            eventually(lambda: api(p, "/api/state")["team"]["membership_status"] == "approved")
        identity = api(ports[1], "/api/state")["network"]["identity"]
        for p in ports:
            ws = connections.enter_context(connect(f"ws://127.0.0.1:{p}/api/heading/live", open_timeout=15)); sockets.append(ws)
            eventually(lambda: json.loads(ws.recv(timeout=15)).get("ready"), timeout=30)
            ws.send(json.dumps({"type":"heading.clock", "request_id":123}))
            def clock_reply():
                frame = json.loads(ws.recv(timeout=15))
                if frame.get("type") != "heading.clock": return False
                assert frame["request_id"] == 123
                assert abs(frame["server_at"] - time.time()*1000) < 1000
                return True
            eventually(clock_reply, timeout=10)
        frames, errors = [], []
        stopped = threading.Event()
        def receive():
            while not stopped.is_set():
                try:
                    frame = json.loads(sockets[2].recv(timeout=.5))
                    if frame.get("type") == "heading.sample": frames.append((time.time()*1000, frame))
                except TimeoutError: pass
                except Exception as exc: errors.append(exc); return
        reader = threading.Thread(target=receive, daemon=True); reader.start()
        for index in range(30):
            sockets[1].send(json.dumps({"type":"heading.sample", "heading":index*10, "at":int(time.time()*1000), "sender_hash":"f"*32}))
            time.sleep(.11)
        eventually(lambda: len(frames) >= 12, timeout=8)
        assert all(frame["sender_hash"] == identity for _, frame in frames)
        latencies = [received-frame["at"] for received, frame in frames]
        print(f"Real RNS: {len(frames)}/30 heading samples, median {sorted(latencies)[len(latencies)//2]:.0f}ms, max {max(latencies):.0f}ms")
        sockets[1].send(json.dumps({"type":"heading.sample", "heading":None, "at":int(time.time()*1000)}))
        eventually(lambda: frames[-1][1]["heading"] is None, timeout=5)
        assert not any(event["type"].startswith("heading") for event in api(ports[0], "/api/state")["events"])
        assert api(ports[1], "/api/state")["network"]["queued_events"] == 0
        stopped.set(); reader.join(2)
        assert not errors
    finally:
        connections.close()
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=10)
        for log in logs: log.close()
