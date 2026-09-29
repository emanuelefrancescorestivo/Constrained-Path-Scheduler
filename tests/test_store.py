"""
The SQLite store: documents under secret tokens, versioned updates, deletion that
deletes, expiry, and a copy that can be taken while the file is in use.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from cps.store import FILENAME, Conflict, Store

TOKEN = "a" * 32
OTHER = "b" * 32


def test_a_directory_holds_one_file(tmp_path):
    store = Store(tmp_path)
    assert store.path == tmp_path / FILENAME
    assert Store(tmp_path / "custom.sqlite").path == tmp_path / "custom.sqlite"
    assert Store(tmp_path / "new" / "dir").path == tmp_path / "new" / "dir" / FILENAME


def test_put_get_and_versions(tmp_path):
    store = Store(tmp_path)
    assert store.get(TOKEN) is None
    store.put(TOKEN, {"x": 1})
    store.put(TOKEN, {"x": 2}, "2027-01-01T00:00:00+00:00")
    assert store.get(TOKEN) == {"x": 2}
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT version, expires FROM subscriptions").fetchall() == [
            (2, "2027-01-01T00:00:00+00:00")
        ]
    assert store.count() == 1


@pytest.mark.parametrize("token", ["../../etc/passwd", "short", "a/b" * 12, "x" * 65, "a" * 31 + "'"])
def test_what_is_not_a_token_reads_and_writes_nothing(tmp_path, token):
    store = Store(tmp_path)
    assert store.get(token) is None
    assert store.update(token, lambda d: (d, None)) is None
    assert not store.delete(token)
    with pytest.raises(ValueError):
        store.put(token, {})


def test_an_update_is_not_lost_to_a_concurrent_write(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {"n": 0, "seen": []})
    first = True

    def change(data):
        nonlocal first
        if first:  # another writer gets in between the read and the write
            first = False
            store.put(TOKEN, {**data, "seen": [*data["seen"], "other"]})
        return {**data, "n": data["n"] + 1}, None

    assert store.update(TOKEN, change) == {"n": 1, "seen": ["other"]}
    assert store.get(TOKEN) == {"n": 1, "seen": ["other"]}


def test_an_update_that_keeps_losing_says_so(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {"n": 0})

    def always_overtaken(data):
        store.put(TOKEN, {"n": data["n"] + 100})
        return {"n": -1}, None

    with pytest.raises(Conflict):
        store.update(TOKEN, always_overtaken)


def test_an_update_does_not_bring_back_a_deleted_document(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {"n": 0})

    def deleted_meanwhile(data):
        store.delete(TOKEN)
        return {"n": 1}, None

    assert store.update(TOKEN, deleted_meanwhile) is None
    assert store.get(TOKEN) is None


def test_threads_updating_at_once_lose_nothing(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {"n": 0})
    errors = []

    def bump():
        for _ in range(20):
            try:
                store.update(TOKEN, lambda d: ({"n": d["n"] + 1}, None), attempts=200)
            except Exception as error:
                errors.append(error)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.get(TOKEN) == {"n": 80}


def test_delete_removes_the_log_too(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {})
    store.put(OTHER, {})
    store.log(TOKEN, "report", "done")
    store.log(OTHER, "refresh")
    assert [e["kind"] for e in store.events(TOKEN)] == ["report"]
    assert store.delete(TOKEN)
    assert store.events(TOKEN) == [] and store.get(TOKEN) is None
    assert [e["kind"] for e in store.events(OTHER)] == ["refresh"]
    assert not store.delete(TOKEN)


def test_sweep_deletes_what_has_expired(tmp_path):
    store = Store(tmp_path)
    store.put(TOKEN, {}, "2026-02-01T00:00:00+00:00")
    store.put(OTHER, {}, "2026-06-01T00:00:00+00:00")
    store.put("c" * 32, {})  # no expiry: kept
    store.log(TOKEN, "report")
    assert store.sweep(datetime(2026, 3, 1, tzinfo=UTC)) == 1
    assert store.get(TOKEN) is None and store.events(TOKEN) == []
    assert store.count() == 2


def test_a_backup_is_a_whole_copy(tmp_path):
    store = Store(tmp_path / "live")
    store.put(TOKEN, {"x": 1})
    copy = store.backup(tmp_path / "backups" / "copy.sqlite")
    assert Store(copy).get(TOKEN) == {"x": 1}
