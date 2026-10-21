"""
Flashcards (DECISIONS.md D29): a student's own cards, by course, private, and
scheduled by FSRS-6 (`cps.memory.FSRS_6`, D52), the model the product's plans use,
one card at a time. Before D52 it was FSRS-4.5; a card's stored stability and
difficulty carry over, and the next answer updates them with FSRS-6.

A card's first answer sets its memory state (`MODEL.initial_state`); each later
answer updates it with the time since the last review (`MODEL.review`). The card
comes back when its predicted recall falls to `RETENTION`, which is FSRS's own
interval (`MODEL.interval`). Two stated choices sit on top of the model and are not
FSRS:

- "Again" brings the card back `RELEARN` later in the same sitting, as Anki and
  py-fsrs do with their relearning steps; the answer then, under a day after the
  last, uses FSRS-6's same-day update.
- At most `NEW_PER_DAY` cards are shown for the first time each day, so a deck
  pasted at once does not become a wall of reviews the next week.

Intervals follow FSRS-6's population defaults until the student has given
`fitting.MIN_REVIEWS` answers a day or more after the one before; from then on,
parameters fitted to their own answers (`refit`, D53), fitted again each time those
answers have grown by a quarter (`REFIT_GROWTH`).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import fitting, memory
from .i18n import _
from .store import Store

MODEL = memory.FSRS_6  # D52: until the student's own parameters (D53)
REFIT_GROWTH = 1.25  # fit again once the scored answers have grown by a quarter
FIT_CARDS = 1000  # a fit reads the cards answered most recently, so it stays quick

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
    # The Monday of the week of lectures the card belongs to (D87), ISO; None for a
    # card of the whole course.
    week: str | None = None

    @property
    def new(self) -> bool:
        return self.stability is None

    def state(self) -> memory.MemoryState | None:
        if self.stability is None or self.difficulty is None:
            return None
        return memory.MemoryState(self.stability, self.difficulty)


_COLUMNS = "id, token, course, front, back, created, stability, difficulty, last, due, reps, lapses, week"


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


_KEEP = object()  # `edit`: leave the card's week as it is


def _week(value: object) -> str | None:
    """A week's Monday as the cards keep it, or None: anything else is refused."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        monday = date.fromisoformat(text)
    except ValueError:
        raise CardError(_("choose a week of the course, or none")) from None
    if monday.weekday() != 0:
        raise CardError(_("choose a week of the course, or none"))
    return monday.isoformat()


def add(
    store: Store,
    token: str,
    course: str,
    pairs: list[tuple[str, str]],
    now: datetime,
    week: str | None = None,
) -> list[Card]:
    """New cards for `course` (and one of its weeks, D87), from (question, answer)
    pairs; all checked before any is kept."""
    course = _text(course, "course")
    week = _week(week)
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
            week,
        )
        for f, b in pairs
    ]
    with store.connection() as db:
        db.executemany(
            f"INSERT INTO cards ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (c.id, c.token, c.course, c.front, c.back, c.created, None, None, None, None, 0, 0, c.week)
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


def edit(
    store: Store,
    token: str,
    card_id: str,
    course: str,
    front: str,
    back: str,
    week: object = _KEEP,
) -> Card:
    """Change a card; its week only when one is given (None takes it off)."""
    card = get(store, token, card_id)
    if card is None:
        raise CardError(_("this card is not, or no longer, there"))
    course, front, back = _text(course, "course"), _text(front, "front"), _text(back, "back")
    kept = card.week if week is _KEEP else _week(week)
    with store.connection() as db:
        db.execute(
            "UPDATE cards SET course = ?, front = ?, back = ?, week = ? WHERE id = ? AND token = ?",
            (course, front, back, kept, card_id, token),
        )
    return replace(card, course=course, front=front, back=back, week=kept)


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


def of_week(store: Store, token: str, course: str, week: str) -> list[Card]:
    """A course's cards of one week (D87), in the order they were written."""
    return [c for c in of(store, token, course) if c.week == week]


def week_practice(
    store: Store, token: str, now: datetime, zone: ZoneInfo, course: str, week: str
) -> list[Card]:
    """A week's cards to practise now (D87): each not yet answered today, the due
    and the longest overdue first, then the rest by when they come back, then the
    new ones. Answering them early is a review like any other: FSRS counts the days
    since the last one, so a card practised before it is due gains less."""
    today = _stamp(_day_start(now, zone))
    left = [c for c in of_week(store, token, course, week) if c.last is None or c.last < today]
    seen = sorted((c for c in left if not c.new), key=lambda c: c.due or "")
    return seen + [c for c in left if c.new]


def schedule(
    card: Card, grade: memory.Grade, now: datetime, model: memory.Model = MODEL
) -> tuple[memory.MemoryState, datetime, float | None]:
    """The card's state after this answer, when it comes back, and the days since
    its last review (None for a first answer)."""
    state = card.state()
    if state is None or card.last is None:
        after, elapsed = model.initial_state(grade), None
    else:
        elapsed = max(0.0, (now - datetime.fromisoformat(card.last)) / timedelta(days=1))
        after = model.review(state, elapsed, grade)
    if grade == memory.Grade.AGAIN:
        due = now + RELEARN
    else:
        due = now + timedelta(days=model.interval(after.stability, RETENTION))
    return after, due, elapsed


@dataclass(frozen=True)
class Personal:
    """A student's own parameters (D53): the scored answers they were fitted to,
    when, and the model they make."""

    reviews: int
    at: str
    model: memory.Model


def personal(store: Store, token: str) -> Personal | None:
    with store.connection() as db:
        row = db.execute("SELECT weights, reviews, at FROM card_fits WHERE token = ?", (token,)).fetchone()
    if row is None:
        return None
    weights = memory.Weights6.from_vector(json.loads(row[0]))
    return Personal(int(row[1]), str(row[2]), memory.Model("FSRS-6, fitted", weights))


def model_for(store: Store, token: str) -> memory.Model:
    """The student's own parameters once fitted, else the population's."""
    found = personal(store, token)
    return found.model if found else MODEL


def scored(store: Store, token: str) -> int:
    """The answers a fit learns from: a day or more after the card's previous one
    (a first answer has no time since the last)."""
    with store.connection() as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM card_reviews WHERE token = ? AND elapsed >= 1", (token,)
            ).fetchone()[0]
        )


def answer_log(
    store: Store, token: str, cards: int = FIT_CARDS
) -> list[list[tuple[str, float | None, memory.Grade]]]:
    """Each card's answers in order, (when, days since the previous, grade), for
    the `cards` answered most recently."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT card, at, grade, elapsed FROM card_reviews WHERE token = ? AND card IN ("
            "SELECT card FROM card_reviews WHERE token = ? GROUP BY card ORDER BY MAX(at) DESC LIMIT ?) "
            "ORDER BY card, at, rowid",
            (token, token, cards),
        ).fetchall()
    by_card: dict[str, list[tuple[str, float | None, memory.Grade]]] = {}
    for card, at, grade, elapsed in rows:
        by_card.setdefault(card, []).append((at, elapsed, memory.Grade(grade)))
    # A history starts with a first answer; a card whose first answer is not kept
    # (it cannot happen today, but a copy of the store might) is left out.
    return [log for log in by_card.values() if log[0][1] is None]


def histories(store: Store, token: str, cards: int = FIT_CARDS) -> list[fitting.History]:
    """Each card's answers in order, for the `cards` answered most recently."""
    return [[(e, g) for _, e, g in log] for log in answer_log(store, token, cards)]


def refit(store: Store, token: str, now: datetime) -> fitting.Fit | None:
    """Fit the student's own parameters when they have enough answers and none
    were fitted yet, or the answers have grown by `REFIT_GROWTH` since; the new fit,
    or None when nothing was due."""
    count = scored(store, token)
    if count < fitting.MIN_REVIEWS:
        return None
    found = personal(store, token)
    if found is not None and count < found.reviews * REFIT_GROWTH:
        return None
    result = fitting.fit(histories(store, token))
    with store.connection() as db:
        db.execute(
            "INSERT INTO card_fits (token, weights, reviews, at, loss, default_loss) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET weights = excluded.weights, reviews = excluded.reviews, "
            "at = excluded.at, loss = excluded.loss, default_loss = excluded.default_loss",
            (
                token,
                json.dumps(list(result.weights.as_vector())),
                count,
                _stamp(now),
                result.loss,
                result.default_loss,
            ),
        )
    return result


def answer(store: Store, token: str, card_id: str, grade: str, now: datetime) -> Card:
    """Record how well the card was recalled and schedule it again."""
    card = get(store, token, card_id)
    if card is None:
        raise CardError(_("this card is not, or no longer, there"))
    if grade not in GRADES:
        raise CardError(_("choose how well you recalled it"))
    after, due, elapsed = schedule(card, GRADES[grade], now, model_for(store, token))
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
