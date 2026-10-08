"""
The study network (DECISIONS.md D16 to D20): profiles, posts, photos, follows,
kudos, comments, reports and blocks, in the store's SQLite file.

This module stores and reads; it knows nothing of plans. `cps.service` composes it
with a student's plan (a focus session reports the plan's session; the streak counts
logged sessions) and the web app only calls `cps.service`.

What a post is: a focus session ("session": a course, what was done, minutes,
perceived effort 1 to 10, progress 1 to 5, a note, photos, whether the focus was
checked and how often the student left) or an explanation ("explain": a concept, a
course, an explanation for someone outside the field). Who may see it is the post's
own choice: "me", "followers" or "everyone".
"""

from __future__ import annotations

import json
import re
import secrets
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .i18n import _
from .store import Store

VISIBILITIES = ("me", "followers", "everyone")
KINDS = ("session", "explain")
MAX_PHOTOS = 4
MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_PHOTO_SIDE = 8000
LIMITS = {"course": 80, "title": 120, "note": 1000, "concept": 120, "text": 1200}


class SocialError(ValueError):
    """Something a student asked for that cannot be done; the message says why."""


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def _new_id() -> str:
    return secrets.token_urlsafe(9)


# --------------------------------------------------------------------------- #
# Photos (D18): JPEG or PNG, metadata removed, stored under a random name
# --------------------------------------------------------------------------- #

_PNG = b"\x89PNG\r\n\x1a\n"
# PNG chunks a picture needs; the rest (text, time, EXIF, ...) is metadata.
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT"}


def _clean_jpeg(data: bytes) -> tuple[bytes, int, int]:
    """The JPEG without its APP1 to APP15 and comment segments (where EXIF, GPS
    positions and editing history live), and its size in pixels."""
    if not data.startswith(b"\xff\xd8"):
        raise SocialError(_("a photo must be a JPEG or a PNG"))
    out = bytearray(b"\xff\xd8")
    i, width, height = 2, 0, 0
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            raise SocialError(_("this photo could not be read"))
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        segment = data[i : i + 2 + length]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and length >= 7:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
        if marker == 0xDA:  # start of scan: the image data follows, to the end
            out += data[i:]
            break
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out += segment
        i += 2 + length
    else:
        raise SocialError(_("this photo could not be read"))
    return bytes(out), width, height


def _clean_png(data: bytes) -> tuple[bytes, int, int]:
    out = bytearray(_PNG)
    i, width, height = len(_PNG), 0, 0
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind = data[i + 4 : i + 8]
        chunk = data[i : i + 12 + length]
        if len(chunk) < 12 + length:
            raise SocialError(_("this photo could not be read"))
        if kind == b"IHDR":
            width, height = struct.unpack(">II", data[i + 8 : i + 16])
        if kind in _PNG_KEEP:
            out += chunk
        i += 12 + length
        if kind == b"IEND":
            break
    else:
        raise SocialError(_("this photo could not be read"))
    return bytes(out), width, height


def clean_photo(data: bytes) -> tuple[bytes, str]:
    """A photo ready to keep: JPEG or PNG only, at most `MAX_PHOTO_BYTES`, its
    metadata removed; returns the bytes and the extension."""
    if len(data) > MAX_PHOTO_BYTES:
        raise SocialError(_("a photo can be at most {mb} MB", mb=MAX_PHOTO_BYTES // (1024 * 1024)))
    if data.startswith(_PNG):
        cleaned, width, height = _clean_png(data)
        ext = "png"
    else:
        cleaned, width, height = _clean_jpeg(data)
        ext = "jpg"
    if not (0 < width <= MAX_PHOTO_SIDE and 0 < height <= MAX_PHOTO_SIDE):
        raise SocialError(_("this photo could not be read"))
    return cleaned, ext


def save_photo(store: Store, data: bytes) -> str:
    cleaned, ext = clean_photo(data)
    store.photos.mkdir(parents=True, exist_ok=True)
    name = f"{_new_id()}.{ext}"
    (store.photos / name).write_bytes(cleaned)
    return name


PHOTO_NAME = re.compile(r"[A-Za-z0-9_-]{12}\.(jpg|png)")


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Post:
    id: str
    token: str
    kind: str
    created: str
    day: str  # the local date the work was done
    visibility: str
    data: dict
    hidden: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "created": self.created,
            "day": self.day,
            "visibility": self.visibility,
            "hidden": self.hidden,
            **self.data,
        }


def _row(row: tuple) -> Post:
    pid, token, kind, created, day, visibility, data, hidden = row
    return Post(pid, token, kind, created, day, visibility, json.loads(data), bool(hidden))


_COLUMNS = "id, token, kind, created, day, visibility, data, hidden"


def _text(value: str, field: str, required: bool = False) -> str:
    """A field's text, checked: one line, except a note or an explanation."""
    value = str(value or "").strip() if field in ("note", "text") else " ".join(str(value or "").split())
    if required and not value:
        empty = _("say what you studied") if field in ("course", "concept") else _("write something")
        raise SocialError(empty)
    if len(value) > LIMITS[field]:
        raise SocialError(_("at most {n} characters here", n=LIMITS[field]))
    return value


def _scale(value: object, low: int, high: int, what: str) -> int:
    wrong = SocialError(_("{what} is a number from {low} to {high}", what=what, low=low, high=high))
    try:
        number = int(str(value))
    except ValueError:
        raise wrong from None
    if not low <= number <= high:
        raise wrong
    return number


def create_post(
    store: Store,
    token: str,
    kind: str,
    *,
    day: str,
    visibility: str,
    data: dict,
    photos: Iterable[bytes] = (),
    now: datetime | None = None,
) -> Post:
    """Check a post's fields, keep its photos, and store it."""
    if kind not in KINDS:
        raise SocialError(f"unknown kind of post {kind!r}")
    if visibility not in VISIBILITIES:
        raise SocialError(_("choose who sees it: only you, your followers, or everyone"))
    photos = [p for p in photos if p]
    if len(photos) > MAX_PHOTOS:
        raise SocialError(_("at most {n} photos", n=MAX_PHOTOS))
    clean: dict = {}
    if kind == "session":
        clean["course"] = _text(data.get("course", ""), "course", required=True)
        clean["title"] = _text(data.get("title", ""), "title")
        clean["note"] = _text(data.get("note", ""), "note")
        clean["effort"] = _scale(data.get("effort"), 1, 10, _("Effort"))
        clean["progress"] = _scale(data.get("progress"), 1, 5, _("Progress"))
        minutes = _scale(data.get("minutes"), 1, 16 * 60, _("Minutes"))
        clean.update(
            minutes=minutes,
            timed=bool(data.get("timed")),
            checked=bool(data.get("checked")),
            away_minutes=int(data.get("away_minutes") or 0),
            interruptions=int(data.get("interruptions") or 0),
        )
        if data.get("sid"):
            clean["sid"] = str(data["sid"])[:32]
    else:
        clean["concept"] = _text(data.get("concept", ""), "concept", required=True)
        clean["course"] = _text(data.get("course", ""), "course")
        clean["text"] = _text(data.get("text", ""), "text", required=True)
    names = [save_photo(store, p) for p in photos]  # checked before anything is stored
    clean["photos"] = names
    post = Post(_new_id(), token, kind, _now(now), day, visibility, clean)
    with store.connection() as db:
        db.execute(
            f"INSERT INTO posts ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (post.id, token, kind, post.created, day, visibility, json.dumps(clean)),
        )
        db.executemany("INSERT INTO photos (name, post) VALUES (?, ?)", [(n, post.id) for n in names])
    return post


def get_post(store: Store, post_id: str) -> Post | None:
    with store.connection() as db:
        row = db.execute(f"SELECT {_COLUMNS} FROM posts WHERE id = ?", (post_id,)).fetchone()
    return _row(row) if row else None


def posts_of(store: Store, token: str, kind: str | None = None, limit: int = 200) -> list[Post]:
    """A student's own posts, newest first: the diary."""
    query = f"SELECT {_COLUMNS} FROM posts WHERE token = ?"
    args: list = [token]
    if kind:
        query += " AND kind = ?"
        args.append(kind)
    with store.connection() as db:
        rows = db.execute(query + " ORDER BY day DESC, created DESC LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def delete_post(store: Store, token: str, post_id: str) -> bool:
    """Delete one's own post, with what others left on it and its photos."""
    post = get_post(store, post_id)
    if post is None or post.token != token:
        return False
    with store.connection() as db:
        db.execute("DELETE FROM comments WHERE post = ?", (post_id,))
        db.execute("DELETE FROM kudos WHERE post = ?", (post_id,))
        db.execute("DELETE FROM reports WHERE target = ?", (post_id,))
        db.execute("DELETE FROM photos WHERE post = ?", (post_id,))
        db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    for name in post.data.get("photos", []):
        (store.photos / name).unlink(missing_ok=True)
    return True


def set_visibility(store: Store, token: str, post_id: str, visibility: str) -> bool:
    if visibility not in VISIBILITIES:
        raise SocialError(_("choose who sees it: only you, your followers, or everyone"))
    with store.connection() as db:
        return bool(
            db.execute(
                "UPDATE posts SET visibility = ? WHERE id = ? AND token = ?", (visibility, post_id, token)
            ).rowcount
        )


def photo_post(store: Store, name: str) -> Post | None:
    """The post a photo belongs to, to decide who may see it."""
    if not PHOTO_NAME.fullmatch(name):
        return None
    with store.connection() as db:
        row = db.execute("SELECT post FROM photos WHERE name = ?", (name,)).fetchone()
    return get_post(store, row[0]) if row else None


def may_see(store: Store, viewer: str | None, post: Post) -> bool:
    """Whether `viewer` (a token, or None for someone without a plan) may see
    `post`: its author always; anyone for "everyone"; accepted followers for
    "followers"; and never across a block, nor a hidden post except its author."""
    if viewer == post.token:
        return True
    if post.hidden or post.visibility == "me":
        return False
    if viewer is not None and blocked_between(store, viewer, post.token):
        return False
    if post.visibility == "everyone":
        return True
    return viewer is not None and follows(store, viewer, post.token)


def follows(store: Store, follower: str, followed: str) -> bool:
    with store.connection() as db:
        row = db.execute(
            "SELECT 1 FROM follows WHERE follower = ? AND followed = ? AND status = 'accepted'",
            (follower, followed),
        ).fetchone()
    return row is not None


def blocked_between(store: Store, a: str, b: str) -> bool:
    with store.connection() as db:
        row = db.execute(
            "SELECT 1 FROM blocks WHERE (blocker = ? AND blocked = ?) OR (blocker = ? AND blocked = ?)",
            (a, b, b, a),
        ).fetchone()
    return row is not None
