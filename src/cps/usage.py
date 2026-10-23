"""
What a student uses of the app, counted only with their yes (DECISIONS.md D96).

The pilot is there to find out what to keep and what to take away, and the event
log of D15 says only who came back. With a student's yes, the server counts, per day,
which part of the app they opened or used: the part's name (from the page's address,
never what the page held or what they wrote) and how many times. Nothing runs in the
browser for it (D6) and nothing is stored on the device: the counting is done where
the page is answered.

The yes or no is kept per plan; a no, or a yes taken back, deletes what was counted.
Counts are kept `KEPT` days, and go with the plan. The owner reads totals over the
students who said yes, never one student's (`report`); a student reads their own
(`own`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .store import Store

KEPT = timedelta(days=180)  # "the first months of the trial": six, then gone

# Every part the app counts, in the order a report lists them, with the words a
# student and the owner read (English keys of `_()`, translated where shown).
PARTS: tuple[tuple[str, str], ...] = (
    ("today", "Today"),
    ("calendar", "Calendar"),
    ("tasks", "Tasks"),
    ("courses", "Courses"),
    ("course", "A course's page"),
    ("topics", "A course's topics"),
    ("program", "The semester's program"),
    ("focus", "Focus"),
    ("timer", "Timing a session"),
    ("diary", "Writing in the diary"),
    ("report", "Saying how a session went"),
    ("move", "Moving a session"),
    ("lecture", "Changing a class (skipped, a quiz)"),
    ("task_add", "Adding a task"),
    ("community", "Community"),
    ("people", "People and profiles"),
    ("notes", "Notes"),
    ("groups", "Study groups"),
    ("post", "Posting or commenting"),
    ("kudos", "Kudos and helpful marks"),
    ("progress", "Progress"),
    ("trends", "Trends"),
    ("cards", "Flashcards"),
    ("card_answer", "Answering a flashcard"),
    ("cards_make", "Cards from notes"),
    ("tips", "Tips"),
    ("weekly", "The weekly note"),
    ("coach", "The coach"),
    ("science", "The science behind it"),
    ("you", "You"),
    ("settings", "Settings"),
    ("connect", "Connect calendar"),
    ("routine", "The questions about your week"),
    ("reminders", "Reminders"),
    ("appearance", "Appearance and language"),
    ("semester", "Next semester"),
    ("feedback", "Sending feedback"),
    ("export", "Downloading a copy"),
)
LABELS = dict(PARTS)

# A plan's address after `/p/<token>`, by method, to the part it is. The first
# pattern that matches the whole rest of the address wins; one that matches nothing
# is not counted (the panel fetched again after a change, the sky, a photo, the
# manifest, an answer to a script).
_RULES: tuple[tuple[str, str, str], ...] = (
    ("GET", r"", "today"),
    ("GET", r"/(week|agenda|timetable)", "calendar"),
    ("GET", r"/tasks", "tasks"),
    ("GET", r"/courses", "courses"),
    ("GET", r"/courses/import", "program"),
    ("GET", r"/courses/\d+", "course"),
    ("GET", r"/topics(/\d+)?", "topics"),
    ("GET", r"/focus", "focus"),
    ("GET", r"/log", "diary"),
    ("GET", r"/(community|explain)", "community"),
    ("GET", r"/(people|profile|u/[^/]+|post/[^/]+)", "people"),
    ("GET", r"/notes(/new)?", "notes"),
    ("GET", r"/(groups(/[^/]+)?|join/[^/]+)", "groups"),
    ("GET", r"/progress", "progress"),
    ("GET", r"/trends", "trends"),
    ("GET", r"/cards/make", "cards_make"),
    ("GET", r"/cards(/review|/[^/]+)?", "cards"),
    ("GET", r"/tips", "tips"),
    ("GET", r"/weekly(/[^/]+)?", "weekly"),
    ("GET", r"/coach", "coach"),
    ("GET", r"/science", "science"),
    ("GET", r"/you", "you"),
    ("GET", r"/settings", "settings"),
    ("GET", r"/feed", "connect"),
    ("GET", r"/routine", "routine"),
    ("GET", r"/semester", "semester"),
    ("GET", r"/feedback", "feedback"),
    ("GET", r"/export", "export"),
    ("POST", r"/sessions/[^/]+/report", "report"),
    ("POST", r"/sessions/[^/]+/(move|unpin)", "move"),
    ("POST", r"/focus/(start|finish)", "timer"),
    ("POST", r"/log", "diary"),
    ("POST", r"/lecture", "lecture"),
    ("POST", r"/tasks", "task_add"),
    ("POST", r"/tasks/(done|delete|understand)", "tasks"),
    ("POST", r"/(notes/new|explain|post/[^/]+/comments)", "post"),
    ("POST", r"/post/[^/]+/kudos", "kudos"),
    ("POST", r"/cards/[^/]+/answer", "card_answer"),
    ("POST", r"/cards/make", "cards_make"),
    ("POST", r"/cards", "cards"),
    ("POST", r"/coach", "coach"),
    ("POST", r"/(groups(/.*)?|join(/.*)?)", "groups"),
    ("POST", r"/(u/[^/]+/(follow|unfollow)|requests/[^/]+|profile)", "people"),
    ("POST", r"/courses/import", "program"),
    ("POST", r"/courses/\d+/(credits|links.*)", "course"),
    ("POST", r"/topics/\d+(/take)?", "topics"),
    ("POST", r"/(settings|exam|budget)", "settings"),
    ("POST", r"/routine", "routine"),
    ("POST", r"/(reminders|push(/.*)?)", "reminders"),
    ("POST", r"/(appearance|motion|glass|language)", "appearance"),
    ("POST", r"/semester", "semester"),
    ("POST", r"/feedback", "feedback"),
)
_COMPILED = tuple((method, re.compile(pattern), part) for method, pattern, part in _RULES)


def part_of(method: str, rest: str) -> str | None:
    """The part of the app a request is, from its method and the rest of the plan's
    address after `/p/<token>` (without the query); None if it is not counted."""
    rest = rest.rstrip("/") if rest != "/" else ""
    for wanted, pattern, part in _COMPILED:
        if wanted == method and pattern.fullmatch(rest):
            return part
    return None


@dataclass(frozen=True)
class Answer:
    yes: bool
    at: str  # when it was given, ISO


def _day(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


def answer_of(store: Store, token: str) -> Answer | None:
    """The plan's yes or no, or None if it was not asked yet."""
    with store.connection() as db:
        row = db.execute("SELECT answer, at FROM usage_consent WHERE token = ?", (token,)).fetchone()
    return Answer(row[0] == "yes", row[1]) if row else None


def answer(store: Store, token: str, yes: bool, now: datetime) -> None:
    """Keep the plan's answer; a no deletes what was counted, at once."""
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    with store.connection() as db:
        db.execute(
            "INSERT INTO usage_consent (token, answer, at) VALUES (?, ?, ?) "
            "ON CONFLICT (token) DO UPDATE SET answer = excluded.answer, at = excluded.at",
            (token, "yes" if yes else "no", stamp),
        )
        if not yes:
            db.execute("DELETE FROM usage WHERE token = ?", (token,))


def count(store: Store, token: str, part: str, now: datetime) -> bool:
    """One more use of `part` today, if the plan said yes; True if it was counted.
    One statement: a plan that said no, or was never asked, writes nothing."""
    if part not in LABELS:
        raise ValueError(f"not a part of the app: {part!r}")
    with store.connection() as db:
        written = db.execute(
            "INSERT INTO usage (token, day, part, n) "
            "SELECT ?, ?, ?, 1 WHERE EXISTS "
            "(SELECT 1 FROM usage_consent WHERE token = ? AND answer = 'yes') "
            "ON CONFLICT (token, day, part) DO UPDATE SET n = n + 1",
            (token, _day(now), part, token),
        ).rowcount
    return bool(written)


@dataclass(frozen=True)
class Use:
    part: str
    label: str  # English; translated where it is shown
    days: int  # days it was used on
    times: int
    last: str | None  # the last day, ISO


def own(store: Store, token: str) -> tuple[list[Use], list[Use]]:
    """A student's own counts: the parts they used, most days first, and the parts
    they have not opened since they said yes."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT part, COUNT(*), SUM(n), MAX(day) FROM usage WHERE token = ? GROUP BY part", (token,)
        ).fetchall()
    found = {part: (days, times, last) for part, days, times, last in rows}
    order = {part: i for i, (part, _) in enumerate(PARTS)}
    used = sorted(
        (Use(p, LABELS[p], *found[p]) for p in found if p in LABELS),
        key=lambda u: (-u.days, -u.times, order[u.part]),
    )
    unused = [Use(p, label, 0, 0, None) for p, label in PARTS if p not in found]
    return used, unused


@dataclass(frozen=True)
class Total:
    part: str
    label: str
    students: int  # who used it at least once, of those who said yes
    days: int  # student-days it was used on, all together
    times: int
    last_7: int  # students who used it in the last 7 days


def report(store: Store, now: datetime) -> dict:
    """The owner's view: how many said yes and no, and for each part how many of
    those who said yes used it, on how many days, how often; the parts nobody used.
    Totals only: no plan's address, no one student's row."""
    since = (now.astimezone(UTC).date() - timedelta(days=6)).isoformat()
    with store.connection() as db:
        answers = dict(db.execute("SELECT answer, COUNT(*) FROM usage_consent GROUP BY answer").fetchall())
        rows = db.execute(
            "SELECT part, COUNT(DISTINCT token), COUNT(*), SUM(n), "
            "COUNT(DISTINCT CASE WHEN day >= ? THEN token END) FROM usage GROUP BY part",
            (since,),
        ).fetchall()
    found = {part: rest for part, *rest in rows}
    totals = sorted(
        (Total(p, LABELS[p], *found[p]) for p in found if p in LABELS),
        key=lambda t: (-t.students, -t.days, -t.times),
    )
    return {
        "yes": answers.get("yes", 0),
        "no": answers.get("no", 0),
        "parts": totals,
        "unused": [label for part, label in PARTS if part not in found],
    }


def sweep(store: Store, now: datetime) -> None:
    """Counts older than `KEPT` go."""
    oldest = (now.astimezone(UTC) - KEPT).date().isoformat()
    with store.connection() as db:
        db.execute("DELETE FROM usage WHERE day < ?", (oldest,))
