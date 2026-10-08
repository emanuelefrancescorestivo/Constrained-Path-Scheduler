"""
Where the hosted product keeps its students: one SQLite file (docs/ROADMAP.md, D2).

A student is a subscription (`service.Subscription`), stored as one JSON document
under its secret token, plus a log of what happened to it (a feedback tap, a
refresh), which is what a pilot measures. This module knows nothing about plans: it
stores documents, so that `service` can use it without an import cycle.

Three properties matter more than speed:

* **No lost update.** A feedback tap and a background refresh can touch the same
  student at once. Every document carries a version; `update` reads, computes
  outside any lock (a refresh may read a timetable from the network), and writes
  only if the version is unchanged, retrying otherwise.
* **Deletion is deletion.** `delete` removes the document and its log; `sweep`
  removes what has expired (30 days after the last exam, D6).
* **Safe to copy while running.** `backup` uses SQLite's online backup, so a copy
  taken while students are being served is consistent.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

TOKEN = re.compile(r"[A-Za-z0-9_-]{22,64}")
FILENAME = "cps.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    token   TEXT PRIMARY KEY,
    data    TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated TEXT NOT NULL,
    expires TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    token  TEXT NOT NULL,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_by_token ON events (token);

-- The study network (DECISIONS.md D16 to D20). Rows belong to a plan's token and
-- go with it (`delete`, `sweep`). Post and comment ids are random.
CREATE TABLE IF NOT EXISTS profiles (
    token      TEXT PRIMARY KEY,
    handle     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    university TEXT NOT NULL DEFAULT '',
    programme  TEXT NOT NULL DEFAULT '',
    bio        TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id         TEXT PRIMARY KEY,
    token      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    created    TEXT NOT NULL,
    day        TEXT NOT NULL,
    visibility TEXT NOT NULL,
    data       TEXT NOT NULL,
    hidden     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS posts_by_token ON posts (token, created);
CREATE INDEX IF NOT EXISTS posts_by_visibility ON posts (visibility, created);
CREATE TABLE IF NOT EXISTS photos (
    name TEXT PRIMARY KEY,
    post TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follows (
    follower TEXT NOT NULL,
    followed TEXT NOT NULL,
    status   TEXT NOT NULL,
    created  TEXT NOT NULL,
    PRIMARY KEY (follower, followed)
);
CREATE TABLE IF NOT EXISTS kudos (
    post    TEXT NOT NULL,
    token   TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (post, token)
);
CREATE TABLE IF NOT EXISTS comments (
    id      TEXT PRIMARY KEY,
    post    TEXT NOT NULL,
    token   TEXT NOT NULL,
    created TEXT NOT NULL,
    body    TEXT NOT NULL,
    hidden  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS comments_by_post ON comments (post, created);
CREATE TABLE IF NOT EXISTS reports (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    target   TEXT NOT NULL,
    kind     TEXT NOT NULL,
    reporter TEXT NOT NULL,
    reason   TEXT NOT NULL,
    created  TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    UNIQUE (target, reporter)
);
CREATE TABLE IF NOT EXISTS ai_usage (
    created       TEXT NOT NULL,
    token         TEXT NOT NULL,
    feature       TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost          REAL NOT NULL,
    ok            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_usage_by_time ON ai_usage (created);
CREATE TABLE IF NOT EXISTS blocks (
    blocker TEXT NOT NULL,
    blocked TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (blocker, blocked)
);
"""

# What deleting a plan removes from the network: its own rows, and what others
# left on its posts (comments, kudos, reports on them).
_FORGET = (
    "DELETE FROM reports WHERE target IN (SELECT c.id FROM comments c JOIN posts p ON p.id = c.post "
    "WHERE p.token = :t)",
    "DELETE FROM comments WHERE post IN (SELECT id FROM posts WHERE token = :t)",
    "DELETE FROM kudos WHERE post IN (SELECT id FROM posts WHERE token = :t)",
    "DELETE FROM reports WHERE target IN (SELECT id FROM posts WHERE token = :t)",
    "DELETE FROM reports WHERE target IN (SELECT id FROM comments WHERE token = :t)",
    "DELETE FROM photos WHERE post IN (SELECT id FROM posts WHERE token = :t)",
    "DELETE FROM posts WHERE token = :t",
    "DELETE FROM comments WHERE token = :t",
    "DELETE FROM kudos WHERE token = :t",
    "DELETE FROM reports WHERE reporter = :t",
    "DELETE FROM follows WHERE follower = :t OR followed = :t",
    "DELETE FROM blocks WHERE blocker = :t OR blocked = :t",
    "DELETE FROM profiles WHERE token = :t",
    # The AI ledger keeps what each call cost, for the month's cap, and forgets whose
    # plan it was (D12).
    "UPDATE ai_usage SET token = '' WHERE token = :t",
)


class Conflict(Exception):
    """The document kept changing under an update; the caller may try again."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """One SQLite file. `path` may be the file, or a directory to keep it in."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        path = Path(path)
        if path.suffix == "" or path.is_dir():
            path = path / FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A connection per operation: safe across the server's threads, and SQLite
        # opens a local file in microseconds.
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def photos(self) -> Path:
        """Where posts' photos are kept, next to the database (DECISIONS.md D18)."""
        return self.path.parent / "photos"

    def connection(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        """A connection in a transaction, for the modules that keep their own tables
        here (`cps.social`)."""
        return self._connect()

    def _forget(self, db: sqlite3.Connection, token: str) -> None:
        photos = [
            name
            for (data,) in db.execute("SELECT data FROM posts WHERE token = ?", (token,))
            for name in json.loads(data).get("photos", [])
        ]
        for statement in _FORGET:
            db.execute(statement, {"t": token})
        for name in photos:
            (self.photos / name).unlink(missing_ok=True)

    @staticmethod
    def valid(token: str) -> bool:
        return bool(TOKEN.fullmatch(token))

    def get(self, token: str) -> dict | None:
        if not self.valid(token):
            return None
        with self._connect() as db:
            row = db.execute("SELECT data FROM subscriptions WHERE token = ?", (token,)).fetchone()
        return json.loads(row[0]) if row else None

    def _get_versioned(self, token: str) -> tuple[dict, int] | None:
        with self._connect() as db:
            row = db.execute("SELECT data, version FROM subscriptions WHERE token = ?", (token,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def put(self, token: str, data: dict, expires: str | None = None) -> None:
        """Create or replace a document."""
        if not self.valid(token):
            raise ValueError("not a token")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO subscriptions (token, data, version, updated, expires) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT (token) DO UPDATE SET
                    data = excluded.data, version = version + 1,
                    updated = excluded.updated, expires = excluded.expires
                """,
                (token, json.dumps(data), _now(), expires),
            )

    def update(
        self,
        token: str,
        change: Callable[[dict], tuple[dict, str | None]],
        attempts: int = 3,
    ) -> dict | None:
        """Apply `change` (document -> new document, its expiry) without losing a
        concurrent write. None if the document does not exist (or was deleted
        meanwhile, which is not undone)."""
        if not self.valid(token):
            return None
        for _ in range(attempts):
            current = self._get_versioned(token)
            if current is None:
                return None
            data, version = current
            new, expires = change(data)
            with self._connect() as db:
                done = db.execute(
                    """
                    UPDATE subscriptions SET data = ?, version = version + 1, updated = ?, expires = ?
                    WHERE token = ? AND version = ?
                    """,
                    (json.dumps(new), _now(), expires, token, version),
                ).rowcount
            if done:
                return new
        raise Conflict(f"the document changed {attempts} times during an update")

    def delete(self, token: str) -> bool:
        if not self.valid(token):
            return False
        with self._connect() as db:
            gone = db.execute("DELETE FROM subscriptions WHERE token = ?", (token,)).rowcount
            db.execute("DELETE FROM events WHERE token = ?", (token,))
            self._forget(db, token)
        return bool(gone)

    def log(self, token: str, kind: str, detail: str = "") -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO events (token, at, kind, detail) VALUES (?, ?, ?, ?)",
                (token, _now(), kind, detail),
            )

    def log_daily(self, token: str, kind: str, detail: str = "") -> bool:
        """Log `kind` once per token per day (UTC); True if this was the first
        today. A page's visits count as one a day (DECISIONS.md, D15)."""
        stamp = _now()
        with self._connect() as db:
            added = db.execute(
                """
                INSERT INTO events (token, at, kind, detail)
                SELECT ?, ?, ?, ? WHERE NOT EXISTS (
                    SELECT 1 FROM events WHERE token = ? AND kind = ? AND substr(at, 1, 10) = ?
                )
                """,
                (token, stamp, kind, detail, token, kind, stamp[:10]),
            ).rowcount
        return bool(added)

    def events(self, token: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT at, kind, detail FROM events WHERE token = ? ORDER BY id", (token,)
            ).fetchall()
        return [{"at": at, "kind": kind, "detail": detail} for at, kind, detail in rows]

    def sweep(self, now: datetime | None = None) -> int:
        """Delete every document whose expiry has passed, with its log."""
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        with self._connect() as db:
            tokens = [
                t
                for (t,) in db.execute(
                    "SELECT token FROM subscriptions WHERE expires IS NOT NULL AND expires < ?", (stamp,)
                )
            ]
            for token in tokens:
                db.execute("DELETE FROM subscriptions WHERE token = ?", (token,))
                db.execute("DELETE FROM events WHERE token = ?", (token,))
                self._forget(db, token)
        return len(tokens)

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0])

    def backup_rotating(self, directory: str | Path, keep_days: int = 7, now: datetime | None = None) -> Path:
        """A dated copy in `directory`, and the copies older than `keep_days`
        deleted: the privacy page promises that deleted data leaves backups
        within a week."""
        now = now or datetime.now(UTC)
        directory = Path(directory)
        target = self.backup(directory / f"cps-{now:%Y%m%d-%H%M%S}.sqlite")
        cutoff = now.timestamp() - keep_days * 86400
        for old in directory.glob("cps-*.sqlite"):
            if old != target and old.stat().st_mtime < cutoff:
                old.unlink()
        return target

    def backup(self, destination: str | Path) -> Path:
        """A consistent copy of the whole file, taken while it is in use."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination
