from retium.command_snapshot import read_team_snapshot
from retium.protocol import new_event
from retium.store import EventStore
from retium.tasks import TaskStore


def test_snapshot_keeps_old_map_markers_and_honors_deletions(tmp_path):
    store = EventStore(tmp_path / 'events.sqlite3')
    kept = new_event('marker.created', 'A', marker_type='waypoint', label='Keep', lat=50, lon=4)
    deleted = new_event('marker.created', 'A', marker_type='waypoint', label='Remove', lat=50, lon=4)
    store.insert(kept, 'a')
    store.insert(deleted, 'a')
    for index in range(550):
        store.insert(new_event('chat.message', 'A', message=str(index)), 'a')
    store.insert(new_event('marker.deleted', 'A', marker_id=deleted['id']), 'a')
    store.close()
    result = read_team_snapshot(tmp_path, None, [])
    ids = {event['id'] for event in result['events']}
    assert kept['id'] in ids
    assert deleted['id'] not in ids
    assert len([event for event in result['events'] if event['type'] == 'chat.message']) == 100


def test_snapshot_counts_all_open_tasks_and_does_not_create_missing_stores(tmp_path):
    assert read_team_snapshot(tmp_path, None, [])['events'] == []
    assert list(tmp_path.iterdir()) == []
    tasks = TaskStore(tmp_path / 'tasks.sqlite3')
    for index in range(60):
        tasks.create(f'Task {index}')
    tasks.close()
    assert read_team_snapshot(tmp_path, None, ['tasks'])['open_tasks'] == 60
    assert read_team_snapshot(tmp_path, None, [])['open_tasks'] == 0


def test_joined_team_snapshot_counts_cached_mission_tasks(tmp_path):
    from retium.mission import MissionInbox
    store = EventStore(tmp_path / 'events.sqlite3')
    inbox = MissionInbox(store)
    assert read_team_snapshot(tmp_path, None, ['tasks'])['open_tasks'] is None
    with store._connection:
        inbox._save({'last_synced_at': 10, 'tasks': [{'id': 'open'}, {'id': 'done', 'completed_at': 9}]})
    assert read_team_snapshot(tmp_path, None, ['tasks'])['open_tasks'] == 1
    store.close()
