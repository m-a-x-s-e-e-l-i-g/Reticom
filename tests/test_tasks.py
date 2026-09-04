import pytest

from retium.tasks import TaskError, TaskStore


def test_tasks_are_persistent_and_completion_is_idempotent(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    task = store.create("Check north entrance", "ALPHA")

    restored = TaskStore(path)
    assert restored.list()[0]["title"] == "Check north entrance"
    assert restored.list()[0]["completed_at"] is None

    completed = restored.complete(task["id"], "ALPHA", "sender-1")
    repeated = restored.complete(task["id"], "BRAVO", "sender-2")

    assert completed is not None
    assert completed["completed_by"] == "ALPHA"
    assert repeated is not None
    assert repeated["completed_by"] == "ALPHA"
    assert repeated["completed_at"] == completed["completed_at"]


def test_task_validation_is_bounded(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")

    with pytest.raises(TaskError, match="task title"):
        store.create("")
    with pytest.raises(TaskError, match="assignee"):
        store.create("Task", "X" * 25)
    with pytest.raises(TaskError, match="UUID"):
        store.complete("not-a-task", "ALPHA", "sender")


def test_command_deletes_open_and_completed_tasks(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    completed = store.create("Archive this", "ALPHA")
    open_task = store.create("Keep this", None)
    store.complete(completed["id"], "ALPHA", "sender-1")

    deleted = store.delete(completed["id"])

    assert deleted is not None
    assert deleted["title"] == "Archive this"
    assert [task["id"] for task in store.list()] == [open_task["id"]]
    deleted_open = store.delete(open_task["id"])
    assert deleted_open is not None
    assert deleted_open["completed_at"] is None
    assert store.list() == []
    assert store.delete("00112233-4455-6677-8899-aabbccddeeff") is None
