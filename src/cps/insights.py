"""
Insights (DECISIONS.md D80): what a student's own data says about how they study,
in a few measures they can act on, each against their own past and never against
other students (D16). Pure functions of plain values; `cps.service` gathers the
inputs and words the results, the pages draw them.

Each measure shows only once there is enough to say something (`NEED`); until then
it says how many more sessions or self-tests it needs. That is both honest (two
self-tests say nothing about when you remember best) and something to look forward
to. The thresholds below are stated choices, not results.

- **Starting on time**: a planned session started with the focus timer, by how many
  minutes after its planned start. Early counts as on time.
- **Focus**: of the time a timed session ran, the share spent on the page (the
  timer's time away, D17, is the rest).
- **When you remember best**: self-tests' answers (nothing, some, most, all, D25)
  by part of the day.
- **Memory against the forecast**: how often the self-tests found something
  remembered against how often the plan predicted it would. FSRS counts every answer
  but "nothing" as remembered, and predicts the probability of that, so this is the
  comparison the pilot reads too (D55); the answers' own scale (nothing to all) is
  not a probability and is kept for the parts of the day only.
- **Pace**: this week's hours against the student's own limit and usual weeks, and
  days of study in a row: the guard against burnout. It never asks for more.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from .progress import monday, part_of_day
from .trends import RECALL_SCORE, Week, direction

ON_TIME = timedelta(minutes=10)  # started at most this long after the plan said
NEED = {"on_time": 3, "focus": 3, "recall_time": 6, "forecast": 5}  # before a measure shows
PART_NEED = 3  # self-tests in a part of the day before it is compared with another
CLEAR = 0.15  # a difference in mean recall (0 to 1) that counts as clear
SERIES_WEEKS = 8  # the weeks a card's small line covers
ABOVE_USUAL = 1.3  # this week's hours over the usual weeks' mean that counts as above
DAYS_IN_A_ROW = 10  # days of study in a row before a day off is suggested
RECORD_FROM = 20  # minutes: a longest session shorter than this is not called a record
# FSRS's grades, as the plan's history keeps a self-test's answer (D25).
GRADE_SCORE = {
    "again": RECALL_SCORE["forgot"],
    "hard": RECALL_SCORE["some"],
    "good": RECALL_SCORE["most"],
    "easy": RECALL_SCORE["all"],
}


@dataclass(frozen=True)
class Start:
    """A planned session started with the focus timer."""

    planned: datetime  # local
    started: datetime  # local

    @property
    def late(self) -> float:
        """Minutes after the planned start (negative: early)."""
        return (self.started - self.planned) / timedelta(minutes=1)


@dataclass(frozen=True)
class Timed:
    """A session timed with the focus timer, as the diary keeps it."""

    day: date
    minutes: int  # on the page
    away: int  # minutes away from the page
    interruptions: int


@dataclass(frozen=True)
class SelfTest:
    """A self-test answered with how much was recalled."""

    start: datetime  # local
    predicted: float  # the recall the plan predicted, 0 to 1
    grade: str  # "again", "hard", "good" or "easy"

    @property
    def score(self) -> float:
        return GRADE_SCORE[self.grade]


def _weekly(points: Sequence[tuple[date, float]], mondays: Sequence[date]) -> list[float | None]:
    """The mean of the values falling in each week (None for a week without any)."""
    out: list[float | None] = []
    for first in mondays:
        values = [v for d, v in points if monday(d) == first]
        out.append(sum(values) / len(values) if values else None)
    return out


def _trend(series: Sequence[float | None]) -> str | None:
    """The last two weeks with a value against the four before them."""
    values = [v for v in series if v is not None]
    return direction(values[-2:], values[-6:-2]) if len(values) >= 4 else None


def on_time(starts: Sequence[Start], mondays: Sequence[date]) -> dict:
    """The share of timer-started planned sessions begun on time, and the typical
    delay of the late ones, with a line over the weeks."""
    have = len(starts)
    if have < NEED["on_time"]:
        return {"key": "on_time", "unlocked": False, "have": have, "need": NEED["on_time"]}
    punctual = [s for s in starts if s.late <= ON_TIME / timedelta(minutes=1)]
    late = [s.late for s in starts if s not in punctual]
    series = _weekly([(s.started.date(), 100.0 if s in punctual else 0.0) for s in starts], mondays)
    return {
        "key": "on_time",
        "unlocked": True,
        "have": have,
        "share": round(100 * len(punctual) / have),
        "late_by": round(median(late)) if late else 0,
        "series": series,
        "trend": _trend(series),
    }


def focus(sessions: Sequence[Timed], mondays: Sequence[date], today: date) -> dict:
    """Of the time timed sessions ran, the share on the page; the longest session
    without leaving the page, and whether it was set in the last seven days."""
    have = len(sessions)
    if have < NEED["focus"]:
        return {"key": "focus", "unlocked": False, "have": have, "need": NEED["focus"]}
    on, ran = sum(s.minutes for s in sessions), sum(s.minutes + s.away for s in sessions)
    series = _weekly(
        [(s.day, 100 * s.minutes / (s.minutes + s.away)) for s in sessions if s.minutes + s.away], mondays
    )
    whole = [s for s in sessions if not s.interruptions and not s.away]
    best = max(whole, key=lambda s: (s.minutes, s.day), default=None)
    earlier = [s.minutes for s in whole if best is not None and s.day < today - timedelta(days=6)]
    return {
        "key": "focus",
        "unlocked": True,
        "have": have,
        "share": round(100 * on / ran) if ran else 0,
        "series": series,
        "trend": _trend(series),
        "longest": best.minutes if best else 0,
        # A record is new when set this week, beats an earlier best and is worth a cheer.
        "record": bool(
            best
            and earlier
            and best.day >= today - timedelta(days=6)
            and best.minutes > max(earlier)
            and best.minutes >= RECORD_FROM
        ),
    }


def recall_by_part(tests: Sequence[SelfTest]) -> dict:
    """Mean recall of the self-tests by part of the day, and the best part when the
    difference is clear and each part compared has enough answers."""
    have = len(tests)
    if have < NEED["recall_time"]:
        return {"key": "recall_time", "unlocked": False, "have": have, "need": NEED["recall_time"]}
    parts = {}
    for part in ("morning", "afternoon", "evening"):
        scores = [t.score for t in tests if part_of_day(t.start) == part]
        if scores:
            parts[part] = {"n": len(scores), "mean": sum(scores) / len(scores)}
    compared = {p: v for p, v in parts.items() if v["n"] >= PART_NEED}
    best = None
    if len(compared) >= 2:
        ranked = sorted(compared, key=lambda p: compared[p]["mean"], reverse=True)
        if compared[ranked[0]]["mean"] - compared[ranked[1]]["mean"] >= CLEAR:
            best = ranked[0]
    return {
        "key": "recall_time",
        "unlocked": True,
        "have": have,
        "parts": {p: {"n": v["n"], "percent": round(100 * v["mean"])} for p, v in parts.items()},
        "best": best,
    }


def against_forecast(tests: Sequence[SelfTest]) -> dict:
    """The share of self-tests that found something remembered (any answer but
    "nothing") against the share the plan predicted, in points out of 100, rounded
    to 5 (the forecast's own precision, D24)."""
    have = len(tests)
    if have < NEED["forecast"]:
        return {"key": "forecast", "unlocked": False, "have": have, "need": NEED["forecast"]}
    remembered = sum(t.grade != "again" for t in tests) / have
    expected = sum(t.predicted for t in tests) / have
    return {
        "key": "forecast",
        "unlocked": True,
        "have": have,
        "remembered": round(100 * remembered),
        "expected": round(100 * expected),
        "gap": 5 * round(20 * (remembered - expected)),
    }


def days_in_a_row(studied: set[date], today: date) -> int:
    """Days of study in a row up to today, or up to yesterday while today is not
    studied yet."""
    day = today if today in studied else today - timedelta(days=1)
    count = 0
    while day in studied:
        count += 1
        day -= timedelta(days=1)
    return count


def pace(weeks: Sequence[Week], limit: float | None, in_a_row: int) -> dict:
    """How this week's study compares with the student's own limit and usual weeks,
    and whether a day off is due. It never asks for more: the states are "rest" (many
    days in a row), "above" (over the limit, or well over the usual weeks),
    "steady", or "start" before there is a usual week to compare with."""
    hours = weeks[-1].hours if weeks else 0.0
    before = [w.hours for w in weeks[:-1]][-4:]
    usual = sum(before) / len(before) if before else None
    if in_a_row >= DAYS_IN_A_ROW:
        state = "rest"
    elif (limit and hours > limit) or (usual and hours > ABOVE_USUAL * usual and hours - usual >= 2):
        state = "above"
    elif usual is None:
        state = "start"
    else:
        state = "steady"
    return {
        "key": "pace",
        "state": state,
        "hours": hours,
        "usual": usual,
        "limit": limit,
        "in_a_row": in_a_row,
    }
