"""Opt-in real TCP/Reticulum crash test. Never touches a user's team or ports.

Run: RETICOM_NETWORK_TESTS=1 python -m pytest tests/test_continuity_network.py -s
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def eventually(fn, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as exc:
            last = exc
        time.sleep(.5)
    raise AssertionError(f'Condition timed out: {last}')


@pytest.mark.skipif(os.environ.get('RETICOM_NETWORK_TESTS') != '1', reason='explicit real-network integration test')
def test_real_reticulum_backup_handover_crash_and_rejoin(tmp_path):
    import httpx
    processes = []
    logs = []
    tcp_port = port()
    script = str(Path(__file__).resolve())

    def launch(name, role, web_port=0):
        log = (tmp_path / f'{name}.log').open('ab')
        logs.append(log)
        # Windows venv's launcher can leave its child alive when killed. Start
        # the real interpreter so killing this PID genuinely kills the host.
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(p for p in sys.path if p)}
        proc = subprocess.Popen([sys._base_executable, script, str(tmp_path/name), role, str(web_port), str(tcp_port)], env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(proc)
        return proc

    def api(web_port, path, body=None):
        with httpx.Client(timeout=25) as client:
            response = client.get(f'http://127.0.0.1:{web_port}{path}') if body is None else client.post(f'http://127.0.0.1:{web_port}{path}', json=body)
            response.raise_for_status()
            return response.json()

    try:
        launch('carrier', 'carrier')
        command_port, backup_port, client_port = port(), port(), port()
        command = launch('command', 'gateway', command_port)
        backup_process = launch('backup', 'field', backup_port)
        launch('client', 'field', client_port)
        for web_port in (command_port, backup_port, client_port):
            eventually(lambda: api(web_port, '/api/health'))
        team = api(command_port, '/api/team/create', {'name': 'Continuity test', 'modules': ['tasks']})
        root_code = team['join_code']
        for web_port, name in ((backup_port, 'Backup'), (client_port, 'Client')):
            api(web_port, '/api/team/join', {'join_code': root_code, 'callsign': name})
            member = api(web_port, '/api/state')['network']['identity']
            api(command_port, '/api/team/members', {'identity': member, 'status': 'approved', 'callsign': name})
        message = api(command_port, '/api/send', {'type': 'chat.message', 'message': 'Before host failure'})['event']
        task = api(command_port, '/api/tasks', {'title': 'Task survives'})['task']
        marker = api(command_port, '/api/send', {'type': 'marker.created', 'lat': 52.0, 'lon': 5.0, 'marker_type': 'waypoint', 'label': 'Safe point'})['event']
        audio = (Path(__file__).resolve().parents[1]/'src/retium/static/audio/incoming-transmission-start.ogg').read_bytes()
        with httpx.Client(timeout=20) as client:
            voice_response = client.post(f'http://127.0.0.1:{command_port}/api/ptt?duration_ms=1000', content=audio, headers={'Content-Type': 'audio/ogg'})
            voice_response.raise_for_status()
            voice = voice_response.json()['event']
        client_identity = api(client_port, '/api/state')['network']['identity']
        eventually(lambda: any(e['network']['sender_hash'] == client_identity for e in api(command_port, '/api/state')['events']))
        private = api(command_port, '/api/private/messages', {'recipient_hash': client_identity, 'message': 'Private survives failover'})['event']
        offer = api(backup_port, '/api/team/continuity/offer', {})
        api(command_port, '/api/team/continuity/approve', {**offer, 'label': 'Backup phone', 'trust_host': True})
        def backup_ready():
            state = api(backup_port, '/api/team/continuity')
            return state if state.get('ready') else None
        eventually(backup_ready)
        eventually(lambda: len((api(client_port, '/api/team/continuity').get('policy') or {}).get('hosts', [])) == 2)
        assert api(backup_port, '/api/feed')['team']['join_code'] == root_code
        assert any(e['id'] == message['id'] for e in api(backup_port, '/api/feed')['events'])
        assert any(t['id'] == task['id'] for t in api(backup_port, '/api/tasks')['tasks'])
        # Field-authored report and lifecycle update travel over real RNS links,
        # through the offline queue, feed cache and the replica journal.
        report = api(client_port, '/api/send', {'type': 'marker.created', 'lat': 52, 'lon': 5,
            'marker_type': 'electrical-hazard', 'label': 'Power line', 'description': 'Fallen wire',
            'urgent': True, 'report_status': 'reported'})['event']
        eventually(lambda: any(e['id'] == report['id'] for e in api(command_port, '/api/state')['events']))
        api(client_port, '/api/feed')
        api(client_port, '/api/send', {'type': 'marker.status', 'marker_id': report['id'], 'report_status': 'cleared'})
        def report_cleared(web_port, endpoint):
            found = [e for e in api(web_port, endpoint)['events'] if e['id'] == report['id']]
            return len(found) == 1 and found[0].get('report_view', {}).get('status') == 'cleared'
        eventually(lambda: report_cleared(command_port, '/api/state'))
        eventually(lambda: report_cleared(client_port, '/api/feed'))
        eventually(lambda: report_cleared(backup_port, '/api/feed'))
        # Owner handover requires a current acknowledgment, then clients learn
        # the new signed preference. It does not copy the owner's private key.
        eventually(lambda: api(command_port, '/api/team/continuity/handover', {'destination': offer['destination']}), timeout=45)
        eventually(lambda: (api(client_port, '/api/team/continuity').get('policy') or {}).get('preferred') == offer['destination'])
        # Test abrupt failover independently of deliberate handover: select the
        # original again and let the client learn that policy before killing it.
        api(command_port, '/api/team/continuity/handover', {'destination': team['destination']})
        eventually(lambda: (api(client_port, '/api/team/continuity').get('policy') or {}).get('preferred') == team['destination'])
        owner_key = (tmp_path/'command'/'data'/'gateway.identity').read_bytes()
        assert (tmp_path/'backup'/'data'/'backups'/team['destination']/'gateway.identity').read_bytes() != owner_key
        # Abruptly kill the application host, NOT the independent TCP carrier.
        command.kill()
        command.wait(10)
        with pytest.raises(httpx.TransportError):
            api(command_port, '/api/health')
        after = api(client_port, '/api/send', {'type': 'chat.message', 'message': 'Sent with Command dead'})['event']
        area = api(client_port, '/api/send', {'type': 'drawing.created', 'drawing_type': 'area',
            'points': [[5, 52], [5.01, 52], [5.01, 52.01]], 'label': 'Search Alpha',
            'drawing_color': 'blue', 'fill_style': 'crosses'})['event']
        def same_area(events):
            return any(all(e.get(k) == v for k, v in area.items()) for e in events)
        eventually(lambda: same_area(api(backup_port, '/api/feed')['events']))
        eventually(lambda: same_area(api(client_port, '/api/feed')['events']))
        eventually(lambda: any(e['id'] == after['id'] for e in api(backup_port, '/api/feed')['events']))
        assert any(e['id'] == message['id'] for e in api(client_port, '/api/feed')['events'])
        assert any(e['id'] == marker['id'] for e in api(client_port, '/api/feed')['events'])
        assert any(e['id'] == private['id'] for e in api(client_port, '/api/private/messages')['messages'])
        with httpx.Client(timeout=25) as client:
            voice_copy = client.get(f'http://127.0.0.1:{client_port}/api/audio/{voice["clip_id"]}')
            voice_copy.raise_for_status()
            assert voice_copy.content == audio
        api(client_port, f"/api/tasks/{task['id']}/complete", {})
        eventually(lambda: api(backup_port, '/api/tasks')['tasks'][0]['completed_at'] is not None)
        with httpx.Client(timeout=25) as client:
            assert client.delete(f'http://127.0.0.1:{backup_port}/api/tasks/{task["id"]}').status_code == 200
        # A backup must recover from disk while the original host remains dead.
        backup_process.kill()
        backup_process.wait(10)
        backup_process = launch('backup', 'field', backup_port)
        eventually(backup_ready)
        assert api(backup_port, '/api/feed')['team']['join_code'] == root_code
        # Restore the same original identity/store. Changes created through the
        # backup must reach the original host over RNS, without manual import.
        command = launch('command', 'gateway', command_port)
        eventually(lambda: api(command_port, '/api/health'))
        eventually(lambda: any(e['id'] == after['id'] for e in api(command_port, '/api/state')['events']))
        eventually(lambda: same_area(api(command_port, '/api/state')['events']))
        assert api(command_port, '/api/state')['team']['join_code'] == root_code
        eventually(lambda: not any(t['id'] == task['id'] for t in api(command_port, '/api/tasks')['tasks']))
        assert any(e['id'] == marker['id'] for e in api(command_port, '/api/state')['events'])
        assert api(backup_port, '/api/team/continuity')['ready']
        # The owner-signed revocation must also deny a previously approved
        # operator on the replica after the policy has synchronized.
        api(command_port, '/api/team/members', {'identity': client_identity, 'status': 'revoked'})
        membership_file = tmp_path/'backup'/'data'/'backups'/team['destination']/'membership'/team['destination']/'policy.json'
        eventually(lambda: json.loads(membership_file.read_text())['policy']['members'][client_identity]['status'] == 'revoked')
        blocked_event = api(backup_port, '/api/send', {'type': 'chat.message', 'message': 'Not for revoked device'})['event']
        denied_feed = api(client_port, '/api/feed')
        assert denied_feed['network']['online'] is False
        assert not any(e['id'] == blocked_event['id'] for e in denied_feed['events'])
        print('Verified independent RNS identities, signed approval/handover, original host killed, delivery through backup, and rejoin convergence.')
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        for log in logs:
            log.close()
        for path in tmp_path.glob('*.log'):
            print(f'--- {path.name} ---\n{path.read_text(errors="replace")[-5000:]}')


def node_main():
    import RNS
    import uvicorn
    from retium.server import create_app
    root, role, web_port, tcp_port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    config = root/'config'
    config.mkdir(parents=True, exist_ok=True)
    carrier = role == 'carrier'
    interface = (f'  [[TCP]]\n    type = TCPServerInterface\n    enabled = Yes\n    listen_ip = 127.0.0.1\n    listen_port = {tcp_port}\n' if carrier else
        f'  [[TCP]]\n    type = TCPClientInterface\n    enabled = Yes\n    target_host = 127.0.0.1\n    target_port = {tcp_port}\n')
    (config/'config').write_text(f'[reticulum]\n  share_instance = No\n  enable_transport = {"Yes" if carrier else "No"}\n[logging]\n  loglevel = 2\n[interfaces]\n'+interface)
    RNS.Reticulum(str(config))
    if carrier:
        while True:
            time.sleep(1)
    else:
        # Keep the test independent of optional Whisper downloads. The Ogg clip
        # itself is transferred byte-for-byte over real Reticulum Links.
        from retium.transcription import LocalTranscriber
        LocalTranscriber.schedule = lambda *args, **kwargs: False
        uvicorn.run(create_app(role, config, root/'data'), host='127.0.0.1', port=web_port, log_level='warning')


if __name__ == '__main__':
    node_main()
