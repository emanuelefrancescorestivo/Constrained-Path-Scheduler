"""
What reaches the owner (DECISIONS.md D43): students' messages from the feedback
form, and the error log.

Feedback is what a student chose to write, with the kind they picked, the page they
were on (its route, never the plan's address) and their language. It belongs to
their plan and is deleted with it (`store._FORGET`).

The error log is for the defects nobody reports: an error the server did not
expect, or one a page's script met in the student's browser. One row per kind of
error (where, what, the message) with how many times and when it last happened, so
a loop that fails a thousand times is one line to read, not a thousand. It names no
plan: a plan's address is cut out of every text before it is kept
(`scrub`), and rows not seen for `ERRORS_KEPT` are dropped.

Rejected: a hosted error tracker (a third party receiving students' requests, and
an account and a script on every page, against D6); the server's log alone (on the
free host it is gone at every restart, and the browser's errors never reach it).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from .store import Store

FEEDBACK_KINDS = (
    ("wrong", "Something is wrong"),
    ("confusing", "Something is confusing"),
    ("idea", "An idea"),
    ("like", "Something I like"),
)
MAX_FEEDBACK = 2000  # characters
ERRORS_KEPT = timedelta(days=30)
_MESSAGE = 300  # characters of an error's message kept
_DETAIL = 2000

# A plan's address, its sessions' and a token wherever it appears in a text.
_PLAN_PATH = re.compile(r"/(p|s|feed)/[A-Za-z0-9_-]{22,64}")
_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{32,64}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def scrub(text: str, limit: int = _MESSAGE) -> str:
    """A text with every plan's address, token and e-mail address cut out, and cut
    to `limit` characters."""
    text = _PLAN_PATH.sub(lambda m: f"/{m.group(1)}/…", str(text))
    text = _EMAIL.sub("…@…", _TOKEN.sub("…", text))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def page_of(path: str) -> str:
    """The page a request was for, as the owner reads it: "/p/…/week"."""
    return scrub(path.split("?", 1)[0], 120)


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


def add_feedback(
    store: Store, token: str, *, kind: str, body: str, page: str, lang: str, now: datetime
) -> int:
    with store.connection() as db:
        cursor = db.execute(
            "INSERT INTO feedback (token, created, kind, page, lang, body) VALUES (?, ?, ?, ?, ?, ?)",
            (token, _stamp(now), kind, page_of(page), lang, body),
        )
        return int(cursor.lastrowid or 0)


def feedback(store: Store, limit: int = 200) -> list[dict]:
    """The latest messages, newest first, without the plans they came from."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT id, created, kind, page, lang, body, seen FROM feedback ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    keys = ("id", "created", "kind", "page", "lang", "body", "seen")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def mark_seen(store: Store, ident: int) -> None:
    with store.connection() as db:
        db.execute("UPDATE feedback SET seen = 1 WHERE id = ?", (ident,))


def unseen(store: Store) -> int:
    with store.connection() as db:
        return int(db.execute("SELECT COUNT(*) FROM feedback WHERE seen = 0").fetchone()[0])


# --------------------------------------------------------------------------- #
# The error log
# --------------------------------------------------------------------------- #


def record_error(
    store: Store, *, source: str, route: str, kind: str, message: str, detail: str = "", now: datetime
) -> None:
    """Count one error: a new row the first time this route meets this kind of error
    with this message, else one more on that row. Rows unseen for `ERRORS_KEPT` go."""
    route, kind = scrub(route, 120), scrub(kind, 80)
    message, detail = scrub(message), scrub(detail, _DETAIL)
    key = hashlib.sha256(f"{source}\0{route}\0{kind}\0{message}".encode()).hexdigest()[:24]
    stamp = _stamp(now)
    with store.connection() as db:
        db.execute("DELETE FROM errors WHERE last < ?", (_stamp(now - ERRORS_KEPT),))
        db.execute(
            """
            INSERT INTO errors (key, source, route, kind, message, detail, first, last, count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (key) DO UPDATE SET last = excluded.last, count = count + 1,
                detail = excluded.detail
            """,
            (key, source, route, kind, message, detail, stamp, stamp),
        )


def errors(store: Store, limit: int = 100) -> list[dict]:
    """The error log, the most recent first."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT source, route, kind, message, detail, first, last, count FROM errors "
            "ORDER BY last DESC LIMIT ?",
            (limit,),
        ).fetchall()
    keys = ("source", "route", "kind", "message", "detail", "first", "last", "count")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def errors_since(store: Store, since: datetime) -> int:
    """How many errors happened since `since` (each row counted once per time it
    happened, if its last time is after `since`)."""
    with store.connection() as db:
        return int(
            db.execute(
                "SELECT COALESCE(SUM(count), 0) FROM errors WHERE last >= ?", (_stamp(since),)
            ).fetchone()[0]
        )
