"""
Flashcards (DECISIONS.md D29): a student's own cards, by course, private, and
scheduled by the same FSRS-4.5 model as the planner (`cps.memory`), one card at a
time.

A card's first answer sets its memory state (`memory.initial_state`); each later
answer updates it with the time since the last review (`memory.review`). The card
comes back when its predicted recall falls to `RETENTION`, which is FSRS's own
interval (`memory.interval_for_retention`). Two stated choices sit on top of the
model and are not FSRS:

- "Again" brings the card back `RELEARN` later in the same sitting, as Anki and
  py-fsrs do with their relearning steps; FSRS-4.5 has no same-day formula, and a
  review minutes later barely moves stability (recall is near certain, so the
  model sees little to gain) while difficulty still moves with the answer.
- At most `NEW_PER_DAY` cards are shown for the first time each day, so a deck
  pasted at once does not become a wall of reviews the next week.

Intervals are FSRS's population defaults, not fitted to the student (AUDIT,
"FSRS weights are population defaults").
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import memory
from .i18n import _
from .store import Store

RETENTION = 0.9  # the recall a card is scheduled for, FSRS's usual default
RELEARN = timedelta(minutes=10)  # "Again": back in the same sitting
NEW_PER_DAY = 20
MAX_CARDS = 2000  # one student's cards, all courses
MAX_PASTE = 200  # cards added in one paste
LIMITS = {"course": 80, "front": 300, "back": 600}
GRADES = {
    "again": memory.Grade.AGAIN,
    "hard": memory.Grade.HARD,
    "good": memory.Grade.GOOD,
    "easy": memory.Grade.EASY,
}


class CardError(ValueError):
    """A card or an answer that cannot be kept, said for the student."""


@dataclass(frozen=True)
class Card:
    id: str
    token: str
    course: str
    front: str
    back: str
    created: str
    stability: float | None
    difficulty: float | None
    last: str | None
    due: str | None
    reps: int
    lapses: int

    @property
    def new(self) -> bool:
        return self.stability is None

    def state(self) -> memory.MemoryState | None:
        if self.stability is None or self.difficulty is None:
            return None
        return memory.MemoryState(self.stability, self.difficulty)


_COLUMNS = "id, token, course, front, back, created, stability, difficulty, last, due, reps, lapses"


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds")


def _text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if field == "course":
        text = " ".join(text.split())
    if not text:
        raise CardError(
            {
                "course": _("say which course it is for"),
                "front": _("write the question"),
                "back": _("write the answer"),
            }[field]
        )
    if len(text) > LIMITS[field]:
        raise CardError(_("at most {n} characters here", n=LIMITS[field]))
    return text


def _count(store: Store, token: str) -> int:
    with store.connection() as db:
        return int(db.execute("SELECT COUNT(*) FROM cards WHERE token = ?", (token,)).fetchone()[0])


def add(store: Store, token: str, course: str, pairs: list[tuple[str, str]], now: datetime) -> list[Card]:
    """New cards for `course`, from (question, answer) pairs; all checked before
    any is kept."""
    course = _text(course, "course")
    if not pairs:
        raise CardError(_("write the question"))
    if len(pairs) > MAX_PASTE:
        raise CardError(_("at most {n} cards at a time", n=MAX_PASTE))
    if _count(store, token) + len(pairs) > MAX_CARDS:
        raise CardError(_("at most {n} cards in all", n=MAX_CARDS))
    made = [
        Card(
            secrets.token_urlsafe(9),
            token,
            course,
            _text(f, "front"),
            _text(b, "back"),
            _stamp(now),
            None,
            None,
            None,
            None,
            0,
            0,
        )
        for f, b in pairs
    ]
    with store.connection() as db:
        db.executemany(
            f"INSERT INTO cards ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (c.id, c.token, c.course, c.front, c.back, c.created, None, None, None, None, 0, 0)
                for c in made
            ],
        )
    return made


def parse_paste(text: str) -> list[tuple[str, str]]:
    """Cards pasted one per line, the question and the answer separated by a tab
    (as spreadsheets and Anki's text export give them) or by " | ". Empty lines are
    skipped; a line without a separator is refused, with its number."""
    pairs = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        if not line.strip():
            continue
        if "\t" in line:
            front, back = line.split("\t", 1)
        elif " | " in line:
            front, back = line.split(" | ", 1)
        else:
            raise CardError(_("line {n}: put a tab or ' | ' between the question and the answer", n=number))
        pairs.append((front.strip(), back.strip()))
    return pairs


def get(store: Store, token: str, card_id: str) -> Card | None:
    """One of the student's own cards; nobody else's."""
    with store.connection() as db:
        row = db.execute(
            f"SELECT {_COLUMNS} FROM cards WHERE id = ? AND token = ?", (card_id, token)
        ).fetchone()
    return Card(*row) if row else None


def of(store: Store, token: str, course: str = "") -> list[Card]:
    query = f"SELECT {_COLUMNS} FROM cards WHERE token = ?"
    args: list = [token]
    if course:
        query += " AND course = ? COLLATE NOCASE"
        args.append(course)
    with store.connection() as db:
        rows = db.execute(query + " ORDER BY course COLLATE NOCASE, created, rowid", args).fetchall()
    return [Card(*r) for r in rows]


def edit(store: Store, token: str, card_id: str, course: str, front: str, back: str) -> Card:
    card = get(store, token, card_id)
    if card is None:
        raise CardError(_("this card is not, or no longer, there"))
    course, front, back = _text(course, "course"), _text(front, "front"), _text(back, "back")
    with store.connection() as db:
        db.execute(
            "UPDATE cards SET course = ?, front = ?, back = ? WHERE id = ? AND token = ?",
            (course, front, back, card_id, token),
        )
    return replace(card, course=course, front=front, back=back)


def delete(store: Store, token: str, card_id: str) -> bool:
    with store.connection() as db:
        db.execute("DELETE FROM card_reviews WHERE card = ? AND token = ?", (card_id, token))
        return db.execute("DELETE FROM cards WHERE id = ? AND token = ?", (card_id, token)).rowcount > 0


def _day_start(now: datetime, zone: ZoneInfo) -> datetime:
    local = now.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def new_today(store: Store, token: str, now: datetime, zone: ZoneInfo) -> int:
    """Cards seen for the first time since local midnight."""
    since = _stamp(_day_start(now, zone))
    with store.connection() as db:
        return int(
            db.execute(
                "SELECT COUNT(DISTINCT card) FROM card_reviews WHERE token = ? AND at >= ? "
                "AND elapsed IS NULL",  # a first answer has no time since the last
                (token, since),
            ).fetchone()[0]
        )


def queue(store: Store, token: str, now: datetime, zone: ZoneInfo, course: str = "") -> list[Card]:
    """What to review now: the cards due, the longest overdue first, then new cards
    in the order they were written, up to what is left of today's `NEW_PER_DAY`."""
    cards = of(store, token, course)
    stamp = _stamp(now)
    due = sorted((c for c in cards if c.due is not None and c.due <= stamp), key=lambda c: c.due or "")
    room = max(0, NEW_PER_DAY - new_today(store, token, now, zone))
    fresh = [c for c in cards if c.new][:room]
    return due + fresh


def schedule(
    card: Card, grade: memory.Grade, now: datetime
) -> tuple[memory.MemoryState, datetime, float | None]:
    """The card's state after this answer, when it comes back, and the days since
    its last review (None for a first answer)."""
    state = card.state()
    if state is None or card.last is None:
        after, elapsed = memory.initial_state(grade), None
    else:
        elapsed = max(0.0, (now - datetime.fromisoformat(card.last)) / timedelta(days=1))
        after = memory.review(state, elapsed, grade)
    if grade == memory.Grade.AGAIN:
        due = now + RELEARN
    else:
        due = now + timedelta(days=memory.interval_for_retention(after.stability, RETENTION))
    return after, due, elapsed


def answer(store: Store, token: str, card_id: str, grade: str, now: datetime) -> Card:
    """Record how well the card was recalled and schedule it again."""
    card = get(store, token, card_id)
    if card is None:
        raise CardError(_("this card is not, or no longer, there"))
    if grade not in GRADES:
        raise CardError(_("choose how well you recalled it"))
    after, due, elapsed = schedule(card, GRADES[grade], now)
    lapse = GRADES[grade] == memory.Grade.AGAIN and not card.new
    with store.connection() as db:
        db.execute(
            "UPDATE cards SET stability = ?, difficulty = ?, last = ?, due = ?, reps = reps + 1, "
            "lapses = lapses + ? WHERE id = ? AND token = ?",
            (after.stability, after.difficulty, _stamp(now), _stamp(due), int(lapse), card_id, token),
        )
        db.execute(
            "INSERT INTO card_reviews (card, token, at, grade, elapsed) VALUES (?, ?, ?, ?, ?)",
            (card_id, token, _stamp(now), int(GRADES[grade]), elapsed),
        )
    found = get(store, token, card_id)
    assert found is not None
    return found


def reviews_since(store: Store, token: str, since: datetime) -> int:
    with store.connection() as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM card_reviews WHERE token = ? AND at >= ?", (token, _stamp(since))
            ).fetchone()[0]
        )


def reviewed_today(store: Store, token: str, now: datetime, zone: ZoneInfo) -> int:
    """Answers given since local midnight."""
    return reviews_since(store, token, _day_start(now, zone))


def next_due(store: Store, token: str, now: datetime, course: str = "") -> datetime | None:
    """When the next card already reviewed comes back, if after `now`."""
    stamp = _stamp(now)
    later = [c.due for c in of(store, token, course) if c.due and c.due > stamp]
    return datetime.fromisoformat(min(later)) if later else None
