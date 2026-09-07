"""Real encrypted Reticulum link admission/revocation, on isolated local ports."""
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from test_continuity_network import port, eventually


@pytest.mark.skipif(os.environ.get("RETICOM_NETWORK_TESTS") != "1", reason="explicit real-network integration test")
def test_real_membership_queue_admission_and_revocation(tmp_path):
    script = str(Path(__file__).with_name("test_continuity_network.py"))
    processes, logs = [], []
    tcp, host_port, phone_port = port(), port(), port()
    def launch(name, role, web=0):
        log=(tmp_path/f"{name}.log").open("ab"); logs.append(log)
        env={**os.environ,"PYTHONPATH":os.pathsep.join(p for p in sys.path if p)}
        process=subprocess.Popen([sys._base_executable,script,str(tmp_path/name),role,str(web),str(tcp)],env=env,stdout=log,stderr=subprocess.STDOUT)
        processes.append(process);return process
    def api(web,path,body=None):
        with httpx.Client(timeout=25) as client:
            response=client.get(f"http://127.0.0.1:{web}{path}") if body is None else client.post(f"http://127.0.0.1:{web}{path}",json=body)
            response.raise_for_status();return response.json()
    try:
        launch("carrier","carrier")
        host=launch("host","gateway",host_port);launch("phone","field",phone_port)
        for web in (host_port,phone_port): eventually(lambda:api(web,"/api/health"))
        team=api(host_port,"/api/team/create",{"name":"Approval test","modules":["tasks"]})
        secret=api(host_port,"/api/send",{"type":"chat.message","message":"Approved members only"})["event"]
        api(phone_port,"/api/team/join",{"join_code":team["join_code"],"callsign":"Phone"})
        key=api(phone_port,"/api/state")["network"]["identity"]
        eventually(lambda:any(item["identity"]==key for item in api(host_port,"/api/team/members")["requests"]))
        blocked=api(phone_port,"/api/feed")
        assert not any(e["id"]==secret["id"] for e in blocked["events"])
        assert blocked["network"]["online"] is False
        queued=api(phone_port,"/api/send",{"type":"chat.message","message":"Held until approved"})["event"]
        assert not any(e["id"]==queued["id"] for e in api(host_port,"/api/state")["events"])
        # A remote member cannot use the local owner-only approval endpoint.
        with httpx.Client() as client:
            denied=client.post(f"http://127.0.0.1:{phone_port}/api/team/members",json={"identity":key,"status":"approved"})
            assert denied.status_code==403
        api(host_port,"/api/team/members",{"identity":key,"status":"approved"})
        eventually(lambda:api(phone_port,"/api/state")["team"]["membership_status"]=="approved")
        eventually(lambda:any(e["id"]==queued["id"] for e in api(host_port,"/api/state")["events"]))
        eventually(lambda:any(e["id"]==secret["id"] for e in api(phone_port,"/api/feed")["events"]))
        api(host_port,"/api/team/members",{"identity":key,"status":"revoked"})
        later=api(host_port,"/api/send",{"type":"chat.message","message":"After revocation"})["event"]
        assert not any(e["id"]==later["id"] for e in api(phone_port,"/api/feed")["events"])
        eventually(lambda:api(phone_port,"/api/state")["team"]["membership_status"]=="revoked")
        host.terminate();host.wait(15);launch("host","gateway",host_port)
        eventually(lambda:api(host_port,"/api/health"))
        assert next(m for m in api(host_port,"/api/team/members")["members"] if m["identity"]==key)["status"]=="revoked"
        print("Verified real RNS denial, pending queue, owner approval, delivery, revocation and restart persistence.")
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
        for process in processes:
            try:process.wait(15)
            except subprocess.TimeoutExpired:process.kill();process.wait(5)
        for log in logs:log.close()
        for path in tmp_path.glob("*.log"):print(path.name,path.read_text(errors="replace")[-2500:])
