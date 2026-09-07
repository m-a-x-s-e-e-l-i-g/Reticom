import copy
import json

import pytest
import RNS

from retium.continuity import HostDirectory, TeamContinuity, canonical, endpoint, verify_policy
from retium.protocol import new_event
from retium.replication import ReplicatedTable
from retium.store import EventStore
from retium.tasks import TaskStore


def pair(tmp_path, store_type=EventStore, table="events", key="event_id"):
    stores = [store_type(tmp_path / f'{name}.sqlite3') for name in ('a', 'b')]
    replicas = [ReplicatedTable(store, table, key, name) for store, name in zip(stores, ('a', 'b'))]
    return *stores, *replicas


def sync(source, target, name):
    while True:
        page = source.page(target.cursor(name))
        target.merge(name, page, {'a', 'b'})
        if page['cursor'] == page['head']:
            break


def test_full_history_pagination_duplicates_and_restart(tmp_path):
    a, b, ra, rb = pair(tmp_path)
    for index in range(145):
        a.insert(new_event('chat.message', 'A', message=str(index)), 'sender')
    sync(ra, rb, 'a')
    assert b.count() == 145
    sync(rb, ra, 'b')
    assert a.count() == 145
    before = rb.head()
    sync(ra, rb, 'a')
    assert rb.head() == before
    b.close()
    b = EventStore(tmp_path / 'b.sqlite3')
    rb = ReplicatedTable(b, 'events', 'event_id', 'b')
    assert rb.cursor('a') == ra.head()
    assert b.count() == 145


def test_partition_merges_and_deleted_tasks_never_resurrect(tmp_path):
    a, b, ra, rb = pair(tmp_path, TaskStore, 'tasks', 'task_id')
    task = a.create('Shared task')
    sync(ra, rb, 'a')
    a.delete(task['id'])
    b.complete(task['id'], 'B', 'b')
    a.create('Only on A')
    b.create('Only on B')
    sync(rb, ra, 'b')
    sync(ra, rb, 'a')
    assert {t['title'] for t in a.list()} == {t['title'] for t in b.list()} == {'Only on A', 'Only on B'}
    with b._lock, b._connection:
        b._connection.execute('INSERT OR IGNORE INTO tasks(task_id,title,created_at) VALUES(?,?,?)', (task['id'], 'Replay', 1))
    assert len(b.list()) == 2


def test_clear_history_tombstones_propagate_and_keep_map(tmp_path):
    a, b, ra, rb = pair(tmp_path)
    event = new_event('chat.message', 'A', message='Delete me')
    a.insert(event, 'sender')
    a.insert(new_event('marker.created', 'A', lat=1, lon=2, label='Keep', marker_type='waypoint'), 'sender')
    sync(ra, rb, 'a')
    a.clear_communications()
    sync(ra, rb, 'a')
    assert [e['type'] for e in b.recent()] == ['marker.created']
    assert b.insert(event, 'sender') is False


def test_concurrent_edits_converge_without_wall_clock(tmp_path):
    a, b, ra, rb = pair(tmp_path, TaskStore, 'tasks', 'task_id')
    task = a.create('Complete')
    sync(ra, rb, 'a')
    a.complete(task['id'], 'A', 'a')
    b.complete(task['id'], 'B', 'b')
    sync(ra, rb, 'a')
    sync(rb, ra, 'b')
    assert a.list() == b.list()


def test_invalid_batch_rolls_back_data_cursor_and_trigger_state(tmp_path):
    a, b, ra, rb = pair(tmp_path)
    a.insert(new_event('chat.message', 'A', message='first'), 'sender')
    a.insert(new_event('chat.message', 'A', message='second'), 'sender')
    page = ra.page()
    page['rows'][1]['origin'] = 'unapproved'
    with pytest.raises(ValueError):
        rb.merge('a', page, {'a', 'b'})
    assert b.count() == 0 and rb.cursor('a') == 0
    b.insert(new_event('chat.message', 'B', message='local still journals'), 'sender')
    assert rb.head() == 1


def signed_policy(owner, backup=None, revision=1):
    def host(identity):
        return {'destination': endpoint(identity), 'public_key': identity.get_public_key().hex(), 'label': 'Host', 'ready': True}
    policy = {'v': 1, 'team': endpoint(owner), 'owner': owner.get_public_key().hex(), 'revision': revision,
        'hosts': [host(owner)] + ([host(backup)] if backup else []), 'preferred': endpoint(owner)}
    return {'policy': policy, 'signature': owner.sign(canonical(policy)).hex()}


def test_policy_binds_original_join_code_and_rejects_forgery_rollback(tmp_path):
    owner, backup, attacker = RNS.Identity(), RNS.Identity(), RNS.Identity()
    root = endpoint(owner)
    envelope = signed_policy(owner, backup, 2)
    directory = HostDirectory(tmp_path / 'policy.json', root)
    directory.accept(envelope)
    assert directory.candidates() == [root, endpoint(backup)]
    directory.failed(root)
    assert directory.candidates()[0] == endpoint(backup)
    changed = copy.deepcopy(envelope)
    changed['policy']['preferred'] = endpoint(backup)
    with pytest.raises(ValueError):
        verify_policy(changed, root)
    with pytest.raises(ValueError):
        directory.accept(signed_policy(owner, revision=1))
    with pytest.raises(ValueError):
        directory.accept(signed_policy(attacker))
    assert HostDirectory(tmp_path / 'policy.json', root).envelope == envelope


def test_replication_never_exports_host_private_key(tmp_path):
    owner = RNS.Identity()
    envelope = signed_policy(owner)
    assert owner.get_private_key().hex() not in json.dumps(envelope)
    assert verify_policy(envelope, endpoint(owner))['owner'] == owner.get_public_key().hex()


def test_replication_access_rejects_unapproved_and_cross_team_requests(tmp_path):
    owner, outsider = RNS.Identity(), RNS.Identity()
    coordinator = TeamContinuity.__new__(TeamContinuity)
    coordinator.root = endpoint(owner)
    coordinator.directory = HostDirectory(tmp_path/'policy.json', coordinator.root)
    coordinator.directory.accept(signed_policy(owner))
    request = lambda identity, data: json.loads(coordinator.request('/continuity/page', data, b'id', b'link', identity, 0))
    assert 'Identified' in request(None, {'team': coordinator.root})['error']
    assert 'not approved' in request(outsider, {'team': coordinator.root, 'table': 'private'})['error']
    assert 'Wrong' in request(owner, {'team': endpoint(outsider)})['error']
