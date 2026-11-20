"""
Secrets a plan keeps, sealed (DECISIONS.md D175).

A learning platform's calendar link carries a key of its own: Moodle's export address
holds the student's `authtoken`, and whoever has the address reads their calendar. The
app needs it to read the deadlines again each day, so it cannot only keep a digest. It
keeps it sealed: encrypted and signed (Fernet, from `cryptography`, already the web
extra's for passkeys, D35), shown nowhere again, not in a page, a log or the plan's
copy (D43); the student sees "Linked to moodle.example.edu" and can replace or remove
it.

The key: `CPS_SEAL_KEY` when the deployment gives one (a Fernet key: 32 bytes in
base64url), else one made on first use and kept in the store's `server_keys`, as the
reminders' key is (D38). Disclosed: a key kept in the same database protects a plan's
document, its copy and the logs, not a stolen database; `CPS_SEAL_KEY` set on the
host keeps the two apart. Losing the key loses the links: each student pastes theirs
again, and the app says so.

Each sealed value names its key (`sealed:v1:<key id>:<token>`), so a process that has
opened several stores (the tests do) opens each value with the key that sealed it. A
value written before D175, in clear, is read as it is and sealed at its next save.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from datetime import UTC, datetime
from typing import Any

PREFIX = "sealed:v1:"
_LOCK = threading.Lock()
_KEYS: dict[str, Any] = {}  # key id -> Fernet
_CURRENT: list[str] = []  # the key id new values are sealed with


def _fernet(key: bytes) -> Any:
    from cryptography.fernet import Fernet

    return Fernet(key)


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


def _use(key: bytes) -> None:
    with _LOCK:
        kid = _key_id(key)
        _KEYS.setdefault(kid, _fernet(key))
        _CURRENT[:] = [kid]


def configure(store: Any, now: datetime | None = None) -> None:
    """Seal with the deployment's key: `CPS_SEAL_KEY`, else the store's, made once."""
    given = os.environ.get("CPS_SEAL_KEY")
    if given:
        _use(given.strip().encode("ascii"))
        return
    with store.connection() as db:
        row = db.execute("SELECT value FROM server_keys WHERE name = 'seal'").fetchone()
        if row is None:
            fresh = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            # Two threads making one at once: the first kept wins, both read it back.
            db.execute(
                "INSERT OR IGNORE INTO server_keys (name, value, created) VALUES ('seal', ?, ?)",
                (fresh, (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")),
            )
            row = db.execute("SELECT value FROM server_keys WHERE name = 'seal'").fetchone()
    _use(str(row[0]).encode("ascii"))


def _current() -> tuple[str, Any]:
    """The key to seal with. A process that opened no store (a script, a test of the
    service alone) seals with a key of its own, kept in memory for its life."""
    if not _CURRENT:
        _use(base64.urlsafe_b64encode(os.urandom(32)))
    kid = _CURRENT[0]
    return kid, _KEYS[kid]


def seal(text: str) -> str:
    kid, box = _current()
    return f"{PREFIX}{kid}:{box.encrypt(text.encode('utf-8')).decode('ascii')}"


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def unseal(value: object) -> str | None:
    """The secret in clear: a value sealed with a key this process knows, or one kept
    in clear before D175. None for nothing, or for a value whose key is lost."""
    if not value or not isinstance(value, str):
        return None
    if not is_sealed(value):
        return value
    kid, _, token = value[len(PREFIX) :].partition(":")
    box = _KEYS.get(kid)
    if box is None:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return str(box.decrypt(token.encode("ascii")).decode("utf-8"))
    except InvalidToken:
        return None
