"""Exercise the real Command background worker without downloading models."""
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.mark.parametrize("mode", ["ptt", "chat", "dm"])
def test_command_maps_speech_without_browser_transcription_requests(tmp_path, mode):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tmp_path), mode],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def exercise(root, mode):
    import time
    from unittest.mock import patch
    import RNS
    from fastapi.testclient import TestClient
    from retium.server import create_app
    from retium.team import TeamProfile
    from retium.store import EventStore
    from retium.protocol import new_event

    config, data = root / "config", root / "data"
    config.mkdir()
    (config / "config").write_text('[reticulum]\n  share_instance = No\n[logging]\n  loglevel = 1\n[interfaces]\n')
    RNS.Reticulum(str(config))
    TeamProfile(data / "team.json").set_name("Speech test", [])
    interpretation = {"decision": "markers", "reason": "Rally north", "markers": [{
        "marker_type": "rally-point", "label": "Rally", "location": {
            "reference": "speaker", "operator": "", "landmark": "", "distance_m": 100, "direction": "north"}}]}
    with patch("retium.transcription.LocalTranscriber.status", return_value={"status": "ready", "text": "Rally 100m north"}), \
         patch("retium.transcription.LocalTranscriber.schedule"), \
         patch("retium.speech_markers.LocalInterpreter.interpret", return_value=interpretation) as model:
        with TestClient(create_app("gateway", config, data), client=("127.0.0.1", 50000)) as client:
            event_store = EventStore(data / "events.sqlite3")
            if mode == 'ptt':
                voice = client.post('/api/ptt?duration_ms=1000', content=b'fixture audio', headers={"Content-Type": "audio/webm"})
                assert voice.status_code == 200, voice.text
                source_id = voice.json()["event"]["id"]
                source = next(e for e in event_store.mission_events() if e['id'] == source_id)
                event_store.insert(new_event('position.updated', source['callsign'], lat=52, lon=5), source['network']['sender_hash'])
            else:
                position = client.post('/api/command/position', json={'lat':52,'lon':5})
                assert position.status_code == 200, position.text
                assert (data / 'command-position.json').exists()
                if mode == 'chat':
                    response = client.post('/api/send', json={'type':'chat.message','message':'Helo 1 click north'})
                else:
                    recipient = RNS.Identity().hash.hex()
                    event_store.insert(new_event('position.updated','Recipient',lat=53,lon=5),recipient)
                    client.post('/api/team/members', json={'identity':recipient,'status':'approved','callsign':'Recipient'})
                    interpretation['markers'][0]['location']['reference'] = 'recipient'
                    response = client.post('/api/private/messages',json={'recipient_hash':recipient,'message':'Helo 1 click north of your position'})
                assert response.status_code in {200,201}, response.text
                source_id = response.json()['event']['id']
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = client.get('/api/ai/markers').json()
                if state.get('state') == 'created':
                    break
                time.sleep(.2)
            assert state.get('state') == 'created', state
            markers = [e for e in client.get('/api/feed').json()['events'] if e.get('source_report_id') == source_id]
            if mode == 'dm':
                assert not markers, 'DM interpretation must not leak into shared feed'
                markers = state['private_markers']
            assert len(markers) == 1
            assert markers[0]['source_report_id'] == source_id
            assert markers[0]['lat'] > (53 if mode == 'dm' else 52) and markers[0]['lon'] == 5
            assert markers[0]['network']['verified'] is (mode != 'dm')
            assert model.call_count == 1


if __name__ == "__main__":
    exercise(Path(sys.argv[1]), sys.argv[2])
