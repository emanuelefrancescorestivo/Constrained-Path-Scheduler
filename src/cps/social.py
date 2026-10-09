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

import contextlib
import json
import re
import secrets
import sqlite3
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .i18n import _
from .store import Store

VISIBILITIES = ("me", "followers", "everyone")
KINDS = ("session", "explain", "notes")
MAX_PHOTOS = 4
MAX_NOTE_PHOTOS = 8  # a page of notes per photo; a set of notes has more pages (D26)
MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_PHOTO_SIDE = 8000
LIMITS = {"course": 80, "title": 120, "note": 1000, "concept": 120, "text": 1200, "simple": 400}


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
    return _keep_photo(store, *clean_photo(data))


def _keep_photo(store: Store, cleaned: bytes, ext: str) -> str:
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
    lines = field in ("note", "text", "simple")
    value = str(value or "").strip() if lines else " ".join(str(value or "").split())
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
    most = MAX_NOTE_PHOTOS if kind == "notes" else MAX_PHOTOS
    if len(photos) > most:
        raise SocialError(_("at most {n} photos", n=most))
    clean: dict = {}
    if kind == "session":
        clean["course"] = _text(data.get("course", ""), "course", required=True)
        clean["title"] = _text(data.get("title", ""), "title")
        clean["note"] = _text(data.get("note", ""), "note")
        # What was done, said so a student of another subject would follow it: the
        # owner's notebook asks for it as part of logging, not as a second post.
        clean["simple"] = _text(data.get("simple", ""), "simple")
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
    elif kind == "notes":
        # A student's own notes, for the library (D26): a course and a title, a few
        # words about them, the pages as photos, and the student's word that they
        # are their own.
        clean["course"] = _text(data.get("course", ""), "course", required=True)
        clean["title"] = _text(data.get("title", ""), "title", required=True)
        clean["note"] = _text(data.get("note", ""), "note")
        if not data.get("own_work"):
            raise SocialError(_("say that these are your own notes, in your own words"))
        if not photos:
            raise SocialError(_("add at least one photo of your notes"))
        clean["own_work"] = True
    else:
        clean["concept"] = _text(data.get("concept", ""), "concept", required=True)
        clean["course"] = _text(data.get("course", ""), "course")
        clean["text"] = _text(data.get("text", ""), "text", required=True)
    cleaned = [clean_photo(p) for p in photos]  # all checked before any is kept
    names = [_keep_photo(store, *c) for c in cleaned]
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
        db.execute("DELETE FROM reports WHERE target IN (SELECT id FROM comments WHERE post = ?)", (post_id,))
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


# --------------------------------------------------------------------------- #
# Profiles (D16)
# --------------------------------------------------------------------------- #

HANDLE = re.compile(r"[a-z0-9_.]{3,20}")
RESERVED = frozenset(
    {"admin", "administrator", "moderator", "support", "help", "staff", "official", "studyplan"}
)
PROFILE_LIMITS = {"university": 80, "programme": 80, "bio": 280}


@dataclass(frozen=True)
class Profile:
    token: str
    handle: str
    university: str
    programme: str
    bio: str
    created: str

    def public(self) -> dict:
        """What anyone in the network may see: never the token."""
        return {
            "handle": self.handle,
            "university": self.university,
            "programme": self.programme,
            "bio": self.bio,
            # The avatar: the handle's first letter on one of the seven colours.
            "initial": self.handle[:1].upper(),
            "hue": f"c{sum(self.handle.encode()) % 7}",
        }


_PROFILE = "token, handle, university, programme, bio, created"


def get_profile(store: Store, token: str) -> Profile | None:
    with store.connection() as db:
        row = db.execute(f"SELECT {_PROFILE} FROM profiles WHERE token = ?", (token,)).fetchone()
    return Profile(*row) if row else None


def profile_by_handle(store: Store, handle: str) -> Profile | None:
    with store.connection() as db:
        row = db.execute(
            f"SELECT {_PROFILE} FROM profiles WHERE handle = ?", (handle.lstrip("@"),)
        ).fetchone()
    return Profile(*row) if row else None


def set_profile(
    store: Store,
    token: str,
    *,
    handle: str,
    university: str,
    programme: str,
    bio: str = "",
    old_enough: bool,
    now: datetime | None = None,
) -> Profile:
    """Create or change a profile. The handle is unique (ignoring case), the
    student says they are 15 or older (D16)."""
    handle = handle.strip().lstrip("@").lower()
    if not HANDLE.fullmatch(handle):
        raise SocialError(_("a handle is 3 to 20 letters, digits, _ or ."))
    if handle in RESERVED:
        raise SocialError(_("that handle is reserved; choose another"))
    if not old_enough:
        raise SocialError(_("the network is for people aged 15 or older"))
    values = {
        "university": " ".join(university.split()),
        "programme": " ".join(programme.split()),
        "bio": bio.strip(),
    }
    for field, limit in PROFILE_LIMITS.items():
        if len(values[field]) > limit:
            raise SocialError(_("at most {n} characters here", n=limit))
    if not values["university"]:
        raise SocialError(_("say which university you are at"))
    taken = profile_by_handle(store, handle)
    if taken is not None and taken.token != token:
        raise SocialError(_("@{handle} is taken; choose another", handle=handle))
    with store.connection() as db, contextlib.suppress(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO profiles (token, handle, university, programme, bio, created)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (token) DO UPDATE SET
                handle = excluded.handle, university = excluded.university,
                programme = excluded.programme, bio = excluded.bio
            """,
            (token, handle, values["university"], values["programme"], values["bio"], _now(now)),
        )
    profile = get_profile(store, token)
    if profile is None or profile.handle != handle:  # taken between the check and the write
        raise SocialError(_("@{handle} is taken; choose another", handle=handle))
    return profile


def search_profiles(store: Store, viewer: str, query: str, limit: int = 20) -> list[Profile]:
    """People by handle, university or programme, not counting those blocked
    either way."""
    like = f"%{query.strip().lstrip('@').lower()}%"
    if len(like) < 4:
        return []
    with store.connection() as db:
        rows = db.execute(
            f"""
            SELECT {_PROFILE} FROM profiles
            WHERE token != :v AND (handle LIKE :q OR LOWER(university) LIKE :q OR LOWER(programme) LIKE :q)
              AND token NOT IN (SELECT blocked FROM blocks WHERE blocker = :v)
              AND token NOT IN (SELECT blocker FROM blocks WHERE blocked = :v)
            ORDER BY handle LIMIT :n
            """,
            {"v": viewer, "q": like, "n": limit},
        ).fetchall()
    return [Profile(*r) for r in rows]


# --------------------------------------------------------------------------- #
# Follows: asked for, accepted by the person followed (D16)
# --------------------------------------------------------------------------- #


def follow_status(store: Store, follower: str, followed: str) -> str | None:
    """ "accepted", "pending" or None."""
    with store.connection() as db:
        row = db.execute(
            "SELECT status FROM follows WHERE follower = ? AND followed = ?", (follower, followed)
        ).fetchone()
    return row[0] if row else None


def ask_to_follow(store: Store, follower: str, followed: str, now: datetime | None = None) -> str:
    if follower == followed:
        raise SocialError(_("you cannot follow yourself"))
    if get_profile(store, follower) is None:
        raise SocialError(_("choose a handle first, in your profile"))
    if blocked_between(store, follower, followed):
        raise SocialError(_("you cannot follow this person"))
    with store.connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO follows (follower, followed, status, created) VALUES (?, ?, 'pending', ?)",
            (follower, followed, _now(now)),
        )
    return follow_status(store, follower, followed) or "pending"


def answer_request(store: Store, followed: str, follower: str, accept: bool) -> None:
    with store.connection() as db:
        if accept:
            db.execute(
                "UPDATE follows SET status = 'accepted' "
                "WHERE follower = ? AND followed = ? AND status = 'pending'",
                (follower, followed),
            )
        else:
            db.execute("DELETE FROM follows WHERE follower = ? AND followed = ?", (follower, followed))


def unfollow(store: Store, follower: str, followed: str) -> None:
    """Stop following, withdraw a request, or (called the other way round) remove
    a follower."""
    with store.connection() as db:
        db.execute("DELETE FROM follows WHERE follower = ? AND followed = ?", (follower, followed))


def relations(store: Store, token: str) -> dict[str, list[Profile]]:
    """Who asks to follow, who follows, who is followed: for the student only."""
    out: dict[str, list[Profile]] = {}
    queries = {
        "requests": "SELECT follower FROM follows WHERE followed = ? AND status = 'pending'",
        "followers": "SELECT follower FROM follows WHERE followed = ? AND status = 'accepted'",
        "following": "SELECT followed FROM follows WHERE follower = ? AND status = 'accepted'",
        "asked": "SELECT followed FROM follows WHERE follower = ? AND status = 'pending'",
    }
    with store.connection() as db:
        for name, query in queries.items():
            tokens = [t for (t,) in db.execute(query, (token,))]
            rows = [
                db.execute(f"SELECT {_PROFILE} FROM profiles WHERE token = ?", (t,)).fetchone()
                for t in tokens
            ]
            out[name] = sorted((Profile(*r) for r in rows if r), key=lambda p: p.handle)
    return out


# --------------------------------------------------------------------------- #
# Feeds, kudos and comments
# --------------------------------------------------------------------------- #

_VISIBLE_TO_FOLLOWERS = """
    p.hidden = 0
    AND p.token NOT IN (SELECT blocked FROM blocks WHERE blocker = :v)
    AND p.token NOT IN (SELECT blocker FROM blocks WHERE blocked = :v)
"""


def feed_following(store: Store, viewer: str, limit: int = 30, before: str | None = None) -> list[Post]:
    """One's own shared posts and those of people one follows (accepted) that
    they show to followers or everyone, newest first. Posts kept for oneself stay
    in the diary."""
    with store.connection() as db:
        rows = db.execute(
            f"""
            SELECT p.id, p.token, p.kind, p.created, p.day, p.visibility, p.data, p.hidden FROM posts p
            WHERE ((p.token = :v AND p.visibility != 'me') OR (
                    p.visibility IN ('followers', 'everyone')
                    AND p.token IN (SELECT followed FROM follows WHERE follower = :v AND status = 'accepted')
                    AND {_VISIBLE_TO_FOLLOWERS}))
              AND (:before IS NULL OR p.created < :before)
            ORDER BY p.created DESC LIMIT :n
            """,
            {"v": viewer, "before": before, "n": limit},
        ).fetchall()
    return [_row(r) for r in rows]


def feed_explore(
    store: Store,
    viewer: str,
    *,
    university: str = "",
    programme: str = "",
    course: str = "",
    kind: str = "",
    author: str | None = None,
    limit: int = 30,
    before: str | None = None,
) -> list[Post]:
    """Everyone-posts across universities and programmes, newest first, filtered
    by the author's university and programme, the post's course and kind."""
    with store.connection() as db:
        rows = db.execute(
            f"""
            SELECT p.id, p.token, p.kind, p.created, p.day, p.visibility, p.data, p.hidden
            FROM posts p JOIN profiles a ON a.token = p.token
            WHERE p.visibility = 'everyone' AND {_VISIBLE_TO_FOLLOWERS}
              AND (:uni = '' OR LOWER(a.university) LIKE :uni)
              AND (:prog = '' OR LOWER(a.programme) LIKE :prog)
              AND (:course = '' OR LOWER(json_extract(p.data, '$.course')) LIKE :course)
              AND (:kind = '' OR p.kind = :kind)
              AND (:author IS NULL OR p.token = :author)
              AND (:before IS NULL OR p.created < :before)
            ORDER BY p.created DESC LIMIT :n
            """,
            {
                "v": viewer,
                "uni": f"%{university.lower()}%" if university else "",
                "prog": f"%{programme.lower()}%" if programme else "",
                "course": f"%{course.lower()}%" if course else "",
                "kind": kind if kind in KINDS else "",
                "author": author,
                "before": before,
                "n": limit,
            },
        ).fetchall()
    return [_row(r) for r in rows]


def posts_by(store: Store, viewer: str, author: str, limit: int = 30) -> list[Post]:
    """What `viewer` may see of `author`'s posts, newest first."""
    with store.connection() as db:
        rows = db.execute(
            f"SELECT {_COLUMNS} FROM posts WHERE token = ? ORDER BY created DESC LIMIT ?", (author, limit * 3)
        ).fetchall()
    return [p for p in map(_row, rows) if may_see(store, viewer, p)][:limit]


def toggle_kudos(store: Store, token: str, post: Post, now: datetime | None = None) -> bool:
    """Give kudos, or take them back; True if given. One per person per post."""
    if not may_see(store, token, post):
        raise SocialError(_("this post is not, or no longer, visible to you"))
    if get_profile(store, token) is None and post.token != token:
        raise SocialError(_("choose a handle first, in your profile"))
    if post.kind == "notes" and post.token == token:
        raise SocialError(_("others mark your notes helpful; you cannot mark your own"))
    with store.connection() as db:
        gone = db.execute("DELETE FROM kudos WHERE post = ? AND token = ?", (post.id, token)).rowcount
        if not gone:
            db.execute(
                "INSERT INTO kudos (post, token, created) VALUES (?, ?, ?)", (post.id, token, _now(now))
            )
    return not gone


def kudos_of(store: Store, post_ids: Iterable[str], viewer: str) -> dict[str, tuple[int, bool]]:
    """Per post: how many kudos, and whether `viewer` gave one."""
    ids = list(post_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with store.connection() as db:
        counts = dict(
            db.execute(f"SELECT post, COUNT(*) FROM kudos WHERE post IN ({marks}) GROUP BY post", ids)
        )
        mine = {
            p
            for (p,) in db.execute(
                f"SELECT post FROM kudos WHERE token = ? AND post IN ({marks})", [viewer, *ids]
            )
        }
    return {i: (counts.get(i, 0), i in mine) for i in ids}


MAX_COMMENT = 600


@dataclass(frozen=True)
class Comment:
    id: str
    post: str
    token: str
    created: str
    body: str
    hidden: bool


def add_comment(store: Store, token: str, post: Post, body: str, now: datetime | None = None) -> Comment:
    if not may_see(store, token, post):
        raise SocialError(_("this post is not, or no longer, visible to you"))
    if get_profile(store, token) is None:
        raise SocialError(_("choose a handle first, in your profile"))
    body = body.strip()
    if not body:
        raise SocialError(_("write something"))
    if len(body) > MAX_COMMENT:
        raise SocialError(_("at most {n} characters here", n=MAX_COMMENT))
    comment = Comment(_new_id(), post.id, token, _now(now), body, False)
    with store.connection() as db:
        db.execute(
            "INSERT INTO comments (id, post, token, created, body, hidden) VALUES (?, ?, ?, ?, ?, 0)",
            (comment.id, post.id, token, comment.created, body),
        )
    return comment


def comments_on(store: Store, viewer: str, post: Post) -> list[Comment]:
    """A post's comments, oldest first, without hidden ones (except one's own) and
    without those across a block."""
    with store.connection() as db:
        rows = db.execute(
            """
            SELECT id, post, token, created, body, hidden FROM comments c
            WHERE c.post = :p AND (c.hidden = 0 OR c.token = :v)
              AND c.token NOT IN (SELECT blocked FROM blocks WHERE blocker = :v)
              AND c.token NOT IN (SELECT blocker FROM blocks WHERE blocked = :v)
            ORDER BY c.created
            """,
            {"p": post.id, "v": viewer},
        ).fetchall()
    return [Comment(i, p, t, c, b, bool(h)) for i, p, t, c, b, h in rows]


def comment_counts(store: Store, post_ids: Iterable[str]) -> dict[str, int]:
    ids = list(post_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with store.connection() as db:
        return dict(
            db.execute(
                f"SELECT post, COUNT(*) FROM comments WHERE hidden = 0 AND post IN ({marks}) GROUP BY post",
                ids,
            )
        )


def delete_comment(store: Store, token: str, comment_id: str) -> bool:
    """A comment goes when its author deletes it, or the author of the post."""
    with store.connection() as db:
        row = db.execute(
            "SELECT c.token, p.token FROM comments c JOIN posts p ON p.id = c.post WHERE c.id = ?",
            (comment_id,),
        ).fetchone()
        if row is None or token not in row:
            return False
        db.execute("DELETE FROM reports WHERE target = ?", (comment_id,))
        db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return True


def leave(store: Store, token: str) -> None:
    """Leave the network and keep the diary: the profile, follows both ways,
    kudos and comments given go; one's own posts stay, visible only to oneself,
    with what others left on them removed."""
    with store.connection() as db:
        for statement in (
            "DELETE FROM reports WHERE target IN (SELECT c.id FROM comments c JOIN posts p ON p.id = c.post "
            "WHERE p.token = :t)",
            "DELETE FROM comments WHERE post IN (SELECT id FROM posts WHERE token = :t)",
            "DELETE FROM kudos WHERE post IN (SELECT id FROM posts WHERE token = :t)",
            "UPDATE posts SET visibility = 'me' WHERE token = :t",
            "DELETE FROM comments WHERE token = :t",
            "DELETE FROM kudos WHERE token = :t",
            "DELETE FROM follows WHERE follower = :t OR followed = :t",
            "DELETE FROM profiles WHERE token = :t",
        ):
            db.execute(statement, {"t": token})


# --------------------------------------------------------------------------- #
# Moderation (D20): reports, hiding at three reporters, blocks, the owner's review
# --------------------------------------------------------------------------- #

REPORT_REASONS = {
    "spam": "Spam or advertising",
    "unkind": "Insulting or harassing",
    "copied": "Not their own work",
    "exam": "Exam papers or answers",
    "private": "Someone's private information",
    "other": "Something else against the guidelines",
}
HIDE_AFTER = 3  # different people reporting the same thing hide it until reviewed


def _target(store: Store, kind: str, target_id: str) -> tuple[Post, Comment | None] | None:
    """The post reported, or the comment and its post."""
    if kind == "post":
        post = get_post(store, target_id)
        return (post, None) if post else None
    if kind == "comment":
        with store.connection() as db:
            row = db.execute(
                "SELECT id, post, token, created, body, hidden FROM comments WHERE id = ?", (target_id,)
            ).fetchone()
        if row is None:
            return None
        comment = Comment(row[0], row[1], row[2], row[3], row[4], bool(row[5]))
        post = get_post(store, comment.post)
        return (post, comment) if post else None
    return None


def report(
    store: Store, reporter: str, kind: str, target_id: str, reason: str, now: datetime | None = None
) -> bool:
    """Report a post or a comment one can see and did not write. One report per
    person per item; the item is hidden once `HIDE_AFTER` people have reported it
    and nobody has reviewed it. Returns whether it is now hidden."""
    if reason not in REPORT_REASONS:
        raise SocialError(_("choose a reason"))
    found = _target(store, kind, target_id)
    if found is None or not may_see(store, reporter, found[0]):
        raise SocialError(_("this post is not, or no longer, visible to you"))
    post, comment = found
    author = comment.token if comment else post.token
    if author == reporter:
        raise SocialError(_("you cannot report what you wrote; delete it instead"))
    table = "comments" if comment else "posts"
    with store.connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO reports (target, kind, reporter, reason, created) VALUES (?, ?, ?, ?, ?)",
            (target_id, kind, reporter, reason, _now(now)),
        )
        (count,) = db.execute(
            "SELECT COUNT(DISTINCT reporter) FROM reports WHERE target = ? AND resolved = 0", (target_id,)
        ).fetchone()
        if count >= HIDE_AFTER:
            db.execute(f"UPDATE {table} SET hidden = 1 WHERE id = ?", (target_id,))
    return count >= HIDE_AFTER


def block(store: Store, blocker: str, blocked: str, now: datetime | None = None) -> None:
    """Neither sees the other's posts or comments any more, and follows between
    them end. The blocked person is not told."""
    if blocker == blocked:
        raise SocialError(_("you cannot block yourself"))
    with store.connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO blocks (blocker, blocked, created) VALUES (?, ?, ?)",
            (blocker, blocked, _now(now)),
        )
        db.execute(
            "DELETE FROM follows WHERE (follower = ? AND followed = ?) OR (follower = ? AND followed = ?)",
            (blocker, blocked, blocked, blocker),
        )


def unblock(store: Store, blocker: str, blocked: str) -> None:
    with store.connection() as db:
        db.execute("DELETE FROM blocks WHERE blocker = ? AND blocked = ?", (blocker, blocked))


def blocked_by(store: Store, token: str) -> list[Profile]:
    with store.connection() as db:
        rows = db.execute(
            f"SELECT {_PROFILE} FROM profiles WHERE token IN (SELECT blocked FROM blocks WHERE blocker = ?) "
            "ORDER BY handle",
            (token,),
        ).fetchall()
    return [Profile(*r) for r in rows]


@dataclass(frozen=True)
class Reported:
    """An item waiting for the owner's review: what it is, who wrote it, why it was
    reported and by how many people."""

    kind: str
    target: str
    post: Post
    comment: Comment | None
    author: str
    reasons: dict[str, int]
    reporters: int
    first: str


def pending_reports(store: Store) -> list[Reported]:
    """Every reported item not yet reviewed, the most reported first."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT target, kind, reason, reporter, created FROM reports WHERE resolved = 0 ORDER BY created"
        ).fetchall()
    grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for target, kind, reason, reporter, created in rows:
        grouped.setdefault((target, kind), []).append((reason, reporter, created))
    out = []
    for (target, kind), reports in grouped.items():
        found = _target(store, kind, target)
        if found is None:
            continue
        post, comment = found
        reasons: dict[str, int] = {}
        for reason, _reporter, _created in reports:
            reasons[reason] = reasons.get(reason, 0) + 1
        out.append(
            Reported(
                kind=kind,
                target=target,
                post=post,
                comment=comment,
                author=comment.token if comment else post.token,
                reasons=reasons,
                reporters=len({r for _, r, _ in reports}),
                first=reports[0][2],
            )
        )
    return sorted(out, key=lambda r: (-r.reporters, r.first))


def review(store: Store, kind: str, target_id: str, keep: bool) -> str | None:
    """The owner's decision on a reported item: keep it (shown again, its reports
    closed) or remove it (deleted, with its photos, comments and kudos). Returns
    the author's token, or None if the item is gone."""
    found = _target(store, kind, target_id)
    if found is None:
        return None
    post, comment = found
    author = comment.token if comment else post.token
    with store.connection() as db:
        db.execute("UPDATE reports SET resolved = 1 WHERE target = ?", (target_id,))
        if keep:
            table = "comments" if comment else "posts"
            db.execute(f"UPDATE {table} SET hidden = 0 WHERE id = ?", (target_id,))
        elif comment:
            db.execute("DELETE FROM comments WHERE id = ?", (target_id,))
    if not keep and comment is None:
        delete_post(store, post.token, post.id)
    return author


# --------------------------------------------------------------------------- #
# The notes library (D26): own notes by course, "helpful" marks, the month's top
# --------------------------------------------------------------------------- #

TOP_NOTES = 3  # recognised per course and month
TOP_MIN_MARKS = 2  # and only with at least this many helpful marks that month


@dataclass(frozen=True)
class LibraryNote:
    post: Post
    marks: int  # helpful marks in all
    month: int  # helpful marks this month


def notes_library(
    store: Store,
    viewer: str,
    *,
    month_start: str,
    course: str = "",
    university: str = "",
    programme: str = "",
    sort: str = "helpful",
    limit: int = 60,
) -> list[LibraryNote]:
    """Notes the viewer may see (everyone's, followed people's, their own), found by
    course, university and programme, the most helpful this month first, or the
    newest."""
    order = "month DESC, marks DESC, p.created DESC" if sort == "helpful" else "p.created DESC"
    with store.connection() as db:
        rows = db.execute(
            f"""
            SELECT {", ".join("p." + c.strip() for c in _COLUMNS.split(","))},
                   (SELECT COUNT(*) FROM kudos k WHERE k.post = p.id) AS marks,
                   (SELECT COUNT(*) FROM kudos k WHERE k.post = p.id AND k.created >= :month) AS month
            FROM posts p LEFT JOIN profiles a ON a.token = p.token
            WHERE p.kind = 'notes'
              AND (p.token = :v OR (p.hidden = 0
                   AND p.token NOT IN (SELECT blocked FROM blocks WHERE blocker = :v)
                   AND p.token NOT IN (SELECT blocker FROM blocks WHERE blocked = :v)
                   AND (p.visibility = 'everyone' OR (p.visibility = 'followers' AND p.token IN
                        (SELECT followed FROM follows WHERE follower = :v AND status = 'accepted')))))
              AND (:course = '' OR LOWER(json_extract(p.data, '$.course')) LIKE :course)
              AND (:uni = '' OR LOWER(COALESCE(a.university, '')) LIKE :uni)
              AND (:prog = '' OR LOWER(COALESCE(a.programme, '')) LIKE :prog)
            ORDER BY {order} LIMIT :n
            """,
            {
                "v": viewer,
                "month": month_start,
                "course": f"%{course.strip().lower()}%" if course.strip() else "",
                "uni": f"%{university.strip().lower()}%" if university.strip() else "",
                "prog": f"%{programme.strip().lower()}%" if programme.strip() else "",
                "n": limit,
            },
        ).fetchall()
    return [LibraryNote(_row(r[:8]), int(r[8]), int(r[9])) for r in rows]


def top_notes(store: Store, month_start: str) -> dict[str, int]:
    """The month's most helpful notes shared with everyone, per course (its name,
    ignoring case): post id -> rank, 1 to `TOP_NOTES`, for notes with at least
    `TOP_MIN_MARKS` helpful marks this month. Notes are ranked, never people."""
    with store.connection() as db:
        rows = db.execute(
            """
            SELECT p.id, LOWER(TRIM(json_extract(p.data, '$.course'))) AS course, COUNT(k.post) AS month
            FROM posts p JOIN kudos k ON k.post = p.id AND k.created >= ?
            WHERE p.kind = 'notes' AND p.visibility = 'everyone' AND p.hidden = 0
            GROUP BY p.id HAVING month >= ?
            ORDER BY course, month DESC, MIN(k.created)
            """,
            (month_start, TOP_MIN_MARKS),
        ).fetchall()
    ranks: dict[str, int] = {}
    seen: dict[str, int] = {}
    for post_id, course, _month in rows:
        seen[course] = seen.get(course, 0) + 1
        if seen[course] <= TOP_NOTES:
            ranks[post_id] = seen[course]
    return ranks


def notes_recognition(store: Store, token: str) -> tuple[int, int]:
    """(notes shared with others, helpful marks they received): the one line of
    recognition a profile shows (D26)."""
    with store.connection() as db:
        shared, marks = db.execute(
            """
            SELECT COUNT(DISTINCT p.id), (SELECT COUNT(*) FROM kudos k JOIN posts q ON q.id = k.post
                                          WHERE q.token = :t AND q.kind = 'notes' AND q.visibility != 'me')
            FROM posts p WHERE p.token = :t AND p.kind = 'notes' AND p.visibility != 'me' AND p.hidden = 0
            """,
            {"t": token},
        ).fetchone()
    return int(shared), int(marks or 0)
