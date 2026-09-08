"""Exercise real Command stacks in a subprocess to isolate RNS's global instance."""
import json
import subprocess
import sys
from pathlib import Path


def test_command_team_isolation_lifecycle_and_legacy_upgrade(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tmp_path)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def exercise(root):
    import RNS
    from fastapi.testclient import TestClient
    from retium.server import create_app
    from retium.team import TeamProfile

    config = root / "config"
    data = root / "data"
    config.mkdir()
    (config / "config").write_text('[reticulum]\n  share_instance = No\n[logging]\n  loglevel = 1\n[interfaces]\n', encoding="utf-8")
    RNS.Reticulum(str(config))  # RNS installs signal handlers on the main thread.
    TeamProfile(data / "team.json").set_name("Existing team", ["tasks"])
    with TestClient(create_app("gateway", config, data), client=("127.0.0.1", 50000)) as client:
        original = client.get('/api/state').json()['team']
        assert original['name'] == 'Existing team'
        assert client.get('/api/command/teams').json()['teams'][0]['id'] == 'default'
        # A nearby re-announce of a locally saved team is an open/manage
        # action, never another join invitation.
        client.app.state.discovery.state.nearby_teams = lambda: [{
            'name': 'Existing team', 'destination': original['destination'], 'join_code': original['join_code'],
            'hops': 1, 'via': 'Reticulum announce',
        }]
        nearby = client.get('/api/command/nearby').json()['teams'][0]
        assert nearby['in_workspace'] is True
        assert nearby['workspace_team_id'] == 'default'
        assert nearby['workspace_hosting'] is True
        assert client.post('/api/team/create', json={'name': 'Oops'}).status_code == 409
        assert client.get('/api/state').json()['team'] == original
        assert client.post('/api/command/teams', json={'name': ''}).status_code == 422
        assert client.post('/api/command/teams', content='{').status_code == 422
        alpha = client.post('/api/command/teams', json={'name': 'Alpha', 'modules': ['tasks']}).json()['id']
        bravo = client.post('/api/command/teams', json={'name': 'Bravo'}).json()['id']
        a = f'?team={alpha}'
        b = f'?team={bravo}'
        alpha_state = client.get('/api/state' + a).json()
        bravo_state = client.get('/api/state' + b).json()
        # Membership decisions are local owner controls, scoped to one team.
        member = RNS.Identity().hash.hex()
        endpoint = '/api/team/members' + a
        decision = {'identity': member, 'status': 'approved', 'callsign': 'Phone'}
        assert client.post(endpoint, json=decision, headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.post(endpoint, json=decision, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.post(endpoint, content=json.dumps(decision), headers={'Content-Type': 'text/plain'}).status_code == 422
        assert client.post(endpoint, json={**decision, 'unexpected': True}).status_code == 422
        assert client.post(endpoint, content='x' * 2049, headers={'Content-Type': 'application/json'}).status_code == 422
        approved = client.post(endpoint, json=decision)
        assert approved.status_code == 200 and approved.headers['cache-control'] == 'no-store'
        assert approved.json()['members'] == [decision]
        assert client.get('/api/team/members' + b).json()['members'] == []
        assert client.get('/api/team/members').json()['members'] == []
        assert client.post(endpoint, json={**decision, 'status': 'revoked'}).status_code == 200
        assert len({alpha_state['team']['join_code'], bravo_state['team']['join_code'], original['join_code']}) == 3
        for query, message in [(a, 'Alpha only'), (b, 'Bravo only')]:
            assert client.post('/api/send' + query, json={'type': 'chat.message', 'message': message}).status_code == 200
        alpha_task = client.post('/api/tasks' + a, json={'title': 'Alpha task'}).json()['task']
        assert client.post('/api/tasks' + b, json={'title': 'Wrong team'}).status_code == 409
        assert client.get('/api/tasks' + a).json()['tasks'][0]['id'] == alpha_task['id']
        events = client.get('/api/state' + a).json()['events']
        assert [e['message'] for e in events if e['type'] == 'chat.message'] == ['Alpha only']
        assert [e['message'] for e in client.get('/api/state' + b).json()['events']] == ['Bravo only']
        assert client.get('/api/state').json()['events'] == []
        # Renaming the running host updates its live profile without resetting it.
        assert client.post('/api/team/permissions' + a, json={'everyone_admin': True}).status_code == 200
        before_rename = client.get('/api/state' + a).json()
        for body in [{'name': ''}, {'name': ' '}, {'name': 'x' * 41}, {'name': None}, {'name': 123}, []]:
            assert client.post(f'/api/command/teams/{alpha}/rename', json=body).status_code == 422
        assert client.post('/api/command/teams/unknown/rename', json={'name': 'No'}).status_code == 404
        assert client.post(f'/api/command/teams/{alpha}/rename', content='{').status_code == 422
        assert client.get('/api/state' + a).json()['team'] == before_rename['team']
        assert client.post(f'/api/command/teams/{alpha}/rename', json={'name': '  Alpha renamed  '}).status_code == 200
        renamed = client.get('/api/state' + a).json()
        assert renamed['team'] == {**before_rename['team'], 'name': 'Alpha renamed'}
        assert renamed['events'] == before_rename['events']
        assert client.get('/api/tasks' + a).json()['tasks'][0]['id'] == alpha_task['id']
        assert client.get('/api/state' + b).json()['team'] == bravo_state['team']
        assert client.get('/api/state').json()['team'] == original
        overview_response = client.get('/api/command/overview')
        assert overview_response.status_code == 200
        assert overview_response.headers['cache-control'] == 'no-store'
        overview = {t['id']: t for t in overview_response.json()['teams']}
        assert set(overview) == {'default', alpha, bravo}
        assert overview[alpha]['hosting'] and overview[bravo]['hosting']
        assert overview[alpha]['name'] == 'Alpha renamed'
        assert overview[alpha]['open_tasks'] == 1
        assert overview[bravo]['open_tasks'] == 0
        assert [e['message'] for e in overview[alpha]['events'] if e['type'] == 'chat.message'] == ['Alpha only']
        assert [e['message'] for e in overview[bravo]['events']] == ['Bravo only']
        # A websocket subscription stays on its selected team too.
        with client.websocket_connect('/api/live' + a) as ws:
            assert ws.receive_json()['type'] == 'stream.ready'
            client.post('/api/send' + b, json={'type': 'chat.message', 'message': 'Not on Alpha stream'})
            client.post('/api/send' + a, json={'type': 'chat.message', 'message': 'On Alpha stream'})
            assert ws.receive_json()['event']['message'] == 'On Alpha stream'
        events = client.get('/api/state' + a).json()['events']
        assert client.get('/api/team/qr' + a).status_code == 200
        assert client.get('/api/state?team=../../other').status_code == 409
        assert client.get('/api/state?team=none').status_code == 409
        # Stop does not remove any identity or data, nor stop another team's host.
        with client.websocket_connect('/api/live' + a) as ws:
            assert ws.receive_json()['type'] == 'stream.ready'
            assert client.post(f'/api/command/teams/{alpha}/hosting', json={'hosting': False}).status_code == 200
            assert ws.receive()['type'] == 'websocket.close'
        assert client.get('/api/state' + a).status_code == 409
        stopped = next(t for t in client.get('/api/command/overview').json()['teams'] if t['id'] == alpha)
        assert stopped['hosting'] is False
        assert stopped['snapshot_available'] is True
        assert stopped['events'] == events
        assert client.get('/api/state' + b).status_code == 200
        assert client.post(f'/api/command/teams/{alpha}/hosting', json={'hosting': True}).status_code == 200
        assert client.get('/api/state' + a).json()['team']['join_code'] == alpha_state['team']['join_code']
        assert client.get('/api/state' + a).json()['events'] == events
        assert client.post(f'/api/command/teams/{bravo}/hosting', json={'hosting': False}).status_code == 200
        assert client.post(f'/api/command/teams/{bravo}/rename', json={'name': ''}).status_code == 422
        assert client.post(f'/api/command/teams/{bravo}/rename', json={'name': 'Bravo renamed'}).status_code == 200
        assert client.get('/api/state' + b).status_code == 409
        saved_bravo = TeamProfile(data / 'teams' / bravo / 'team.json')
        assert saved_bravo.name == 'Bravo renamed'
        assert saved_bravo.modules == []
        assert saved_bravo.created_at == bravo_state['team']['created_at']
        assert client.get('/').status_code == 200
    with TestClient(create_app('gateway', config, data)) as client:
        listed = {team['id']: team for team in client.get('/api/command/teams').json()['teams']}
        assert listed[alpha]['hosting'] is True
        assert listed[bravo]['hosting'] is False
        assert listed[alpha]['name'] == 'Alpha renamed'
        assert listed[bravo]['name'] == 'Bravo renamed'
        assert client.get('/api/state' + a).json()['team'] == renamed['team']
        assert client.post(f'/api/command/teams/{bravo}/hosting', json={'hosting': True}).status_code == 200
        assert client.get('/api/state' + b).json()['team'] == {**bravo_state['team'], 'name': 'Bravo renamed'}
        assert client.post(f'/api/command/teams/{bravo}/hosting', json={'hosting': False}).status_code == 200
        assert client.get('/api/state' + a).json()['events'] == events
        assert client.get('/api/state').json()['team']['join_code'] == original['join_code']
        for team_id in ['default', alpha]:
            assert client.post(f'/api/command/teams/{team_id}/hosting', json={'hosting': False}).status_code == 200
        assert client.get('/api/health').status_code == 200
        assert client.get('/').status_code == 200
        assert all(not team['hosting'] for team in client.get('/api/command/overview').json()['teams'])
        # Joining a locally saved team reopens it; never creates a duplicate.
        duplicate = client.post('/api/command/join', json={'join_code': original['join_code']})
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()['id'] == 'default'
        assert duplicate.json()['existing'] is True
        assert client.get('/api/command/nearby').status_code == 200
        assert client.post('/api/command/join', json={'join_code': 'invalid'}).status_code == 422
        assert client.post('/api/command/join', content='{').status_code == 422
        assert client.post('/api/command/teams/default/remove', json={}).status_code == 422
        assert client.post('/api/command/teams/unknown/remove', json={'confirm': True}).status_code == 404
        # Removal is local and recoverable, including the legacy root team.
        with client.websocket_connect('/api/live') as ws:
            assert ws.receive_json()['type'] == 'stream.ready'
            assert client.post('/api/command/teams/default/remove', json={'confirm': True}).status_code == 200
            assert ws.receive()['type'] == 'websocket.close'
        listed = client.get('/api/command/teams').json()
        assert 'default' not in {team['id'] for team in listed['teams']}
        assert 'default' in {team['id'] for team in listed['removed']}
        assert client.get('/api/state').status_code == 409
        assert (data / 'gateway.identity').exists()
        assert client.post('/api/command/teams/default/hosting', json={'hosting': True}).status_code == 409
        assert client.post('/api/command/teams/default/restore').status_code == 200
        assert client.get('/api/state').status_code == 409
        assert client.post('/api/command/teams/default/hosting', json={'hosting': True}).status_code == 200
        assert client.get('/api/state').json()['team']['join_code'] == original['join_code']
        assert client.post('/api/command/teams/default/restore').status_code == 409
        # A remote membership uses Field transport, never a new host/admin identity.
        from retium.team import encode_join_code
        remote = client.post('/api/command/join', json={'join_code': encode_join_code('11' * 16)})
        assert remote.status_code == 200, remote.text
        remote_id = remote.json()['id']
        remote_state = client.get('/api/state?team=' + remote_id).json()
        assert remote_state['role'] == 'field'
        assert remote_state['team']['joined'] and not remote_state['team']['admin']
        assert remote_state['team']['destination'] == '11' * 16
        assert remote_state['user']['callsign'] == 'COMMAND'
        # A joined Command team must transcribe with its own engine, not
        # delegate inference to an Android host that may lack the engine.
        import time
        from types import SimpleNamespace
        from retium.ptt import PTTStore
        from retium.transcription import LocalTranscriber
        from retium.transport import FieldSender
        clip = '00112233-4455-6677-8899-aabbccddeeff'
        PTTStore(data / 'teams' / remote_id / 'field-ptt').save(clip, b'voice', 'audio/webm')
        original_model, original_remote = LocalTranscriber._get_model, FieldSender.request_transcription
        def reject_remote(*args, **kwargs):
            raise AssertionError('Command must not delegate transcription to the host')
        LocalTranscriber._get_model = lambda self: SimpleNamespace(transcribe=lambda *args, **kwargs: (
            [SimpleNamespace(text='Local engine result')], SimpleNamespace(language='en', language_probability=1)))
        FieldSender.request_transcription = reject_remote
        try:
            url = f'/api/transcriptions/{clip}?team={remote_id}'
            assert client.get(url + '&start=true').json()['status'] == 'processing'
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                transcript = client.get(url).json()
                if transcript['status'] != 'processing': break
                time.sleep(.01)
            assert transcript['text'] == 'Local engine result', transcript
        finally:
            LocalTranscriber._get_model, FieldSender.request_transcription = original_model, original_remote

        assert client.post(f'/api/command/teams/{remote_id}/rename', json={'name': 'No'}).status_code == 409
        assert client.post(f'/api/command/teams/{remote_id}/remove', json={'confirm': True}).status_code == 200
        assert client.get('/api/state?team=' + remote_id).status_code == 409
        assert client.get('/api/command/overview').status_code == 200
    with TestClient(create_app('gateway', config, data)) as client:
        listed = client.get('/api/command/teams').json()
        assert remote_id not in {team['id'] for team in listed['teams']}
        assert remote_id in {team['id'] for team in listed['removed']}
        resumed = client.post('/api/command/join', json={'join_code': encode_join_code('11' * 16)}).json()
        assert resumed['id'] == remote_id
        assert client.get('/api/state?team=' + remote_id).json()['network']['identity'] == remote_state['network']['identity']
    print('Real RNS identities, isolated stores, stop/resume, restart and legacy team verified')


if __name__ == '__main__':
    exercise(Path(sys.argv[1]))
