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
  removes what has expired (12 months after the student last opened it, D36).
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
-- Study groups (D28): a few students and a shared weekly goal, in minutes. The
-- owner is the member who set it up, then the longest-standing member.
CREATE TABLE IF NOT EXISTS study_groups (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    goal    INTEGER NOT NULL,
    owner   TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    grp        TEXT NOT NULL,
    token      TEXT NOT NULL,
    invited_by TEXT NOT NULL,
    invited    TEXT NOT NULL,
    joined     TEXT,
    PRIMARY KEY (grp, token)
);
CREATE INDEX IF NOT EXISTS group_members_by_token ON group_members (token);
-- Flashcards (D29): a student's own, private; FSRS-4.5 state per card, NULL until
-- its first review, and every review kept.
CREATE TABLE IF NOT EXISTS cards (
    id         TEXT PRIMARY KEY,
    token      TEXT NOT NULL,
    course     TEXT NOT NULL,
    front      TEXT NOT NULL,
    back       TEXT NOT NULL,
    created    TEXT NOT NULL,
    stability  REAL,
    difficulty REAL,
    last       TEXT,
    due        TEXT,
    reps       INTEGER NOT NULL DEFAULT 0,
    lapses     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cards_by_token ON cards (token, due);
CREATE TABLE IF NOT EXISTS card_reviews (
    card    TEXT NOT NULL,
    token   TEXT NOT NULL,
    at      TEXT NOT NULL,
    grade   INTEGER NOT NULL,
    elapsed REAL
);
CREATE INDEX IF NOT EXISTS card_reviews_by_token ON card_reviews (token, at);
-- A student's own FSRS-6 parameters for their cards (D53), once their answers are
-- enough: 21 numbers as JSON, the scored answers they were fitted to, when, and the
-- mean log loss there of theirs and of the population's.
CREATE TABLE IF NOT EXISTS card_fits (
    token        TEXT PRIMARY KEY,
    weights      TEXT NOT NULL,
    reviews      INTEGER NOT NULL,
    at           TEXT NOT NULL,
    loss         REAL NOT NULL,
    default_loss REAL NOT NULL
);
-- Accounts that last (D35): a plan's passkeys, of which only the public half is
-- kept, and the digest of its recovery code. A passkey ceremony's challenge waits
-- here a few minutes and is used once.
CREATE TABLE IF NOT EXISTS passkeys (
    id         TEXT PRIMARY KEY,
    token      TEXT NOT NULL,
    public_key BLOB NOT NULL,
    alg        INTEGER NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    device     TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    used       TEXT
);
CREATE INDEX IF NOT EXISTS passkeys_by_token ON passkeys (token);
CREATE TABLE IF NOT EXISTS recovery_codes (
    token   TEXT PRIMARY KEY,
    digest  TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS challenges (
    value   TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    token   TEXT NOT NULL DEFAULT '',
    expires TEXT NOT NULL
);
-- Reminders (D38): the devices where a plan's reminders are on (the address the
-- browser's push service gave each, and the keys to encrypt for it), which sessions
-- were reminded (kept three days, so none is reminded twice), and the server's own
-- key pair that signs every push.
CREATE TABLE IF NOT EXISTS push_devices (
    endpoint TEXT PRIMARY KEY,
    token    TEXT NOT NULL,
    p256dh   TEXT NOT NULL,
    auth     TEXT NOT NULL,
    device   TEXT NOT NULL DEFAULT '',
    created  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS push_devices_by_token ON push_devices (token);
CREATE TABLE IF NOT EXISTS push_sent (
    token TEXT NOT NULL,
    sid   TEXT NOT NULL,
    at    TEXT NOT NULL,
    PRIMARY KEY (token, sid)
);
CREATE TABLE IF NOT EXISTS server_keys (
    name    TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    created TEXT NOT NULL
);
-- What reaches the owner (D43): students' messages from the feedback form, which
-- belong to the plan that sent them and go with it; and the error log, one row per
-- kind of error with how often and when it last happened, which names no plan.
CREATE TABLE IF NOT EXISTS feedback (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    token   TEXT NOT NULL,
    created TEXT NOT NULL,
    kind    TEXT NOT NULL,
    page    TEXT NOT NULL DEFAULT '',
    lang    TEXT NOT NULL DEFAULT '',
    body    TEXT NOT NULL,
    seen    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS feedback_by_time ON feedback (created);
CREATE TABLE IF NOT EXISTS errors (
    key     TEXT PRIMARY KEY,
    source  TEXT NOT NULL,
    route   TEXT NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT '',
    first   TEXT NOT NULL,
    last    TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 1
);
"""

# Columns added after their table was first made: a store from before gets them
# when it is opened (D39, D40), with the table's other rows unchanged.
_ADDED_COLUMNS = (
    ("profiles", "year", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "country", "TEXT NOT NULL DEFAULT ''"),
    ("study_groups", "invite", "TEXT"),
)
_AFTER_COLUMNS = "CREATE UNIQUE INDEX IF NOT EXISTS study_groups_by_invite ON study_groups (invite);"

# Leaving every group (D28): the next member in line owns a group its owner left,
# and a group nobody is left in goes, with its invitations.
LEAVE_GROUPS = (
    "DELETE FROM group_members WHERE token = :t",
    "UPDATE study_groups SET owner = (SELECT m.token FROM group_members m WHERE m.grp = study_groups.id "
    "AND m.joined IS NOT NULL ORDER BY m.joined, m.rowid LIMIT 1) WHERE owner = :t",
    "DELETE FROM group_members WHERE grp IN (SELECT id FROM study_groups WHERE owner IS NULL)",
    "DELETE FROM study_groups WHERE owner IS NULL",
)

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
    *LEAVE_GROUPS,
    "DELETE FROM card_reviews WHERE token = :t",
    "DELETE FROM card_fits WHERE token = :t",
    "DELETE FROM cards WHERE token = :t",
    "DELETE FROM passkeys WHERE token = :t",
    "DELETE FROM recovery_codes WHERE token = :t",
    "DELETE FROM challenges WHERE token = :t",
    "DELETE FROM push_devices WHERE token = :t",
    "DELETE FROM push_sent WHERE token = :t",
    "DELETE FROM feedback WHERE token = :t",
    # The AI ledger keeps what each call cost, for the month's cap, and forgets whose
    # plan it was (D12).
    "UPDATE ai_usage SET token = '' WHERE token = :t",
)


# A plan's copy of its data (D43): the plan's own rows of each table, column by
# column, beside what `_FORGET` deletes. Left out are the secrets of the server and
# of a device, which say nothing about the student: a passkey's public key, a push
# address and its keys, the recovery code's digest, a ceremony's challenge, which
# sessions were reminded.
_EXPORT = {
    "events": "SELECT at, kind, detail FROM events WHERE token = :t ORDER BY id",
    "profile": "SELECT handle, university, programme, year, country, bio, created FROM profiles "
    "WHERE token = :t",
    "posts": "SELECT id, kind, created, day, visibility, data, hidden FROM posts WHERE token = :t "
    "ORDER BY created",
    "comments": "SELECT id, post, created, body, hidden FROM comments WHERE token = :t ORDER BY created",
    "kudos": "SELECT post, created FROM kudos WHERE token = :t ORDER BY created",
    "follows": "SELECT CASE WHEN f.follower = :t THEN 'you follow' ELSE 'follows you' END AS direction, "
    "COALESCE(p.handle, '') AS handle, f.status, f.created FROM follows f LEFT JOIN profiles p "
    "ON p.token = CASE WHEN f.follower = :t THEN f.followed ELSE f.follower END "
    "WHERE f.follower = :t OR f.followed = :t ORDER BY f.created",
    "blocks": "SELECT COALESCE(p.handle, '') AS handle, b.created FROM blocks b "
    "LEFT JOIN profiles p ON p.token = b.blocked WHERE b.blocker = :t ORDER BY b.created",
    "reports": "SELECT target, kind, reason, created FROM reports WHERE reporter = :t ORDER BY created",
    "groups": "SELECT g.name, g.goal, m.invited, m.joined, g.owner = :t AS owner FROM group_members m "
    "JOIN study_groups g ON g.id = m.grp WHERE m.token = :t ORDER BY m.invited",
    "cards": "SELECT id, course, front, back, created, stability, difficulty, last, due, reps, lapses "
    "FROM cards WHERE token = :t ORDER BY created",
    "card_reviews": "SELECT card, at, grade, elapsed FROM card_reviews WHERE token = :t ORDER BY at",
    "card_fit": "SELECT weights, reviews, at, loss, default_loss FROM card_fits WHERE token = :t",
    "passkeys": "SELECT device, created, used FROM passkeys WHERE token = :t ORDER BY created",
    "recovery_code": "SELECT created FROM recovery_codes WHERE token = :t",
    "reminder_devices": "SELECT device, created FROM push_devices WHERE token = :t ORDER BY created",
    "ai_usage": "SELECT created, feature, model, input_tokens, output_tokens, cost, ok FROM ai_usage "
    "WHERE token = :t ORDER BY created",
    "feedback": "SELECT created, kind, page, body FROM feedback WHERE token = :t ORDER BY id",
}


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
            for table, column, declaration in _ADDED_COLUMNS:
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            db.executescript(_AFTER_COLUMNS)

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

    def export(self, token: str) -> dict[str, list[dict]]:
        """Every row kept for a plan outside its document, table by table (`_EXPORT`)."""
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            return {
                name: [dict(row) for row in db.execute(sql, {"t": token})] for name, sql in _EXPORT.items()
            }

    def events(self, token: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT at, kind, detail FROM events WHERE token = ? ORDER BY id", (token,)
            ).fetchall()
        return [{"at": at, "kind": kind, "detail": detail} for at, kind, detail in rows]

    def sweep(self, now: datetime | None = None, expiry: Callable[[dict], str | None] | None = None) -> int:
        """Delete every document whose expiry has passed, with its log.

        With `expiry` (document -> its expiry, ISO), a document is checked again
        before it goes, and one without an expiry is checked too: a document saved
        under an older rule gets the current one (D36), and is deleted only if that
        has passed as well."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        gone = 0
        with self._connect() as db:
            db.execute("DELETE FROM challenges WHERE expires < ?", (stamp,))
            rows = db.execute(
                "SELECT token, data, expires FROM subscriptions WHERE expires < ?"
                + (" OR expires IS NULL" if expiry is not None else ""),
                (stamp,),
            ).fetchall()
            for token, data, expires in rows:
                current = expiry(json.loads(data)) if expiry is not None else None
                if current is not None and current >= stamp:
                    db.execute("UPDATE subscriptions SET expires = ? WHERE token = ?", (current, token))
                    continue
                if current is None and expires is None:
                    continue  # nothing says when: kept, as before
                db.execute("DELETE FROM subscriptions WHERE token = ?", (token,))
                db.execute("DELETE FROM events WHERE token = ?", (token,))
                self._forget(db, token)
                gone += 1
        return gone

    def tokens(self) -> list[str]:
        with self._connect() as db:
            return [t for (t,) in db.execute("SELECT token FROM subscriptions ORDER BY token")]

    def all_events(self, since: str) -> list[tuple[str, str, str, str]]:
        """(token, at, kind, detail) since an ISO time, for the pilot's measures."""
        with self._connect() as db:
            return db.execute(
                "SELECT token, at, kind, detail FROM events WHERE at >= ? ORDER BY id", (since,)
            ).fetchall()

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
