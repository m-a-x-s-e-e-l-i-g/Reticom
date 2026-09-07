import copy
import hashlib
import time

import pytest

from retium.mission import MissionInbox, MissionPublisher, encoded
from retium.store import EventStore

SENDER = "a" * 32


def event(index, kind="marker.created"):
    return {"id": str(index), "type": kind, "callsign": "Alpha", "created_at": int(time.time()),
            "label": f"Waypoint {index}", "lat": 51, "lon": 4, "description": "x" * 300}


def content(store):
    return {"events": store.mission_events(), "team": {"name": "Mission", "modules": ["tasks"]},
            "tasks": [{"id": "task-a", "title": "Meet here"}], "private_events": []}


def transfer(publisher, inbox, source, owner=SENDER):
    for _ in range(100):
        page = publisher.page(owner, inbox.request(), lambda: content(source))
        inbox.accept(page)
        if page["mission"].get("complete") or page["mission"].get("unchanged"):
            return
    pytest.fail("mission did not finish")


def test_old_map_objects_survive_hundreds_of_new_positions_and_recent_intel_is_bounded(tmp_path):
    store = EventStore(tmp_path / "host.sqlite")
    store.insert(event("old"), SENDER)
    store.insert(event("drawing", "drawing.created"), SENDER)
    for n in range(700):
        store.insert(event(n, "position.updated"), SENDER)
    for n in range(110):
        store.insert(event(f"chat-{n}", "chat.message"), SENDER)
    mission = store.mission_events()
    assert {e["id"] for e in mission if e["type"] in {"marker.created", "drawing.created"}} == {"old", "drawing"}
    assert sum(e["type"] == "position.updated" for e in mission) == 20
    assert sum(e["type"] == "chat.message" for e in mission) == 100
    store.close()


def test_resume_atomic_apply_prune_deleted_and_preserve_queued_and_concurrent_work(tmp_path):
    source = EventStore(tmp_path / "host.sqlite")
    cache_path = tmp_path / "field.sqlite"
    cache = EventStore(cache_path)
    cache.insert(event("removed"), SENDER)
    cache.insert(event("queued"), SENDER, delivery_status="queued")
    for n in range(150): source.insert(event(n), SENDER)
    publisher, inbox = MissionPublisher(), MissionInbox(cache)
    first = publisher.page(SENDER, inbox.request(), lambda: content(source))
    assert len(encoded(first)) < 14000
    assert first["mission"]["complete"] is False
    inbox.accept(first)
    assert len(cache.mission_events()) == 2  # no partial mission displayed
    cursor = inbox.request()["offset"]
    cache.mark_verified("queued")  # acknowledgement during download
    cache.insert(event("concurrent"), SENDER)
    cache.close()
    cache = EventStore(cache_path)
    inbox = MissionInbox(cache)
    assert inbox.request()["offset"] == cursor
    transfer(publisher, inbox, source)
    ids = {e["id"] for e in cache.mission_events()}
    assert "removed" not in ids
    assert {"queued", "concurrent", *map(str, range(150))} <= ids
    assert inbox.status()["tasks"][0]["id"] == "task-a"
    assert inbox.status()["state"] == "synced"
    unchanged = publisher.page(SENDER, inbox.request(), lambda: content(source))
    assert unchanged["mission"]["unchanged"] is True
    inbox.accept(unchanged)
    inbox.accept({"mission_restart": True})
    assert inbox.status()["state"] == "pending"
    assert len(cache.mission_events()) == len(ids)
    inbox.accept(unchanged)
    assert inbox.status()["state"] == "synced"
    assert inbox.request(force=True) == {"known": None}
    assert inbox.status()["state"] == "pending"
    source.close(); cache.close()


def test_bad_checksum_out_of_order_and_other_identity_cannot_apply_or_read_snapshot(tmp_path):
    source, cache = EventStore(tmp_path / "host.sqlite"), EventStore(tmp_path / "cache.sqlite")
    source.insert(event("one"), SENDER)
    cache.insert(event("keep"), SENDER)
    publisher, inbox = MissionPublisher(), MissionInbox(cache)
    page = publisher.page(SENDER, {}, lambda: content(source))
    corrupted = copy.deepcopy(page)
    corrupted["records"][0]["value"]["label"] = "tampered"
    with pytest.raises(ValueError, match="checksum"): inbox.accept(corrupted)
    assert cache.mission_events()[0]["id"] == "keep"
    wrong = copy.deepcopy(page)
    wrong["mission"]["offset"] = 99
    with pytest.raises(ValueError): inbox.accept(wrong)
    assert publisher.page("b" * 32, {"snapshot": page["mission"]["snapshot"], "offset": 0}, lambda: pytest.fail()) == {"mission_restart": True}
    source.close(); cache.close()


def test_confirmed_deletions_and_report_status_are_in_the_next_snapshot(tmp_path):
    source, cache = EventStore(tmp_path / "host.sqlite"), EventStore(tmp_path / "cache.sqlite")
    source.insert(event("remove"), SENDER)
    source.insert(event("clear"), SENDER)
    publisher, inbox = MissionPublisher(), MissionInbox(cache)
    transfer(publisher, inbox, source)
    source.insert({**event("delete", "marker.deleted"), "marker_id": "remove"}, SENDER)
    source.insert({**event("status", "marker.status"), "marker_id": "clear", "report_status": "cleared"}, SENDER)
    transfer(publisher, inbox, source)
    events = cache.mission_events()
    assert [e["id"] for e in events] == ["clear"]
    assert events[0]["report_view"]["status"] == "cleared"
    source.close(); cache.close()
