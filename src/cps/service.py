"""
The service layer: everything a user-facing surface needs, and nothing else.

The CLI, the Streamlit app and any future web API call these functions and only
these, so they can be swapped without touching the planner. Nothing here imports a
UI framework or prints: bytes and plain values in, frozen result objects out. Two
kinds of input and output are allowed, both explicit: `fetch_calendar` reads a
calendar link the person gave, and the `*_subscription` functions keep plans in
the SQLite store (`cps.store`) the caller names. Every result has `to_dict()` returning plain JSON
types, and `PlanReport.from_dict` rebuilds a plan, so a front end can hold a plan as
JSON and send it back to `replan_after` or `export_ics`.

    analyse_calendar(ics, *, start, tz, ...)          -> CalendarReport
    fetch_calendar(url)                                -> bytes
    make_plan(report, subjects, *, retention, ...)     -> PlanReport
    continue_plan(report, subjects, *, done, now)      -> PlanReport
    replan_after(plan, session_index, outcome)         -> PlanReport
    export_ics(plan)                                   -> bytes
    recall_curve(plan, subject)                        -> [(day, probability), ...]
    new_subscription(...), refresh_subscription(sub)   -> Subscription
    feed_ics(sub)                                      -> bytes

Errors a user can cause come back as `ServiceError` subclasses with a stable
`code` and a sentence that says what to do, never as a traceback: no free blocks,
an exam that is not in the future, a target no schedule can reach, a calendar that
cannot be read, an input that makes no sense.

Familiarity is a prior, not a measurement
-----------------------------------------
Students do not know their FSRS stability. A 1-to-5 familiarity rating is mapped to
the memory state FSRS-4.5 would assign after a first review with the matching
grade (1 Again, 2 Hard, 3 Good, 4 Easy), and 5 to Easy followed by one successful
review at 90% recall. That anchors the scale to the model's own parameters instead
of to numbers made up here, but it is still a guess about a person, and every
report says so.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from . import accounts as _accounts
from . import ai as _ai
from . import cardmaker as _cardmaker
from . import cards as flashcards
from . import coursepages as _coursepages
from . import crests as _crests
from . import daylight as _daylight
from . import fitting
from . import focussounds as _focussounds
from . import i18n as _i18n
from . import inbox as _inbox
from . import insights as _insights
from . import learning as _learning
from . import mail as _mail
from . import notefiles as _notefiles
from . import pdftext as _pdftext
from . import progress as _progress
from . import reminders as _reminders
from . import sealed as _sealed
from . import social as social
from . import sources as _sources
from . import studyplan as _studyplan
from . import syllabus as _syllabus
from . import tips as _tips
from . import trends as _trends
from . import universities as _universities
from . import usage as _usage
from . import weekly as _weekly
from .assistant import Exam, Pin, Preferences, Quick, Task, Topic, blocks_for_hours
from .assistant import schedule as assist
from .calendar_io import (
    BusyEvent,
    BusyRow,
    availability_from_events,
    busy_from_table,
    course_of,
    decode_ics,
    expand_events,
    find_assignments,
    find_deadlines,
    find_lectures,
    plan_to_ics,
)
from .i18n import _, _n, format_date, pick, use
from .i18n import current as current_language
from .i18n import number as format_number
from .memory import (
    FSRS_4_5,
    FSRS_6,
    MODELS,
    Grade,
    MemoryState,
    Model,
    retrievability,
)
from .plan import Block, short_gaps, tile_free_time
from .rolling import DEFAULT_FAILURE_PENALTY, Subject, run_rolling, solve_deadlines
from .sources import SourceError
from .sources import fetch_calendar as _fetch
from .store import Conflict, Store
from .timegrid import SLOTS_PER_DAY

OUTCOMES = ("recalled", "lapsed", "skipped")
ASSESSMENT_SEARCH_DAYS = 366

LIMITATIONS = (
    "The memory model uses population-default FSRS-4.5 parameters, not parameters fitted to you.",
    "Each subject's starting point is your own estimate (a familiarity rating or a "
    "stability and difficulty), not a measurement.",
    "Nothing is personalised: the plan does not learn from how your reviews go, "
    "except when you tell it that a session was forgotten or skipped.",
    "The target is a choice: recall at your chosen level for as long again as the "
    "preparation lasts. A missed exam is priced at a fixed number of study blocks.",
    "Each week of a subject's lectures is a topic that one study block reviews, and it starts "
    "out 'seen once and shaky'; both are simplifications, not measurements.",
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ServiceError(Exception):
    """An error the user caused and can fix. `code` is stable for programs."""

    code = "service_error"

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self)}


class InvalidInput(ServiceError):
    code = "invalid_input"


class InvalidCalendar(ServiceError):
    code = "invalid_calendar"


class NoFreeBlocks(ServiceError):
    code = "no_free_blocks"


class ExamInPast(ServiceError):
    code = "exam_in_past"


class UnreachableTarget(ServiceError):
    code = "unreachable_target"


class UnreadableLink(ServiceError):
    """A calendar link that cannot be read: the message says why."""

    code = "unreadable_link"


class MissingExam(InvalidInput):
    """A subject has no date and no assessment in the calendar carries its name."""

    code = "missing_exam"

    def __init__(self, subject: str):
        super().__init__(
            f"No exam date for {subject!r}: none of the assessments in the calendar is called "
            f"that. Give the date of its exam."
        )
        self.subject = subject


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def familiarity_prior(level: int, model: Model = FSRS_4_5) -> MemoryState:
    """Map a 1-to-5 self-rating to a starting memory state in `model`. A prior; see
    module docs."""
    if level not in (1, 2, 3, 4, 5):
        raise InvalidInput(f"familiarity must be 1 to 5, got {level!r}")
    if level <= 4:
        return model.initial_state(Grade(level))
    easy = model.initial_state(Grade.EASY)
    return model.review(easy, model.interval(easy.stability, 0.9), Grade.GOOD)


def model_of(settings: PlanSettings) -> Model:
    """The memory model a plan was made with (D52): what its preferences name, FSRS-6
    for the product's plans since D52; FSRS-4.5 for the research planner's plans and
    for plans made before D52 until they are made again."""
    return MODELS.get(str(settings.preferences.get("model", "fsrs-4.5")), FSRS_4_5)


@dataclass(frozen=True)
class SubjectSpec:
    """A subject as a person describes it.

    `exam` is a datetime, a date (midnight at its start, so nothing is planned on
    the exam day) or an ISO string, read in the plan's time zone when naive. None
    means "the assessment with this name found in the calendar". Give either
    `familiarity` (1 to 5) or both `stability` and `difficulty`.
    """

    name: str
    exam: datetime | date | str | None = None
    familiarity: int | None = None
    stability: float | None = None
    difficulty: float | None = None

    def memory(self, model: Model = FSRS_4_5) -> tuple[MemoryState, str]:
        """The starting memory state and a label saying where it came from."""
        if self.stability is not None and self.difficulty is not None:
            try:
                return MemoryState(float(self.stability), float(self.difficulty)), "explicit"
            except ValueError as exc:
                raise InvalidInput(f"{self.name}: {exc}") from exc
        if self.familiarity is not None:
            return familiarity_prior(int(self.familiarity), model), f"familiarity {int(self.familiarity)}"
        raise InvalidInput(
            _("{name}: give a familiarity from 1 to 5, or a stability and a difficulty", name=self.name)
        )

    def exam_datetime(self, zone: ZoneInfo) -> datetime:
        value = self.exam
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.strip())
            except ValueError as exc:
                raise InvalidInput(
                    _("{name}: {value} is not a date like 2026-06-15", name=self.name, value=repr(self.exam))
                ) from exc
        if isinstance(value, datetime):
            return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        if isinstance(value, date):
            return datetime.combine(value, time(0, 0), tzinfo=zone)
        raise InvalidInput(f"{self.name}: unsupported exam value {value!r}")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInput(
            _("unknown time zone {zone}; use a name such as Europe/Paris", zone=repr(name))
        ) from exc


def _days_after(start: date, when: datetime) -> float:
    """Wall-clock days from midnight at `start`; see `cli._days_after`."""
    midnight = datetime.combine(start, time(0, 0), tzinfo=when.tzinfo)
    return (when - midnight) / timedelta(days=1)


# --------------------------------------------------------------------------- #
# Calendar analysis
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EventView:
    summary: str
    start: str  # ISO 8601, local
    end: str
    # "calendar" (the .ics), "typed" (busy rows), "commute" (made from the trip's
    # length around each day's classes, `commute_events`), "lunch" (kept free every
    # day, D69), "skipped" (a class of
    # the .ics the student will not attend, D60: shown, but not busy), or a class
    # that is a test (D61): "quiz", "mock" or "partial"
    source: str = "calendar"

    def to_dict(self) -> dict:
        return {"summary": self.summary, "start": self.start, "end": self.end, "source": self.source}


@dataclass(frozen=True)
class BlockView:
    slot: int
    day: int
    start_day: float
    start: str
    end: str
    # "" for a study block; "gap" for a quick self-test in a gap between classes,
    # "way" for one on the way to university (D48): self-tests only, shorter.
    quick: str = ""

    def to_dict(self) -> dict:
        out = {
            "slot": self.slot,
            "day": self.day,
            "start_day": self.start_day,
            "start": self.start,
            "end": self.end,
        }
        if self.quick:
            out["quick"] = self.quick
        return out

    @property
    def minutes(self) -> int:
        return round(
            (datetime.fromisoformat(self.end) - datetime.fromisoformat(self.start)) / timedelta(minutes=1)
        )

    def as_block(self) -> Block:
        return Block(self.slot, self.day, self.start_day)


@dataclass(frozen=True)
class AssessmentView:
    subject: str
    when: str
    summary: str

    def to_dict(self) -> dict:
        return {"subject": self.subject, "when": self.when, "summary": self.summary}


@dataclass(frozen=True)
class LectureView:
    """A teaching event and the course it belongs to (`calendar_io.course_of`)."""

    course: str
    start: str
    end: str

    def to_dict(self) -> dict:
        return {"course": self.course, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class CalendarReport:
    """What ingestion saw. Check it before trusting any plan built on it."""

    start: date
    tz: str
    days: int
    study_window: tuple[float, float]
    blocks_per_day: int
    block_minutes: int
    slots_per_day: int
    busy_hours: float
    total_hours: float
    events: tuple[EventView, ...]
    blocks: tuple[BlockView, ...]
    assessments: tuple[AssessmentView, ...]
    # The input itself, kept so that a plan whose exams lie beyond this horizon can
    # re-read the calendar for a longer one. Personal data: it stays in memory and
    # in `to_dict`, and is never written anywhere by this module.
    ics_text: str | None = field(default=None, repr=False)
    busy_rows: tuple[BusyRow, ...] = field(default=(), repr=False)
    # Every non-assessment event of the .ics from half a year before the start to
    # a year after it, with its course: what `make_plan` turns into topics.
    lectures: tuple[LectureView, ...] = field(default=(), repr=False)
    # The trip to campus, one way, kept around each day's classes (`commute_events`).
    commute_minutes: int = 0
    # When the student studies best: each day's free time is filled from that end (D45).
    study_time: str = "any"
    # Quick self-tests (D48): in gaps too short for a block, and on the way to
    # university when the student can study there.
    quick: tuple[BlockView, ...] = ()
    quick_tests: bool = False
    study_on_the_way: bool = False
    # Classes the student will not attend (D60), as `options["lecture_changes"]`.
    lecture_changes: tuple[Mapping, ...] = ()
    # Lunch, kept free every day (D69): its start and end, "12:30" and "13:30".
    lunch: tuple[str, str] | None = None

    @property
    def block_slots(self) -> int:
        return max(1, self.block_minutes // (24 * 60 // self.slots_per_day))

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "tz": self.tz,
            "days": self.days,
            "study_window": list(self.study_window),
            "blocks_per_day": self.blocks_per_day,
            "block_minutes": self.block_minutes,
            "slots_per_day": self.slots_per_day,
            "busy_hours": self.busy_hours,
            "total_hours": self.total_hours,
            "events": [e.to_dict() for e in self.events],
            "blocks": [b.to_dict() for b in self.blocks],
            "assessments": [a.to_dict() for a in self.assessments],
            "ics_text": self.ics_text,
            "busy_rows": [_row_to_dict(r) for r in self.busy_rows],
            "lectures": [x.to_dict() for x in self.lectures],
            "commute_minutes": self.commute_minutes,
            "study_time": self.study_time,
            "quick": [b.to_dict() for b in self.quick],
            "quick_tests": self.quick_tests,
            "study_on_the_way": self.study_on_the_way,
            "lecture_changes": [dict(c) for c in self.lecture_changes],
            "lunch": list(self.lunch) if self.lunch else None,
        }


def _row_to_dict(row: BusyRow) -> dict:
    end = "24:00" if row.end == time.max else row.end.strftime("%H:%M")
    return {
        "label": row.label,
        "start": row.start.strftime("%H:%M"),
        "end": end,
        "weekday": row.weekday,
        "date": row.on.isoformat() if row.on else None,
    }


def _events(
    ics_text: str | None, rows: Sequence[BusyRow], start: date, days: int, tz: str
) -> list[BusyEvent]:
    return [e for e, _ in _tagged_events(ics_text, rows, start, days, tz)]


def _tagged_events(
    ics_text: str | None, rows: Sequence[BusyRow], start: date, days: int, tz: str
) -> list[tuple[BusyEvent, str]]:
    """Every busy event with where it came from: "calendar" or "typed"."""
    zone = _zone(tz)
    events: list[tuple[BusyEvent, str]] = []
    if ics_text is not None:
        window_start = datetime.combine(start, time(0, 0), tzinfo=zone)
        try:
            found = expand_events(ics_text, window_start, window_start + timedelta(days=days), zone)
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidCalendar(
                f"the calendar file could not be read ({exc}); export it again as .ics"
            ) from exc
        events += [(e, "calendar") for e in found]
    events += [(e, "typed") for e in busy_from_table(rows, start, days, tz)]
    events.sort(key=lambda pair: (pair[0].start, pair[0].end))
    return events


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="minutes")


# Changing a class of the timetable (D60): the student skips it, once or every week,
# and may catch up on their own. A class is known by its start and its title, as
# the timetable gives them, so a change outlives a reading of the timetable's link;
# a class the university moves is a new class, and the change stays with the old.
LECTURE_ACTIONS = ("skip", "self")
# A class may be a test (D61): a pop-up quiz, a mock exam, or a partial exam. It
# stays busy; it is drawn and planned around as what it is.
TEST_KINDS = ("quiz", "mock", "partial")
# The preparation a quiz brings, as a task due when it starts: a short self-test of
# what the course has taught, which is what a quiz rewards [ref:yang2021].
QUIZ_PREPARATION_HOURS = 1.0
# D194: tests the timetable does not show. A course with quizzes in class, announced
# or not, keeps each week of its lectures above QUIZ_READY_RECALL for QUIZ_READY_DAYS
# after it is taught (a quiz asks about what was taught lately); a midterm the student
# gives is a partial exam (D61) added to the timetable, MIDTERM_LENGTH long when no end
# is given. The two weeks and the 95 % are choices, measured by benchmarks/quiz_ready.py.
QUIZ_READY_DAYS = 14
QUIZ_READY_RECALL = 0.95
MIDTERM_LENGTH = timedelta(hours=2)


def test_label(kind: str, summary: str) -> str:
    """A class that is a test (D61), as the calendar names it."""
    course = course_of(summary) or short_title(summary)
    if kind == "quiz":
        return _("Quiz: {course}", course=course)
    if kind == "mock":
        return _("Mock exam: {course}", course=course)
    return _("Partial exam: {course}", course=course)


def lecture_ref(summary: str) -> str:
    """How a page names a class of the timetable, with its start: a digest of its
    title, so that the page never carries the timetable's raw text ("Grp: CM ., Salle:
    ..."), which it shows only cleaned (`short_title`)."""
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]


def _lecture_change(changes: Iterable[Mapping], event: BusyEvent, zone: ZoneInfo) -> Mapping | None:
    """The change, once or weekly, that the timetable's `event` falls under."""
    local = event.start.astimezone(zone)
    for change in changes:
        if change.get("summary") != event.summary:
            continue
        if change.get("at"):
            if datetime.fromisoformat(change["at"]) == event.start:
                return change
        elif (
            local.weekday() == change.get("weekday")
            and f"{local:%H:%M}" == change.get("time")
            and local.date() >= date.fromisoformat(change["from"])
        ):
            return change
    return None


# When a student studies best (D45), as the hour of the day each day's sessions
# gather around (`plan.tile_free_time`): None is the earliest free time, as before
# the question existed; 24 is as late as the study window allows.
STUDY_TIMES = {"any": None, "morning": None, "afternoon": 15.0, "evening": 24.0}
# Free time kept after training or work before a session (D45): to get home and eat.
PAUSE_AFTER = timedelta(minutes=30)
_PAUSE_KINDS = ("training", "work")
# Quick self-tests (D48): a gap of an hour between classes holds one of 45 minutes,
# 15 left to get to the next class; a trip of at least half an hour holds one on
# the way, when the student says they can study there.
QUICK_SLACK = timedelta(minutes=15)
QUICK_MIN_SLOTS = 2
WAY_MIN = timedelta(minutes=30)
# The time around an exam kept free of study (D183): the hour before it, to get there
# with a clear head, and the half hour after it, to leave and breathe. An exam typed
# by hand, with no event in the timetable, is taken to last two hours.
EXAM_BEFORE = timedelta(hours=1)
EXAM_AFTER = timedelta(minutes=30)
TYPED_EXAM_LENGTH = timedelta(hours=2)
_EXAM_KINDS = ("mock", "partial")  # the classes marked as tests (D61) that are kept free alike

# The longest trip asked about (the question offers up to 90 minutes; a typed
# value above this is refused rather than blocking a whole morning by mistake).
MAX_COMMUTE_MINUTES = 180


def commute_events(campus: Iterable[BusyEvent], minutes: int, zone: ZoneInfo, label: str) -> list[BusyEvent]:
    """The trip to campus and back, made from the day's classes (D42): `minutes`
    before the first one each day and after the last one. Time between two classes
    stays free: a student who stays on campus can study there. All-day events (a
    holiday, a "week of exams" marker) are not a day on campus.

    Rejected: asking for the trip's times day by day (a timetable that changes from
    week to week would be wrong by the second week); a weekly commute row (the same);
    blocking the trip on every weekday (most students are not on campus every day)."""
    if minutes <= 0:
        return []
    trip = timedelta(minutes=minutes)
    days: dict[date, tuple[datetime, datetime]] = {}
    for event in campus:
        if event.all_day or event.duration >= timedelta(hours=12):
            continue
        begin, end = event.start.astimezone(zone), event.end.astimezone(zone)
        first, last = days.get(begin.date(), (begin, end))
        days[begin.date()] = (min(first, begin), max(last, end))
    out = []
    for first, last in days.values():
        out += [BusyEvent(first - trip, first, label), BusyEvent(last, last + trip, label)]
    out.sort(key=lambda e: (e.start, e.end))
    return out


def lunch_events(
    lunch: tuple[str, str], start: date, days: int, zone: ZoneInfo, label: str
) -> list[BusyEvent]:
    """Lunch on each day of the horizon (D69), at the same wall-clock time."""
    begin, end = (time.fromisoformat(x) for x in lunch)
    return [
        BusyEvent(
            datetime.combine(start + timedelta(days=k), begin, tzinfo=zone),
            datetime.combine(start + timedelta(days=k), end, tzinfo=zone),
            label,
        )
        for k in range(days)
    ]


def analyse_calendar(
    ics: bytes | None = None,
    *,
    start: date,
    tz: str = "UTC",
    days: int | None = None,
    study_window: tuple[float, float] = (8.0, 22.0),
    blocks_per_day: int = 2,
    block_minutes: int = 90,
    busy_rows: Iterable[BusyRow | Mapping] = (),
    commute_minutes: int = 0,
    study_time: str = "any",
    quick_tests: bool = False,
    study_on_the_way: bool = False,
    lecture_changes: Sequence[Mapping] = (),
    lunch: tuple[str, str] | None = None,
) -> CalendarReport:
    """Read a calendar (an `.ics`, typed rows, or both) and find the free blocks.
    With `quick_tests`, also the gaps too short for a block, for quick self-tests;
    with `study_on_the_way`, the trips to campus (D48).
    With `commute_minutes`, the trip to campus and back is busy too, around each
    day's events of the timetable and classes drawn by hand (`commute_events`).
    `lecture_changes` names classes of the timetable the student will not attend
    (D60): they stay in `events` as "skipped", and their time is free. `lunch`,
    a start and an end, is busy every day (D69).

    Without `days` the horizon runs to the latest assessment found in the next year,
    including its day, or 21 days if there is none. Assessments are looked for over
    at least a year whatever the horizon, because `make_plan` extends a horizon
    that ends before an exam. (It was 120 days, which missed the end of a semester:
    AUDIT.md item 32.)
    """
    if ics is None and not busy_rows:
        busy_rows = ()
    try:
        rows = tuple(BusyRow.parse(r) for r in busy_rows)
    except ValueError as exc:
        raise InvalidInput(str(exc)) from exc
    if not 0 <= study_window[0] < study_window[1] <= 24:
        raise InvalidInput(_("the study window must run from an earlier to a later hour, within 0 to 24"))
    if blocks_per_day < 1 or block_minutes < 30:
        raise InvalidInput(_("use at least one block a day of at least 30 minutes"))
    if study_time not in STUDY_TIMES:
        raise InvalidInput(_("choose when you study best: morning, afternoon, evening or no preference"))
    if not 0 <= commute_minutes <= MAX_COMMUTE_MINUTES:
        raise InvalidInput(_("the trip to university must be 0 to {most} minutes", most=MAX_COMMUTE_MINUTES))
    text = decode_ics(ics) if ics is not None else None

    if days is not None and days < 1:
        raise InvalidInput("the horizon must be at least one day")
    found = find_deadlines(_events(text, rows, start, max(days or 0, ASSESSMENT_SEARCH_DAYS), tz))
    lectures = _lectures(text, start, tz, lecture_changes)
    if days is None:
        latest = max((_days_after(start, d.when) for d in found), default=21.0)
        days = max(1, math.ceil(latest - 1e-9))

    tagged = _tagged_events(text, rows, start, days, tz)
    if lecture_changes:
        exams = {d.when for d in found}
        zone_ = _zone(tz)

        def changed(e: BusyEvent, source: str) -> str:
            change = _lecture_change(lecture_changes, e, zone_) if source == "calendar" else None
            if change is None or e.start in exams:
                return source
            # Skipped: shown, not busy (D60). A test: busy, shown as what it is (D61).
            return change["action"] if change["action"] in TEST_KINDS else "skipped"

        tagged = [(e, changed(e, source)) for e, source in tagged]
        # A test the timetable does not have, added by the student (D194): busy, and a
        # test like a class marked as one.
        horizon = (
            datetime.combine(start, time(0, 0), tzinfo=zone_),
            datetime.combine(start + timedelta(days=days), time(0, 0), tzinfo=zone_),
        )
        added = [
            (
                BusyEvent(datetime.fromisoformat(c["at"]), datetime.fromisoformat(c["end"]), c["summary"]),
                c["action"],
            )
            for c in lecture_changes
            if c.get("added") and c.get("action") in TEST_KINDS
        ]
        tagged = sorted(
            tagged + [(e, kind) for e, kind in added if horizon[0] <= e.start < horizon[1]],
            key=lambda pair: (pair[0].start, pair[0].end),
        )
    if commute_minutes:
        campus = [e for e, source in tagged if source in ("calendar", *TEST_KINDS)]
        campus += busy_from_table([r for r in rows if r.kind == "class"], start, days, tz)
        trips = commute_events(campus, commute_minutes, _zone(tz), _("Commute"))
        tagged = sorted(
            tagged + [(e, "commute") for e in trips], key=lambda pair: (pair[0].start, pair[0].end)
        )
    if lunch:
        meals = lunch_events(lunch, start, days, _zone(tz), _("Lunch"))
        tagged = sorted(tagged + [(e, "lunch") for e in meals], key=lambda pair: (pair[0].start, pair[0].end))
    events = [e for e, source in tagged if source != "skipped"]
    # A pause after training or work (D45): busy for the grid, not an event to show.
    pauses = [
        BusyEvent(e.end, e.end + PAUSE_AFTER, "")
        for e in busy_from_table([r for r in rows if r.kind in _PAUSE_KINDS], start, days, tz)
    ]
    # The hour before each exam of the timetable and the half hour after it (D183): busy
    # for the grid, so that the day's blocks are cut around them. An exam typed by hand
    # is kept free later, when the plan knows it (`_around_exams`).
    exam_ends = {e.start: e.end for e, source in tagged if source in ("calendar", *TEST_KINDS)}
    tests = {e.start for e, source in tagged if source in _EXAM_KINDS}
    for when in {d.when for d in found} | tests:
        end = exam_ends.get(when, when + TYPED_EXAM_LENGTH)
        pauses += [BusyEvent(when - EXAM_BEFORE, when, ""), BusyEvent(end, end + EXAM_AFTER, "")]
    grid = availability_from_events(events + pauses, start, days, SLOTS_PER_DAY, study_window)
    minutes_per_slot = 24 * 60 // grid.slots_per_day
    block_slots = max(1, block_minutes // minutes_per_slot)
    tiles = tile_free_time(
        grid, block_slots=block_slots, max_blocks_per_day=blocks_per_day, around=STUDY_TIMES[study_time]
    )
    zone = _zone(tz)
    midnight = datetime.combine(start, time(0, 0), tzinfo=zone)

    def clock(slot: int) -> datetime:
        return midnight + timedelta(minutes=slot * minutes_per_slot)

    quick: list[BlockView] = []
    if quick_tests:
        quick += [
            BlockView(
                b.slot,
                b.day,
                b.start_day,
                _iso(clock(b.slot)),
                _iso(clock(b.slot + n) - QUICK_SLACK),
                "gap",
            )
            for b, n in short_gaps(grid, block_slots, QUICK_MIN_SLOTS)
        ]
    if study_on_the_way:
        for e, source in tagged:
            if source == "commute" and e.end - e.start >= WAY_MIN:
                begin = (e.start.astimezone(zone) - midnight) / timedelta(minutes=minutes_per_slot)
                slot = math.floor(begin + 1e-9)
                quick.append(
                    BlockView(
                        slot,
                        slot // grid.slots_per_day,
                        _days_after(start, e.start.astimezone(zone)),
                        _iso(e.start.astimezone(zone)),
                        _iso(
                            min(e.end, e.start + timedelta(minutes=block_minutes) - QUICK_SLACK).astimezone(
                                zone
                            )
                        ),
                        "way",
                    )
                )
    return CalendarReport(
        start=start,
        tz=tz,
        days=days,
        study_window=(float(study_window[0]), float(study_window[1])),
        blocks_per_day=blocks_per_day,
        block_minutes=block_minutes,
        slots_per_day=grid.slots_per_day,
        busy_hours=grid.busy_slots() * minutes_per_slot / 60,
        total_hours=grid.total_slots * minutes_per_slot / 60,
        events=tuple(EventView(e.summary, _iso(e.start), _iso(e.end), source) for e, source in tagged),
        blocks=tuple(
            BlockView(b.slot, b.day, b.start_day, _iso(clock(b.slot)), _iso(clock(b.slot + block_slots)))
            for b in tiles
        ),
        assessments=tuple(AssessmentView(d.subject, _iso(d.when), d.summary) for d in found),
        ics_text=text,
        busy_rows=rows,
        lectures=lectures,
        commute_minutes=commute_minutes,
        study_time=study_time,
        quick=tuple(sorted(quick, key=lambda b: b.start_day)),
        quick_tests=quick_tests,
        study_on_the_way=study_on_the_way,
        lecture_changes=tuple(dict(c) for c in lecture_changes),
        lunch=(str(lunch[0]), str(lunch[1])) if lunch else None,
    )


LECTURE_LOOKBACK_DAYS = 183


def _lectures(
    ics_text: str | None, start: date, tz: str, changes: Sequence[Mapping] = ()
) -> tuple[LectureView, ...]:
    """Teaching events of the .ics around the plan: see `CalendarReport.lectures`.
    A class marked as a test (D61) teaches nothing new: it is not one of them."""
    if ics_text is None:
        return ()
    zone = _zone(tz)
    origin = datetime.combine(start, time(0, 0), tzinfo=zone) - timedelta(days=LECTURE_LOOKBACK_DAYS)
    try:
        events = expand_events(
            ics_text, origin, origin + timedelta(days=LECTURE_LOOKBACK_DAYS + ASSESSMENT_SEARCH_DAYS), zone
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidCalendar(
            f"the calendar file could not be read ({exc}); export it again as .ics"
        ) from exc
    return tuple(
        LectureView(course, _iso(e.start), _iso(e.end))
        for course, e in find_lectures(events)
        if (_lecture_change(changes, e, zone) or {}).get("action") not in TEST_KINDS
    )


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SubjectView:
    name: str
    exam: str
    exam_day: float
    stability: float  # starting point
    difficulty: float
    prior: str  # "explicit" or "familiarity k"
    target: float
    ready: bool
    unreachable: bool
    recall_at_exam: float
    stability_at_exam: float
    first_review: str | None
    # For a subject whose lectures are topics: how many, and how many reach their
    # own target. `target` and `stability_at_exam` are then the weakest topic's.
    topics: int = 1
    topics_ready: int = 0
    status_line: str = ""  # set when the engine words its own status

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class SessionView:
    index: int
    subject: str
    start: str
    end: str
    day: int
    start_day: float
    slot: int
    recall: float  # predicted probability of recall when the session starts
    # "recalled", "lapsed" or "skipped"; "unknown" for a self-test nobody reported
    # within a day (`UNREPORTED_AFTER`): still on the calendar, no memory gained.
    outcome: str
    stability_before: float
    stability_after: float
    rationale: str
    # The topic studied, "Algebra 3 · week of 05 Oct", when the subject's lectures
    # are topics; "" when the subject is one topic. For the assistant, also the
    # task, or "Exam practice: <course>".
    topic: str = ""
    kind: str = "review"  # "review", "first review", "task", "practice"
    detail: str = ""  # what to do in the block (the assistant says; the planner does not)
    pinned: bool = False  # the student put it at this time (`move_session`)
    # How much a self-test recalled, as the student answered (D25): "again", "hard",
    # "good" or "easy", FSRS's grades; "" when not answered (the outcome decides).
    grade: str = ""

    @property
    def title(self) -> str:
        return self.topic or self.subject

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class PlanSettings:
    start: date
    tz: str
    retention: float
    window: int
    seed: int | None
    failure_penalty: float
    block_minutes: int
    slots_per_day: int
    horizon_days: int
    # "planner" (FSRS value functions and AO*, `rolling.py`) or "assistant" (the
    # rules of `assistant.py`), and the assistant's preferences.
    engine: str = "planner"
    preferences: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["start"] = self.start.isoformat()
        out["preferences"] = dict(self.preferences)
        return out


@dataclass(frozen=True)
class PlanReport:
    """A plan: what to study when, why, and how likely it is to work.

    `sessions` are the upcoming sessions. `history` holds sessions already
    reported through `replan_after`, with their real outcomes. Unless a seed was
    given, upcoming outcomes are all "recalled": the plan as it stands if every
    review works, which is what a student can put in a calendar.
    """

    settings: PlanSettings
    subjects: tuple[SubjectView, ...]
    specs: tuple[dict, ...]  # the starting states, for replanning
    sessions: tuple[SessionView, ...]
    history: tuple[SessionView, ...]
    blocks: tuple[BlockView, ...]
    events: tuple[EventView, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...] = LIMITATIONS
    tasks: tuple[TaskView, ...] = ()

    @property
    def blocks_used(self) -> int:
        return len(self.sessions)

    def to_dict(self) -> dict:
        return {
            "settings": self.settings.to_dict(),
            "subjects": [s.to_dict() for s in self.subjects],
            "specs": [dict(s) for s in self.specs],
            "sessions": [s.to_dict() for s in self.sessions],
            "history": [s.to_dict() for s in self.history],
            "blocks": [b.to_dict() for b in self.blocks],
            "events": [e.to_dict() for e in self.events],
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> PlanReport:
        try:
            settings = dict(data["settings"])
            settings["start"] = date.fromisoformat(settings["start"])
            return cls(
                settings=PlanSettings(**settings),
                subjects=tuple(SubjectView(**s) for s in data["subjects"]),
                specs=tuple(dict(s) for s in data["specs"]),
                sessions=tuple(SessionView(**s) for s in data["sessions"]),
                history=tuple(SessionView(**s) for s in data["history"]),
                blocks=tuple(BlockView(**b) for b in data["blocks"]),
                events=tuple(EventView(**e) for e in data["events"]),
                warnings=tuple(data["warnings"]),
                limitations=tuple(data["limitations"]),
                tasks=tuple(TaskView(**t) for t in data.get("tasks", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput(f"not a plan produced by this version: {exc}") from exc


@dataclass(frozen=True)
class TaskSpec:
    """Work with a deadline, as a person describes it: "Stats report, due 20 Nov,
    about 6 hours". `due` is read like `SubjectSpec.exam`."""

    name: str
    due: datetime | date | str
    hours: float
    course: str = ""
    done: bool = False  # finished: nothing more is planned for it

    def due_datetime(self, zone: ZoneInfo) -> datetime:
        return SubjectSpec(self.name, self.due).exam_datetime(zone)


@dataclass(frozen=True)
class TaskView:
    name: str
    course: str
    due: str
    due_day: float
    blocks: int  # the work, in study blocks
    scheduled: int = 0  # blocks done or planned before the due date
    finish: str | None = None  # start of the last block planned for it
    at_risk: bool = False  # some of the work does not fit before the due date
    done: bool = False  # the student finished it; its remaining blocks are freed

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _resolve(
    report: CalendarReport, subjects: Sequence[SubjectSpec], model: Model = FSRS_4_5
) -> tuple[list[dict[str, Any]], float]:
    """Specs to plain starting states with exam days, validated: one per subject."""
    if not subjects:
        raise InvalidInput(_("add at least one subject with an exam date"))
    names = [s.name.strip() for s in subjects]
    if any(not n for n in names):
        raise InvalidInput(_("every subject needs a name"))
    if len(set(n.casefold() for n in names)) != len(names):
        raise InvalidInput(_("two subjects have the same name"))
    zone = _zone(report.tz)
    found = {a.subject.casefold(): a.when for a in report.assessments}
    resolved: list[dict[str, Any]] = []
    for spec in subjects:
        memory, prior = spec.memory(model)
        if spec.exam is None:
            if spec.name.strip().casefold() not in found:
                raise MissingExam(spec.name.strip())
            spec = replace(spec, exam=found[spec.name.strip().casefold()])
        exam = spec.exam_datetime(zone)
        exam_day = _days_after(report.start, exam)
        if exam_day <= 0:
            raise ExamInPast(
                f"the exam for {spec.name} ({exam:%Y-%m-%d %H:%M}) is not after the start of the "
                f"plan ({report.start}); change the date or the start"
            )
        resolved.append(
            {
                "name": spec.name.strip(),
                "subject": spec.name.strip(),
                "topic": "",
                "exam": _iso(exam),
                "exam_day": exam_day,
                "stability": memory.stability,
                "difficulty": memory.difficulty,
                "last_review_day": 0.0,
                "available_day": 0.0,
                "target_days": None,
                "prior": prior,
            }
        )
    return resolved, max(float(r["exam_day"]) for r in resolved)


# The state of a week of lectures right after it is taught: seen once and shaky,
# familiarity 2 (a first review graded Hard). A stated guess, like every prior.
LECTURE_FAMILIARITY = 2


def _week_target(days: float) -> float:
    """Preparation length for a topic's target, rounded down to whole weeks past
    the first, so that topics taught in the same week share a value-function solve
    whatever their exam. Rounding down makes the target easier, never harder."""
    return days if days < 7 else 7 * math.floor(days / 7 + 1e-9)


def _week_label(monday: date) -> str:
    """How a week of lectures is named in its topic, "05 Oct" in "Algebra 3 · week
    of 05 Oct": part of a session's title, hence of its id, so it never changes."""
    return f"{monday:%d %b}"


def _topics(report: CalendarReport, specs: Sequence[dict], model: Model = FSRS_4_5) -> list[dict[str, Any]]:
    """Each subject whose lectures are in the calendar becomes a topic per week of
    lectures, plus one for what was taught before the plan starts; the others stay
    one topic. See "Lectures become topics" in docs/METHOD.md."""
    zone = _zone(report.tz)
    midnight = datetime.combine(report.start, time(0, 0), tzinfo=zone)
    lecture_memory = familiarity_prior(LECTURE_FAMILIARITY, model)
    out: list[dict[str, Any]] = []
    for spec in specs:
        exam = datetime.fromisoformat(spec["exam"])
        ends = sorted(
            datetime.fromisoformat(x.end)
            for x in report.lectures
            if x.course.casefold() == spec["subject"].casefold() and datetime.fromisoformat(x.end) < exam
        )
        if not ends:
            out.append({**spec, "target_days": _week_target(spec["exam_day"])})
            continue
        if ends[0] <= midnight:
            out.append(
                {
                    **spec,
                    "name": f"{spec['subject']} · taught before {report.start:%d %b}",
                    "topic": f"taught before {report.start:%d %b}",
                    "note": _(
                        "Review what was taught before {day}.", day=format_date(report.start, "day_short")
                    ),
                    "about": _("what was taught before {day}", day=format_date(report.start, "day")),
                    "target_days": _week_target(spec["exam_day"]),
                }
            )
        weeks: dict[date, list[tuple[datetime, datetime]]] = {}
        for x in report.lectures:
            begin, end = datetime.fromisoformat(x.start), datetime.fromisoformat(x.end)
            if x.course.casefold() == spec["subject"].casefold() and midnight < end < exam:
                local = end.astimezone(zone)
                weeks.setdefault(local.date() - timedelta(days=local.weekday()), []).append((begin, end))
        for monday, taught in sorted(weeks.items()):
            parts = _week_parts(sorted(taught))
            for k, part in enumerate(parts, start=1):
                available = _days_after(report.start, max(end for _, end in part))
                if available >= spec["exam_day"]:
                    continue
                label = f"week of {_week_label(monday)}" + (
                    f" ({k} of {len(parts)})" if len(parts) > 1 else ""
                )
                day_short, day = format_date(monday, "day_short"), format_date(monday, "day")
                out.append(
                    {
                        **spec,
                        "name": f"{spec['subject']} · {label}",
                        "topic": label,
                        "note": _("Review the lectures of the week of {day}.", day=day_short)
                        if len(parts) == 1
                        else _(
                            "Review the lectures of the week of {day}, part {k} of {n}.",
                            day=day_short,
                            k=k,
                            n=len(parts),
                        ),
                        "about": _("the lectures of the week of {day}", day=day)
                        if len(parts) == 1
                        else _(
                            "the lectures of the week of {day}, part {k} of {n}", day=day, k=k, n=len(parts)
                        ),
                        "stability": lecture_memory.stability,
                        "difficulty": lecture_memory.difficulty,
                        "last_review_day": available,
                        "available_day": available,
                        "target_days": _week_target(spec["exam_day"] - available),
                        "prior": f"lectures, familiarity {LECTURE_FAMILIARITY}",
                    }
                )
    return out


def _partials(report: CalendarReport) -> list[dict[str, Any]]:
    """The classes of the timetable marked as partial exams (D61), in the plan's
    horizon: each an exam of its own for its course, named for practice sessions
    ("Algebra 3, partial exam of 12 Nov"), and `exempt` when passing it takes its
    material off the final exam."""
    zone = _zone(report.tz)
    out = []
    for e in report.events:
        if e.source != "partial":
            continue
        start = datetime.fromisoformat(e.start)
        change = _lecture_change(report.lecture_changes, BusyEvent(start, start, e.summary), zone) or {}
        course = course_of(e.summary) or short_title(e.summary)
        out.append(
            {
                "key": _(
                    "{course}, partial exam of {day}", course=course, day=format_date(start.date(), "short")
                ),
                "course": course,
                "exam": e.start,
                "exam_day": _days_after(report.start, start),
                "exempt": bool(change.get("exempt")),
            }
        )
    return out


def _mocks(report: CalendarReport) -> list[dict[str, Any]]:
    """The classes of the timetable marked as mock exams (D61): practice for their
    course's exam, held in class."""
    return [
        {
            "course": course_of(e.summary) or short_title(e.summary),
            "day": _days_after(report.start, datetime.fromisoformat(e.start)),
            "hours": (datetime.fromisoformat(e.end) - datetime.fromisoformat(e.start)) / timedelta(hours=1),
        }
        for e in report.events
        if e.source == "mock"
    ]


def _split_at_partials(report: CalendarReport, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Weeks of lectures taught before a partial exam that takes its material off
    the final (D61) are examined there: their exam is the partial's, so they are
    reviewed for it and not after. A course that is one topic (its lectures are not
    in the timetable) cannot be split, and keeps its final."""
    out = []
    for spec in specs:
        spec = dict(spec)
        for p in _partials(report):
            if (
                p["exempt"]
                and spec.get("topic")
                and spec["subject"].casefold() == p["course"].casefold()
                and spec.get("available_day", 0.0) < p["exam_day"] < spec["exam_day"]
            ):
                spec.update(
                    exam=p["exam"],
                    exam_day=p["exam_day"],
                    exam_key=p["key"],
                    target_days=_week_target(p["exam_day"] - spec.get("available_day", 0.0)),
                )
        out.append(spec)
    return out


# A week of one course with more lecture hours than this is more than one session
# can review (D50): it becomes as many topics as it has three-hour shares, its
# lectures split in the order taught.
LECTURE_HOURS_PER_TOPIC = 3.0


def _week_parts(taught: Sequence[tuple[datetime, datetime]]) -> list[list[tuple[datetime, datetime]]]:
    """A week's lectures (in order) in as many parts as `LECTURE_HOURS_PER_TOPIC`
    shares of its hours, each about as long; one part for three hours or fewer."""
    hours = [(end - begin) / timedelta(hours=1) for begin, end in taught]
    count = max(1, math.ceil(sum(hours) / LECTURE_HOURS_PER_TOPIC - 1e-9))
    count = min(count, len(taught))
    share, done = sum(hours) / count, 0.0
    parts: list[list[tuple[datetime, datetime]]] = [[]]
    for lecture, length in zip(taught, hours, strict=True):
        if parts[-1] and done >= share * len(parts) - 1e-9 and len(parts) < count:
            parts.append([])
        parts[-1].append(lecture)
        done += length
    return parts


def _extend(report: CalendarReport, days: int) -> CalendarReport:
    return analyse_calendar(
        report.ics_text.encode("utf-8") if report.ics_text is not None else None,
        start=report.start,
        tz=report.tz,
        days=days,
        study_window=report.study_window,
        blocks_per_day=report.blocks_per_day,
        block_minutes=report.block_minutes,
        busy_rows=report.busy_rows,
        commute_minutes=report.commute_minutes,
        study_time=report.study_time,
        quick_tests=report.quick_tests,
        study_on_the_way=report.study_on_the_way,
        lecture_changes=report.lecture_changes,
        lunch=report.lunch,
    )


def make_plan(
    report: CalendarReport,
    subjects: Sequence[SubjectSpec],
    *,
    retention: float = 0.9,
    window: int = 6,
    seed: int | None = None,
    failure_penalty: float = DEFAULT_FAILURE_PENALTY,
    lectures_as_topics: bool = True,
) -> PlanReport:
    """Plan every subject towards its own exam on the free blocks of `report`.

    With `lectures_as_topics`, a subject whose lectures are in the calendar is
    planned as one topic per week of lectures, each studied from the day it is
    taught (AUDIT.md item 33); a subject without lectures is one topic, as before.
    """
    settings, specs, report = _prepare(
        report, subjects, retention, window, seed, failure_penalty, lectures_as_topics
    )
    return _plan(settings, specs, tuple(report.blocks), tuple(report.events), history=(), strict=True)


def _prepare(
    report: CalendarReport,
    subjects: Sequence[SubjectSpec],
    retention: float,
    window: int,
    seed: int | None,
    failure_penalty: float,
    lectures_as_topics: bool,
    model: Model = FSRS_4_5,
) -> tuple[PlanSettings, list[dict[str, Any]], CalendarReport]:
    """Validate, resolve exam dates, make the topics, and settle the horizon; the
    starting states are `model`'s (D52)."""
    if not 0.5 <= retention < 1.0:
        raise InvalidInput("target recall must be at least 0.5 and below 1")
    if window < 1:
        raise InvalidInput("the planning window must be at least one block")
    specs, latest = _resolve(report, subjects, model)
    needed = max(1, math.ceil(latest - 1e-9))
    if needed > report.days:
        report = _extend(report, needed)
    if lectures_as_topics:
        topics = _topics(report, specs, model)
        if any(t["topic"] for t in topics):
            specs = _split_at_partials(report, topics)
    settings = PlanSettings(
        start=report.start,
        tz=report.tz,
        retention=retention,
        window=window,
        seed=seed,
        failure_penalty=failure_penalty,
        block_minutes=report.block_minutes,
        slots_per_day=report.slots_per_day,
        horizon_days=report.days,
    )
    report = _around_exams(report, specs)
    usable = [b for b in report.blocks if b.start_day < latest]
    if not usable:
        raise NoFreeBlocks(
            "there is no free study block before the exams; widen the study window, allow more "
            "blocks a day, shorten the blocks, or free some time in the calendar"
        )
    return settings, specs, report


def _around_exams(report: CalendarReport, specs: Sequence[Mapping[str, Any]]) -> CalendarReport:
    """The report without the blocks, and the quick self-tests' room, that fall in the
    hour before an exam or the half hour after it (D183), whatever the course: an exam
    of the timetable ends when its event does, one typed by hand two hours after it
    starts. A mock or partial exam the student marked in the timetable (D61) is kept free
    alike; a quiz, a few minutes inside a class, is not."""
    ends: dict[datetime, datetime] = {}
    for e in report.events:
        if e.source in ("calendar", *TEST_KINDS):
            begin = datetime.fromisoformat(e.start)
            ends[begin] = max(ends.get(begin, begin), datetime.fromisoformat(e.end))
    starts = {datetime.fromisoformat(str(s["exam"])) for s in specs if s.get("exam")}
    starts |= {datetime.fromisoformat(e.start) for e in report.events if e.source in _EXAM_KINDS}
    windows = [(t - EXAM_BEFORE, ends.get(t, t + TYPED_EXAM_LENGTH) + EXAM_AFTER) for t in starts]
    if not windows:
        return report

    def clear(b: BlockView) -> bool:
        begin, end = datetime.fromisoformat(b.start), datetime.fromisoformat(b.end)
        return all(end <= w0 or begin >= w1 for w0, w1 in windows)

    return replace(
        report,
        blocks=tuple(b for b in report.blocks if clear(b)),
        quick=tuple(b for b in report.quick if clear(b)),
    )


def continue_plan(
    report: CalendarReport,
    subjects: Sequence[SubjectSpec],
    *,
    done: Sequence[SessionView],
    now: datetime,
    retention: float = 0.9,
    window: int = 6,
    seed: int | None = None,
    failure_penalty: float = DEFAULT_FAILURE_PENALTY,
    lectures_as_topics: bool = True,
) -> PlanReport:
    """Plan from `now` on, on a calendar that may have changed since the last plan.

    `done` are the sessions behind: they are replayed, with their outcomes, from
    the starting states, and the plan continues from the states they lead to, on the
    free blocks that start after `now`. A session whose topic no longer exists, a
    week of lectures that was cancelled, is dropped. This is what keeps a
    subscribed feed (`refresh_subscription`) current without forgetting what was
    already studied.
    """
    settings, specs, report = _prepare(
        report, subjects, retention, window, seed, failure_penalty, lectures_as_topics
    )
    names = {s["name"] for s in specs}
    history = tuple(
        replace(s, index=i)
        for i, s in enumerate(sorted((s for s in done if s.title in names), key=lambda s: s.start_day))
    )
    after = _days_after(report.start, now.astimezone(_zone(report.tz)))
    states = _replay(specs, history, FSRS_4_5)
    current = [
        {
            **s,
            "stability": states[s["name"]][0].stability,
            "difficulty": states[s["name"]][0].difficulty,
            "last_review_day": states[s["name"]][1],
        }
        for s in specs
    ]
    return _plan(
        settings,
        specs,
        tuple(report.blocks),
        tuple(report.events),
        history,
        after_day=after,
        current=current,
    )


# Topics a window chooses between; see `rolling._candidates`. Measured by
# `benchmarks/semester.py --candidates 3 4 6` on a synthetic semester of 69 topics:
# 62, 63 and 66 of them reach their target, and 6 takes about three times as long
# as 4 (AUDIT.md item 33).
TOPIC_CANDIDATES = 4


def _subject(s: Mapping) -> Subject:
    return Subject(
        s["name"],
        MemoryState(s["stability"], s["difficulty"]),
        s["exam_day"],
        s["last_review_day"],
        s.get("available_day", 0.0),
        s.get("target_days"),
    )


def _plan(
    settings: PlanSettings,
    specs: Sequence[dict],
    blocks: tuple[BlockView, ...],
    events: tuple[EventView, ...],
    history: tuple[SessionView, ...],
    after_day: float = -1.0,
    current: Sequence[dict] | None = None,
    strict: bool = False,
) -> PlanReport:
    """Run the planner from the states in `current` (default: the specs) on the
    blocks that start after `after_day`."""
    states = list(current) if current is not None else [dict(s) for s in specs]
    subjects = [_subject(s) for s in states]
    # Topics share one horizon so that topics taught in the same week share their
    # solves, and each window chooses among the most urgent few. A plan without
    # topics runs exactly as it always has.
    topical = any(s.get("topic") for s in specs)
    pending = tuple(b for b in blocks if b.start_day > after_day)
    rng = np.random.default_rng(settings.seed) if settings.seed is not None else None
    continuation = (
        solve_deadlines(
            subjects,
            settings.retention,
            settings.failure_penalty,
            horizon=max(s.exam_day for s in subjects),
        )
        if topical and pending
        else None
    )
    result = (
        run_rolling(
            [b.as_block() for b in pending],
            subjects,
            continuation,
            window=settings.window,
            retention=settings.retention,
            failure_penalty=settings.failure_penalty,
            rng=rng,
            max_candidates=TOPIC_CANDIDATES if topical else None,
        )
        if pending
        else None
    )

    by_slot = {b.slot: b for b in blocks}
    by_name = {s["name"]: s for s in specs}
    built: list[SessionView] = []
    for number, s in enumerate(result.sessions if result else (), start=len(history)):
        view = by_slot[s.block.slot]
        spec = by_name[s.subject]
        rationale = s.rationale
        if spec.get("topic"):
            rationale = f"{spec['note']} {rationale}"
        built.append(
            SessionView(
                index=number,
                subject=spec.get("subject", s.subject),
                start=view.start,
                end=view.end,
                day=view.day,
                start_day=view.start_day,
                slot=view.slot,
                recall=s.retrievability_at_review,
                outcome="recalled" if s.outcome != Grade.AGAIN else "lapsed",
                stability_before=s.stability_before,
                stability_after=s.stability_after,
                rationale=rationale,
                topic=s.subject if spec.get("topic") else "",
            )
        )
    sessions = tuple(built)

    finals = _replay(specs, history + sessions, FSRS_4_5)
    targets = result.targets if result else tuple(_subject(s).target(settings.retention) for s in specs)
    unreachable = result.unreachable if result else tuple(False for _ in specs)
    views, warnings = [], []
    groups: dict[str, list[tuple[dict, float, bool]]] = {}
    for spec, target, lost in zip(specs, targets, unreachable, strict=True):
        groups.setdefault(spec.get("subject", spec["name"]), []).append((spec, target, lost))
    for name, members in groups.items():
        first = next((x.start for x in history + sessions if x.subject == name), None)
        if len(members) == 1 and not members[0][0].get("topic"):
            spec, target, lost = members[0]
            memory, last = finals[spec["name"]]
            ready = memory.stability >= target
            views.append(
                SubjectView(
                    name=name,
                    exam=spec["exam"],
                    exam_day=spec["exam_day"],
                    stability=spec["stability"],
                    difficulty=spec["difficulty"],
                    prior=spec["prior"],
                    target=target,
                    ready=ready,
                    unreachable=lost and not ready,
                    recall_at_exam=retrievability(max(spec["exam_day"] - last, 0.0), memory.stability),
                    stability_at_exam=memory.stability,
                    first_review=first,
                )
            )
            if lost and not ready:
                warnings.append(
                    f"{name}: even if every review succeeded, the free blocks before this exam "
                    f"cannot build a stability of {target:.0f} days, so no time was spent on it. More "
                    f"free time spread over more days would change that."
                )
            elif not ready:
                warnings.append(f"{name}: this plan does not reach the target by the exam.")
            continue
        summary, warning = _topic_view(name, members, finals, first)
        views.append(summary)
        warnings.extend(warning)
    if strict and all(v.unreachable for v in views):
        raise UnreachableTarget(
            "no subject can reach its target before its exam on this calendar, even if every "
            "review succeeded; " + " ".join(warnings)
        )
    return PlanReport(
        settings=settings,
        subjects=tuple(views),
        specs=tuple(dict(s) for s in specs),
        sessions=sessions,
        history=history,
        blocks=blocks,
        events=events,
        warnings=tuple(warnings),
    )


def _topic_view(
    name: str,
    members: Sequence[tuple[dict, float, bool]],
    finals: Mapping[str, tuple[MemoryState, float]],
    first: str | None,
) -> tuple[SubjectView, list[str]]:
    """A subject taught over the horizon, summarised over its topics: ready when
    every topic is, recall at the exam averaged over topics, and the weakest
    topic's stability against its own target. The subject's exam is its final: a
    topic examined at a partial exam (D61) is measured at that one."""
    ready = [finals[s["name"]][0].stability >= t for s, t, _ in members]
    lost = [bad and not ok for (_, _, bad), ok in zip(members, ready, strict=True)]
    recall = [
        retrievability(max(s["exam_day"] - finals[s["name"]][1], 0.0), finals[s["name"]][0].stability)
        for s, _, _ in members
    ]
    weakest, weakest_target, _ = min(members, key=lambda m: finals[m[0]["name"]][0].stability / m[1])
    head = next((m[0] for m in members if not m[0].get("exam_key")), members[0][0])
    view = SubjectView(
        name=name,
        exam=head["exam"],
        exam_day=head["exam_day"],
        stability=head["stability"],
        difficulty=head["difficulty"],
        prior=head["prior"],
        target=weakest_target,
        ready=all(ready),
        unreachable=all(lost),
        recall_at_exam=sum(recall) / len(recall),
        stability_at_exam=finals[weakest["name"]][0].stability,
        first_review=first,
        topics=len(members),
        topics_ready=sum(ready),
    )
    warnings = []
    if not any(s.get("available_day", 0.0) == 0.0 for s, _, _ in members):
        warnings.append(
            f"{name}: all its lectures in the calendar come after the start of the plan, so your "
            f"estimate of what you already know is not used; each week is planned from the day it "
            f"is taught."
        )
    if any(lost):
        warnings.append(
            f"{name}: {sum(lost)} of {len(members)} topics cannot reach their target before the exam "
            f"even if every review succeeded, so no time was spent on them. More free time spread "
            f"over more days would change that."
        )
    if not all(ready) and sum(lost) < len(members) - sum(ready):
        warnings.append(
            f"{name}: {len(members) - sum(ready)} of {len(members)} topics do not reach their target "
            f"by the exam."
        )
    return view, warnings


def _replay(
    specs: Sequence[dict], done: Sequence[SessionView], model: Model
) -> dict[str, tuple[MemoryState, float]]:
    """Memory state and last review day of every subject after `done`, recomputed
    with `model`'s exact transitions (a single source of truth, not stored numbers)."""
    state = {s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in specs}
    for session in sorted(done, key=lambda x: x.start_day):
        if session.outcome in _NO_MEMORY or session.title not in state:
            continue  # skipped, not reported, or a task or exam practice: nothing to remember
        memory, last = state[session.title]
        state[session.title] = (
            model.review(memory, session.start_day - last, _grade(session)),
            session.start_day,
        )
    return state


def replan_after(plan: PlanReport, session_index: int, outcome: str) -> PlanReport:
    """Report what happened at an upcoming session and plan the rest again.

    Sessions before it are taken to have gone as planned. "recalled" confirms the
    session, "lapsed" records that the material was forgotten, "skipped" that the
    session did not happen. The plan from the next block on is recomputed.
    """
    if outcome not in OUTCOMES:
        raise InvalidInput(f"outcome must be one of {', '.join(OUTCOMES)}, not {outcome!r}")
    positions = {s.index: n for n, s in enumerate(plan.sessions)}
    if session_index not in positions:
        raise InvalidInput(f"session {session_index} is not an upcoming session of this plan")
    cut = positions[session_index]
    reported = replace(plan.sessions[cut], outcome=outcome)
    history = plan.history + plan.sessions[:cut] + (reported,)
    if plan.settings.engine == "assistant":
        return _assist(
            plan.settings, plan.specs, plan.tasks, plan.blocks, plan.events, history, reported.start_day
        )
    specs = [dict(s) for s in plan.specs]
    after = _replay(specs, history, FSRS_4_5)
    current = [
        {
            **s,
            "stability": after[s["name"]][0].stability,
            "difficulty": after[s["name"]][0].difficulty,
            "last_review_day": after[s["name"]][1],
        }
        for s in specs
    ]
    return _plan(
        plan.settings, specs, plan.blocks, plan.events, history, after_day=reported.start_day, current=current
    )


def export_ics(
    plan: PlanReport,
    *,
    include_history: bool = False,
    uid_prefix: str = "",
    refresh: timedelta | None = None,
) -> bytes:
    """The upcoming sessions as an importable calendar, as bytes (see AUDIT item 21).

    `include_history` adds the sessions already behind, which a subscribed feed
    keeps so that they do not vanish from the person's calendar once done.
    """
    shown = [s for s in (plan.history if include_history else ()) + plan.sessions if s.outcome != "skipped"]
    text = plan_to_ics(
        [(s.slot, s.title, s.rationale) for s in shown],
        plan.settings.start,
        plan.settings.tz,
        slots_per_day=plan.settings.slots_per_day,
        block_slots=max(1, plan.settings.block_minutes // (24 * 60 // plan.settings.slots_per_day)),
        uid_prefix=uid_prefix,
        refresh=refresh,
        calendar_name=_("Study plan"),
        summaries=[f"{_('Study')}: {topic_words(s.title)}" for s in shown],
        times=[(datetime.fromisoformat(s.start), datetime.fromisoformat(s.end)) for s in shown],
    )
    return text.encode("utf-8")


def recall_curve(plan: PlanReport, subject: str, step: float = 0.25) -> list[tuple[float, float]]:
    """Predicted probability of recall over time, from the start to the exam.

    Includes the sessions already reported and the upcoming ones; at each session
    the curve has two points, just before and just after the review. A subject
    whose lectures are topics gets the average over the topics taught so far, on
    a grid of `step` days.
    """
    specs = [s for s in plan.specs if s.get("subject", s["name"]) == subject]
    if not specs:
        raise InvalidInput(f"no subject called {subject!r} in this plan")
    done = [s for s in plan.history + plan.sessions if s.outcome not in _NO_MEMORY]
    model = model_of(plan.settings)
    if len(specs) == 1 and not specs[0].get("topic"):
        return _topic_curve(specs[0], [s for s in done if s.title == specs[0]["name"]], step, model)
    curves = [
        (s, dict(_topic_curve(s, [x for x in done if x.title == s["name"]], step, model))) for s in specs
    ]
    exam_day = specs[0]["exam_day"]
    points = []
    t = 0.0
    while t <= exam_day + 1e-9:
        taught = [c for s, c in curves if s.get("available_day", 0.0) <= t + 1e-9]
        values = [_curve_at(c, t) for c in taught]
        if values:
            points.append((round(t, 6), float(sum(values) / len(values))))
        t += step
    return points


def _curve_at(curve: Mapping[float, float], t: float) -> float:
    """The value of a sampled curve at `t`: the last sample at or before it."""
    at = max((d for d in curve if d <= t + 1e-9), default=None)
    return curve[at] if at is not None else 1.0


def _topic_curve(
    spec: Mapping, reviews: Sequence[SessionView], step: float, model: Model
) -> list[tuple[float, float]]:
    memory = MemoryState(spec["stability"], spec["difficulty"])
    last = spec["last_review_day"]
    points: list[tuple[float, float]] = []
    t = spec.get("available_day", 0.0)
    for session in [*sorted(reviews, key=lambda s: s.start_day), None]:
        until = session.start_day if session else spec["exam_day"]
        while t < until:
            points.append((t, model.retrievability(t - last, memory.stability)))
            t += step
        points.append((until, model.retrievability(until - last, memory.stability)))
        if session is None:
            break
        grade = Grade.GOOD if session.outcome == "recalled" else Grade.AGAIN
        memory = model.review(memory, session.start_day - last, grade)
        last = session.start_day
        points.append((until, 1.0))
        t = until + step
    return [(round(d, 6), float(p)) for d, p in points]


# --------------------------------------------------------------------------- #
# Tables for display
# --------------------------------------------------------------------------- #
# Shaped for a table widget but free of any framework, so that what a UI shows is
# computed here, once, and a test can compare it with what the UI displays.


def subject_status(subject: SubjectView) -> str:
    """One line on where a subject ends up: "ready: stability 18 of 18 days", or
    for a subject planned week by week, "not ready: 11 of 13 topics at target"."""
    if subject.status_line:
        return subject.status_line
    status = "ready" if subject.ready else ("out of reach" if subject.unreachable else "not ready")
    if subject.topics > 1:
        return f"{status}: {subject.topics_ready} of {subject.topics} topics at target"
    return f"{status}: stability {subject.stability_at_exam:.0f} of {subject.target:.0f} days"


def task_rows(plan: PlanReport) -> list[dict]:
    """The deadlines, one row each: when the work is planned to finish, and whether
    it all fits."""
    return [
        {
            "task": t.name,
            "course": t.course,
            "due": datetime.fromisoformat(t.due).strftime("%a %d %b %H:%M"),
            "planned": f"{t.scheduled} of {t.blocks} blocks",
            "finishes": datetime.fromisoformat(t.finish).strftime("%a %d %b") if t.finish else "",
            "status": "at risk" if t.at_risk else "on track",
        }
        for t in plan.tasks
    ]


def session_rows(plan: PlanReport) -> list[dict]:
    """The upcoming sessions, one row each, as a person reads them."""
    what = any(s.detail for s in plan.sessions)
    rows = []
    for s in plan.sessions:
        row = {
            "#": s.index,
            "when": datetime.fromisoformat(s.start).strftime("%a %d %b %H:%M"),
            "subject": s.title,
            "recall now": f"{s.recall:.0%}" if s.kind in ("review", "first review") else "",
            "why": s.rationale,
        }
        if what:
            row["what to do"] = s.detail
        rows.append(row)
    return rows


def week_count(plan: PlanReport) -> int:
    return max(1, math.ceil(plan.settings.horizon_days / 7))


def calendar_week(
    source: CalendarReport | PlanReport,
    week: int,
    *,
    typed: bool = True,
    first: date | None = None,
    count: int = 7,
    history: bool = False,
) -> dict:
    """One week, Monday or not, as positioned items for a calendar widget.

    `{"days": [{"date", "label", "weekday", "in_horizon"} x 7], "items": [{"day",
    "start", "end", "label", "kind"}]}`, with `start` and `end` in minutes after
    local midnight and an item cut at midnight when it runs into the next day.
    `kind` is "calendar" (from the .ics), "typed" (a busy row), "exam" or "study";
    a study item also carries its `id` (`session_id`) and `detail`.
    The week starts on the plan's first day, like `week_view`, unless `first` names
    the first of `count` days to show. `typed=False` leaves out the busy rows, for
    an editor that draws them itself; `history` adds the sessions already done.
    """
    if isinstance(source, PlanReport):
        start, tz, days = source.settings.start, source.settings.tz, source.settings.horizon_days
        exams = {s.exam: s.name for s in source.subjects}
        shown_sessions = source.history + source.sessions if history else source.sessions
        sessions = [(x.start, x.end, x.title, x) for x in shown_sessions]
    else:
        start, tz, days = source.start, source.tz, source.days
        exams = {a.when: a.subject for a in source.assessments}
        sessions = []
    weeks = max(1, math.ceil(days / 7))
    if first is None:
        if not 0 <= week < weeks:
            raise InvalidInput(f"week must be 0 to {weeks - 1}")
        first = start + timedelta(days=7 * week)
    if not 1 <= count <= CALENDAR_MOST_DAYS:
        raise InvalidInput(f"show 1 to {CALENDAR_MOST_DAYS} days")
    zone = _zone(tz)
    dates = [first + timedelta(days=d) for d in range(count)]
    items: list[dict] = []

    def place(begin: datetime, end: datetime, label: str, kind: str, **extra: Any) -> None:
        begin, end = begin.astimezone(zone), end.astimezone(zone)
        for index, day in enumerate(dates):
            midnight = datetime.combine(day, time(0, 0), tzinfo=zone)
            lo, hi = max(begin, midnight), min(end, midnight + timedelta(days=1))
            if lo < hi:
                items.append(
                    {
                        "day": index,
                        "start": round((lo - midnight) / timedelta(minutes=1)),
                        "end": round((hi - midnight) / timedelta(minutes=1)),
                        "label": label,
                        "kind": kind,
                        **extra,
                    }
                )

    shown_exams = set()
    for event in source.events:
        if event.source == "typed" and not typed:
            continue
        kind = (
            event.source
            if event.source in ("typed", "commute", "lunch", "skipped", *TEST_KINDS)
            else "calendar"
        )
        if event.start in exams and event.source not in ("commute", "lunch", "skipped"):
            kind = "exam"
            shown_exams.add(event.start)
        label = (
            event.summary
            if kind == "exam"
            else test_label(kind, event.summary)
            if kind in TEST_KINDS
            else short_title(event.summary)
        )
        course = None if kind in ("commute", "lunch") else exams.get(event.start) or course_of(event.summary)
        lo, hi = datetime.fromisoformat(event.start), datetime.fromisoformat(event.end)
        changeable = ("calendar", "skipped", *TEST_KINDS)
        named = {"at": event.start, "ref": lecture_ref(event.summary)} if kind in changeable else {}
        place(lo, hi, label, kind, course=course, **named)
    for when, name in exams.items():
        if when not in shown_exams:
            moment = datetime.fromisoformat(when)
            place(moment, moment + timedelta(hours=1), _("Exam: {name}", name=name), "exam", course=name)
    for begin, end, label, session in sessions:
        extra: dict[str, Any] = {}
        if isinstance(session, SessionView):
            label = session_label(session)
            extra = {
                "id": session_id(session),
                "detail": session.detail,
                "why": session.rationale,
                "title": topic_words(session.title),
                "session_kind": session.kind,
                "pinned": session.pinned,
                "course": session.subject,
                "at": session.start,
                "until": session.end,
            }
        place(datetime.fromisoformat(begin), datetime.fromisoformat(end), label, "study", **extra)
    return {
        "days": [
            {
                "date": d.isoformat(),
                "label": format_date(d, "short"),
                "weekday": WEEKDAY_NAMES[d.weekday()],
                "in_horizon": 0 <= (d - start).days < days,
            }
            for d in dates
        ],
        "items": items,
    }


# How a calendar box names a session: short enough for a phone's column.
_SESSION_VERBS = {
    "first review": "Self-test",
    "review": "Self-test",
    "task": "Work on",
    "practice": "Practice",
}


def session_label(session: SessionView) -> str:
    """ "Self-test: Algebra 3", "Work on: Stats report", "Practice: Analysis 3"."""
    what = session.title if session.kind == "task" else session.subject
    verb = _SESSION_VERBS.get(session.kind)
    return _("{verb}: {what}", verb=_(verb), what=what) if verb else topic_words(session.title)


_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TOPIC_WEEK = re.compile(r"(week of|taught before) (\d{1,2}) (" + "|".join(_MONTH_ABBR) + r")")
_TOPIC_PART = re.compile(r"(week of \d{1,2} \w{3}) \((\d+) of (\d+)\)")


def topic_words(title: str) -> str:
    """A topic's name in the current language. Topic names are identifiers (a
    session's id is made from its title, so a report survives a change of
    language): "Algebra 3 · week of 05 Oct" is stored in English and shown as
    "Algebra 3 · semaine du 5 oct." in French, and a week in parts (D50), "week of 16
    Nov (1 of 2)", as "semaine du 16 nov. (1 sur 2)" (AUDIT 107)."""

    def say(match: re.Match[str]) -> str:
        day = date(2000, _MONTH_ABBR.index(match.group(3)) + 1, int(match.group(2)))
        phrase = _("week of {day}") if match.group(1) == "week of" else _("taught before {day}")
        return phrase.format(day=format_date(day, "day_short"))

    def part(match: re.Match[str]) -> str:
        return match.group(1) + " " + _("({k} of {n})", k=match.group(2), n=match.group(3))

    return _TOPIC_WEEK.sub(say, _TOPIC_PART.sub(part, title))


def short_title(summary: str) -> str:
    """A timetable event's title as a person reads it: "Algebra 3 · CM · Salle 4"
    for ADE's "Algebra 3, Grp: CM ., Salle: Salle 4". Fields written "Key: value"
    keep their value; stray punctuation goes; a title without fields is unchanged."""
    fields = [f.strip() for f in summary.split(",")]
    if len(fields) < 2 or not any(re.match(r"^[\w ]{1,20}:\s", f) for f in fields[1:]):
        return summary.strip()
    shown: list[str] = []
    for part in fields:
        key, sep, value = part.partition(":")
        text = value if sep and len(key) <= 20 and value.strip() else part
        text = text.strip(" .-·")
        if text.casefold().startswith("salle ") and shown and shown[-1].casefold().startswith("salle"):
            continue
        if text:
            shown.append(text)
    return " · ".join(shown)


# The most days the calendar draws at once: a month's six weeks (D79); the strip
# that scrolls sideways asks for a week shown and a week each side (D68).
CALENDAR_MOST_DAYS = 42


def calendar_week_count(source: CalendarReport | PlanReport) -> int:
    days = source.settings.horizon_days if isinstance(source, PlanReport) else source.days
    return max(1, math.ceil(days / 7))


def week_view(plan: PlanReport, week: int, first_hour: int = 7, last_hour: int = 23) -> list[dict]:
    """Half-hour rows by day columns for one week: "" free, the busy event's name,
    "STUDY: subject", or "EXAM: subject". An exam wins over a session, a session
    over a busy event."""
    if not 0 <= week < week_count(plan):
        raise InvalidInput(f"week must be 0 to {week_count(plan) - 1}")
    zone = _zone(plan.settings.tz)
    first_day = plan.settings.start + timedelta(days=7 * week)
    days = [
        first_day + timedelta(days=d)
        for d in range(7)
        if (first_day + timedelta(days=d) - plan.settings.start).days < plan.settings.horizon_days
    ]
    columns = {d: d.strftime("%a %d %b") for d in days}
    cells: dict[tuple[date, time], str] = {}

    def paint(start: datetime, end: datetime, label: str) -> None:
        moment = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)
        while moment < end:
            local = moment.astimezone(zone)
            if local.date() in columns:
                cells[(local.date(), local.time())] = label
            moment += timedelta(minutes=30)

    for event in plan.events:
        if event.source != "skipped":
            paint(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), event.summary)
    for session in plan.sessions:
        paint(
            datetime.fromisoformat(session.start),
            datetime.fromisoformat(session.end),
            f"STUDY: {session.subject}",
        )
    for subject in plan.subjects:
        exam = datetime.fromisoformat(subject.exam)
        paint(exam, exam + timedelta(minutes=30), f"EXAM: {subject.name}")

    rows = []
    for minutes in range(first_hour * 60, last_hour * 60, 30):
        clock = time(minutes // 60, minutes % 60)
        row = {"time": clock.strftime("%H:%M")}
        row.update({columns[d]: cells.get((d, clock), "") for d in days})
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Calendars from a link, and plans published as a feed
# --------------------------------------------------------------------------- #


# A page or server that runs on the person's own machine may read links on their own
# network; one that fetches links for strangers must not (see `cps.sources`).
LINKS_MAY_BE_PRIVATE = os.environ.get("CPS_ALLOW_PRIVATE_LINKS") == "1"


def fetch_calendar(url: str, *, allow_private: bool | None = None) -> bytes:
    """The calendar behind a link (an ADE export address, Google's secret iCal
    address, a `webcal://` link), as bytes for `analyse_calendar`. The link must be
    on the public internet unless `allow_private`, which defaults to the
    `CPS_ALLOW_PRIVATE_LINKS` environment variable; see `cps.sources`."""
    if allow_private is None:
        allow_private = LINKS_MAY_BE_PRIVATE
    try:
        return _fetch(url, allow_private=allow_private)
    except SourceError as exc:
        raise UnreadableLink(str(exc)) from exc


FEED_REFRESH = timedelta(hours=6)
# Where feeds are kept and the address the feed server answers on: the same
# machine and port unless a deployment says otherwise.
# The store is one SQLite file (`cps.store`); a directory means cps.sqlite inside it.
FEED_STORE = Path(os.environ.get("CPS_DB") or os.environ.get("CPS_FEED_DIR") or Path.home() / ".cps")
FEED_URL = os.environ.get("CPS_FEED_URL") or "http://localhost:8765"
# Kept 12 months after the student last opened it, then deleted (D36; until
# 2026-10 it was 30 days after the last exam, D6, which lost a student's history
# between two semesters and never deleted a plan without an exam, AUDIT item 52).
RETENTION = timedelta(days=365)
# What a student can report about a session.
REPORTS = ("done", "skipped", "struggled", "forgot", "some", "most", "all")
# A self-test's answer to "how much could you recall, without your notes?" (D25),
# as FSRS's four grades: the forecast then uses what the student recalled instead
# of assuming every session went well.
RECALL_GRADES = {"forgot": "again", "some": "hard", "most": "good", "all": "easy"}
_GRADES = {"again": Grade.AGAIN, "hard": Grade.HARD, "good": Grade.GOOD, "easy": Grade.EASY}


def _grade(session: SessionView) -> Grade:
    """The grade a reported session gives its topic: the answer to the recall
    question when there is one, else good if recalled and again if forgotten."""
    if session.grade in _GRADES:
        return _GRADES[session.grade]
    return Grade.GOOD if session.outcome == "recalled" else Grade.AGAIN


@dataclass(frozen=True)
class Subscription:
    """A plan kept up to date for a calendar app to subscribe to.

    It holds what is needed to plan again: the timetable's link (or, for an
    uploaded file, the file itself), the person's own activities, the subjects and
    the settings, and the current plan. The token is the feed's only secret: whoever
    has the feed address can read the plan, as with any calendar subscription link.
    Personal data: a store keeps it in plain JSON files, and `delete_subscription`
    removes them.
    """

    token: str
    created: str
    source_url: str | None
    ics_text: str | None
    busy_rows: tuple[dict, ...]
    subjects: tuple[dict, ...]
    options: dict
    plan: dict | None = None
    refreshed: str | None = None
    error: str | None = None
    # What the student reported, by `session_id`: "done", "skipped" or "struggled".
    outcomes: dict = field(default_factory=dict)
    # When the student last opened the plan (UTC, ISO), counted once a day: what
    # the plan's deletion is counted from (`subscription_expiry`, D36).
    seen: str | None = None

    def to_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["busy_rows"] = [dict(r) for r in self.busy_rows]
        out["subjects"] = [dict(s) for s in self.subjects]
        out["outcomes"] = dict(self.outcomes)
        return out

    @classmethod
    def from_dict(cls, data: Mapping) -> Subscription:
        try:
            return cls(
                **{
                    **data,
                    "busy_rows": tuple(dict(r) for r in data["busy_rows"]),
                    "subjects": tuple(dict(s) for s in data["subjects"]),
                    "outcomes": dict(data.get("outcomes", {})),
                }
            )
        except (KeyError, TypeError) as exc:
            raise InvalidInput(f"not a subscription made by this version: {exc}") from exc


def _task_dict(task: TaskSpec) -> dict:
    due = task.due.isoformat() if isinstance(task.due, date | datetime) else task.due
    out: dict[str, Any] = {"name": task.name, "due": due, "hours": task.hours, "course": task.course}
    if task.done:
        out["done"] = True
    return out


def _spec_dict(spec: SubjectSpec) -> dict:
    exam = spec.exam.isoformat() if isinstance(spec.exam, date | datetime) else spec.exam
    return {
        "name": spec.name,
        "exam": exam,
        "familiarity": spec.familiarity,
        "stability": spec.stability,
        "difficulty": spec.difficulty,
    }


def new_subscription(
    *,
    subjects: Sequence[SubjectSpec],
    start: date,
    tz: str,
    source_url: str | None = None,
    ics: bytes | None = None,
    busy_rows: Iterable[BusyRow | Mapping] = (),
    study_window: tuple[float, float] = (8.0, 22.0),
    blocks_per_day: int = 2,
    block_minutes: int = 90,
    retention: float = 0.9,
    window: int = 4,
    lectures_as_topics: bool = True,
    engine: str = "assistant",
    tasks: Sequence[TaskSpec] = (),
    preferences: Mapping | None = None,
    plan: PlanReport | None = None,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    require_plan: bool = True,
    lang: str | None = None,
    blank: bool = False,
    quick_tests: bool = False,
) -> Subscription:
    """A new feed. With a `source_url`, the timetable is read again at every
    refresh; with `ics`, the file given now is the timetable for good. `plan`, if
    the caller has just made it from the same inputs, saves planning again.
    `blank` starts with nothing at all: the student draws their week first (D32).

    The calendar last read is kept (`ics_text`), also for a link, so that a
    feedback tap replans at once from it instead of waiting for the network."""
    if source_url is None and ics is None and not busy_rows and not blank:
        raise InvalidInput(_("give a calendar link, a calendar file or your week"))
    rows = tuple(_row_to_dict(BusyRow.parse(r)) for r in busy_rows)
    subscription = Subscription(
        token=secrets.token_urlsafe(24),
        created=(now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        source_url=source_url.strip() if source_url else None,
        ics_text=decode_ics(ics) if ics is not None else None,
        busy_rows=rows,
        subjects=tuple(_spec_dict(s) for s in subjects),
        options={
            "start": start.isoformat(),
            "tz": tz,
            "study_window": list(study_window),
            "blocks_per_day": blocks_per_day,
            "block_minutes": block_minutes,
            "retention": retention,
            "window": window,
            "lectures_as_topics": lectures_as_topics,
            "engine": engine,
            "tasks": [_task_dict(t) for t in tasks],
            "preferences": dict(preferences or {}),
            # A plan speaks the app's languages only: a visitor reading the front page
            # in Spanish (D193) gets their plan in English.
            "lang": pick(lang or current_language()),
            "quick_tests": quick_tests,
        },
    )
    if plan is not None:
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        return replace(subscription, plan=plan.to_dict(), refreshed=stamp)
    refreshed = refresh_subscription(subscription, now=now, fetch=fetch, reread=ics is None)
    if refreshed.plan is None and require_plan:
        raise ServiceError(refreshed.error or "the plan could not be made")
    return refreshed


def refresh_subscription(
    subscription: Subscription,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    reread: bool = True,
) -> Subscription:
    """`_refresh`, in the student's language: what each session says to do, and
    the plan's warnings, are written when the plan is made (D10). A background
    refresh has no request to take the language from."""
    with use(subscription.options.get("lang") or current_language()):
        return _refresh(subscription, now=now, fetch=fetch, reread=reread)


def _refresh(
    subscription: Subscription,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    reread: bool = True,
) -> Subscription:
    """Read the timetable again (unless `reread` is False) and plan from `now` on.

    Sessions of the current plan that are behind `now` count as done as planned;
    sessions the student reported (`report_session`) count as reported, whenever
    they were; and the plan goes on from the memory they leave. If the link cannot
    be read or the plan cannot be made, the previous plan stays and `error` says
    why, so that a feed never goes blank because a university server was down for
    an hour.
    """
    now = now or datetime.now(UTC)
    if reread:
        subscription = sync_deadlines(subscription, now=now, fetch=fetch)
    options = subscription.options
    cached = subscription.ics_text
    try:
        if subscription.source_url and (reread or cached is None):
            cached = decode_ics(fetch(subscription.source_url))
        ics = cached.encode("utf-8") if cached is not None else None
        report = analyse_calendar(
            ics,
            start=date.fromisoformat(options["start"]),
            tz=options["tz"],
            study_window=tuple(options["study_window"]),
            blocks_per_day=options["blocks_per_day"],
            block_minutes=options["block_minutes"],
            busy_rows=subscription.busy_rows,
            commute_minutes=int(options.get("commute_minutes", 0)),
            study_time=str(options.get("study_time", "any")),
            quick_tests=bool(options.get("quick_tests", False)),
            study_on_the_way=bool(options.get("study_on_the_way", False)),
            lecture_changes=tuple(options.get("lecture_changes", ())),
            lunch=lunch_hours(options),
        )
        done: tuple[SessionView, ...] = ()
        if subscription.plan is not None:
            previous = PlanReport.from_dict(subscription.plan)
            cut = _days_after(previous.settings.start, now.astimezone(_zone(previous.settings.tz)))
            reported = subscription.outcomes
            done = tuple(
                _as_reported(s, reported.get(session_id(s)), now)
                for s in previous.history + previous.sessions
                if s.start_day < cut or session_id(s) in reported
            )
        subjects = [SubjectSpec(**s) for s in subscription.subjects]
        if options.get("engine", "planner") == "assistant":
            plan = make_schedule(
                report,
                subjects,
                tasks=[TaskSpec(**t) for t in options.get("tasks", ())],
                retention=options["retention"],
                lectures_as_topics=options["lectures_as_topics"],
                done=done,
                now=now,
                pins=options.get("pins", ()),
                **options.get("preferences", {}),
            )
        else:
            plan = continue_plan(
                report,
                subjects,
                done=done,
                now=now,
                retention=options["retention"],
                window=options["window"],
                lectures_as_topics=options["lectures_as_topics"],
            )
    except ServiceError as error:
        return replace(subscription, error=str(error))
    # A pin behind now has done its work: its session is in the history.
    pins = [p for p in options.get("pins", ()) if datetime.fromisoformat(p["end"]) > now]
    return replace(
        subscription,
        options={**options, "pins": pins} if "pins" in options else options,
        ics_text=cached,
        plan=plan.to_dict(),
        refreshed=now.astimezone(UTC).isoformat(timespec="seconds"),
        error=None,
    )


def session_id(session: SessionView) -> str:
    """A short, stable name for a session: the same block and the same title give
    the same id from one plan to the next, so a link in a calendar event still
    finds its session after a refresh."""
    return hashlib.sha256(f"{session.start}|{session.title}".encode()).hexdigest()[:12]


# A self-test not reported this long after it ended counts as unknown: it stays on
# the calendar, but its topic gains no memory from it (DECISIONS.md D44). Until then
# it counts as done as planned, so the plan does not change the moment it ends.
UNREPORTED_AFTER = timedelta(days=1)
# Outcomes from which a topic gains no memory.
_NO_MEMORY = ("skipped", "unknown")


def _as_reported(session: SessionView, report: str | None, now: datetime | None = None) -> SessionView:
    """A session with the outcome the student reported. "struggled" is a lapse for
    something to remember (it comes back sooner) and done for a task or practice
    (a task gets one more block instead, in `report_session`). A self-test with no
    report a day after it ended (`now` given) is "unknown": students judge what they
    know too kindly already, and a plan that took silence for success would show a
    student who stopped reporting as perfectly on track (ENGINE-REVIEW W4). A task or
    exam practice not reported still counts as done: no memory hangs on it."""
    if report is None:
        overdue = now is not None and datetime.fromisoformat(session.end) <= now - UNREPORTED_AFTER
        if overdue and session.kind in _REVIEW_KINDS:
            return replace(session, outcome="unknown", grade="")
        return session
    if report == "skipped":
        return replace(session, outcome="skipped")
    if report in RECALL_GRADES and session.kind in ("review", "first review"):
        grade = RECALL_GRADES[report]
        return replace(session, outcome="lapsed" if grade == "again" else "recalled", grade=grade)
    if report == "struggled" and session.kind in ("review", "first review"):
        return replace(session, outcome="lapsed")
    return replace(session, outcome="recalled")


def find_session(subscription: Subscription, sid: str) -> SessionView | None:
    """The session called `sid` in the current plan, done or to come."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    return next((s for s in plan.history + plan.sessions if session_id(s) == sid), None)


def report_session(
    subscription: Subscription, sid: str, report: str, *, now: datetime | None = None
) -> Subscription:
    """Record what happened at a session and plan again at once, from the calendar
    last read: no network, so a tap is answered immediately."""
    if report not in REPORTS:
        raise InvalidInput(_("a session is done, skipped or hard, not {value}", value=repr(report)))
    session = find_session(subscription, sid)
    if session is None:
        raise InvalidInput(_("this session is no longer in your plan; the plan has changed since"))
    if report in RECALL_GRADES and session.kind not in ("review", "first review"):
        raise InvalidInput(
            _("only a self-test asks how much you recalled; this one is done, skipped or hard")
        )
    now = now or datetime.now(UTC)
    settings = PlanReport.from_dict(subscription.plan or {}).settings
    if session.start_day > _days_after(settings.start, now.astimezone(_zone(settings.tz))):
        # What happens at a session is known once it has started. Skipping one ahead
        # of time is blocking its time out, which is an activity, not a report.
        raise InvalidInput(_("this session has not started yet; report it once it has"))
    options = subscription.options
    if report == "struggled" and session.kind == "task":
        block_hours = options["block_minutes"] / 60
        options = {
            **options,
            "tasks": [
                {**t, "hours": t["hours"] + block_hours} if t["name"] == session.title else t
                for t in options.get("tasks", ())
            ],
        }
    changed = replace(subscription, options=options, outcomes={**subscription.outcomes, sid: report})
    return refresh_subscription(changed, now=now, reread=False)


def _local(value: str | datetime, zone: ZoneInfo) -> datetime:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)


def move_session(
    subscription: Subscription, sid: str, start: str | datetime, *, now: datetime | None = None
) -> Subscription:
    """Put a session the student has not started at another time (`start`, local
    when naive). It stays there, pinned, whatever the rules say; what it studies is
    not planned again between its old and its new time; the rest of the plan is
    made again around it at once. Refused, with a sentence saying why, when the new
    time is taken, past, or after the deadline or exam it prepares."""
    now = now or datetime.now(UTC)
    session = find_session(subscription, sid)
    if session is None or subscription.plan is None:
        raise InvalidInput(_("this session is no longer in your plan; the plan has changed since"))
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    old_start, old_end = _local(session.start, zone), _local(session.end, zone)
    if session not in plan.sessions or old_start <= now:
        raise InvalidInput(_("this session has already started; report it instead of moving it"))
    begin = _local(start, zone).replace(second=0, microsecond=0)
    end = begin + (old_end - old_start)
    if begin <= now:
        raise InvalidInput(_("that time is already past"))
    if _days_after(plan.settings.start, begin) >= plan.settings.horizon_days:
        raise InvalidInput(_("that is after the end of your plan"))
    for event in plan.events:
        if event.source == "skipped":
            continue
        lo, hi = _local(event.start, zone), _local(event.end, zone)
        if lo < end and begin < hi:
            raise InvalidInput(
                _(
                    "that time is taken: {what}, {start}–{end}",
                    what=short_title(event.summary),
                    start=f"{lo:%H:%M}",
                    end=f"{hi:%H:%M}",
                )
            )
    limit = None
    if session.kind == "task":
        task = next((t for t in plan.tasks if t.name == session.title), None)
        limit = (_local(task.due, zone), _("it is due")) if task else None
    else:
        subject = next((x for x in plan.subjects if x.name == session.subject), None)
        limit = (_local(subject.exam, zone), _("the exam")) if subject else None
    if limit is not None and end > limit[0]:
        raise InvalidInput(
            _(
                "that is after {limit} ({when})",
                limit=limit[1],
                when=f"{format_date(limit[0].date(), 'short')} {limit[0]:%H:%M}",
            )
        )
    if session.kind in ("review", "first review"):
        spec = next((x for x in plan.specs if x["name"] == session.title), None)
        if spec is not None and _days_after(plan.settings.start, begin) < spec.get("available_day", 0.0):
            raise InvalidInput(_("those lectures have not been taught yet at that time"))
    pins = [dict(p) for p in subscription.options.get("pins", ())]
    origin = session.start
    for p in list(pins):
        if p["title"] == session.title and _local(p["start"], zone) == old_start:
            origin = p.get("from") or origin
            pins.remove(p)
    for p in pins:
        if _local(p["start"], zone) < end and begin < _local(p["end"], zone):
            raise InvalidInput(
                _("that time is taken by another session you placed: {title}", title=p["title"])
            )
    pins.append(
        {
            "kind": session.kind,
            "title": session.title,
            "course": session.subject,
            "start": _iso(begin),
            "end": _iso(end),
            "from": origin,
        }
    )
    changed = replace(subscription, options={**subscription.options, "pins": pins})
    fresh = refresh_subscription(changed, now=now, reread=False)
    if fresh.error:
        raise InvalidInput(fresh.error)
    return fresh


def unpin_session(subscription: Subscription, sid: str, *, now: datetime | None = None) -> Subscription:
    """Give a moved session back to the planner: it goes wherever the rules put it."""
    session = find_session(subscription, sid)
    if session is None or subscription.plan is None:
        raise InvalidInput(_("this session is no longer in your plan; the plan has changed since"))
    zone = _zone(subscription.options["tz"])
    pins = [
        p
        for p in subscription.options.get("pins", ())
        if not (p["title"] == session.title and _local(p["start"], zone) == _local(session.start, zone))
    ]
    changed = replace(subscription, options={**subscription.options, "pins": pins})
    return refresh_subscription(changed, now=now, reread=False)


def _task_specs(subscription: Subscription) -> list[TaskSpec]:
    return [TaskSpec(**t) for t in subscription.options.get("tasks", ())]


def _find_task(subscription: Subscription, name: str) -> int:
    for i, t in enumerate(subscription.options.get("tasks", ())):
        if t["name"].casefold() == name.strip().casefold():
            return i
    raise InvalidInput(_("there is no task called {name} any more", name=repr(name)))


def add_task(subscription: Subscription, task: TaskSpec, *, now: datetime | None = None) -> Subscription:
    """A new deadline, planned at once (`revise_subscription`)."""
    if not task.name.strip():
        raise InvalidInput(_("give the task a name"))
    return revise_subscription(subscription, tasks=[*_task_specs(subscription), task], now=now)


def set_task_done(
    subscription: Subscription, name: str, done: bool = True, *, now: datetime | None = None
) -> Subscription:
    """Mark a task finished, or not. A finished task keeps its history but nothing
    more is planned for it, and the time it would have taken goes back to the rest
    of the plan; reopening it plans what is left again."""
    tasks = _task_specs(subscription)
    i = _find_task(subscription, name)
    tasks[i] = replace(tasks[i], done=done)
    return revise_subscription(subscription, tasks=tasks, now=now, strict=False)


def delete_task(subscription: Subscription, name: str, *, now: datetime | None = None) -> Subscription:
    """Remove a task altogether. One imported from a learning platform stays
    removed: the platform's next reading does not bring it back."""
    tasks = _task_specs(subscription)
    del tasks[_find_task(subscription, name)]
    steps = {k: v for k, v in _steps(subscription).items() if k != name.strip().casefold()}
    subscription = replace(subscription, options={**subscription.options, "steps": steps})
    return revise_subscription(subscription, tasks=tasks, now=now, strict=False)


# Assignments in steps (D145; the owner's pick, 2026-11-18: "steps"). A task is broken into
# the student's own steps, each ticked off as it is done: a specific plan for an unfinished
# goal (Masicampo and Baumeister, 2011). The steps are the student's words, not the
# planner's blocks: they change nothing in the plan, and the plan's sessions are counted
# beside them as before. Kept under the task's name, and deleted with it.
TASK_STEPS = 12  # the most a task holds
STEP_TEXT = 120  # a step's characters


def _steps(subscription: Subscription) -> dict[str, list[dict]]:
    return {k: [dict(s) for s in v] for k, v in subscription.options.get("steps", {}).items()}


def task_steps(subscription: Subscription, name: str) -> list[dict]:
    """A task's steps, in the order written: each its text and whether it is done."""
    return _steps(subscription).get(name.strip().casefold(), [])


def add_step(subscription: Subscription, name: str, text: str) -> Subscription:
    """A step at the end of a task's list."""
    _find_task(subscription, name)
    text = " ".join(str(text or "").split())[:STEP_TEXT]
    if not text:
        raise InvalidInput(_("write the step"))
    steps = _steps(subscription)
    key = name.strip().casefold()
    if len(steps.get(key, [])) >= TASK_STEPS:
        raise InvalidInput(_n("a task holds {n} step at most", "a task holds {n} steps at most", TASK_STEPS))
    steps[key] = [*steps.get(key, []), {"text": text, "done": False}]
    return replace(subscription, options={**subscription.options, "steps": steps})


def tick_step(subscription: Subscription, name: str, index: int, done: bool = True) -> Subscription:
    """A step done, or not; the task itself is ticked on its own."""
    steps = _steps(subscription)
    key = name.strip().casefold()
    if not 0 <= index < len(steps.get(key, [])):
        raise InvalidInput(_("this step is no longer there"))
    steps[key][index] = {**steps[key][index], "done": bool(done)}
    return replace(subscription, options={**subscription.options, "steps": steps})


def drop_step(subscription: Subscription, name: str, index: int) -> Subscription:
    steps = _steps(subscription)
    key = name.strip().casefold()
    if not 0 <= index < len(steps.get(key, [])):
        raise InvalidInput(_("this step is no longer there"))
    del steps[key][index]
    if not steps[key]:
        del steps[key]
    return replace(subscription, options={**subscription.options, "steps": steps})


def _step_rows(subscription: Subscription, name: str) -> dict:
    """A task's steps as its card shows them: each with its place, and how many are done."""
    steps = task_steps(subscription, name)
    return {
        "entries": [{"index": i, "text": x["text"], "done": bool(x["done"])} for i, x in enumerate(steps)],
        "done": sum(1 for x in steps if x["done"]),
        "count": len(steps),
        "full": len(steps) >= TASK_STEPS,
    }


# A class the student catches up on alone (D60) is due before the course's next
# class, looked for this far ahead; with none, a week after the class missed.
CATCH_UP_LOOKAHEAD = timedelta(days=14)


def change_lecture(
    subscription: Subscription,
    at: str,
    which: str,
    action: str,
    *,
    every_week: bool = False,
    exempt: bool = False,
    now: datetime | None = None,
) -> Subscription:
    """Change one class of the timetable (D60, D61), named by its start `at` and
    `which`, its title as the timetable gives it or that title's `lecture_ref`:

    - "skip" it (and, `every_week`, the same class every week from this one on), or
      skip it and study it on one's own ("self": a task as long as the class, due
      before the course's next one);
    - mark it as a test (D61): a pop-up "quiz" (an hour's preparation, a task due
      when it starts), a "mock" exam (it counts as exam practice), or a "partial"
      exam (an exam of its own, practised for; `exempt` when passing it takes its
      material off the final exam);
    - or "restore" it (a weekly skip is restored for every week; a task it brought,
      not yet finished, goes with it).

    The plan is made again at once."""
    now = now or datetime.now(UTC)
    options = subscription.options
    zone = _zone(options["tz"])
    if action not in (*LECTURE_ACTIONS, *TEST_KINDS, "restore"):
        raise InvalidInput(_("choose to skip the class, to study it on your own, or to keep it"))
    try:
        start = datetime.fromisoformat(at)
    except ValueError:
        raise InvalidInput(_("this class is not in your timetable any more")) from None
    if start.tzinfo is None:
        start = start.replace(tzinfo=zone)
    if action == "restore":
        # A test the student added (D194) is not in the timetable: restoring it removes it.
        added = [
            c
            for c in options.get("lecture_changes", ())
            if c.get("added")
            and datetime.fromisoformat(c["at"]) == start
            and which in (c["summary"], lecture_ref(c["summary"]))
        ]
        if added:
            return remove_test(subscription, added[0]["summary"], added[0]["at"], now=now)
    text = subscription.ics_text
    local = start.astimezone(zone)
    day = _events(text, (), local.date(), 1, options["tz"]) if text is not None else []
    event = next((e for e in day if e.start == start and which in (e.summary, lecture_ref(e.summary))), None)
    if event is None:
        raise InvalidInput(_("this class is not in your timetable any more"))
    summary = event.summary
    if any(d.when == start for d in find_deadlines(day)):
        raise InvalidInput(_("an exam is not skipped here: give it another date in Settings"))
    changes = [dict(c) for c in options.get("lecture_changes", ())]
    tasks = _task_specs(subscription)
    current = _lecture_change(changes, event, zone)
    if action == "restore":
        if current is None:
            raise InvalidInput(_("this class is not skipped"))
        changes = [c for c in changes if c != current]
        brought = current.get("task")
        tasks = [t for t in tasks if t.done or t.name != brought]
        return revise_subscription(subscription, tasks=tasks, lecture_changes=changes, now=now, strict=False)
    if start <= now:
        raise InvalidInput(_("this class has already begun"))
    if every_week and action == "self":
        raise InvalidInput(_("a class skipped every week is not caught up by itself: add the work as a task"))
    if every_week and action in TEST_KINDS:
        raise InvalidInput(_("a test is one class: mark each one"))
    if current is not None:
        if current.get("action") in TEST_KINDS:
            raise InvalidInput(_("this class is already marked as a test"))
        raise InvalidInput(_("this class is already skipped"))
    if every_week:
        change: dict = {
            "weekday": local.weekday(),
            "time": f"{local:%H:%M}",
            "from": local.date().isoformat(),
        }
    else:
        change = {"at": _iso(start)}
    change.update(summary=summary, action=action)
    course = course_of(summary) or short_title(summary)
    task: TaskSpec | None = None
    if action == "self":
        ahead = _events(text, (), local.date(), CATCH_UP_LOOKAHEAD.days + 1, options["tz"])
        later = [e.start for e in ahead if e.start > event.end and course_of(e.summary) == course_of(summary)]
        due = min(later, default=event.start + timedelta(days=7)).astimezone(zone)
        hours = max(0.5, math.ceil(event.duration / timedelta(minutes=30)) / 2)
        name = _("Catch up: {course}, class of {day}", course=course, day=format_date(local.date(), "short"))
        task = TaskSpec(name, f"{due:%Y-%m-%d %H:%M}", hours, course)
    elif action == "quiz":
        name = _(
            "Prepare for the quiz: {course}, {day}", course=course, day=format_date(local.date(), "short")
        )
        task = TaskSpec(name, f"{local:%Y-%m-%d %H:%M}", QUIZ_PREPARATION_HOURS, course)
    elif action == "partial":
        change["exempt"] = bool(exempt)
    if task is not None:
        taken = {t.name.casefold() for t in tasks}
        base, n = task.name, 2
        while task.name.casefold() in taken:
            task, n = replace(task, name=f"{base} ({n})"), n + 1
        tasks.append(task)
        change["task"] = task.name
    changes.append(change)
    return revise_subscription(subscription, tasks=tasks, lecture_changes=changes, now=now, strict=False)


# --------------------------------------------------------------------------- #
# Tests the timetable does not show (DECISIONS.md D194)
# --------------------------------------------------------------------------- #


def tested_courses(subscription: Subscription) -> list[str]:
    """The courses a student can say have tests the timetable does not show: those
    with an exam the plan prepares for, in the order of their colours."""
    if subscription.plan is None:
        return [s["name"] for s in subscription.subjects]
    plan = PlanReport.from_dict(subscription.plan)
    planned = {x.name.casefold() for x in plan.subjects}
    return [name for name in course_names(subscription) if name.casefold() in planned]


def _known_course(subscription: Subscription, course: str) -> str:
    wanted = course.strip().casefold()
    for name in tested_courses(subscription):
        if name.casefold() == wanted:
            return name
    raise InvalidInput(_("choose one of your courses with an exam"))


def set_quizzes(
    subscription: Subscription, course: str, on: bool, *, now: datetime | None = None
) -> Subscription:
    """Say that `course` has quizzes in class, announced or not (`on`), or not: its
    recent weeks are kept ready (`QUIZ_READY_RECALL` for `QUIZ_READY_DAYS` after each is
    taught). The plan is made again at once."""
    name = _known_course(subscription, course)
    prefs = dict(subscription.options.get("preferences", {}))
    ready = [c for c in prefs.get("quiz_ready") or () if c.casefold() != name.casefold()]
    if on:
        ready.append(name)
    prefs["quiz_ready"] = sorted(ready)
    asked = sorted({*(subscription.options.get("tests_asked") or ()), name})
    changed = replace(
        subscription, options={**subscription.options, "preferences": prefs, "tests_asked": asked}
    )
    return refresh_subscription(changed, now=now, reread=False)


def add_test(
    subscription: Subscription,
    course: str,
    day: str,
    at: str = "",
    *,
    until: str = "",
    exempt: bool = False,
    now: datetime | None = None,
) -> Subscription:
    """A midterm of `course` the timetable does not have, on `day` (YYYY-MM-DD) at `at`
    (HH:MM; 09:00 when left out) until `until` (`MIDTERM_LENGTH` later when left out): a
    partial exam (D61), practised for and kept free around (D183); `exempt` when
    passing it takes its material off the final. The plan is made again at once."""
    now = now or datetime.now(UTC)
    name = _known_course(subscription, course)
    zone = _zone(subscription.options["tz"])
    try:
        when = date.fromisoformat(day)
        begin = time.fromisoformat(at or "09:00")
        end_time = time.fromisoformat(until) if until else None
    except ValueError:
        raise InvalidInput(_("give the midterm's day, and its time if you know it")) from None
    start = datetime.combine(when, begin, tzinfo=zone)
    end = datetime.combine(when, end_time, tzinfo=zone) if end_time else start + MIDTERM_LENGTH
    if start <= now:
        raise InvalidInput(_("this day is already behind you"))
    if end <= start:
        raise InvalidInput(_("a test ends after it begins"))
    finals = (
        [
            datetime.fromisoformat(x.exam)
            for x in PlanReport.from_dict(subscription.plan).subjects
            if x.name.casefold() == name.casefold()
        ]
        if subscription.plan is not None
        else []
    )
    if finals and start >= max(finals):
        raise InvalidInput(_("a midterm comes before the course's exam"))
    changes = [dict(c) for c in subscription.options.get("lecture_changes", ())]
    if any(
        c.get("added") and datetime.fromisoformat(c["at"]) == start and c["summary"] == name for c in changes
    ):
        raise InvalidInput(_("this test is there already"))
    changes.append(
        {
            "at": _iso(start),
            "end": _iso(end),
            "summary": name,
            "action": "partial",
            "exempt": bool(exempt),
            "added": True,
        }
    )
    asked = sorted({*(subscription.options.get("tests_asked") or ()), name})
    changed = replace(subscription, options={**subscription.options, "tests_asked": asked})
    return revise_subscription(changed, lecture_changes=changes, now=now, strict=False)


def remove_test(
    subscription: Subscription, course: str, at: str, *, now: datetime | None = None
) -> Subscription:
    """Take back a test the student added (D194). The plan is made again at once."""
    changes = [dict(c) for c in subscription.options.get("lecture_changes", ())]
    try:
        when = datetime.fromisoformat(at)
    except ValueError:
        raise InvalidInput(_("this test is not in your plan")) from None
    kept = [
        c
        for c in changes
        if not (
            c.get("added")
            and c["summary"].casefold() == course.strip().casefold()
            and datetime.fromisoformat(c["at"]) == when
        )
    ]
    if len(kept) == len(changes):
        raise InvalidInput(_("this test is not in your plan"))
    return revise_subscription(subscription, lecture_changes=kept, now=now, strict=False)


def tests_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """Each course with an exam: whether it has quizzes in class, the midterms the
    student added (each with its day, time and whether it takes material off the final),
    and those of the timetable marked as partial exams (D61), for the page that asks."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    ready = {c.casefold() for c in subscription.options.get("preferences", {}).get("quiz_ready") or ()}
    asked = {c.casefold() for c in subscription.options.get("tests_asked") or ()}
    changes = subscription.options.get("lecture_changes", ())
    courses = []
    for name in tested_courses(subscription):
        added = sorted(
            (c for c in changes if c.get("added") and c["summary"].casefold() == name.casefold()),
            key=lambda c: c["at"],
        )
        marked = [
            c
            for c in changes
            if not c.get("added")
            and c.get("action") == "partial"
            and (course_of(c["summary"]) or short_title(c["summary"])).casefold() == name.casefold()
        ]
        courses.append(
            {
                "course": name,
                "quizzes": name.casefold() in ready,
                "asked": name.casefold() in asked,
                "midterms": [
                    {
                        "at": c["at"],
                        "day": format_date(_local(c["at"], zone).date(), "long"),
                        "time": f"{_local(c['at'], zone):%H:%M}",
                        "exempt": bool(c.get("exempt")),
                        "past": _local(c["at"], zone) <= now.astimezone(zone),
                    }
                    for c in added
                ],
                "marked": len(marked),
            }
        )
    return {
        "courses": courses,
        "ready_days": QUIZ_READY_DAYS,
        "ready_recall": round(QUIZ_READY_RECALL * 100),
        "today": now.astimezone(zone).date().isoformat(),
    }


def revise_subscription(
    subscription: Subscription,
    *,
    subjects: Sequence[SubjectSpec] | None = None,
    tasks: Sequence[TaskSpec] | None = None,
    busy_rows: Iterable[BusyRow | Mapping] | None = None,
    preferences: Mapping | None = None,
    now: datetime | None = None,
    deadlines_url: str | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    strict: bool = True,
    commute_minutes: int | None = None,
    study_time: str | None = None,
    quick_tests: bool | None = None,
    study_on_the_way: bool | None = None,
    warm_up: bool | None = None,
    lecture_changes: Sequence[Mapping] | None = None,
    day: Mapping | None = None,
    **options: Any,
) -> Subscription:
    """Change what the plan is made from (subjects, deadlines, activities, the trip
    to university, weekly hours and days off, or any of `new_subscription`'s
    settings) and plan again at
    once from the calendar last read. A new `deadlines_url` ("" removes it) is read
    at once, which is the only network access here. A change that leaves nothing to
    plan is refused, unless `strict` is False: busy times drawn before any exam is
    known are kept, with `error` saying what is missing."""
    merged = dict(subscription.options)
    unknown = set(options) - set(merged)
    if unknown:
        raise InvalidInput(f"unknown settings: {', '.join(sorted(unknown))}")
    merged.update(options)
    if day is not None:
        # When the student gets up, goes to bed and has lunch (D69): it sets the
        # study window too.
        merged.update(
            _day_options(
                day.get("wake"),
                day.get("bed"),
                str(day.get("lunch") or ""),
                day.get("lunch_start"),
                day.get("lunch_end"),
            )
        )
    if commute_minutes is not None:
        if not 0 <= int(commute_minutes) <= MAX_COMMUTE_MINUTES:
            raise InvalidInput(
                _("the trip to university must be 0 to {most} minutes", most=MAX_COMMUTE_MINUTES)
            )
        merged["commute_minutes"] = int(commute_minutes)
    if study_time is not None:
        if study_time not in STUDY_TIMES:
            raise InvalidInput(_("choose when you study best: morning, afternoon, evening or no preference"))
        merged["study_time"] = study_time
    if quick_tests is not None:
        merged["quick_tests"] = bool(quick_tests)
    if study_on_the_way is not None:
        merged["study_on_the_way"] = bool(study_on_the_way)
    if warm_up is not None:
        merged["warm_up"] = bool(warm_up)
    if lecture_changes is not None:
        merged["lecture_changes"] = [dict(c) for c in lecture_changes]
    if preferences is not None:
        merged["preferences"] = {**merged.get("preferences", {}), **preferences}
    if tasks is not None:
        merged["tasks"] = [_task_dict(t) for t in tasks]
    changed = replace(
        subscription,
        options=merged,
        subjects=subscription.subjects if subjects is None else tuple(_spec_dict(s) for s in subjects),
        busy_rows=subscription.busy_rows if busy_rows is None else _rows(busy_rows),
    )
    if deadlines_url is not None and deadlines_url.strip() != (_deadlines_link(merged) or ""):
        url = deadlines_url.strip() or None
        refusal = moodle_page_refusal(url) if url else None  # AUDIT 138
        if refusal:
            raise InvalidInput(_("the learning platform's link: {error}", error=refusal))
        # D175: the link carries Moodle's key to the student's calendar: kept sealed,
        # never shown again; its site, which is no secret, beside it.
        site = _MOODLE_EXPORT.match(url) if url else None
        changed = replace(
            changed,
            options={
                **changed.options,
                "deadlines_url": _sealed.seal(url) if url else None,
                "deadlines_site": site.group(1) if site else None,  # a Moodle's, for its pages
                "deadlines_host": (urlsplit(url).hostname or "") if url else None,
                "deadlines_error": None,
                "deadlines_read": None,
            },
        )
        if url:
            changed = sync_deadlines(changed, now=now, fetch=fetch)
            if changed.options.get("deadlines_error"):
                raise InvalidInput(
                    _("the learning platform's link: {error}", error=changed.options["deadlines_error"])
                )
    fresh = refresh_subscription(changed, now=now, reread=False)
    if fresh.error and strict:
        raise InvalidInput(fresh.error)
    return fresh


# A deadline from a learning platform says when, not how long: this is the guess
# until the student changes it, shown as a guess on the settings page.
DEFAULT_TASK_HOURS = 2.0


def sync_deadlines(
    subscription: Subscription,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
) -> Subscription:
    """Read the learning platform's calendar (`options["deadlines_url"]`, Moodle's
    export) and add each deadline not seen before as a task of
    `DEFAULT_TASK_HOURS`. The platform owns the dates: a deadline already imported
    follows its new date (an extension, say); the student owns the rest: the hours,
    the name, and a deadline removed from the list does not come back. Past
    deadlines are skipped. A link that cannot be read leaves the tasks as they
    were and says why in `options["deadlines_error"]`."""
    options = subscription.options
    if not options.get("deadlines_url"):
        return subscription
    url = _deadlines_link(options)
    if not url:
        # D175: sealed with a key the server no longer has (a new deployment's).
        return replace(
            subscription,
            options={**options, "deadlines_error": _("the link was lost on the server: paste it again")},
        )
    now = now or datetime.now(UTC)
    zone = _zone(options["tz"])
    try:
        found = find_assignments(decode_ics(fetch(url)), options["tz"])
    except ServiceError as error:
        return replace(subscription, options={**options, "deadlines_error": str(error)})
    except ValueError as error:
        return replace(subscription, options={**options, "deadlines_error": f"not a calendar ({error})"})
    tasks = [dict(t) for t in options.get("tasks", ())]
    imported: dict[str, str] = dict(options.get("imported", {}))
    names = {t["name"].casefold(): t for t in tasks}
    for a in found:
        key = f"{a.course}|{a.name}".casefold()
        due = a.due.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        if key in imported:
            task = names.get(imported[key].casefold())
            if task is not None and a.due > now:
                task["due"] = due
            continue
        if a.due <= now:
            continue
        name = a.name if a.name.casefold() not in names else f"{a.name} ({a.course})"
        if name.casefold() in names:
            continue
        task = {"name": name, "due": due, "hours": DEFAULT_TASK_HOURS, "course": a.course}
        tasks.append(task)
        names[name.casefold()] = task
        imported[key] = name
    return replace(
        subscription,
        options={
            **options,
            "tasks": tasks,
            "imported": imported,
            "deadlines_error": None,
            "deadlines_read": now.astimezone(UTC).isoformat(timespec="seconds"),  # D175
            # A link kept in clear before D175, sealed at its first read since.
            **(
                {}
                if _sealed.is_sealed(options.get("deadlines_url"))
                else {
                    "deadlines_url": _sealed.seal(url),
                    "deadlines_site": _deadlines_site(options),
                    "deadlines_host": _deadlines_host(options),
                }
            ),
        },
    )


def seal_with(store: str | os.PathLike) -> None:
    """Seal plans' secrets with the deployment's key (D175, `sealed.configure`): the
    web app and the feed's refresher call it as they start."""
    _sealed.configure(_store(store))


def _deadlines_link(options: Mapping) -> str | None:
    """The learning platform's calendar link in clear (D175): sealed since D175, in
    clear before; None without one or when its key is lost."""
    return _sealed.unseal(options.get("deadlines_url"))


def _deadlines_site(options: Mapping) -> str | None:
    """The Moodle site of the learning platform's calendar link: kept apart since D175,
    read from a link kept in clear before; None for a calendar that is not Moodle's."""
    if options.get("deadlines_site"):
        return str(options["deadlines_site"])
    found = _MOODLE_EXPORT.match(str(_deadlines_link(options) or "").strip())
    return found.group(1) if found else None


def _deadlines_host(options: Mapping) -> str:
    """Where the calendar link leads, to say it is linked without showing it."""
    if options.get("deadlines_host"):
        return str(options["deadlines_host"])
    return (urlsplit(_deadlines_link(options) or "").hostname or "").removeprefix("www.")


# What a student can block out on the calendar. Every kind is busy time to the
# planner; the kind only colours the block and names it until the student does.
ACTIVITY_KINDS = (
    # A class drawn by hand when there is no timetable file (D32): busy, and its
    # name is a course, with the course's colour and a place among the exams.
    ("class", "Class"),
    ("training", "Training"),
    ("commute", "Commute"),
    ("work", "Work"),
    ("timeoff", "Time off"),
    ("other", "Other"),
)


# The first-run questions about the week (D42). The trips offered, in minutes one
# way (0: classes from home, or none on campus); then what else fills a week, each
# a kind of busy time with the question's words and an example of a name.
COMMUTE_CHOICES = (0, 10, 20, 30, 45, 60, 90)
ROUTINE_QUESTIONS = (
    ("training", "Do you play a sport or train?", "Football"),
    ("work", "Do you have a job?", "Shifts at the café"),
    ("other", "Anything else every week?", "Music lessons"),
)
ROUTINE_ANSWERS = 2  # answers per question on the page; more go on the calendar
# What Spark says before each of these questions, while setting up (D70).
ROUTINE_SAYS = {
    "training": "Do you train or play a sport? Drag on the bar to draw when, then pick the days.",
    "work": "A job? Draw its hours the same way, or type them.",
    "other": "Anything else every week, like music lessons? Draw it too, or skip it. "
    "Then save, and I will plan around all of it.",
}
_ROUTINE_NAMES = {"training": "Training", "work": "Work", "other": "Busy"}
# The answers to "When do you study best?" (D45), in the order the page offers them.
STUDY_TIME_WORDS = (
    ("morning", "Morning"),
    ("afternoon", "Afternoon"),
    ("evening", "Evening"),
    ("any", "No preference"),
)


# The student's day (D69): when they get up and go to bed, and lunch. Study is
# planned from half an hour after getting up to half an hour before bed (a bed
# after midnight: until midnight, the planner's day); lunch is kept free every day
# unless the student would rather study then.
DAY_MARGIN = 0.5  # hours
LUNCH_DEFAULT = ("12:30", "13:30")
LUNCH_CHOICES = (("free", "Keep it free"), ("study", "I can study then"))
SHORTEST_DAY = 3.0  # hours between getting up and going to bed, at least


def _hours_of(text: object, what: str) -> float:
    try:
        moment = time.fromisoformat(str(text or "").strip())
    except ValueError:
        raise InvalidInput(_("{what}: give a time like 07:30", what=what)) from None
    return moment.hour + moment.minute / 60


def _clock_of(hours: float) -> str:
    minutes = round(hours * 60) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def lunch_hours(options: Mapping) -> tuple[str, str] | None:
    """Lunch's start and end when it is kept free (D69), else None."""
    lunch = options.get("lunch") or {}
    return (lunch["start"], lunch["end"]) if lunch.get("free") else None


def day_view(subscription: Subscription) -> dict:
    """When the student gets up and goes to bed, and lunch (D69). A plan made
    before these questions shows its study window widened by the margins."""
    options = subscription.options
    first, last = (float(x) for x in options["study_window"])
    lunch = options.get("lunch") or {}
    return {
        "wake": options.get("wake") or _clock_of(max(first - DAY_MARGIN, 0)),
        "bed": options.get("bed") or _clock_of(min(last + DAY_MARGIN, 24)),
        "lunch_start": lunch.get("start", LUNCH_DEFAULT[0]),
        "lunch_end": lunch.get("end", LUNCH_DEFAULT[1]),
        "lunch": ("free" if lunch.get("free") else "study") if lunch else "free",
        "lunch_choices": LUNCH_CHOICES,
    }


def _day_options(wake: object, bed: object, lunch: str, lunch_start: object, lunch_end: object) -> dict:
    """The options a day sets (D69), checked: the times, the study window they
    leave, lunch."""
    up = _hours_of(wake, _("Get up"))
    down = _hours_of(bed, _("Go to bed"))
    if down <= up:
        down += 24  # to bed after midnight
    first = math.ceil((up + DAY_MARGIN) * 4) / 4
    last = math.floor(min(down - DAY_MARGIN, 24.0) * 4) / 4
    if down - up < SHORTEST_DAY or last - first < SHORTEST_DAY - 2 * DAY_MARGIN:
        raise InvalidInput(
            _("leave at least three hours between getting up and going to bed, before midnight")
        )
    out: dict[str, Any] = {"wake": _clock_of(up), "bed": _clock_of(down), "study_window": [first, last]}
    if lunch not in dict(LUNCH_CHOICES):
        raise InvalidInput(_("say whether lunch is kept free"))
    begin = _hours_of(lunch_start, _("Lunch"))
    end = _hours_of(lunch_end, _("Lunch"))
    if end <= begin:
        raise InvalidInput(_("lunch must end after it starts"))
    out["lunch"] = {"start": _clock_of(begin), "end": _clock_of(end), "free": lunch == "free"}
    return out


def set_day(
    subscription: Subscription,
    *,
    wake: object,
    bed: object,
    lunch: str = "free",
    lunch_start: object = LUNCH_DEFAULT[0],
    lunch_end: object = LUNCH_DEFAULT[1],
    now: datetime | None = None,
    strict: bool = True,
) -> Subscription:
    """The student's day (D69), planned around at once."""
    day = {"wake": wake, "bed": bed, "lunch": lunch, "lunch_start": lunch_start, "lunch_end": lunch_end}
    return revise_subscription(subscription, day=day, now=now, strict=strict)


def routine_view(subscription: Subscription) -> dict:
    """What the questions about the week show: the trip's choices with the current
    one, the questions, the days, and how many busy times of each kind are there."""
    commute = int(subscription.options.get("commute_minutes", 0))
    counts: dict[str, int] = {}
    for r in subscription.busy_rows:
        counts[r.get("kind", "other")] = counts.get(r.get("kind", "other"), 0) + 1
    return {
        "commute": commute,
        "choices": sorted({*COMMUTE_CHOICES, commute}),
        "study_time": str(subscription.options.get("study_time", "any")),
        "study_times": STUDY_TIME_WORDS,
        "study_on_the_way": bool(subscription.options.get("study_on_the_way", False)),
        "questions": [
            {
                "kind": kind,
                "question": question,
                "example": example,
                "say": ROUTINE_SAYS[kind],
                "have": counts.get(kind, 0),
            }
            for kind, question, example in ROUTINE_QUESTIONS
        ],
        "answers": ROUTINE_ANSWERS,
        "weekdays": list(WEEKDAY_NAMES),
        "drawn": subscription.ics_text is None and subscription.source_url is None,
    }


def answer_routine(
    subscription: Subscription,
    *,
    commute_minutes: int,
    answers: Iterable[Mapping],
    now: datetime | None = None,
    study_time: str | None = None,
    study_on_the_way: bool | None = None,
    day: Mapping | None = None,
) -> Subscription:
    """The answers to the questions about the week (D42), planned around at once:
    the trip to university (`commute_events`), and each other answer as weekly busy
    times, one per day chosen, added to those already there. An answer left empty
    is no answer; one half given says what is missing.

    `answers` are `{"kind", "label", "days": ["Mon", ...], "start", "end"}`; `day`,
    if given, `{"wake", "bed", "lunch", "lunch_start", "lunch_end"}` (D69)."""
    rows = [dict(r) for r in subscription.busy_rows] + routine_rows(answers)
    return revise_subscription(
        subscription,
        busy_rows=rows,
        commute_minutes=commute_minutes,
        study_time=study_time,
        study_on_the_way=study_on_the_way,
        day=day,
        now=now,
        strict=False,
    )


def _rows(busy_rows: Iterable[BusyRow | Mapping]) -> tuple[dict, ...]:
    """Busy rows as stored: validated, with the kind the student chose kept."""
    kinds = {k for k, _ in ACTIVITY_KINDS}
    out = []
    try:
        for r in busy_rows:
            row = _row_to_dict(BusyRow.parse(r))
            kind = r.get("kind") if isinstance(r, Mapping) else None
            out.append({**row, "kind": kind if kind in kinds else "other"})
    except ValueError as exc:
        raise InvalidInput(str(exc)) from exc
    return tuple(out)


def subscription_expiry(subscription: Subscription) -> datetime:
    """When a subscription is deleted: `RETENTION` after the student last opened it
    (or made it, if never opened since). Exams no longer count: a plan between two
    semesters has none, and is kept (D36)."""
    return datetime.fromisoformat(subscription.seen or subscription.created) + RETENTION


def _expiry_of(data: Mapping) -> str | None:
    """`subscription_expiry` of a stored document, for the sweep, which checks
    documents saved under an older rule again before deleting them (D36)."""
    moment = data.get("seen") or data.get("created")
    if not moment:
        return None
    return (datetime.fromisoformat(str(moment)) + RETENTION).astimezone(UTC).isoformat(timespec="seconds")


def is_stale(subscription: Subscription, now: datetime | None = None, age: timedelta = FEED_REFRESH) -> bool:
    if subscription.refreshed is None:
        return True
    return (now or datetime.now(UTC)) - datetime.fromisoformat(subscription.refreshed) >= age


def feed_ics(subscription: Subscription, base_url: str | None = None) -> bytes:
    """The feed a calendar app reads: every session, done and to come, with stable
    identifiers and a request to be read again every `FEED_REFRESH`. With the web
    app's `base_url`, each event also says what to do and carries the link where the
    student reports how it went."""
    if subscription.plan is None:
        raise InvalidInput(_("this feed has no plan yet"))
    plan = PlanReport.from_dict(subscription.plan)
    if base_url is not None:

        def described(s: SessionView) -> SessionView:
            parts = [s.detail, _("Why: {why}", why=s.rationale) if s.detail else s.rationale]
            parts.append(_("Done, skipped or hard? {url}", url=session_url(base_url, subscription.token, s)))
            return replace(s, rationale="\n\n".join(p for p in parts if p))

        plan = replace(
            plan,
            history=tuple(described(s) for s in plan.history),
            sessions=tuple(described(s) for s in plan.sessions),
        )
    return export_ics(
        plan, include_history=True, uid_prefix=subscription.token[:8] + "-", refresh=FEED_REFRESH
    )


def session_url(base_url: str, token: str, session: SessionView) -> str:
    return f"{base_url.rstrip('/')}/s/{token}/{session_id(session)}"


def feed_url(base_url: str, token: str) -> str:
    """The address to subscribe to, under the feed server at `base_url`."""
    return f"{base_url.rstrip('/')}/feed/{token}.ics"


def save_subscription(store: str | os.PathLike, subscription: Subscription) -> None:
    """Write a subscription into the store (`cps.store`), with the date it expires."""
    data = subscription.to_dict()
    _store(store).put(subscription.token, data, _expiry_of(data))


def load_subscription(store: str | os.PathLike, token: str) -> Subscription | None:
    data = _store(store).get(token)
    return Subscription.from_dict(data) if data is not None else None


def update_subscription(
    store: str | os.PathLike, token: str, change: Callable[[Subscription], Subscription]
) -> Subscription | None:
    """Apply `change` to a stored subscription without losing a concurrent write
    (`Store.update`). None if it does not exist, or was deleted meanwhile."""

    def apply(data: dict) -> tuple[dict, str | None]:
        new = change(Subscription.from_dict(data)).to_dict()
        return new, _expiry_of(new)

    data = _store(store).update(token, apply)
    return Subscription.from_dict(data) if data is not None else None


def delete_subscription(store: str | os.PathLike, token: str) -> bool:
    return _store(store).delete(token)


@functools.lru_cache(maxsize=16)
def _open(path: str) -> Store:
    return Store(path)


_OPENING = threading.Lock()


def _store(store: str | os.PathLike) -> Store:
    """One `Store` per file, opened once (its schema is checked on opening). At the
    first start on a fresh disk the server's threads all ask at once, and the cache
    alone let each build its own (AUDIT 66): one thread opens it, the others wait."""
    with _OPENING:
        return _open(os.fspath(store))


def store_healthy(store: str | os.PathLike) -> bool:
    """Whether the store answers a read, for `/health` (AUDIT 88)."""
    try:
        return _store(store).healthy()
    except (OSError, sqlite3.Error):
        return False


def log_event(store: str | os.PathLike, token: str, kind: str, detail: str = "") -> None:
    """Record what happened to a plan (created, changed, a session reported), for the
    pilot's measures. No timetable content, no names."""
    _store(store).log(token, kind, detail)


# --------------------------------------------------------------------------- #
# What reaches the owner, and a plan's copy of its data (D43)
# --------------------------------------------------------------------------- #

FEEDBACK_KINDS = _inbox.FEEDBACK_KINDS


def send_feedback(
    store: str | os.PathLike, token: str, *, kind: str, body: str, page: str = "", now: datetime | None = None
) -> None:
    """A student's message to the owner, from the feedback form: what it is about,
    what they wrote, and the page they came from (without the plan's address)."""
    body = str(body or "").strip()
    if kind not in dict(FEEDBACK_KINDS):
        raise InvalidInput(_("choose what your message is about"))
    if len(body) < 3:
        raise InvalidInput(_("write a few words"))
    if len(body) > _inbox.MAX_FEEDBACK:
        raise InvalidInput(_("at most {n} characters, please", n=_inbox.MAX_FEEDBACK))
    _inbox.add_feedback(
        _store(store),
        token,
        kind=kind,
        body=body,
        page=page,
        lang=current_language(),
        now=now or datetime.now(UTC),
    )
    log_event(store, token, "feedback", kind)


def inbox_view(store: str | os.PathLike) -> dict:
    """The owner's page of students' messages and the error log, newest first."""
    words = dict(FEEDBACK_KINDS)
    messages = [{**m, "about": _(words.get(m["kind"], m["kind"]))} for m in _inbox.feedback(_store(store))]
    return {"messages": messages, "errors": _inbox.errors(_store(store))}


def page_of(path: str) -> str:
    """A page's path as the owner's log keeps it: a plan's address cut out."""
    return _inbox.page_of(path)


def mark_feedback_seen(store: str | os.PathLike, ident: int) -> None:
    _inbox.mark_seen(_store(store), ident)


def record_error(
    store: str | os.PathLike,
    *,
    source: str,
    route: str,
    kind: str,
    message: str,
    detail: str = "",
    now: datetime | None = None,
) -> None:
    """Count an error in the owner's log (`inbox.record_error`): never a plan's
    address, never more than a few lines."""
    _inbox.record_error(
        _store(store),
        source=source,
        route=route,
        kind=kind,
        message=message,
        detail=detail,
        now=now or datetime.now(UTC),
    )


def export_data(store: str | os.PathLike, token: str, now: datetime | None = None) -> dict:
    """Everything kept under a plan's address, in one document a student can save
    (D43; the right to a copy of one's data, D6): the plan itself (the timetable as
    read, the settings, the exams, the busy times, every report), its log, and each
    table's rows that are the plan's (`Store.export`), with the photos of its posts.
    Nothing here is someone else's: another student appears by their handle only."""
    where = _store(store)
    document = where.get(token)
    if document is None:
        raise InvalidInput(_("there is no plan at this address"))
    tables = where.export(token)
    names = []
    for post in tables["posts"]:
        post["data"] = json.loads(post["data"])
        names += [str(n) for n in post["data"].get("photos", [])]
        names += [str(f["name"]) for f in post["data"].get("pdfs", [])]  # D95
    photos = {}
    for name in names:
        path = where.photos / name
        if path.parent == where.photos and path.is_file():
            photos[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "exported": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        "about": "Everything this study planner keeps under your plan's address. "
        "The photos and PDFs are in base64, by the names your posts give.",
        "plan": document,
        **tables,
        "photos": photos,
    }


def log_visit(store: str | os.PathLike, token: str, now: datetime | None = None) -> bool:
    """A student opened their plan today; logged once a day (D15), and the plan is
    kept 12 months from today (`mark_seen`, D36)."""
    first = _store(store).log_daily(token, "visit")
    if first:
        mark_seen(store, token, now)
    return first


def mark_seen(store: str | os.PathLike, token: str, now: datetime | None = None) -> None:
    """The student is here: the plan's deletion moves to `RETENTION` from now. A
    calendar app reading the feed does not count; a person opening a page does."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")

    def seen(current: Subscription) -> Subscription:
        return current if (current.seen or "") >= stamp else replace(current, seen=stamp)

    with contextlib.suppress(Conflict):  # changed three times meanwhile: the next visit tries again
        update_subscription(store, token, seen)


def events(store: str | os.PathLike, token: str) -> list[dict]:
    return _store(store).events(token)


def sweep_subscriptions(store: str | os.PathLike, now: datetime | None = None) -> int:
    """Delete every subscription past its expiry (`subscription_expiry`), the
    sessions, links and plan-less accounts past theirs (D94), and what was counted of
    the app's use more than six months ago (D96)."""
    gone = _store(store).sweep(now, expiry=_expiry_of)
    _accounts.sweep_accounts(_store(store), now or datetime.now(UTC))
    _usage.sweep(_store(store), now or datetime.now(UTC))  # counts past six months (D96)
    return gone


def backup_subscriptions(
    store: str | os.PathLike, directory: str | os.PathLike, now: datetime | None = None
) -> Path:
    """A consistent dated copy of the store in `directory`; copies older than a week go."""
    return _store(store).backup_rotating(Path(directory), now=now)


# --------------------------------------------------------------------------- #
# Accounts that last (DECISIONS.md D35): passkeys and a recovery code
# --------------------------------------------------------------------------- #

# How long "Not now" hides the Today page's suggestion to keep the plan safe.
ACCOUNT_LATER = timedelta(days=14)


class SignInFailed(ServiceError):
    """A passkey or a recovery code that opens no plan, or a passkey that could not
    be added; the message is the student's, the reason is in the server's log."""


def device_name(user_agent: str) -> str:
    """The kind of device a passkey was added on, from the browser's own words,
    so the list in Settings can tell a phone's from a laptop's ("" if unclear)."""
    for needle, name in (
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("Android", "Android"),
        ("CrOS", "Chromebook"),
        ("Macintosh", "Mac"),
        ("Windows", "Windows"),
        ("Linux", "Linux"),
    ):
        if needle in (user_agent or ""):
            return name
    return ""


def _passkey_user(store: str | os.PathLike, subscription: Subscription) -> str:
    """The name a device shows for this plan's passkey: the handle in the network,
    else "Study plan" and the month it was made. Never the plan's address."""
    profile = social.get_profile(_store(store), subscription.token)
    if profile is not None:
        return f"@{profile.handle}"
    made = datetime.fromisoformat(subscription.created).date()
    return f"{_('Study plan')} · {format_date(made, 'month')}"


def passkey_options(
    store: str | os.PathLike, subscription: Subscription, *, rp_id: str, now: datetime | None = None
) -> dict:
    """What the browser needs to make a passkey for this plan (JSON)."""
    return _accounts.registration_options(
        _store(store),
        subscription.token,
        rp_id=rp_id,
        user_name=_passkey_user(store, subscription),
        now=now or datetime.now(UTC),
    )


def add_passkey(
    store: str | os.PathLike,
    token: str,
    answer: Mapping,
    *,
    origin: str,
    rp_id: str,
    user_agent: str = "",
    now: datetime | None = None,
) -> None:
    try:
        _accounts.register(
            _store(store),
            token,
            answer,
            origin=origin,
            rp_id=rp_id,
            device=device_name(user_agent),
            now=now or datetime.now(UTC),
        )
    except (_accounts.PasskeyError, ValueError, TypeError, KeyError) as error:
        raise SignInFailed(
            _("That passkey could not be added. Try again, or keep a recovery code instead.")
        ) from error
    log_event(store, token, "passkey", "added")


def signin_options(store: str | os.PathLike, *, rp_id: str, now: datetime | None = None) -> dict:
    return _accounts.signin_options(_store(store), rp_id=rp_id, now=now or datetime.now(UTC))


def sign_in_with_passkey(
    store: str | os.PathLike, answer: Mapping, *, origin: str, rp_id: str, now: datetime | None = None
) -> str:
    """The token of the plan this passkey opens."""
    now = now or datetime.now(UTC)
    try:
        token = _accounts.sign_in(_store(store), answer, origin=origin, rp_id=rp_id, now=now)
    except _accounts.UnknownPasskey as error:
        raise SignInFailed(_("This passkey belongs to a plan that no longer exists.")) from error
    except (_accounts.PasskeyError, ValueError, TypeError, KeyError) as error:
        raise SignInFailed(_("That did not work. Try again.")) from error
    if load_subscription(store, token) is None:
        raise SignInFailed(_("This passkey belongs to a plan that no longer exists."))
    log_event(store, token, "signin", "passkey")
    mark_seen(store, token, now)
    return token


def remove_passkey(store: str | os.PathLike, token: str, passkey: str) -> bool:
    gone = _accounts.remove_passkey(_store(store), token, passkey)
    if gone:
        log_event(store, token, "passkey", "removed")
    return gone


def make_recovery_code(store: str | os.PathLike, token: str, now: datetime | None = None) -> str:
    """A new recovery code for the plan, to show once; the old one stops working."""
    code = _accounts.new_recovery_code(_store(store), token, now or datetime.now(UTC))
    log_event(store, token, "recovery", "made")
    return code


def recover(store: str | os.PathLike, code: str, now: datetime | None = None) -> tuple[str, str]:
    """The plan a recovery code opens, and its new code: each code works once."""
    now = now or datetime.now(UTC)
    token = _accounts.use_recovery_code(_store(store), code)
    if token is None or load_subscription(store, token) is None:
        raise SignInFailed(_("No plan has this recovery code. Check it, or use your passkey."))
    log_event(store, token, "signin", "recovery")
    mark_seen(store, token, now)
    return token, make_recovery_code(store, token, now)


def account_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """How the plan can be found again: its passkeys, its recovery code, when it
    will be deleted, and whether the Today page suggests keeping it safe."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])

    def day(stamp: str | None) -> str:
        return format_date(datetime.fromisoformat(stamp).astimezone(zone).date(), "day") if stamp else ""

    keys = [
        {**k, "device": k["device"] or _("A device"), "added": day(k["created"]), "last": day(k["used"])}
        for k in _accounts.passkey_list(_store(store), subscription.token)
    ]
    recovery = _accounts.recovery_made(_store(store), subscription.token)
    later = subscription.options.get("account_later")
    deleted = subscription_expiry(subscription).astimezone(zone).date()
    # A plan of an e-mail account (D94) is found again by signing in: no nudge.
    owned = _accounts.owner_of(_store(store), subscription.token) is not None
    return {
        "passkeys": keys,
        "recovery": day(recovery) if recovery else None,
        "safe": bool(keys or recovery or owned),
        "owned": owned,
        "nudge": not (keys or recovery or owned)
        and (not later or now - datetime.fromisoformat(later) >= ACCOUNT_LATER),
        "deleted": format_date(deleted, "day") + f" {deleted.year}",
    }


def account_later(subscription: Subscription, now: datetime | None = None) -> Subscription:
    """ "Not now": the suggestion to keep the plan safe waits `ACCOUNT_LATER`."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    return replace(subscription, options={**subscription.options, "account_later": stamp})


# --------------------------------------------------------------------------- #
# Today's one message (DECISIONS.md D97)
# --------------------------------------------------------------------------- #

# What Today may say besides the plan, most pressing first. One shows at a time; the
# rest wait for the next visit or stay under "More". An exam just past asks first (the
# answer is wanted while it is fresh, D56), then the answer just given beside its
# forecast (D105), then, on the plan's first day with nothing left to do that day, a
# first short session (D106), then, once a first run's first session is done, the
# question about reminders (D107), then a first week without forgetting, the week after it (D130),
# then the end of the semester, then a holiday ahead (D184), then the question about
# counting what is used (D96), then a question that makes the plan fit (D98), then an
# insight (D80), then a tip the day calls for (D63).
MESSAGES = (
    "exam",
    "outcome",
    "repair",
    "first",
    "remind",
    "recalled",
    # Last week's friends' league (D189): where the student finished, said in the week after.
    "league",
    "semester",
    # A week without classes ahead (D184): rest or keep studying, asked once.
    "holiday",
    "usage",
    # Hours that fit the exams (D147): after the question about counting use, before the
    # questions that make the plan fit, which it waits a week for (`HOURS_AFTER_DAYS`).
    "hours",
    "fit",
    "insight",
    "tip",
)


FIRST_DAY_MINUTES = 25  # the first day's short session (D106), the timer's shortest goal


def first_day(
    subscription: Subscription, now: datetime | None = None, store: str | os.PathLike | None = None
) -> dict | None:
    """On the day the plan is made, if no session of it is left that day, a short one
    now (D106): a new student who set up on a Tuesday, the first review on Thursday,
    read "Nothing planned today" and had nothing to do. It offers the focus timer on
    the course of the first session ahead, so the streak starts the day they join;
    None on any other day, or when a session of the plan is still to come today."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    if datetime.fromisoformat(subscription.created).astimezone(zone).date() != today:
        return None
    if subscription.options.get("first_day_later") == today.isoformat():
        return None  # "Not now"
    if store is not None and any(s.start.date() == today for s in logged_sessions(store, subscription)):
        return None  # done already: a session logged today (AUDIT 90)
    plan = PlanReport.from_dict(subscription.plan)
    ahead = sorted(
        (s for s in plan.sessions if datetime.fromisoformat(s.end) > now),
        key=lambda s: s.start,
    )
    if any(datetime.fromisoformat(s.start).astimezone(zone).date() == today for s in ahead):
        return None
    names = course_names(subscription)
    course = ahead[0].subject if ahead else (names[0] if names else "")
    if not course:
        return None
    when = (
        format_date(datetime.fromisoformat(ahead[0].start).astimezone(zone).date(), "short") if ahead else ""
    )
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    color = colours.get(course.casefold())
    return {"course": course, "minutes": FIRST_DAY_MINUTES, "when": when, "color": color}


def first_day_later(subscription: Subscription, now: datetime | None = None) -> Subscription:
    """ "Not now" to the first day's short session (D106): Today says what comes next."""
    zone = _zone(subscription.options["tz"])
    today = (now or datetime.now(UTC)).astimezone(zone).date().isoformat()
    return replace(subscription, options={**subscription.options, "first_day_later": today})


# -- the first run (D107) ----------------------------------------------------------

# A new student's first run, after Strava's (D107): the timetable, who is studying,
# the courses, the class, who sees the sessions; then Today with one session, the
# session's summary, and the question about reminders once it is done. Each step
# after the account can be skipped. A plan made before D107 has no first run.
FIRST_RUN_STEPS = ("Timetable", "You", "Your courses", "Your class", "Sharing")
FIRST_RUN_CLASSMATES = 5  # the classmates the class step offers to follow
SHARE_DEFAULT = "followers"  # who sees a shared session unless the student chose (D107)
_CLASS_CODE = re.compile(r"[A-Za-z0-9_-]{12}")


def class_code(value: object) -> str:
    """A class link's code as an address or a form gives it (D104), else ""."""
    text = str(value or "").strip()
    return text if _CLASS_CODE.fullmatch(text) else ""


def begin_first_run(subscription: Subscription, code: object = "") -> Subscription:
    """A plan a new student just made starts its first run (D107); the class link
    they came with, if any, is what the class step offers."""
    state = {"step": "you", "class": class_code(code)}
    return replace(subscription, options={**subscription.options, "first_run": state})


def first_run(subscription: Subscription) -> dict | None:
    """The first run's state while it lasts (D107); None for a plan made before it
    and once its last question, about reminders, is answered."""
    state = subscription.options.get("first_run")
    return None if not isinstance(state, dict) or state.get("finished") else state


def first_run_at(subscription: Subscription, step: str) -> Subscription:
    """The first run moved on to `step` (D107); a plan without one is unchanged."""
    state = first_run(subscription)
    if state is None:
        return subscription
    return replace(subscription, options={**subscription.options, "first_run": {**state, "step": step}})


def finish_first_run(subscription: Subscription) -> Subscription:
    """The question about reminders answered (D107): the first run is over. Today's
    tour is left as it was: it shows once the first run reaches Today (D170)."""
    state = first_run(subscription)
    if state is None:
        return subscription
    return replace(subscription, options={**subscription.options, "first_run": {**state, "finished": True}})


def _touring(subscription: Subscription) -> bool:
    """Whether tours may show (D170): not while a first run's steps are still ahead
    (D107), and from the moment it reaches Today. Before D170 a first run stood in for
    every tour and its end marked Today's seen, so a new student never had one (AUDIT 116)."""
    state = first_run(subscription)
    return state is None or state.get("step") == "today"


def share_default(subscription: Subscription) -> str:
    """Who sees a session the student shares, unless they choose otherwise (D107)."""
    value = subscription.options.get("share")
    return value if value in social.VISIBILITIES else SHARE_DEFAULT


def set_share_default(subscription: Subscription, visibility: object) -> Subscription:
    """The first run's question, "Who sees your sessions?" (D107): each post can
    still say otherwise."""
    if visibility not in social.VISIBILITIES:
        raise InvalidInput(_("choose who sees it: only you, your followers, or everyone"))
    return replace(subscription, options={**subscription.options, "share": str(visibility)})


def studied_once(store: str | os.PathLike, subscription: Subscription) -> bool:
    """Whether the student has studied at all: a session logged in the diary, or one
    of the plan reported as done (D107)."""
    return bool(logged_sessions(store, subscription)) or any(
        s.confirmed for s in progress_sessions(subscription)
    )


def first_session(store: str | os.PathLike, subscription: Subscription) -> bool:
    """The diary's page after the first session of a first run: it opens as the
    session's summary, "First session done!", day 1 of the streak (D107)."""
    return first_run(subscription) is not None and not studied_once(store, subscription)


def remind_ask(store: str | os.PathLike, subscription: Subscription) -> bool:
    """Today's question after the first session (D107), until it is answered."""
    return first_run(subscription) is not None and studied_once(store, subscription)


def first_run_class_view(store: str | os.PathLike, subscription: Subscription) -> dict:
    """The class step (D107): the class link the student came with, the classmates
    their profile finds at the same university (D39), and whom they already follow
    or asked to; without a profile there is no one to follow with."""
    where = _store(store)
    state = first_run(subscription) or {}
    code = class_code(state.get("class"))
    me = social.get_profile(where, subscription.token)
    mates = []
    if me is not None:
        for person in social.classmates(where, subscription.token, limit=FIRST_RUN_CLASSMATES):
            status = social.follow_status(where, subscription.token, person.token)
            mates.append({**person.public(), "status": status or ""})
    return {
        "class": class_page_view(store, code) if code else None,
        "profile": me.public() if me else None,
        "classmates": mates,
    }


def first_run_remind_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict:
    """The question after the first session (D107): the plan's next session, and
    what turning reminders on needs (D38)."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    upcoming = None
    if subscription.plan is not None:
        plan = PlanReport.from_dict(subscription.plan)
        ahead = sorted(
            (s for s in plan.sessions if datetime.fromisoformat(s.start) > now), key=lambda s: s.start
        )
        if ahead:
            first = ahead[0]
            start = datetime.fromisoformat(first.start).astimezone(zone)
            end = datetime.fromisoformat(first.end).astimezone(zone)
            upcoming = {
                "course": first.subject,
                "when": f"{format_date(start.date(), 'short')} · {start:%H:%M}",
                "minutes": round((end - start).total_seconds() / 60),
            }
    return {
        "next": upcoming,
        "lead": reminder_lead(subscription),
        "reminders": reminders_view(store, subscription),
    }


def today_message(**candidates: object) -> str | None:
    """The one message Today shows: the first of `MESSAGES` that has something to say."""
    unknown = set(candidates) - set(MESSAGES)
    if unknown:
        raise ValueError(f"not a message of Today: {sorted(unknown)}")
    return next((name for name in MESSAGES if candidates.get(name)), None)


# --------------------------------------------------------------------------- #
# Making the plan fit, one question a day (DECISIONS.md D98)
# --------------------------------------------------------------------------- #

# What setting up used to ask before the first plan (D42, D45, D69, D33), asked once
# the plan is there: from the day after it was made, one a day on Today, each with
# "Not now". Its key, its title, what it says, where it leads (under the plan's
# address) and its button.
FIT_QUESTIONS = (
    (
        "week",
        "Make the plan fit your week",
        "Sport, a job, the trip to campus, when you study best: two minutes, and the plan works around them.",
        "/routine",
        "Answer",
    ),
    (
        "tests",
        "Tests your timetable doesn't show",
        "Quizzes in class, announced or not, or a midterm it leaves out: tell the plan, course by course, "
        "and it gets you ready for them.",
        "/tests",
        "Tell the plan",
    ),
    (
        "deadlines",
        "Bring in your deadlines",
        "Your learning platform's calendar link (Moodle) brings every assignment that is due, and the plan "
        "makes time for each.",
        "/settings/exams#deadlines",
        "Add the link",
    ),
    (
        "calendar",
        "Your sessions in your phone's calendar",
        "Subscribe once, and every session shows where you already look, kept up to date.",
        "/feed",
        "Set it up",
    ),
)
_FIT = {key: (title, says, path, button) for key, title, says, path, button in FIT_QUESTIONS}


def fit_question(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Today's question that makes the plan fit (D98), or None: not on the day the
    plan was made, not again on a day one was answered or put off, never one already
    in place (a learning platform's link given) or seen."""
    if subscription.plan is None:
        return None
    zone = _zone(subscription.options["tz"])
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    made = datetime.fromisoformat(subscription.created).astimezone(zone).date()
    state = subscription.options.get("fit") or {}
    if today <= made or state.get("on") == today.isoformat():
        return None
    settled = set(state.get("done") or ()) | set(state.get("later") or ())
    if subscription.options.get("deadlines_url"):
        settled.add("deadlines")
    # D194: asked once there is a course with an exam, and not once one was answered.
    if subscription.options.get("tests_asked") or not subscription.subjects:
        settled.add("tests")
    for key, title, says, path, button in FIT_QUESTIONS:
        if key not in settled:
            return {"key": key, "title": _(title), "says": _(says), "href": path, "button": _(button)}
    return None


def answer_fit(subscription: Subscription, key: str, go: bool, now: datetime | None = None) -> Subscription:
    """The student took the question (`go`) or put it off: either way it is settled,
    and the next one waits for tomorrow."""
    if key not in _FIT:
        raise InvalidInput(_("There is no such question."))
    zone = _zone(subscription.options["tz"])
    state = dict(subscription.options.get("fit") or {})
    field = "done" if go else "later"
    state[field] = [*dict.fromkeys([*(state.get(field) or []), key])]
    state["on"] = (now or datetime.now(UTC)).astimezone(zone).date().isoformat()
    return replace(subscription, options={**subscription.options, "fit": state})


def fit_target(key: str) -> str:
    """Where a question leads, under the plan's address."""
    if key not in _FIT:
        raise InvalidInput(_("There is no such question."))
    return _FIT[key][2]


# --------------------------------------------------------------------------- #
# What a student uses, counted with their yes (DECISIONS.md D96)
# --------------------------------------------------------------------------- #

USAGE_KEPT_DAYS = _usage.KEPT.days


def usage_part(method: str, rest: str) -> str | None:
    """The part of the app a request to a plan's page is (`rest` is the address
    after `/p/<token>`), or None when it is not one the counts name."""
    return _usage.part_of(method, rest)


def usage_answer(store: str | os.PathLike, token: str) -> bool | None:
    """Whether the student said yes to counting what they use; None if not asked."""
    found = _usage.answer_of(_store(store), token)
    return found.yes if found else None


def usage_ask(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> bool:
    """Whether Today asks to count what is used (D96): once there is a plan and no answer,
    from the day after the plan was made, so a new student's first Today is their plan,
    not a question about data (DECISIONS.md D106)."""
    if subscription.plan is None or usage_answer(store, subscription.token) is not None:
        return False
    zone = _zone(subscription.options["tz"])
    made = datetime.fromisoformat(subscription.created).astimezone(zone).date()
    return (now or datetime.now(UTC)).astimezone(zone).date() > made


def answer_usage(store: str | os.PathLike, token: str, yes: bool, now: datetime | None = None) -> None:
    """Keep the student's answer. A no, now or later, deletes what was counted."""
    _usage.answer(_store(store), token, yes, now or datetime.now(UTC))
    log_event(store, token, "usage", "yes" if yes else "no")


def count_use(store: str | os.PathLike, token: str, part: str, now: datetime | None = None) -> bool:
    """One more use of `part` today, only if the student said yes."""
    return _usage.count(_store(store), token, part, now or datetime.now(UTC))


def usage_view(store: str | os.PathLike, token: str) -> dict:
    """A student's own counts, as they are kept: the parts used (on how many days,
    how many times, the last day) and those not opened since the yes."""
    found = _usage.answer_of(_store(store), token)
    used, unused = _usage.own(_store(store), token)
    return {
        "answer": found.yes if found else None,
        "since": format_date(datetime.fromisoformat(found.at).date()) if found and found.yes else None,
        "used": [
            {
                "label": _(u.label),
                "days": u.days,
                "times": u.times,
                "last": format_date(date.fromisoformat(u.last)) if u.last else None,
            }
            for u in used
        ],
        "unused": [_(u.label) for u in unused],
        "kept_days": USAGE_KEPT_DAYS,
    }


def usage_report(store: str | os.PathLike, now: datetime | None = None) -> dict:
    """For the owner: how many said yes and no, and for each part how many of those
    who said yes used it, on how many days and how often; the parts nobody used.
    Totals over students, never one student's row."""
    found = _usage.report(_store(store), now or datetime.now(UTC))
    return {
        "yes": found["yes"],
        "no": found["no"],
        "parts": [
            {
                "part": t.part,
                "label": t.label,
                "students": t.students,
                "days": t.days,
                "times": t.times,
                "last_7": t.last_7,
            }
            for t in found["parts"]
        ],
        "unused": found["unused"],
    }


# --------------------------------------------------------------------------- #
# E-mail accounts (DECISIONS.md D94)
# --------------------------------------------------------------------------- #

Account = _accounts.Account
SESSION_LIFE = _accounts.SESSION_LIFE
PASSWORD_MIN = _accounts.PASSWORD_MIN
PASSWORD_MAX = _accounts.PASSWORD_MAX


class AccountRefused(ServiceError):
    """An account that could not be made, opened or changed; the message says why."""


def _refused(reason: str) -> AccountRefused:
    return AccountRefused(
        {
            "email": _("That does not look like an e-mail address."),
            "short": _(
                "Choose a password of at least {n} characters: three or four words make a good one.",
                n=PASSWORD_MIN,
            ),
            "long": _("A password can have at most {n} characters.", n=PASSWORD_MAX),
            "common": _("That password is one of the most common: choose another."),
            "same": _("Choose a password that does not contain your e-mail address."),
            "taken": _(
                "An account with this e-mail address exists already: sign in, or choose a new "
                "password if you forgot it."
            ),
            "wrong": _("Wrong e-mail address or password."),
            "locked": _(
                "Too many wrong passwords: try again in a quarter of an hour, or choose a new password."
            ),
            "bound": _("This account has a plan already."),
        }.get(reason, _("Wrong e-mail address or password."))
    )


def sign_up(store: str | os.PathLike, email: str, password: str, now: datetime | None = None) -> Account:
    """A new account, before its plan (D94)."""
    normal = _accounts.normal_email(email)
    if normal is None:
        raise _refused("email")
    try:
        return _accounts.create_account(_store(store), normal, password, now or datetime.now(UTC))
    except _accounts.AccountError as error:
        raise _refused(error.reason) from error


def sign_in_with_password(
    store: str | os.PathLike, email: str, password: str, now: datetime | None = None
) -> Account:
    now = now or datetime.now(UTC)
    try:
        account = _accounts.check_password(_store(store), _accounts.normal_email(email) or "", password, now)
    except _accounts.AccountError as error:
        raise _refused(error.reason) from error
    if account.token:
        log_event(store, account.token, "signin", "password")
        mark_seen(store, account.token, now)
    return account


def open_session(
    store: str | os.PathLike, account: Account, now: datetime | None = None, device: str = ""
) -> str:
    """A device signed in to the account: the value its cookie keeps."""
    return _accounts.open_session(_store(store), account.id, now or datetime.now(UTC), device)


def signed_in(
    store: str | os.PathLike, cookie: str, now: datetime | None = None
) -> tuple[Account, bool] | None:
    """The account a cookie is signed in to, and whether to send the cookie again."""
    return _accounts.session_account(_store(store), cookie, now or datetime.now(UTC))


def sign_out(store: str | os.PathLike, cookie: str) -> None:
    _accounts.close_session(_store(store), cookie)


def sign_out_elsewhere(store: str | os.PathLike, account: Account, cookie: str) -> int:
    """Every other device signed in to the account is signed out; their number."""
    return _accounts.close_sessions(_store(store), account.id, keep=cookie)


def plan_owner(store: str | os.PathLike, token: str) -> str | None:
    """The account a plan belongs to; None for a plan of its address alone (D35)."""
    return _accounts.owner_of(_store(store), token)


def account_of(store: str | os.PathLike, account_id: str) -> Account | None:
    return _accounts.get_account(_store(store), account_id)


def give_plan(store: str | os.PathLike, account: Account, token: str) -> None:
    try:
        _accounts.give_plan(_store(store), account.id, token)
    except _accounts.AccountError as error:
        raise _refused(error.reason) from error


def plan_for_session(
    store: str | os.PathLike, token: str, account_id: str, now: datetime | None = None, device: str = ""
) -> str:
    """A passkey or recovery-code sign-in into a plan that belongs to an account: a
    session for that account, so its pages open (D94)."""
    account = _accounts.get_account(_store(store), account_id)
    assert account is not None
    return open_session(store, account, now, device)


def confirmation_mail(
    store: str | os.PathLike, account: Account, base_url: str, now: datetime | None = None
) -> _mail.Message:
    """The e-mail with the link that confirms the account's address."""
    value = _accounts.new_link(_store(store), account.id, "confirm", now or datetime.now(UTC))
    link = f"{base_url}/account/confirm/{value}"
    return _mail.Message(
        to=account.email,
        subject=_("Confirm your e-mail address"),
        body=_(
            "Hello,\n\nTo confirm that this is the address of your study plan's account, open this "
            "link within 7 days:\n\n{link}\n\nA confirmed address lets you choose a new password "
            "if you forget yours. If you did not make this account, ignore this e-mail.\n\nStudy plan",
            link=link,
        ),
    )


def reset_mail(
    store: str | os.PathLike, email: str, base_url: str, now: datetime | None = None
) -> _mail.Message | None:
    """The e-mail with the link that chooses a new password; None if no account has
    this address (the page says the same either way)."""
    normal = _accounts.normal_email(email)
    account = _accounts.account_by_email(_store(store), normal) if normal else None
    if account is None:
        return None
    value = _accounts.new_link(_store(store), account.id, "reset", now or datetime.now(UTC))
    link = f"{base_url}/account/reset/{value}"
    return _mail.Message(
        to=account.email,
        subject=_("Choose a new password"),
        body=_(
            "Hello,\n\nSomeone, hopefully you, asked to choose a new password for your study plan. "
            "To choose it, open this link within two hours:\n\n{link}\n\nIf it was not you, ignore "
            "this e-mail: your password stays as it is.\n\nStudy plan",
            link=link,
        ),
    )


def link_for(
    store: str | os.PathLike, value: str, purpose: str, now: datetime | None = None
) -> Account | None:
    """The account a link from an e-mail is for, while it is good; not spent."""
    return _accounts.link_account(_store(store), value, purpose, now or datetime.now(UTC), use=False)


def confirm_address(store: str | os.PathLike, value: str, now: datetime | None = None) -> Account | None:
    now = now or datetime.now(UTC)
    account = _accounts.link_account(_store(store), value, "confirm", now, use=True)
    if account is not None:
        _accounts.confirm(_store(store), account.id, now)
        if account.token:
            log_event(store, account.token, "account", "confirmed")
    return account


def reset_password(
    store: str | os.PathLike, value: str, password: str, now: datetime | None = None
) -> Account:
    """A new password from a link sent by e-mail: every device is signed out, and the
    address counts as confirmed (the link reached it)."""
    now = now or datetime.now(UTC)
    account = _accounts.link_account(_store(store), value, "reset", now, use=False)
    if account is None:
        raise AccountRefused(_("This link has expired or was used already. Ask for a new one."))
    try:
        _accounts.set_password(_store(store), account, password)
    except _accounts.AccountError as error:
        raise _refused(error.reason) from error
    _accounts.link_account(_store(store), value, "reset", now, use=True)
    _accounts.close_sessions(_store(store), account.id)
    _accounts.confirm(_store(store), account.id, now)
    if account.token:
        log_event(store, account.token, "account", "reset")
    return account


def change_password(
    store: str | os.PathLike,
    account: Account,
    current: str,
    new: str,
    cookie: str,
    now: datetime | None = None,
) -> int:
    """A new password, the current one given; the other devices are signed out (their
    number is returned)."""
    now = now or datetime.now(UTC)
    try:
        _accounts.check_password(_store(store), account.email, current, now)
    except _accounts.AccountError as error:
        raise AccountRefused(
            _("That is not your current password.")
            if error.reason == "wrong"
            else str(_refused(error.reason))
        ) from error
    try:
        _accounts.set_password(_store(store), account, new)
    except _accounts.AccountError as error:
        raise _refused(error.reason) from error
    if account.token:
        log_event(store, account.token, "account", "password")
    return _accounts.close_sessions(_store(store), account.id, keep=cookie)


def mailer_from_env() -> Callable[[_mail.Message], None] | None:
    """What sends the service's e-mail, from the environment (`cps.mail`); None if
    no SMTP server is named, and nothing is sent."""
    smtp = _mail.Smtp.from_env()
    return smtp.send if smtp is not None else None


def email_account_view(store: str | os.PathLike, account: Account) -> dict:
    """The account part of Settings: the address, whether it is confirmed, how many
    devices are signed in."""
    return {
        "email": account.email,
        "confirmed": account.confirmed is not None,
        "devices": len(_accounts.sessions_of(_store(store), account.id)),
        "password_min": PASSWORD_MIN,
    }


# --------------------------------------------------------------------------- #
# Reminders on the phone (DECISIONS.md D38)
# --------------------------------------------------------------------------- #


def reminder_lead(subscription: Subscription) -> int:
    """Minutes before each session its reminder comes; 0 when they are off."""
    lead = subscription.options.get("reminder_lead", _reminders.DEFAULT_LEAD)
    return int(lead) if lead in (0, *_reminders.LEADS) else _reminders.DEFAULT_LEAD


def set_reminder_lead(subscription: Subscription, minutes: int) -> Subscription:
    if minutes not in (0, *_reminders.LEADS):
        raise InvalidInput(_("a reminder comes 5, 10, 15, 30 or 60 minutes before, or not at all"))
    return replace(subscription, options={**subscription.options, "reminder_lead": minutes})


def reminders_view(store: str | os.PathLike, subscription: Subscription) -> dict:
    """What Settings show about reminders: the devices they are on, how long before
    a session, and the key a browser needs to turn them on."""
    zone = _zone(subscription.options["tz"])
    where = _store(store)
    return {
        "key": _reminders.public_key(_reminders.vapid_key(where)),
        "lead": reminder_lead(subscription),
        "leads": list(_reminders.LEADS),
        "devices": [
            {
                "id": d["id"],
                "device": d["device"] or _("A device"),
                "added": format_date(datetime.fromisoformat(d["created"]).astimezone(zone).date(), "day"),
            }
            for d in _reminders.devices(where, subscription.token)
        ],
    }


def add_push_device(
    store: str | os.PathLike, token: str, info: Mapping, *, user_agent: str = "", now: datetime | None = None
) -> str:
    try:
        ident = _reminders.add_device(
            _store(store), token, info, device=device_name(user_agent), now=now or datetime.now(UTC)
        )
    except _reminders.DeviceRefused as error:
        raise InvalidInput(_("This browser's notifications cannot be used for reminders here.")) from error
    log_event(store, token, "reminders", "on")
    return ident


def remove_push_device(store: str | os.PathLike, token: str, ident: str) -> bool:
    gone = _reminders.remove_device(_store(store), token, ident)
    if gone:
        log_event(store, token, "reminders", "off")
    return gone


def _push_subject(contact: str | None, base_url: str | None) -> str:
    """Who sends the pushes, as push services ask (VAPID's `sub`): the service's
    contact address, else its public address."""
    if contact and "@" in contact:
        return f"mailto:{contact.strip()}"
    if base_url and base_url.startswith("https://"):
        return base_url.rstrip("/")
    return "mailto:study-plan@localhost"


# Reminders that vary (D129). The title stays the course and the time; the body is one
# of a few true lines, rotated along the plan's sessions, so the same words do not come
# twice in a row when the same lines apply. In Duolingo's reminders, wording not seen
# recently moved behaviour more than wording seen often (Yancey and Settles 2020,
# docs/REFERENCES.md `yancey2020`); here it is a plain rotation, not their bandit, with no
# answers yet to learn from. No line asks for more than the plan does or speaks of losing
# a streak, and none is sent between going to bed and getting up (D69).
def _reminder_lines(
    subscription: Subscription,
    session: SessionView,
    start: datetime,
    lead: str,
    *,
    store: str | os.PathLike | None,
    plan: PlanReport,
    gains: Mapping[str, dict],
) -> list[str]:
    """The lines a session's reminder may say beside the plan's own: its stakes (a
    self-test, D128), that it opens with a question from the student's cards, and
    which of the day's sessions it is."""
    sid = session_id(session)
    lines = []
    gain = gains.get(sid)
    stakes = _stakes(gain, subscription.outcomes.get(sid), start.date())
    if stakes and gain:
        lines.append(
            _("Recalled now, it stays above 90 % until the exam (an estimate).")
            if stakes["to_exam"]
            else _(
                "Recalled now, it stays above 90 % until {day} (an estimate).",
                day=format_date(gain["holds"], "short"),
            )
        )
    if store is not None and opening_card(store, subscription, sid, start) is not None:
        lines.append(_("It opens with one question from your cards."))
    zone = start.tzinfo
    today = [
        s
        for s in sorted(plan.sessions, key=lambda x: x.start)
        if datetime.fromisoformat(s.start).astimezone(zone).date() == start.date()
    ]
    if len(today) > 1:
        k = 1 + next(i for i, s in enumerate(today) if session_id(s) == sid)
        lines.append(_("Session {k} of {n} planned today.", k=k, n=len(today)))
    return [f"{lead}. {line}" for line in lines]


def _reminder_payload(
    subscription: Subscription,
    session: SessionView,
    *,
    store: str | os.PathLike | None = None,
    plan: PlanReport | None = None,
    gains: Mapping[str, dict] | None = None,
) -> dict:
    """What a reminder says, in the plan's language: the course and the time, then
    the kind of session and its length with one line: the first sentence of what to
    do, or another true line in turn (D129). Tapping it opens the plan."""
    with use(subscription.options.get("lang") or current_language()):
        zone = _zone(subscription.options["tz"])
        start = datetime.fromisoformat(session.start).astimezone(zone)
        end = datetime.fromisoformat(session.end).astimezone(zone)
        minutes = round((end - start) / timedelta(minutes=1))
        what = session.detail.split(". ")[0].strip().rstrip(".")
        lead = f"{_(KIND_LABELS.get(session.kind, session.kind))} · {minutes} min"
        bodies = [f"{lead}. {what}." if what else lead]
        if plan is not None:
            bodies += _reminder_lines(
                subscription, session, start, lead, store=store, plan=plan, gains=gains or {}
            )
            ordinal = next(
                (
                    i
                    for i, s in enumerate(sorted(plan.sessions, key=lambda x: x.start))
                    if session_id(s) == session_id(session)
                ),
                0,
            )
            body = bodies[ordinal % len(bodies)]
        else:
            body = bodies[0]
        return {
            "title": _(
                "{course} at {time}",
                course=session.title if session.kind == "task" else session.subject,
                time=f"{start:%H:%M}",
            ),
            "body": body,
            "url": f"/p/{subscription.token}",
            "tag": session_id(session),
        }


def awake(subscription: Subscription, now: datetime) -> bool:
    """Whether `now` is between the student's getting up and going to bed (D69): no
    reminder comes outside it (D129)."""
    day = day_view(subscription)
    up = _hours_of(day["wake"], _("Get up"))
    down = _hours_of(day["bed"], _("Go to bed"))
    if down <= up:
        down += 24  # to bed after midnight
    local = now.astimezone(_zone(subscription.options["tz"]))
    hour = local.hour + local.minute / 60
    if hour < up:
        hour += 24
    return up <= hour < down


def send_due_reminders(
    store: str | os.PathLike,
    now: datetime | None = None,
    *,
    contact: str | None = None,
    base_url: str | None = None,
    send: _reminders.Sender = _reminders.send,
) -> int:
    """Send every reminder that is due now, once each; the number of devices
    reached. A device its push service no longer knows (404, 410) is forgotten.
    Run every minute by the web app (`cps web`)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    tokens = _reminders.tokens_with_devices(where)
    if not tokens:
        return 0  # nobody has reminders on: nothing to read, nothing to write
    reached = 0
    key = _reminders.vapid_key(where, now)
    subject = _push_subject(contact, base_url)
    for token in tokens:
        subscription = load_subscription(store, token)
        if subscription is not None:
            reached += _send_line_reminders(where, subscription, now, key, subject, send)
        lead = reminder_lead(subscription) if subscription is not None else 0
        if subscription is None or subscription.plan is None or not lead:
            continue
        if not awake(subscription, now):
            continue  # never between bed and getting up (D129); still due, it waits till then
        plan = PlanReport.from_dict(subscription.plan)
        gains: dict[str, dict] | None = None
        for session in plan.sessions:
            start = datetime.fromisoformat(session.start)
            if not _reminders.due(start, now, lead) or not _reminders.claim(
                where, token, session_id(session), now
            ):
                continue
            gains = session_gains(plan) if gains is None else gains
            payload = _reminder_payload(subscription, session, store=store, plan=plan, gains=gains)
            for device in _reminders.devices(where, token):
                status = send(device, payload, key, subject=subject, ttl=int((start - now).total_seconds()))
                if status in (404, 410):
                    _reminders.remove_device(where, token, device["endpoint"])
                reached += 200 <= status < 300
    _reminders.prune(where, now)
    return reached


TODO_REMIND_LATE = timedelta(minutes=5)  # D202: a line's reminder still goes this long after its hour


def _send_line_reminders(
    where: Store, subscription: Subscription, now: datetime, key: Any, subject: str, send: _reminders.Sender
) -> int:
    """D202: a to-do line written with an hour reminds at that hour, once, whatever the
    sessions' reminders are set to: the student asked for it. A server asleep then sends it
    up to five minutes late, never later."""
    zone = _zone(subscription.options["tz"])
    reached = 0
    for x in _mind(subscription):
        if not x.get("at") or x["done"]:
            continue
        at = datetime.fromisoformat(x["at"]).replace(tzinfo=zone)
        if not at <= now < at + TODO_REMIND_LATE:
            continue
        if not _reminders.claim(where, subscription.token, f"line-{x['id']}-{x['at']}", now):
            continue
        with use(subscription.options.get("lang") or current_language()):
            payload = {
                "title": x["text"],
                "body": _("From your to-do list, at {time}.", time=x["at"][11:16]),
                "url": f"/p/{subscription.token}/todo",
                "tag": f"line-{x['id']}",
            }
        for device in _reminders.devices(where, subscription.token):
            status = send(device, payload, key, subject=subject, ttl=300)
            if status in (404, 410):
                _reminders.remove_device(where, subscription.token, device["endpoint"])
            reached += 200 <= status < 300
    return reached


def send_test_reminder(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    contact: str | None = None,
    base_url: str | None = None,
    send: _reminders.Sender = _reminders.send,
) -> int:
    """A reminder now, to every device of the plan, to see that they arrive; the
    number of devices reached."""
    where = _store(store)
    with use(subscription.options.get("lang") or current_language()):
        payload = {
            "title": _("Reminders are on"),
            "body": _("This is how a reminder looks, before each study session."),
            "url": f"/p/{subscription.token}",
            "tag": "test",
        }
    key = _reminders.vapid_key(where)
    subject = _push_subject(contact, base_url)
    reached = 0
    for device in _reminders.devices(where, subscription.token):
        status = send(device, payload, key, subject=subject, ttl=600)
        if status in (404, 410):
            _reminders.remove_device(where, subscription.token, device["endpoint"])
        reached += 200 <= status < 300
    return reached


class Refresher:
    """Brings subscriptions up to date without making anyone wait: a refresh reads
    a timetable from the network and takes seconds, far longer than a calendar app
    or a page should wait, so it runs in a thread (unless `background` is False,
    for tests) and the next read gets the new plan. One refresh per subscription at
    a time. A report or a deletion arriving meanwhile wins: the result is written
    only over the version it started from (`update_subscription`), and a deleted
    subscription is not brought back."""

    def __init__(
        self,
        store: str | os.PathLike,
        *,
        background: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        fetch: Callable[[str], bytes] = fetch_calendar,
    ) -> None:
        self.store = store
        self.background = background
        self.clock = clock
        self.fetch = fetch
        seal_with(store)  # a refresh reads the sealed links (D175)
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def refresh(self, token: str) -> None:
        with self._lock:
            if token in self._running:
                return
            self._running.add(token)
        if self.background:
            threading.Thread(target=self._refresh, args=(token,), daemon=True).start()
        else:
            self._refresh(token)

    def _fresh(self, current: Subscription) -> Subscription:
        return refresh_subscription(current, now=self.clock(), fetch=self.fetch)

    def _refresh(self, token: str) -> None:
        try:
            update_subscription(
                self.store,
                token,
                lambda current: refresh_subscription(current, now=self.clock(), fetch=self.fetch),
            )
        except Conflict:
            pass  # kept changing: the next read tries again
        finally:
            with self._lock:
                self._running.discard(token)


# --------------------------------------------------------------------------- #
# The hosted product: a subscription, as its pages show it
# --------------------------------------------------------------------------- #

# What a new student starts with, before changing anything: the benchmark's week
# (benchmarks/rule_vs_planner.py). Stated defaults, not recommendations.
# Exam practice: None sizes it by the weeks each course was taught (D46); plans made
# before D46 keep the 4.5 hours stored with them.
DEFAULT_PREFERENCES: dict = {"weekly_hours": 15.0, "rest_days": ["Sun"], "practice_hours": None}
DEFAULT_FAMILIARITY = 3
# How far back the Today page asks about sessions.
REPORT_WINDOW = timedelta(days=7)

KIND_LABELS = {
    "first review": "First review",  # D97: the words of its own detail
    "review": "Self-test",
    "task": "Deadline work",
    "practice": "Exam practice",
}


def start_subscription(
    *,
    tz: str,
    source_url: str | None = None,
    ics: bytes | None = None,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    lang: str | None = None,
    draw: bool = False,
) -> Subscription:
    """The hosted product's first step: a timetable and nothing else. The link is
    read once; every exam found in it becomes a subject; the week starts from the
    stated defaults; and the plan is made if there is anything to plan for yet.
    With `draw` and neither a link nor a file, the timetable starts empty: the
    student draws their classes on the calendar, then gives the exams (D32)."""
    now = now or datetime.now(UTC)
    if source_url:
        ics = fetch(source_url.strip())
    if ics is None and not draw:
        raise InvalidInput(_("give your timetable's link or its file"))
    today = now.astimezone(_zone(tz)).date()
    found = analyse_calendar(ics, start=today, tz=tz).assessments if ics is not None else ()
    return new_subscription(
        subjects=[SubjectSpec(a.subject, None, familiarity=DEFAULT_FAMILIARITY) for a in found],
        start=today,
        tz=tz,
        source_url=source_url,
        ics=ics,
        engine="assistant",
        preferences=DEFAULT_PREFERENCES,
        now=now,
        require_plan=False,
        lang=lang,
        blank=ics is None,
        quick_tests=True,  # new plans use the gaps between classes (D48)
    )


# What a new semester puts away (D36). Everything else in the options stays: the
# week's hours and days off, the study window, the language, the appearance, AI,
# the Moodle link and what was imported from it.
_SEMESTER_ONLY = ("start", "tasks", "steps", "pins", "deadlines_error", "focus", "focus_draft", "topics")


def _archive(subscription: Subscription, now: datetime) -> list[dict]:
    """The sessions so far, of every semester, as progress counts them, to keep
    when the plan they belong to is put away: what has started, or was reported."""
    out = list(subscription.options.get("past", ()))
    for s in _plan_sessions(subscription):
        if s.start <= now or s.report:
            out.append(
                {
                    "start": s.start.isoformat(),
                    "minutes": s.minutes,
                    "kind": s.kind,
                    "course": s.course,
                    "report": s.report,
                    "sid": s.sid,
                }
            )
    return out


def start_next_semester(
    subscription: Subscription,
    *,
    source_url: str | None = None,
    ics: bytes | None = None,
    draw: bool = False,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    recap: Mapping | None = None,
) -> Subscription:
    """The same plan, for a new semester (D36): a new timetable (a link, a file, or
    drawn), its exams found as at the start; the week's settings kept. What was
    studied is kept too (`options["past"]`), so the streak, the weeks, the trends
    and a group's goal go on counting it; and so is everything kept under the plan's
    address: the profile, the people, the groups, the diary and the flashcards.

    `recap` is the semester just ended (D131, `recap_view`), kept with the others.

    Put away: the last semester's plan, exams, reports, moved sessions, deadlines
    already done or past, and the classes drawn by hand. Other activities drawn
    (work, training) stay, except one-off ones in the past."""
    now = now or datetime.now(UTC)
    if source_url:
        ics = fetch(source_url.strip())
    if ics is None and not draw:
        raise InvalidInput(_("give your timetable's link or its file"))
    options = subscription.options
    zone = _zone(options["tz"])
    local = now.astimezone(zone)
    found = analyse_calendar(ics, start=local.date(), tz=options["tz"]).assessments if ics is not None else ()
    tasks = [
        dict(t)
        for t in options.get("tasks", ())
        if not t.get("done")
        and datetime.fromisoformat(str(t["due"]).replace(" ", "T")) > local.replace(tzinfo=None)
    ]
    rows = tuple(
        r
        for r in subscription.busy_rows
        if r.get("kind") != "class"
        and (r.get("date") is None or date.fromisoformat(r["date"]) >= local.date())
    )
    kept = {k: v for k, v in options.items() if k not in _SEMESTER_ONLY}
    # The days each semester after the first began, for the Seasons badge (D189); a plan
    # carried over before D189 has its current semester's start as the first of them.
    begun = list(options.get("semesters", ()))
    if not begun and options.get("past") and options.get("start"):
        begun = [options["start"]]
    fresh = replace(
        subscription,
        source_url=source_url.strip() if source_url else None,
        ics_text=decode_ics(ics) if ics is not None else None,
        busy_rows=rows,
        subjects=tuple(
            _spec_dict(SubjectSpec(a.subject, None, familiarity=DEFAULT_FAMILIARITY)) for a in found
        ),
        options={
            **kept,
            "start": local.date().isoformat(),
            "semesters": [*begun, local.date().isoformat()][-12:],
            "tasks": tasks,
            "past": _archive(subscription, local),
            # The semester just ended, kept to be seen again (D131).
            "recaps": [*options.get("recaps", []), dict(recap)][-RECAPS_KEPT:]
            if recap
            else list(options.get("recaps", [])),
        },
        plan=None,
        refreshed=None,
        error=None,
        outcomes={},
    )
    return refresh_subscription(fresh, now=now, fetch=fetch, reread=ics is None)


def semester_over(subscription: Subscription, now: datetime | None = None) -> bool:
    """Whether every exam of the plan is behind and no deadline is ahead: the Today
    page then offers to start the next semester."""
    if subscription.plan is None:
        return False
    plan = PlanReport.from_dict(subscription.plan)
    if not plan.subjects:
        return False
    now = now or datetime.now(UTC)
    ahead = [datetime.fromisoformat(s.exam) for s in plan.subjects] + [
        datetime.fromisoformat(t.due) for t in plan.tasks if not t.done
    ]
    return all(moment < now for moment in ahead)


# The semester recap (D131): once every exam is behind, the semester in numbers the app
# already keeps, the student's own, shared only if they choose (the device's share
# sheet; nothing is posted). Kept when the next semester starts, so it can be seen
# again; the next semester starts clean (D36).
RECAPS_KEPT = 8


def recap_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """The semester just ended (D131), or None while an exam or a deadline is ahead:
    sessions kept and their hours, days studied, the memory days its reviews added,
    the topics that hold a month or more, the self-tests recalled against the odds
    (D125), the best streak, the best run studied together (D127), and the exams the
    student said how they went (D105). Every figure is one Progress already shows."""
    if subscription.plan is None or not semester_over(subscription, now):
        return None
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    start = plan.settings.start
    sessions = _progress.merge_logged(progress_sessions(subscription), logged_sessions(store, subscription))
    kept = [x for x in sessions if x.confirmed and start <= x.start.date() <= local.date()]
    model = model_of(plan.settings)
    _, gains, state = _memory_timeline(plan, model, [_days_after(start, local)])
    odds = session_gains(plan)
    beaten = sum(
        1
        for x in plan.history
        if x.kind in ("review", "first review")
        and x.grade in _GRADES
        and x.grade != "again"
        and odds.get(session_id(x), {}).get("chance", 100) < BEATEN_BELOW
    )
    first = min([start, *(x.start.date() for x in sessions)])
    paused = _progress.pause_days(pauses_of(subscription.options.get("preferences", {})))
    repaired = {date.fromisoformat(d) for d in subscription.options.get("repairs", {})}
    run = _progress.streak(sessions, first, local.date(), paused, repaired)
    pairs = together_view(store, subscription, now)["pairs"]
    exams = exam_outcomes(subscription, now)["exams"]
    minutes = sum(x.minutes for x in kept)
    return {
        "from": format_date(start, "day"),
        "to": format_date(local.date(), "day"),
        "ended": local.date().isoformat(),
        "sessions": len(kept),
        "hours": round(minutes / 60),
        "days": len({x.start.date() for x in kept}),
        "memory_days": round(sum(g for _, g in gains if g > 0)),
        "month_topics": sum(1 for name, (m, _) in state.items() if m.stability >= MONTH_DAYS),
        "topics": len(plan.specs),
        "beaten": beaten,
        "best_streak": run.best,
        "together": max((p["best"] for p in pairs), default=0),
        "exams": [{"course": e["course"], "says": e["says"]} for e in exams],
    }


def recaps_of(subscription: Subscription) -> list[dict]:
    """The recaps kept at the start of each next semester (D131), latest first."""
    return list(reversed(subscription.options.get("recaps", [])))


def recap_share_text(recap: Mapping) -> str:
    """The recap in a few lines, for the device's share sheet: the numbers only, no
    plan address, no courses' names (D131)."""
    lines = [
        _("My semester, {start} to {end}:", start=recap["from"], end=recap["to"]),
        _n("{n} study session", "{n} study sessions", recap["sessions"])
        + ", "
        + _n("{n} hour", "{n} hours", recap["hours"]),
        _n("{n} memory day built", "{n} memory days built", recap["memory_days"]),
        _n("Best streak: {n} day", "Best streak: {n} days", recap["best_streak"]),
    ]
    if recap["beaten"]:
        lines.append(_n("{n} recall against the odds", "{n} recalls against the odds", recap["beaten"]))
    return "\n".join(lines)


# A weekly budget offered from what is kept (DECISIONS.md D47): after this many
# full weeks in a row with fewer than this share of their sessions done, Today
# offers a budget equal to the hours kept, at least two blocks. "Not now" waits for
# as many full weeks again.
BUDGET_WEEKS = 3
BUDGET_KEPT_BELOW = 70  # percent
BUDGET_LEAST_BLOCKS = 2


def budget_offer(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """The weekly budget to offer a student whose plan is bigger than their weeks
    (D47), with what the plan predicts at the exams with it and with the budget
    they have; None when there is nothing to offer.

    Offered only when each of the last `BUDGET_WEEKS` full weeks had under
    `BUDGET_KEPT_BELOW`% of its past sessions done and at least one session
    reported (a student who reports nothing is asked to report, not told they kept
    nothing: D44), when the plan began before them, when the hours kept are fewer
    than the budget, and when "Not now" was not said since. Nothing changes until
    the student takes it."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    if plan.settings.engine != "assistant" or not plan.subjects:
        return None
    now = now or datetime.now(UTC)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    this_week = local.date() - timedelta(days=local.weekday())
    weeks = [this_week - timedelta(weeks=k) for k in range(BUDGET_WEEKS, 0, -1)]
    began = date.fromisoformat(subscription.options["start"])
    later = subscription.options.get("budget_later")
    # Full weeks of this plan only: not the week it began in, nor any before "Not now".
    if weeks[0] < began or (later and date.fromisoformat(later) >= weeks[0]):
        return None
    sessions = [s for s in progress_sessions(subscription) if s.planned]
    reported = {_progress.monday(s.start.date()) for s in sessions if s.report and s.start <= local}
    measured = _trends.weekly(sessions, (), weeks, local)
    if any(w.kept is None or w.kept >= BUDGET_KEPT_BELOW or w.first not in reported for w in measured):
        return None
    minutes = plan.settings.block_minutes
    kept = sum(w.hours for w in measured) / len(measured)
    hours = max(BUDGET_LEAST_BLOCKS, round(kept * 60 / minutes)) * minutes / 60
    current = plan.settings.preferences.get("weekly_hours")
    if current is not None and hours >= current - 1e-9:
        return None
    preferences = {**subscription.options.get("preferences", {}), "weekly_hours": hours}
    smaller = _refresh(
        replace(subscription, options={**subscription.options, "preferences": preferences}),
        now=now,
        reread=False,
    )
    if smaller.plan is None or smaller.error:
        return None
    offered = PlanReport.from_dict(smaller.plan)
    return {
        "hours": hours,
        "kept": round(kept, 1),
        "planned": round(sum(w.hours_planned for w in measured) / len(measured), 1),
        "weeks": [w.kept for w in measured],
        "ready": sum(s.topics_ready for s in offered.subjects),
        "ready_now": sum(s.topics_ready for s in plan.subjects),
        "topics": sum(s.topics for s in plan.subjects),
    }


# Hours that fit the exams (DECISIONS.md D147; the owner, 2026-11-19, after a last pass
# before each exam was measured and changed nothing): when the week's hours leave topics
# under the target on their exam day, Today says so with the model's numbers, and what a
# few more hours a week would keep, weeks ahead, so the hours can change before the exam
# weeks. More hours are tried a block at a time, up to `HOURS_STEPS` blocks; the offer is
# the fewest that keep the most. Not in the plan's first week (its questions about the
# week come first and change it, D98), not with no weekly limit,
# not while an exam is less than `HOURS_LEAD_DAYS` away for every course, not beside a
# smaller budget offered from what is kept (D47), and "Not now" waits `HOURS_LATER_DAYS`.
HOURS_STEPS = 4
HOURS_AFTER_DAYS = 7
HOURS_LEAD_DAYS = 7
HOURS_LATER_DAYS = 14


def hours_offer(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """The weekly hours that would keep more topics at the target on exam day (D147): the
    topics under it now, each step of more hours that keeps more (`steps`), and the fewest
    that keep the most (`hours`, `below`); None when more hours would keep no more, or when
    there is nothing to offer. Nothing changes until the student takes one."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    current = plan.settings.preferences.get("weekly_hours")
    if plan.settings.engine != "assistant" or not plan.subjects or current is None:
        return None
    now = now or datetime.now(UTC)
    zone = _zone(plan.settings.tz)
    today = now.astimezone(zone).date()
    made = datetime.fromisoformat(subscription.created).astimezone(zone).date()
    if today < made + timedelta(days=HOURS_AFTER_DAYS):
        return None
    later = subscription.options.get("hours_later")
    if later and today < date.fromisoformat(later) + timedelta(days=HOURS_LATER_DAYS):
        return None
    lead = timedelta(days=HOURS_LEAD_DAYS)
    if not any(datetime.fromisoformat(s.exam) - now >= lead for s in plan.subjects):
        return None
    topics = sum(s.topics for s in plan.subjects)
    ready_now = sum(s.topics_ready for s in plan.subjects)
    if ready_now >= topics or budget_offer(subscription, now) is not None:
        return None
    step = plan.settings.block_minutes / 60
    best: tuple[float, int] | None = None
    steps: list[dict] = []
    for k in range(1, HOURS_STEPS + 1):
        hours = float(current) + k * step
        preferences = {**subscription.options.get("preferences", {}), "weekly_hours": hours}
        tried = _refresh(
            replace(subscription, options={**subscription.options, "preferences": preferences}),
            now=now,
            reread=False,
        )
        if tried.plan is None or tried.error:
            break
        ready = sum(s.topics_ready for s in PlanReport.from_dict(tried.plan).subjects)
        if best is None or ready > best[1]:
            best = (hours, ready)
            # Each step that keeps more than the one before it, for the student to choose.
            if ready > ready_now:
                steps.append({"hours": hours, "below": topics - ready})
        if ready >= topics:
            break
    if best is None or best[1] <= ready_now:
        return None
    return {
        "hours_now": float(current),
        "below_now": topics - ready_now,
        "hours": best[0],
        "below": topics - best[1],
        "steps": steps,
        "topics": topics,
        "target": round(100 * plan.settings.retention),
    }


def hours_later(subscription: Subscription, now: datetime | None = None) -> Subscription:
    """ "Not now" to the hours offered (D147): asked again after `HOURS_LATER_DAYS`."""
    local = (now or datetime.now(UTC)).astimezone(_zone(subscription.options["tz"]))
    return replace(subscription, options={**subscription.options, "hours_later": local.date().isoformat()})


def budget_later(subscription: Subscription, now: datetime | None = None) -> Subscription:
    """ "Not now" to the budget offered (D47): asked again after `BUDGET_WEEKS` more
    full weeks."""
    local = (now or datetime.now(UTC)).astimezone(_zone(subscription.options["tz"]))
    week = local.date() - timedelta(days=local.weekday())
    return replace(subscription, options={**subscription.options, "budget_later": week.isoformat()})


def _when(moment: datetime, now: datetime) -> str:
    """A time as a person says it, relative to `now` (both local)."""
    days = (moment.date() - now.date()).days
    day = {0: _("Today"), 1: _("Tomorrow"), -1: _("Yesterday")}.get(days) or format_date(
        moment.date(), "short"
    )
    return f"{day} {moment:%H:%M}"


def _session_card(
    s: SessionView,
    zone: ZoneInfo,
    now: datetime,
    outcomes: Mapping[str, str],
    colours: Mapping[str, int] | None = None,
    gains: Mapping[str, dict] | None = None,
    syllabus: Mapping[tuple[str, str], list[str]] | None = None,
    courses: Sequence[str] = (),
) -> dict:
    start = datetime.fromisoformat(s.start).astimezone(zone)
    end = datetime.fromisoformat(s.end).astimezone(zone)
    sid = session_id(s)
    return {
        "gain": (gains or {}).get(sid),
        "memory": _memory_words((gains or {}).get(sid), outcomes.get(sid), zone, now),
        "stakes": _stakes((gains or {}).get(sid), outcomes.get(sid), start.date()),
        "self_test": s.kind in ("review", "first review"),
        "id": sid,
        # The course, or the deadline's name; which week of lectures is in `what`.
        "title": s.title if s.kind == "task" else s.subject,
        "topic": topic_words(s.topic.removeprefix(f"{s.subject} · ")) if s.kind != "task" and s.topic else "",
        "label": session_label(s),
        "kind": _(KIND_LABELS.get(s.kind, s.kind)),
        "when": _when(start, now),
        "day": _when(start, now).rsplit(" ", 1)[0],
        "time": f"{start:%H:%M}–{end:%H:%M}",
        "until": f"{end:%H:%M}",
        "what": s.detail,
        "why": s.rationale,
        "reported": outcomes.get(sid),
        "started": start <= now,
        "pinned": s.pinned,
        "color": (colours or {}).get(s.subject.casefold()),
        # What the syllabus says the week's lectures were about (D81), and where the
        # course's topics are kept.
        "syllabus": _syllabus_of(s, syllabus or {}),
        "course_index": next(
            (i for i, c in enumerate(courses) if s.kind != "task" and course_key(c) == course_key(s.subject)),
            None,
        ),
        # The week of lectures it reviews, for that week's own cards (D87).
        "week": _topic_monday(s.topic.removeprefix(f"{s.subject} · "), start.date())
        if s.kind in ("review", "first review") and s.topic
        else None,
    }


# The memory loop's verdict on an answered self-test (D125): the answer set against
# the model's chance of recalling it, and what it did to the topic's memory. "beaten"
# is a recall the model gave less than even odds; a second pass on the same day is
# said to add nothing, as the model's short-term term has it (CLAUDE.md, D125).
BEATEN_BELOW = 50

# Unannounced moments (D130): rare, true, never counted down to. A topic that a recall
# takes past a month of memory for the first time blooms on the answer's card; the
# first week in which every self-test answered was recalled is said once, the week
# after. In Deci, Koestner and Ryan's meta-analysis, rewards expected in advance lowered
# free-choice intrinsic motivation and positive feedback raised it (DECISIONS.md D130),
# so each of these is feedback on what the student did, and none is announced.
MONTH_DAYS = 30
RECALL_WEEK_LEAST = 3  # fewer answers than this make no week worth saying


def _memory_words(gain: Mapping | None, answer: str | None, zone: ZoneInfo, now: datetime) -> dict | None:
    """What an answered self-test did (D125): None before the answer, for a skipped
    session, and for anything but a self-test (`gain` carries the model's chance
    for those alone)."""
    if not gain or "chance" not in gain or answer not in RECALL_GRADES:
        return None
    if answer == "forgot":
        verdict = "forgot"
    elif gain["same_day"]:
        verdict = "same_day"
    elif gain["chance"] < BEATEN_BELOW:
        verdict = "beaten"
    else:
        verdict = "forecast"
    after = gain.get("stability", 0.0)
    return {
        "verdict": verdict,
        # An unannounced moment (D130): this answer took the topic past a month.
        "month": verdict in ("beaten", "forecast") and after - gain["added"] < MONTH_DAYS <= after,
        "chance": gain["chance"],
        "added": max(0, round(gain["added"])),
        "holds": format_date(gain["holds"], "short"),
        "next": _when(datetime.fromisoformat(gain["next"]).astimezone(zone), now) if gain["next"] else None,
    }


def _stakes(gain: Mapping | None, answer: str | None, day: date) -> dict | None:
    """A self-test's stakes before it starts (D128): its exam in so many days, and
    the day the topic stays above 90 % until if it is recalled in this session, by
    the model. None once answered or skipped, for a second pass on the same day
    (which adds nothing, D125) and for anything but a self-test."""
    if not gain or "exam" not in gain or answer or gain["same_day"]:
        return None
    days = max((gain["exam"] - day).days, 0)
    exam = _n("Exam in {n} day.", "Exam in {n} days.", days) if days else _("Exam today.")
    if gain["holds"] >= gain["exam"]:
        holds = _("Recalled in this session, it stays above 90 % until then (a model's estimate).")
    else:
        holds = _(
            "Recalled in this session, it stays above 90 % until {day} (a model's estimate).",
            day=format_date(gain["holds"], "short"),
        )
    return {
        "days": days,
        "to_exam": gain["holds"] >= gain["exam"],
        "holds": format_date(gain["holds"], "short"),
        "words": f"{exam} {holds}",
    }


def _topic_monday(topic: str, before: date) -> str | None:
    """The Monday of the week a topic names ("week of 05 Oct", `_week_label`), the
    last such Monday on or before `before`, ISO; None for another topic."""
    week = _WEEK_TOPIC.match(topic)
    if week is None:
        return None
    for year in (before.year, before.year - 1):
        try:
            monday = datetime.strptime(f"{week.group(1)} {year}", "%d %b %Y").date()
        except ValueError:
            return None
        if monday <= before and monday.weekday() == 0:
            return monday.isoformat()
    return None


def session_card(subscription: Subscription, sid: str, now: datetime | None = None) -> dict | None:
    """One session of the plan, done or to come, as the pages show it."""
    session = find_session(subscription, sid)
    if session is None or subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = (now or datetime.now(UTC)).astimezone(zone)
    return _session_card(
        session,
        zone,
        local,
        subscription.outcomes,
        gains=session_gains(plan),
        syllabus=_syllabus_index(subscription),
        courses=topic_courses(subscription),
    )


# How many course colours the pages have (CSS classes c0 to c6): enough to tell a
# semester's courses apart; beyond that, colours repeat. Red is kept for exams.
COURSE_PALETTE = 7


def course_colours(source: CalendarReport | PlanReport, drawn: Sequence[str] = ()) -> list[tuple[str, int]]:
    """Each course and its colour index: the subjects with an exam first, in the
    order of their exams, then the other courses of the timetable, then the classes
    the student drew (`drawn`, D32). A course's lectures and its study sessions
    share its colour."""
    names: list[str] = []
    if isinstance(source, PlanReport):
        names += [x.name for x in sorted(source.subjects, key=lambda x: x.exam)]
        names += sorted({t.course for t in source.tasks if t.course}, key=str.casefold)
    else:
        names += [a.subject for a in source.assessments]
    shown = ("calendar", "skipped", *TEST_KINDS)
    taught = {course_of(e.summary) for e in source.events if e.source in shown} - {""}
    names += sorted(taught, key=str.casefold)
    names += list(drawn)
    seen: dict[str, int] = {}
    ordered = []
    for name in names:
        if name.casefold() not in seen:
            seen[name.casefold()] = len(seen) % COURSE_PALETTE
            ordered.append((name, seen[name.casefold()]))
    return ordered


def drawn_courses(subscription: Subscription) -> list[str]:
    """The courses of the classes the student drew on the calendar (D32), each
    once, in the order first drawn."""
    seen: dict[str, str] = {}
    for row in subscription.busy_rows:
        label = str(row.get("label") or "").strip()
        if row.get("kind") == "class" and label:
            seen.setdefault(label.casefold(), label)
    return list(seen.values())


def course_names(subscription: Subscription) -> list[str]:
    """The courses a student can name a task after: those with an exam, then the
    other courses in the timetable and the classes drawn, in the order of their
    colours."""
    drawn = drawn_courses(subscription)
    if subscription.plan is None:
        names = [s["name"] for s in subscription.subjects]
        return names + [c for c in drawn if c.casefold() not in {n.casefold() for n in names}]
    return [name for name, _ in course_colours(PlanReport.from_dict(subscription.plan), drawn)]


# --------------------------------------------------------------------------- #
# Points and proof of work (D158)
# --------------------------------------------------------------------------- #

# A study session's points (the owner, 2026-11-19: "how many points it will give you if you
# do it ... and you get those points if you post what you did and your peers validate it like
# a proof of work, on top of an inherent bonus for completing it (because a lazy student could
# simply say they studied even if they have not)"). Doing it, on the student's word: 10. Its
# proof, a post saying or showing what was done that another student vouched for: 20 more.
POINTS_DONE = 10
POINTS_PROOF = 20
# The answers that say a session happened; "skipped" does not (D25, D44).
DONE_REPORTS = frozenset({"done", "struggled", "forgot", "some", "most", "all"})


def proof_posts(store: str | os.PathLike, token: str) -> list[dict]:
    """The student's session posts that prove a session (D158): each says what was done (a
    note or a line for anyone) or shows it (a photo), and at least one other student vouched
    for it, someone blocked either way not counted. Each as its session's id (a session
    timed with Focus, or shared from the calendar), its day and its course."""
    where = _store(store)
    posts = [
        p
        for p in social.posts_of(where, token, "session")
        if not p.hidden
        and (
            str(p.data.get("simple") or "").strip()
            or str(p.data.get("note") or "").strip()
            or p.data.get("photos")
        )
    ]
    vouched = social.checks_of(where, [p.id for p in posts], token)
    return [
        {"sid": str(p.data.get("sid") or ""), "day": p.day, "course": str(p.data.get("course") or "")}
        for p in posts
        if vouched.get(p.id, (0, False))[0] > 0
    ]


def session_points(
    items: Sequence[dict], outcomes: Mapping[str, str], proofs: Sequence[dict]
) -> dict[str, dict]:
    """Each study session's points (D158), by its id: what it can give (`possible`), what it
    gave (`earned`) and why (`state`: "to do", "done", "proven", "skipped"). A proof names its
    session, or, logged without one, the first session of its day and course not yet proven."""
    by_id = {p["sid"] for p in proofs if p["sid"]}
    loose = [(p["day"], p["course"].casefold()) for p in proofs if not p["sid"]]
    out: dict[str, dict] = {}
    for item in sorted(items, key=lambda x: x["at"]):
        sid = item["id"]
        proven = sid in by_id
        key = (item["at"][:10], str(item.get("course") or "").casefold())
        if not proven and key in loose:
            loose.remove(key)
            proven = True
        report = outcomes.get(sid)
        if proven:
            state, earned = "proven", POINTS_DONE + POINTS_PROOF
        elif report in DONE_REPORTS:
            state, earned = "done", POINTS_DONE
        elif report == "skipped":
            state, earned = "skipped", 0
        else:
            state, earned = "to do", 0
        out[sid] = {"possible": POINTS_DONE + POINTS_PROOF, "earned": earned, "state": state}
    return out


def calendar_view(
    subscription: Subscription,
    first: date | None = None,
    count: int = 7,
    now: datetime | None = None,
    proofs: Sequence[dict] = (),
) -> dict:
    """What the calendar page draws: `count` days from `first` (today by default)
    with the timetable, exams and study sessions (done and to come) as items, and
    the student's own busy times as editable activities, in the shape the week
    calendar widget takes. Works before there is a plan, from the timetable alone.
    Each study session carries its points and each day their sum (D158), from the
    student's reports and `proofs` (`proof_posts`)."""
    options = subscription.options
    zone = _zone(options["tz"])
    first = first or (now or datetime.now(UTC)).astimezone(zone).date()
    source: CalendarReport | PlanReport
    if subscription.plan is not None:
        source = PlanReport.from_dict(subscription.plan)
    else:
        source = analyse_calendar(
            subscription.ics_text.encode("utf-8") if subscription.ics_text is not None else None,
            start=date.fromisoformat(options["start"]),
            tz=options["tz"],
            days=max(7, (first - date.fromisoformat(options["start"])).days + count),
            # the classes drawn, for the trip around them; drawn rows themselves are
            # left out below (`typed=False`), the page draws them as activities
            busy_rows=subscription.busy_rows,
            commute_minutes=int(options.get("commute_minutes", 0)),
            lecture_changes=tuple(options.get("lecture_changes", ())),
            lunch=lunch_hours(options),
        )
    week = calendar_week(source, 0, typed=False, first=first, count=count, history=True)
    moment = (now or datetime.now(UTC)).astimezone(zone)
    legend = course_colours(source, drawn_courses(subscription))
    colours = {name.casefold(): index for name, index in legend}
    upcoming = (
        {session_id(x) for x in source.sessions}
        if isinstance(source, PlanReport) and source.settings.engine == "assistant"
        else set()
    )
    changes = tuple(options.get("lecture_changes", ()))
    changed = ("skipped", *TEST_KINDS)
    titles = {(e.start, lecture_ref(e.summary)): e.summary for e in source.events if e.source in changed}
    for item in week["items"]:
        course = str(item.get("course") or "").casefold()
        # Practice for a partial exam (D61) wears its course's colour.
        item["color"] = colours.get(
            course, next((v for k, v in colours.items() if course.startswith(f"{k},")), None)
        )
        if item["kind"] in changed:
            at = datetime.fromisoformat(item["at"])
            title = titles.get((item["at"], item["ref"]), "")
            change = _lecture_change(changes, BusyEvent(at, at, title), zone) or {}
            item["weekly"] = not change.get("at")
            item["catch_up"] = change.get("task")  # a catch-up (D60) or a quiz's preparation (D61)
            item["exempt"] = bool(change.get("exempt"))
        if item["kind"] in ("calendar", *changed):
            item["past"] = datetime.fromisoformat(item["at"]) <= moment
        if item["kind"] == "study":
            started = _local(item["at"], zone) <= moment
            item["started"] = started
            item["done"] = _local(item["until"], zone) <= moment
            item["reported"] = subscription.outcomes.get(item["id"])
            item["movable"] = not started and item["id"] in upcoming
    studies = [item for item in week["items"] if item["kind"] == "study"]
    points = session_points(studies, subscription.outcomes, proofs)
    for item in studies:
        item["points"] = points[item["id"]]
    for index, day in enumerate(week["days"]):
        mine = [points[item["id"]] for item in studies if item["day"] == index]
        day["points"] = {
            "earned": sum(p["earned"] for p in mine),
            "possible": sum(p["possible"] for p in mine),
        }
    return {
        **week,
        "points_rule": {"done": POINTS_DONE, "proof": POINTS_PROOF},
        "now": {"date": moment.date().isoformat(), "minute": moment.hour * 60 + moment.minute},
        "legend": [{"course": name, "color": index} for name, index in legend],
        "activities": [
            {
                "label": r["label"],
                "kind": r.get("kind", "other"),
                "weekday": None if r.get("weekday") is None else WEEKDAY_NAMES[r["weekday"]],
                "date": r.get("date"),
                "start": r["start"],
                "end": r["end"],
                # A class drawn by hand wears its course's colour (D32).
                "color": colours.get(r["label"].casefold()) if r.get("kind") == "class" else None,
            }
            for r in subscription.busy_rows
        ],
        "drawn": subscription.ics_text is None and subscription.source_url is None,
        "kinds": [{"id": k, "label": _(label)} for k, label in ACTIVITY_KINDS],
        "hours": [0, 24],  # the grid's first and last hour: the whole day (D59)
        "window": list(options["study_window"]),
        "first": first.isoformat(),
        "planned": subscription.plan is not None,
        "error": subscription.error,
        # Where the sun is at each hour of today (D157), for the ribbon beside the hours.
        "sun": sun_hours(options["tz"], moment.date()),
    }


def sun_hours(tz: str, day: date) -> list[float]:
    """Where the sun is at each hour of `day`, from midnight to midnight (25 values, D157):
    the day's position from 0 (deep night) through 1 (sunrise), 2 (the highest sun) and 3
    (sunset) back towards 4, for the time zone's main city (D76)."""
    zone = _zone(tz)
    midnight = datetime.combine(day, time(), zone)
    latitude, longitude = _daylight.place(tz, midnight)
    return [
        round(_daylight.day_position(latitude, longitude, (midnight + timedelta(hours=h)).astimezone(UTC)), 3)
        for h in range(25)
    ]


def settings_overview(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    login: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Settings as a dashboard (D161): what each of its six parts is set to, a few lines
    each as (label, value), read from the same values its own page shows. `login` is the
    e-mail account's view (`email_account_view`) when the plan belongs to one."""
    now = now or datetime.now(UTC)
    values = setup_view(subscription)
    zone = _zone(subscription.options["tz"])

    def clock(hours: float) -> str:
        whole = int(hours)
        return f"{whole}:{round((hours - whole) * 60):02d}"

    def row(label: str, value: str) -> dict[str, str]:
        return {"label": label, "value": value}

    start, end = values["study_window"]
    hours = values.get("weekly_hours")
    off = [
        _i18n.weekday_name(date(2024, 1, 1) + timedelta(days=WEEKDAY_NAMES.index(d)))
        for d in values.get("rest_days") or []
        if d in WEEKDAY_NAMES
    ]
    busy = len(values.get("activities") or [])
    week = [
        row(_("Study hours"), f"{clock(start)}–{clock(end)}"),
        row(_("At most"), _("{n} h a week", n=format_number(hours)) if hours else _("No limit")),
        row(_("Days off"), ", ".join(off) if off else _("None")),
        row(_("Busy times"), _n("{n} busy time", "{n} busy times", busy) if busy else _("None")),
    ]

    exams: list[dict[str, str]] = []
    dated = []
    for subject in values.get("subjects") or []:
        when = subject.get("exam") or subject.get("found_when") or ""
        with contextlib.suppress(ValueError):
            day = datetime.fromisoformat(when.replace(" ", "T")).date()
            if day >= now.astimezone(zone).date():
                dated.append((day, subject["name"]))
    for day, name in sorted(dated)[:2]:
        exams.append(row(name, format_date(day, "day")))
    tasks = len(values.get("tasks") or [])
    exams.append(row(_("Deadlines"), _n("{n} to do", "{n} to do", tasks) if tasks else _("None")))
    exams.append(
        row(
            _("Timetable"),
            _("drawn by hand")
            if values.get("drawn")
            else _("from a link")
            if values.get("source_url")
            else _("from a file"),
        )
    )

    account = account_view(store, subscription, now)
    keys = len(account.get("passkeys") or [])
    kept: list[dict[str, str]] = []
    if login:
        kept.append(row(_("E-mail"), str(login.get("email") or "")))
        kept.append(row(_("Confirmed"), _("yes") if login.get("confirmed") else _("not yet")))
    kept.append(row(_("Passkeys"), str(keys) if keys else _("None")))
    kept.append(row(_("Recovery code"), _("saved") if account.get("recovery") else _("not yet")))

    reminders = reminders_view(store, subscription)
    devices = len(reminders.get("devices") or [])
    phone = [
        row(
            _("Reminders"),
            _n("on, on {n} device", "on, on {n} devices", devices) if devices else _("off"),
        ),
        row(_("Before a session"), _("{n} min", n=reminders.get("lead") or 10)),
    ]

    theme_words = {"auto": _("Automatic"), "light": _("Light"), "dark": _("Dark")}
    palette = palette_of(subscription)
    light_names = {k: _(n) for k, n, _l in LIGHT_THEMES}
    dark_names = {k: _(n) for k, n, _l in DARK_THEMES}
    accent_names = {k: _(n) for k, n in ACCENTS}
    look = [
        row(_("Language"), LANGUAGES.get(subscription.options.get("lang") or "en", "English")),
        row(_("Appearance"), theme_words[theme_of(subscription)]),
        row(_("Themes"), f"{light_names[palette['light']]} · {dark_names[palette['dark']]}"),
        row(_("Accent"), accent_names[palette["accent"]]),
    ]

    usage = usage_answer(store, subscription.token)
    data = [
        row(
            _("AI"),
            (_("on") if ai_on(subscription) else _("off")) if ai_available() else _("not on this server"),
        ),
        row(
            _("What you use"),
            _("counted") if usage else _("not counted") if usage is False else _("not asked yet"),
        ),
        row(_("Study tips"), _("shown") if tips_on(subscription) else _("hidden")),
    ]
    return {"week": week, "exams": exams, "account": kept, "phone": phone, "look": look, "data": data}


def setup_view(subscription: Subscription) -> dict:
    """What the settings page shows: the exams planned for, the courses in the
    timetable, deadlines, activities and the week, as plain values for a form."""
    options = subscription.options
    courses: list[str] = []
    found: list[dict] = []
    try:
        report = analyse_calendar(
            subscription.ics_text.encode("utf-8") if subscription.ics_text is not None else None,
            start=date.fromisoformat(options["start"]),
            tz=options["tz"],
        )
        courses = sorted({x.course for x in report.lectures if x.course}, key=str.casefold)
        found = [{"subject": a.subject, "when": a.when[:16].replace("T", " ")} for a in report.assessments]
    except ServiceError:
        pass  # the page still shows what the student typed
    drawn = drawn_courses(subscription)
    courses += [c for c in drawn if c.casefold() not in {x.casefold() for x in courses}]
    found_names = {f["subject"].casefold(): f["when"] for f in found}
    prefs = {**DEFAULT_PREFERENCES, **options.get("preferences", {})}
    # Exam practice per exam (D46): the student's hours for it, else what applies.
    chosen = {k.casefold(): v for k, v in (prefs.get("practice") or {}).items()}
    weighs = {k.casefold(): float(v) for k, v in (prefs.get("weights") or {}).items()}
    sized: dict[str, float] = {}
    if subscription.plan is not None:
        sized = PlanReport.from_dict(subscription.plan).settings.preferences.get("practice_sized") or {}
    subjects = [
        {
            "name": x["name"],
            "exam": (x.get("exam") or "")[:16].replace("T", " "),
            "found": x.get("exam") is None,
            "found_when": found_names.get(x["name"].casefold(), ""),
            "familiarity": x.get("familiarity") or DEFAULT_FAMILIARITY,
            "practice": chosen.get(x["name"].casefold(), ""),
            "weight": weighs.get(x["name"].casefold(), 1.0),
            "practice_default": prefs["practice_hours"]
            if prefs["practice_hours"] is not None
            else sized.get(x["name"], PRACTICE_UNSIZED_HOURS),
        }
        for x in subscription.subjects
    ]
    planned = {x["name"].casefold() for x in subscription.subjects}
    return {
        "subjects": subjects,
        "unplanned_exams": [f for f in found if f["subject"].casefold() not in planned],
        # Each class drawn by hand and not planned yet, offered as an exam row
        # with its name filled in: its date is all the student types (D32).
        "suggested": [c for c in drawn if c.casefold() not in planned],
        # The timetable's courses with no exam in it and not planned (D185), such as one
        # assessed as it goes: offered with an empty date, which leaves them out.
        "no_exam": [
            c
            for c in courses
            if c not in drawn and c.casefold() not in planned and c.casefold() not in found_names
        ],
        "drawn": subscription.ics_text is None and subscription.source_url is None,
        "kinds": list(ACTIVITY_KINDS),
        "courses": courses,
        "tasks": [{**t, "due": str(t["due"])[:16].replace("T", " ")} for t in options.get("tasks", ())],
        "activities": [
            {
                **r,
                "weekday": "" if r.get("weekday") is None else WEEKDAY_NAMES[r["weekday"]],
                "date": r.get("date") or "",
            }
            for r in subscription.busy_rows
        ],
        "weekly_hours": prefs["weekly_hours"],
        "rest_days": list(prefs["rest_days"]),
        "practice_hours": "" if prefs["practice_hours"] is None else prefs["practice_hours"],
        "weights": EXAM_WEIGHTS,
        "practice_choices": sorted(
            {*PRACTICE_CHOICES, *(float(v) for v in chosen.values())}
            | ({float(prefs["practice_hours"])} if prefs["practice_hours"] is not None else set())
        ),
        "commute": int(options.get("commute_minutes", 0)),
        "study_time": str(options.get("study_time", "any")),
        "study_times": STUDY_TIME_WORDS,
        "quick_tests": bool(options.get("quick_tests", False)),
        "study_on_the_way": bool(options.get("study_on_the_way", False)),
        "warm_up": bool(options.get("warm_up", False)),
        "commute_choices": sorted({*COMMUTE_CHOICES, int(options.get("commute_minutes", 0))}),
        "study_window": list(options["study_window"]),
        "day": day_view(subscription),
        "blocks_per_day": options["blocks_per_day"],
        "block_minutes": options["block_minutes"],
        "tz": options["tz"],
        "source_url": subscription.source_url,
        # D175: the link is never shown again; the page says it is linked, and where.
        "deadlines_url": "",
        "deadlines_linked": bool(options.get("deadlines_url")),
        "deadlines_host": _deadlines_host(options),
        "deadlines_site": _deadlines_site(options) or "",
        "deadlines_error": options.get("deadlines_error"),
        "imported": sorted(set(options.get("imported", {}).values())),
        "weekdays": list(WEEKDAY_NAMES),
    }


def warm_ups(subscription: Subscription, now: datetime | None = None) -> list[dict]:
    """Today's lectures still ahead, of the courses being planned, when the student
    asked for a warm-up before each (D51): guessing what a lecture will answer
    before it helps learn it [ref:pan2023]. Nothing is scheduled: it is a prompt on
    Today, and the week's hours are not touched."""
    if not subscription.options.get("warm_up") or subscription.plan is None:
        return []
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    return _lectures_ahead_today(plan, (now or datetime.now(UTC)).astimezone(zone))


def _lectures_ahead_today(plan: PlanReport, local: datetime) -> list[dict]:
    """The lectures of planned courses still ahead today (exams aside), by time."""
    zone = local.tzinfo
    courses = {s.name.casefold(): s.name for s in plan.subjects}
    exams = {datetime.fromisoformat(s.exam) for s in plan.subjects}
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    out = []
    for e in plan.events:
        start = datetime.fromisoformat(e.start).astimezone(zone)
        course = course_of(e.summary).casefold()
        if e.source != "calendar" or start.date() != local.date() or start <= local:
            continue
        if course in courses and start not in exams:
            out.append({"course": courses[course], "when": f"{start:%H:%M}", "color": colours.get(course)})
    return sorted(out, key=lambda w: w["when"])


# The question after an exam (D56, Phase D of the engine review): how it went, so
# the pilot can set the recall the model predicted beside what happened.
EXAM_ASK_DAYS = 21  # an exam is asked about for three weeks after it
EXAM_RESULTS = {
    "well": "Passed, and well",
    "passed": "Passed",
    "just": "Just passed",
    "failed": "Not passed",
    "absent": "Did not sit it",
}
EXAM_HELPED = {
    "lot": "Yes, a lot",
    "little": "A little",
    "not": "Not really",
    "unused": "I did not follow it",
}
MARK_CHARS = 20


def exam_questions(subscription: Subscription, now: datetime | None = None) -> list[dict]:
    """The exams of the last `EXAM_ASK_DAYS` days not yet answered about, or said
    no to (D56), soonest first."""
    if subscription.plan is None:
        return []
    plan = PlanReport.from_dict(subscription.plan)
    now = now or datetime.now(UTC)
    zone = _zone(plan.settings.tz)
    answered = subscription.options.get("exam_results", {})
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    out = []
    for x in sorted(plan.subjects, key=lambda x: x.exam):
        exam = datetime.fromisoformat(x.exam)
        due = exam <= now < exam + timedelta(days=EXAM_ASK_DAYS)
        if due and not _exam_answered(answered, x.name, x.exam):
            out.append(
                {
                    "course": x.name,
                    "exam": x.exam,
                    "when": format_date(exam.astimezone(zone).date(), "short"),
                    "color": colours.get(x.name.casefold()),
                }
            )
    return out


def _exam_key(course: str, exam: str) -> str:
    """Where an exam's answer is kept: the course and the exam's moment, so a course
    retaken later is asked about again (AUDIT item 84)."""
    return f"{course}@{exam}"


def _exam_answered(answers: Mapping[str, dict], course: str, exam: str) -> bool:
    """Whether this exam of the course was answered about. An answer kept before
    AUDIT item 84 is under the course's name alone; it answers the exams of that
    course before it was given, never a later one."""
    if _exam_key(course, exam) in answers:
        return True
    old = answers.get(course)
    return old is not None and datetime.fromisoformat(old["at"]) >= datetime.fromisoformat(exam)


def recall_predicted(plan: PlanReport, course: str) -> float | None:
    """The mean recall the model predicts for a course's topics on its exam day,
    from the sessions reported (an unreported self-test counts for nothing, D44)."""
    members = [s for s in plan.specs if s.get("subject", s["name"]) == course]
    if not members:
        return None
    model = model_of(plan.settings)
    return _recall_at_exam(members, _replay(plan.specs, plan.history, model), model)


def answer_exam(
    subscription: Subscription, course: str, form: Mapping[str, str], now: datetime | None = None
) -> Subscription:
    """How an exam went (D56): one of `EXAM_RESULTS`, a mark if the student gives
    one, whether the plan helped (`EXAM_HELPED`, optional), and whether the owner
    may read it without their name. "Rather not say"
    (`skip`) keeps nothing but that it was asked. The recall the model predicted
    for the exam is kept beside the answer, as it stood when the answer came."""
    now = now or datetime.now(UTC)
    asked = next((q for q in exam_questions(subscription, now) if q["course"] == course), None)
    if asked is None:
        raise InvalidInput(_("this exam is not waiting for an answer"))
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    if form.get("skip"):
        record: dict[str, Any] = {"skipped": True, "at": stamp}
    else:
        result = str(form.get("result") or "")
        if result not in EXAM_RESULTS:
            raise InvalidInput(_("choose how it went"))
        mark = " ".join(str(form.get("mark") or "").split())
        if len(mark) > MARK_CHARS:
            raise InvalidInput(_("at most {n} characters here", n=MARK_CHARS))
        helped = str(form.get("helped") or "")
        if helped and helped not in EXAM_HELPED:
            raise InvalidInput(_("choose whether the plan helped"))
        assert subscription.plan is not None  # an exam to ask about means a plan
        predicted = recall_predicted(PlanReport.from_dict(subscription.plan), course)
        record = {
            "result": result,
            "mark": mark,
            "helped": helped,
            "consent": form.get("consent") == "1",
            "predicted": None if predicted is None else round(predicted, 4),
            "at": stamp,
        }
    record = {"course": course, "exam": asked["exam"], **record}
    answers = {**subscription.options.get("exam_results", {}), _exam_key(course, asked["exam"]): record}
    return replace(subscription, options={**subscription.options, "exam_results": answers})


# After an exam, the forecast beside how it went (DECISIONS.md D105).
OUTCOME_HOURS = 24  # Today shows a new answer beside its forecast for a day
# The answers in order, best last, to compare two exams; "Did not sit it" is not
# compared, nor is "Rather not say".
_RESULT_RANK = {"failed": 0, "just": 1, "passed": 2, "well": 3}


def _exam_records(subscription: Subscription) -> list[dict]:
    """Every exam answered about, each with its course and the exam's moment when
    known (an answer kept before AUDIT item 84 holds neither: they come from its key
    and from the plan's exam of that course before the answer)."""
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    out = []
    for key, record in (subscription.options.get("exam_results") or {}).items():
        course = record.get("course", key)
        exam = record.get("exam")
        if exam is None and plan is not None:
            answered = datetime.fromisoformat(record["at"])
            exam = next(
                (
                    x.exam
                    for x in plan.subjects
                    if x.name == course and datetime.fromisoformat(x.exam) <= answered
                ),
                None,
            )
        out.append({**record, "course": course, "exam": exam})
    return out


def exam_outcomes(subscription: Subscription, now: datetime | None = None) -> dict:
    """D105: each exam the student said how it went, beside the recall the plan
    predicted for it when they answered (rounded to 5, as every forecast, D24),
    highest forecast first; and, comparing them two by two, how often the higher
    forecast went with the better result. Two exams with the same result or the same
    forecast shown are not compared. `latest` is the answer given in the last
    `OUTCOME_HOURS`, for Today."""
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    zone = _zone(subscription.options["tz"])
    colours = {name.casefold(): index for name, index in course_colours(plan)} if plan else {}
    rows = []
    for record in _exam_records(subscription):
        if record.get("skipped") or record.get("result") not in EXAM_RESULTS:
            continue
        predicted = record.get("predicted")
        exam = record["exam"]
        rows.append(
            {
                "course": record["course"],
                "result": record["result"],
                "says": translate(EXAM_RESULTS[record["result"]]),
                "mark": record.get("mark") or "",
                "predicted": None if predicted is None else _five(predicted),
                "when": format_date(datetime.fromisoformat(exam).astimezone(zone).date(), "short")
                if exam
                else "",
                "at": record["at"],
                "color": colours.get(record["course"].casefold()),
            }
        )
    rows.sort(key=lambda r: (r["predicted"] is None, -(r["predicted"] or 0), r["at"]))
    ranked = [r for r in rows if r["predicted"] is not None and r["result"] in _RESULT_RANK]
    pairs = agree = 0
    for i, a in enumerate(ranked):
        for b in ranked[i + 1 :]:
            better = _RESULT_RANK[a["result"]] - _RESULT_RANK[b["result"]]
            if a["predicted"] == b["predicted"] or better == 0:
                continue
            pairs += 1
            agree += (a["predicted"] > b["predicted"]) == (better > 0)
    since = (now - timedelta(hours=OUTCOME_HOURS)).astimezone(UTC).isoformat(timespec="seconds")
    latest = max((r for r in rows if r["at"] > since), key=lambda r: r["at"], default=None)
    return {"exams": rows, "pairs": pairs, "agree": agree, "latest": latest}


def today_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """What the Today page shows: sessions to report, the one in progress or next,
    what comes after, deadlines, exams, and anything wrong."""
    now = now or datetime.now(UTC)
    view: dict = {
        "planned": subscription.plan is not None,
        "error": subscription.error,
        "today": "",
        "to_report": [],
        "now": None,
        "next": [],
        "unreported": [],
        "week": {"sessions": 0, "hours": 0.0},
        "tasks": [],
        "exams": [],
        "warnings": [],
        "warm_up": warm_ups(subscription, now),
    }
    if subscription.plan is None:
        return view
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    view["today"] = format_date(local.date())
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    gains = session_gains(plan)
    syllabus, courses = _syllabus_index(subscription), topic_courses(subscription)
    cards = [
        _session_card(s, zone, local, subscription.outcomes, colours, gains, syllabus, courses)
        for s in plan.history + plan.sessions
    ]
    moments = [
        (datetime.fromisoformat(s.start).astimezone(zone), datetime.fromisoformat(s.end).astimezone(zone))
        for s in plan.history + plan.sessions
    ]
    for card, (start, end) in zip(cards, moments, strict=True):
        if start <= local < end:
            view["now"] = card
        elif local - REPORT_WINDOW <= start <= local:
            view["to_report"].append(card)
        elif start > local and len(view["next"]) < 6:
            view["next"].append(card)
    if view["now"] is not None:
        view["to_report"].append(view["now"])
    view["to_report"].reverse()
    # The panel asks only about what has not been answered yet.
    view["unreported"] = [c for c in view["to_report"] if not c["reported"]]
    view["done"] = _day_done(cards, moments, gains, local)
    view["banked"] = _just_banked(cards, moments, local)
    week_end = local + timedelta(days=7)
    coming = [
        s for s in plan.sessions if local <= datetime.fromisoformat(s.start).astimezone(zone) < week_end
    ]
    view["week"] = {
        "sessions": len(coming),
        # Each session's own length: a quick self-test is not a block (D48).
        "hours": round(
            sum(
                (datetime.fromisoformat(s.end) - datetime.fromisoformat(s.start)) / timedelta(hours=1)
                for s in coming
            ),
            1,
        ),
    }
    view["tasks"] = [t for t in _task_rows(subscription, plan, now, colours) if not t["finished"]]
    view["exams"] = _exam_rows(plan, now, colours)
    # A subject short of its target already says so in its status line; other
    # warnings (practice that does not fit, work that does not fit) stay.
    # A warning is written in the plan's language when the plan is made (D10): French
    # puts a space before the colon, which the filter missed, so every course short of
    # its target was said twice on Today, once more in a list (AUDIT 117).
    shown = tuple(f"{x.name}{colon}" for x in plan.subjects for colon in (": ", " : "))
    said = ("predicted below", "does not reach the target", _("are predicted below"), _("is predicted below"))
    view["warnings"] = [w for w in plan.warnings if not (w.startswith(shown) and any(x in w for x in said))]
    return view


# Today as the app's home (D140): the day at a glance and a window on classmates, beside
# the next step, the week, the cards, the tasks and the exams.
DAY_LEFT_OUT = ("commute", "lunch")  # the trips and lunch around classes (D42, D69): the calendar has them
DAY_KINDS = {"class": "Class", "typed": "Busy", "skipped": "Skipped", "exam": "Exam"}


def day_agenda(subscription: Subscription, now: datetime | None = None) -> list[dict]:
    """Today's classes, exams, tests, busy times and study sessions in time order, each
    over, going on or to come (`state`), a session with how it was reported: Today's
    list of the day (D140), the calendar one tap away. The trips to campus and lunch
    are left to the calendar, so the list stays short."""
    if subscription.plan is None:
        return []
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    exams = {x.exam for x in plan.subjects}

    def state(start: datetime, end: datetime) -> str:
        return "past" if end <= local else "now" if start <= local else "later"

    items = []
    for e in plan.events:
        start = datetime.fromisoformat(e.start).astimezone(zone)
        if start.date() != local.date() or e.source in DAY_LEFT_OUT:
            continue
        end = datetime.fromisoformat(e.end).astimezone(zone)
        kind = (
            "exam"
            if e.start in exams and e.source != "skipped"
            else e.source
            if e.source in ("typed", "skipped", *TEST_KINDS)
            else "class"
        )
        items.append(
            {
                "start": f"{start:%H:%M}",
                "end": f"{end:%H:%M}",
                "label": e.summary if kind == "exam" else short_title(e.summary),
                "what": test_label(kind, e.summary) if kind in TEST_KINDS else _(DAY_KINDS[kind]),
                "kind": kind,
                "color": colours.get(course_of(e.summary).casefold()),
                "state": state(start, end),
                "reported": None,
            }
        )
    for x in plan.history + plan.sessions:
        start = datetime.fromisoformat(x.start).astimezone(zone)
        if start.date() != local.date():
            continue
        end = datetime.fromisoformat(x.end).astimezone(zone)
        items.append(
            {
                "start": f"{start:%H:%M}",
                "end": f"{end:%H:%M}",
                "label": x.title if x.kind == "task" else x.subject,
                "what": _(KIND_LABELS.get(x.kind, x.kind)),
                "kind": "study",
                "color": colours.get(x.subject.casefold()),
                "state": state(start, end),
                "reported": subscription.outcomes.get(session_id(x)),
            }
        )
    return sorted(items, key=lambda i: (i["start"], i["end"]))


# The day as one strip across Today (D171, the owner's A): from the hour before getting up
# to the hour after going to bed, wider when the day's items are; items that overlap take
# the next lane, at most three.
DAY_STRIP_LANES = 3
DAY_STRIP_HOURS = (8, 22)  # when the student's day cannot be read


def day_strip(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Today's items (`day_agenda`) placed on one line of time (D171), as `blocks`: each
    item's left edge and width in whole percents of the strip (`x`, `w`, at least 1), its
    lane; the hours to label, and where now is (None outside the strip). None without a
    plan."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    items = day_agenda(subscription, now)
    day = day_view(subscription)
    try:
        up, down = _hours_of(day["wake"], _("Get up")), _hours_of(day["bed"], _("Go to bed"))
    except InvalidInput:
        up, down = DAY_STRIP_HOURS
    if down <= up:
        down = 24.0  # to bed after midnight: the strip stops at the day's end

    def minute(text: str) -> int:
        hours, minutes = text.split(":")
        return int(hours) * 60 + int(minutes)

    spans = []
    for i in items:
        start, end = minute(i["start"]), minute(i["end"])
        spans.append((start, end if end > start else 24 * 60))
    first = min([int(up) * 60, *(a for a, _b in spans)]) // 60 * 60
    last = min(24 * 60, -(-max([round(down * 60), *(b for _a, b in spans)]) // 60) * 60)
    width = max(last - first, 60)

    def at(m: float) -> int:
        return max(0, min(100, round(100 * (m - first) / width)))

    lanes: list[int] = []
    placed = []
    for i, (start, end) in zip(items, spans, strict=True):
        lane = next((k for k, free in enumerate(lanes) if free <= start), None)
        if lane is None:
            if len(lanes) < DAY_STRIP_LANES:
                lanes.append(0)
            lane = len(lanes) - 1
        lanes[lane] = max(lanes[lane], end)
        x = at(start)
        placed.append({**i, "x": min(x, 99), "w": max(1, min(at(end), 100) - x), "lane": lane})
    local = now.astimezone(_zone(subscription.options["tz"]))
    clock = local.hour * 60 + local.minute
    step = 2 if width > 10 * 60 else 1
    return {
        "blocks": placed,
        "lanes": max(1, len(lanes)),
        "hours": [{"x": at(h * 60), "label": f"{h:02d}"} for h in range(first // 60, last // 60 + 1, step)],
        "now": at(clock) if first <= clock <= last else None,
    }


CLASSMATES_POSTS = 2  # the newest posts of people followed on Today (D140)


def community_way(store: str | os.PathLike, subscription: Subscription) -> str:
    """D177: what Today's Community says when there is nothing to show yet: "handle" for a
    student without one (Community starts with choosing it), else "quiet" (nobody followed
    is studying or has shared lately)."""
    return "quiet" if social.get_profile(_store(store), subscription.token) is not None else "handle"


def classmates_glance(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """Today's window on classmates (D140): who of the people followed is studying now
    (D108) and the newest posts of the people followed, as Community shows them to the
    student (their own posts left out). None for a student without a handle, or when
    there is nothing to show: Today keeps no empty window."""
    where = _store(store)
    if social.get_profile(where, subscription.token) is None:
        return None
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    present = studying_now_view(store, subscription, now)[:3]
    others = [
        p for p in social.feed_following(where, subscription.token, limit=12) if p.token != subscription.token
    ][:CLASSMATES_POSTS]
    posts = []
    for card in _cards(where, subscription.token, others, now, zone):
        data = card  # a post's data is spread into its card (social.Post.to_dict)
        minutes = int(data.get("minutes", 0) or 0)
        posts.append(
            {
                "id": card["id"],
                "author": card["author"],
                "says": _("studied {time}", time=duration_words(minutes))
                if card["kind"] == "session"
                else _("explained {title}", title=data.get("concept", ""))
                if card["kind"] == "explain"
                else _("shared notes"),
                "course": str(data.get("course", "")),
                "when": card["when"],
            }
        )
    if not present and not posts:
        return None
    return {"now": present, "posts": posts, "join_minutes": JOIN_MINUTES}


# The owner, 2026-11-18, on Today: "a student should feel in control of their attention and
# time and feel empowered with some daily tips to study and live well" (D140).
TIME_PARTS = ("classes", "study", "busy", "free")


def _span(start: str, end: str) -> float:
    """Hours from one "HH:MM" to another the same day; an end at midnight is 24:00."""
    a = int(start[:2]) + int(start[3:5]) / 60
    b = int(end[:2]) + int(end[3:5]) / 60
    return max(0.0, (b or 24.0) - a)


def time_and_attention(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """Today's hours and the week's attention, for Today (D140). The day: from getting up
    to going to bed (D69), the hours of classes and tests, of study planned, of busy
    times, and what is left, the student's own; drawn as one bar. The week: the minutes
    focused with the timer since Monday, the sessions timed, and of those checked, how
    many were spent without leaving the app (D67). What was planned and what was logged,
    no guess. None without a plan."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    local = now.astimezone(_zone(subscription.options["tz"]))
    day = day_view(subscription)
    up = _hours_of(day["wake"], _("Get up"))
    down = _hours_of(day["bed"], _("Go to bed"))
    awake = (down if down > up else down + 24) - up
    items = day_agenda(subscription, now)
    taught = ("class", "exam", *TEST_KINDS)
    hours = {
        "classes": sum(_span(i["start"], i["end"]) for i in items if i["kind"] in taught),
        "study": sum(_span(i["start"], i["end"]) for i in items if i["kind"] == "study"),
        "busy": sum(_span(i["start"], i["end"]) for i in items if i["kind"] == "typed"),
    }
    hours["free"] = max(0.0, awake - sum(hours.values()))
    whole = max(awake, sum(hours.values())) or 1.0
    bar, x = [], 0.0
    for part in TIME_PARTS:
        width = 100 * hours[part] / whole
        if width > 0:
            bar.append({"part": part, "x": round(x, 2), "width": round(width, 2)})
        x += width
    monday = _progress.monday(local.date())
    posts = [p for p in social.posts_of(_store(store), subscription.token, "session") if p.data.get("timed")]
    timed = [p for p in posts if date.fromisoformat(p.day) >= monday]
    checked = [p for p in timed if p.data.get("checked")]
    minutes = sum(_minutes_of(p) for p in timed)
    return {
        "wake": day["wake"],
        "bed": day["bed"],
        "hours": {part: format_number(round(value * 2) / 2) for part, value in hours.items()},
        "bar": bar,
        "focus": {
            "minutes": minutes,
            "time": duration_words(minutes) if minutes else "",
            "sessions": len(timed),
            "checked": len(checked),
            "whole": sum(1 for p in checked if not p.data.get("interruptions")),
            "days": (days := _focus_days(timed, monday, local.date())),
            "days_said": ", ".join(f"{d['name']} {d['time']}" for d in days if d["minutes"]),
            "best": _focus_best(posts, local),
        },
    }


# Attention over the week (D144; the owner's pick, 2026-11-18: "attention"). Its part of
# the day is named once FOCUS_BEST_SESSIONS timed sessions of the last FOCUS_BEST_DAYS
# days say when they started, and only between parts with two such sessions each: one
# session is an anecdote, and a part with nothing to compare is not "longest". The
# numbers are choices, not results, and the page says what they count.
FOCUS_BEST_SESSIONS = 4
FOCUS_BEST_DAYS = 28
_FOCUS_BEST_WORDS = {
    "morning": "You focus longest in the morning: {time} a session on average.",
    "afternoon": "You focus longest in the afternoon: {time} a session on average.",
    "evening": "You focus longest in the evening: {time} a session on average.",
}


def _minutes_of(post: social.Post) -> int:
    try:
        return max(0, int(post.data.get("minutes", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _focus_days(timed: Sequence[social.Post], monday: date, today: date) -> list[dict]:
    """The minutes timed on each day of the week, Monday first, each column's height a
    share of the week's longest day (D144); the days still ahead are drawn empty."""
    minutes: dict[str, int] = {}
    for post in timed:
        minutes[post.day] = minutes.get(post.day, 0) + _minutes_of(post)
    tallest = max(minutes.values(), default=0) or 1
    days = []
    for k in range(7):
        day = monday + timedelta(days=k)
        spent = minutes.get(day.isoformat(), 0)
        days.append(
            {
                "letter": _i18n.weekday_name(day, short=True)[:1].upper(),
                "name": _i18n.weekday_name(day),
                "minutes": spent,
                "time": duration_words(spent) if spent else "",
                "height": round(100 * spent / tallest),
                "today": day == today,
                "ahead": day > today,
            }
        )
    return days


def _focus_best(posts: Sequence[social.Post], local: datetime) -> dict | None:
    """The part of the day the student's timed sessions last longest, on average, over
    the last four weeks (D144): counted from when each timer started, so only sessions
    saved since 2026-10-21 say. None until there are enough to compare."""
    since = local.date() - timedelta(days=FOCUS_BEST_DAYS)
    zone = local.tzinfo
    parts: dict[str, list[int]] = {}
    count = 0
    for post in posts:
        started = str(post.data.get("started") or "")
        if not started or date.fromisoformat(post.day) <= since:
            continue
        try:
            moment = datetime.fromisoformat(started).astimezone(zone)
        except ValueError:
            continue
        parts.setdefault(_progress.part_of_day(moment), []).append(_minutes_of(post))
        count += 1
    compared = {part: sum(v) / len(v) for part, v in parts.items() if len(v) >= 2}
    if count < FOCUS_BEST_SESSIONS or len(compared) < 2:
        return None
    part = max(compared, key=lambda k: (compared[k], k))
    return {
        "part": part,
        "words": _(_FOCUS_BEST_WORDS[part], time=duration_words(round(compared[part]))),
        "sessions": count,
    }


# Focus from Today (D144; the owner's pick, 2026-11-18: "focus"). The plan's own session
# starts from its card; this starts anything else in one tap: the course last timed
# first, and three of the focus page's lengths, the shortest chosen, since a short start
# is the easiest to begin (D106's first session was 25 minutes too).
FOCUS_QUICK = (25, 45, 90)
FOCUS_QUICK_COURSES = 4


def focus_quick(store: str | os.PathLike, subscription: Subscription) -> dict | None:
    """The focus card on Today: the courses to start with (the one last timed first, then
    the others in their colours' order), three lengths, and the tip on breaks with its
    evidence a tap away. None while a session runs, or without a course to name."""
    if subscription.options.get("focus"):
        return None
    courses = course_names(subscription)
    if not courses:
        return None
    last = next(
        (
            str(p.data.get("course") or "")
            for p in social.posts_of(_store(store), subscription.token, "session", limit=50)
            if p.data.get("timed")
        ),
        "",
    )
    first = next((c for c in courses if c.casefold() == last.casefold()), None)
    ordered = [first, *[c for c in courses if c != first]] if first else list(courses)
    breaks = next(t for t in _tips.TIPS if t.key == "breaks")
    return {
        "courses": ordered[:FOCUS_QUICK_COURSES],
        "more": len(ordered) > FOCUS_QUICK_COURSES,
        "goals": [{"minutes": g, "label": duration_words(g)} for g in FOCUS_QUICK],
        "goal": FOCUS_QUICK[0],
        "tip": {"key": breaks.key, "topic": breaks.topic, "advice": _(breaks.advice)}
        if tips_on(subscription)
        else None,
    }


# The clear-your-mind list (D145; the owner's pick, 2026-11-18: "clear-mind"). What is on a
# student's mind, written down on Today: an item given a day is a plan for it, shown first
# and on its day; one without stays a note until crossed off. Masicampo and Baumeister
# (2011) found that a specific plan for an unfinished goal ended the intrusive thoughts it
# caused; Scullin et al. (2018), that a specific to-do list written for five minutes at
# bedtime brought sleep sooner than a list of what was done. "Close the day" asks for
# tomorrow's first three in the hours before bed. Kept with the plan, never shared.
MIND_ITEMS = 200  # the most a list holds; the oldest line done goes first when it is full (D195)
MIND_TEXT = 160  # an item's characters
CLOSE_BEFORE_BED = timedelta(hours=3)  # "Close the day" is asked from then until bed
CLOSE_FIRSTS = 3  # tomorrow's first things


def _mind(subscription: Subscription) -> list[dict]:
    """The to-do page's lines (D195), "On your mind" since D145: each with its kind ("todo",
    "note" or "heading"), whether it is done, its level (a step under the line above), the
    planned task it stands for, if any, and its course."""
    return [
        {"kind": "todo", "done": False, "indent": 0, "task": "", "course": "", **dict(x)}
        for x in subscription.options.get("mind", ())
    ]


def _with_mind(subscription: Subscription, items: list[dict], **more: Any) -> Subscription:
    return replace(subscription, options={**subscription.options, "mind": items, **more})


def _mind_day(value: object, today: date) -> str:
    """A day for an item: "" (none), "today", "tomorrow" or an ISO date from today on."""
    text = str(value or "").strip()
    if text in ("", "none"):
        return ""
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise InvalidInput(_("{value} is not a day", value=repr(text))) from None
    if day < today:
        raise InvalidInput(_("choose today or a day after it"))
    return day.isoformat()


def add_to_mind(
    subscription: Subscription, text: str, when: object = "", *, now: datetime | None = None
) -> Subscription:
    """Write something down; `when` gives it a day ("today", "tomorrow", a date)."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    text = " ".join(str(text or "").split())[:MIND_TEXT]
    if not text:
        raise InvalidInput(_("write what is on your mind"))
    items = _mind(subscription)
    items.append({"id": secrets.token_hex(4), "text": text, "day": _mind_day(when, today), "kind": "todo"})
    return _with_mind(subscription, _kept(items))


def _kept(items: list[dict]) -> list[dict]:
    """The list within MIND_ITEMS: the oldest line done goes first, then the oldest without a day."""
    while len(items) > MIND_ITEMS:
        oldest = next(
            (i for i, x in enumerate(items) if x.get("done")),
            next((i for i, x in enumerate(items) if not x["day"]), 0),
        )
        del items[oldest]
    return items


def _mind_index(items: list[dict], item: str) -> int:
    for i, x in enumerate(items):
        if x["id"] == item:
            return i
    raise InvalidInput(_("this is no longer on your list"))


def plan_mind_item(
    subscription: Subscription, item: str, when: object, *, now: datetime | None = None
) -> Subscription:
    """Give an item a day, or take it away ("none")."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    items = _mind(subscription)
    i = _mind_index(items, item)
    items[i] = {**items[i], "day": _mind_day(when, today)}
    return _with_mind(subscription, items)


def cross_off(subscription: Subscription, item: str, *, now: datetime | None = None) -> Subscription:
    """An item done: ticked on the to-do page (D195), gone from Today's list; one done
    already is not on the list to cross off."""
    items = _mind(subscription)
    if items[_mind_index(items, item)]["done"]:
        raise InvalidInput(_("That is not on your list any more."))
    return tick_todo(subscription, item, True, now=now)


def close_day(
    subscription: Subscription, firsts: Sequence[str], *, now: datetime | None = None
) -> Subscription:
    """Tomorrow's first things, up to three, each given the day after this evening (after
    midnight, the day that has begun); the day is closed even with none written, and the
    question is not asked again tonight."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_zone(subscription.options["tz"]))
    evening = _evening_of(subscription, local)
    changed = subscription
    for text in [f for f in firsts if str(f or "").strip()][:CLOSE_FIRSTS]:
        changed = add_to_mind(changed, text, (evening + timedelta(days=1)).isoformat(), now=now)
    return replace(changed, options={**changed.options, "closed": evening.isoformat()})


def _evening_of(subscription: Subscription, local: datetime) -> date:
    """The day an evening belongs to: after midnight and before getting up, the day before."""
    wake = _hours_of(day_view(subscription)["wake"], _("Get up"))
    return local.date() - timedelta(days=1) if local.hour + local.minute / 60 < wake else local.date()


# D195 (the owner, 2026-11-25: "the modality to put tasks and to leave a note should actually
# simply become like in notion the ability to make a to-do list page"; points 1 to 6 of the
# proposal, "for now i like the to-do lists yes"). One page, written a line at a time: a line is
# a to-do with its box, "# " makes a heading, "> " a note, "- " a step under the line above. A
# line is read as the new-task sheet reads one (`ai.read_task`, rules only): a day it names
# becomes its day, a course its course, and a day with hours of work a task the plan makes
# time for (`add_task`), ticked off with the line. "On your mind" (D145) is the same list.
#
# D202 (the owner, 2026-11-25: proposals 7 to 16 all approved, then "EMPLOY THE WHOLE PAGE
# SPACE like a notion page, when hovering on a thing a bin should show to delete, formatting
# could be included"): a line ending in "?" is a question for the teacher, ticked when
# answered; an address in a line is kept apart as a link, shown by its name; "every Monday"
# brings a line back the week after it is ticked; an hour without hours of work is a
# reminder to the phone at that hour; a note written "term: definition" becomes a flashcard in
# one tap; three templates write a list at once; and the page says how much of today's work
# fits in today's free time, and offers to move a line to tomorrow when it does not.
TODO_KINDS = ("todo", "note", "heading", "question")
TODO_TICKED = ("todo", "question")  # the kinds with a box
TODO_LINKS = 3  # the addresses a line keeps
TODO_LINK_CHARS = 500
_TODO_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TODO_FORMAT = re.compile(r"\*\*(.+?)\*\*|~~(.+?)~~|`(.+?)`|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_TODO_FILE_KINDS = {"pdf": "PDF", "doc": "DOC", "docx": "DOC", "odt": "DOC", "ppt": "PPT", "pptx": "PPT",
                    "odp": "PPT", "xls": "XLS", "xlsx": "XLS", "ods": "XLS"}  # fmt: skip


def _todo_templates() -> dict[str, tuple[str, tuple[str, ...]]]:
    """D202: the templates, each a heading and its lines, in the page's language."""
    return {
        "exam": (
            _("Prepare the exam"),
            (
                _("Gather past exam papers"),
                _("Go through the course, chapter by chapter"),
                _("A timed mock exam"),
                _("Questions for the teacher?"),
            ),
        ),
        "assignment": (
            _("Hand it in, in steps"),
            (
                _("Read the brief and how it is marked"),
                _("A first draft"),
                _("Check it against how it is marked"),
                _("Hand it in"),
            ),
        ),
        "week": (
            _("Review of the week"),
            (
                _("Tick off what is done, move what is not"),
                _("Choose next week's first three things"),
                _("Look at the deadlines and exams ahead"),
            ),
        ),
    }


def _todo_links(text: str) -> tuple[str, list[str]]:
    """A line's words without its addresses, and the addresses (http and https only)."""
    links: list[str] = []
    for found in _TODO_URL.findall(text):
        url = found.rstrip(".,;:!?)»”'\"")
        if url not in links and len(links) < TODO_LINKS and len(url) <= TODO_LINK_CHARS:
            links.append(url)
        text = text.replace(found, " ")
    return " ".join(text.split()), links


def _link_label(subscription: Subscription, url: str) -> dict:
    """A link as a short label: the name a course's materials give it (D174), else its
    file's name, else its site; and a mark for a document's kind."""
    for links in (subscription.options.get("materials") or {}).values():
        for m in links or ():
            if isinstance(m, Mapping) and m.get("url") == url and m.get("title"):
                return {"href": url, "label": str(m["title"])[:60], "kind": ""}
    parts = urlsplit(url)
    name = parts.path.rstrip("/").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    if ext in _TODO_FILE_KINDS:
        return {"href": url, "label": name[:60], "kind": _TODO_FILE_KINDS[ext]}
    host = (parts.hostname or "").removeprefix("www.")
    return {"href": url, "label": host[:60] or url[:60], "kind": ""}


def todo_parts(text: str) -> list[dict]:
    """D202: a line's words in pieces, each plain ("t"), bold ("b"), italic ("i"), struck
    through ("s") or code ("c"), from Markdown's marks: **bold**, *italic*, ~~struck~~,
    `code`. The page draws each piece; the marks stay in the words, so editing keeps them."""
    out: list[dict] = []
    last = 0
    for m in _TODO_FORMAT.finditer(text):
        if m.start() > last:
            out.append({"k": "t", "x": text[last : m.start()]})
        kind = "b" if m.group(1) else "s" if m.group(2) else "c" if m.group(3) else "i"
        out.append({"k": kind, "x": next(g for g in m.groups() if g)})
        last = m.end()
    if last < len(text):
        out.append({"k": "t", "x": text[last:]})
    return out


def _todo_line(subscription: Subscription, line: str, now: datetime) -> tuple[dict, TaskSpec | None]:
    """A typed line as a list item, and the task it brings, if it names a day and hours."""
    text = " ".join(str(line or "").split())
    kind, indent = "todo", 0
    for prefix, k in (("# ", "heading"), ("> ", "note"), ("? ", "question")):
        if text.startswith(prefix) or text == prefix.strip():
            kind, text = k, text[len(prefix) :]
    if kind == "todo" and (text.startswith(("- ", "* ")) or text in ("-", "*")):
        indent, text = 1, text[2:]
    done = False
    if kind == "todo" and re.match(r"\[[ xX]?\]\s", text):
        done, text = text[1].lower() == "x", text[text.index("]") + 1 :].strip()
    text, links = _todo_links(text)
    if kind == "todo" and text.endswith("?"):
        kind = "question"  # D202: a question for the teacher, ticked when answered
    if not text and links:
        text = _link_label(subscription, links[0])["label"]
    text = text[:MIND_TEXT]
    if not text:
        raise InvalidInput(_("write what is on your mind"))
    item = {"id": secrets.token_hex(4), "text": text, "day": "", "kind": kind, "done": done,
            "indent": indent, "task": "", "course": "", "links": links}  # fmt: skip
    if kind == "heading":
        return item, None
    zone = _zone(subscription.options["tz"])
    local = now.astimezone(zone)
    today = local.date()
    guess = _ai.read_task(text, today=today, courses=course_names(subscription))
    item["course"] = guess.course
    if kind != "todo":
        return item, None
    if guess.every is not None:
        item["every"] = guess.every
    if guess.at and not guess.hours:
        # D202: an hour without hours of work is a reminder at that hour, on its day, or
        # today (tomorrow once the hour has passed) when it names none.
        day = date.fromisoformat(guess.due[:10]) if guess.due else today
        at = datetime.combine(day, time.fromisoformat(guess.at), zone)
        if not guess.due and at <= local:
            at += timedelta(days=1)
        item["day"] = at.date().isoformat()
        item["text"] = guess.name or text
        if at > local:
            item["at"] = f"{at:%Y-%m-%dT%H:%M}"
        return item, None
    if not guess.due:
        return item, None
    due = datetime.fromisoformat(guess.due)
    item["day"] = max(due.date(), today).isoformat()
    words = guess.name or text
    item["text"] = words
    if not guess.hours:
        return item, None
    name = _free_task_name(subscription, words)
    item["task"] = name
    return item, TaskSpec(name, f"{due:%Y-%m-%d %H:%M}", float(guess.hours), guess.course)


def _free_task_name(subscription: Subscription, words: str) -> str:
    taken = {t["name"].casefold() for t in subscription.options.get("tasks", ())}
    name, n = words, 2
    while name.casefold() in taken:
        name, n = f"{words} ({n})", n + 1
    return name


def add_todo(
    subscription: Subscription,
    line: str,
    *,
    after: str = "",
    course: str = "",
    now: datetime | None = None,
) -> Subscription:
    """A line written on the to-do page, after the line `after` (else at the end). A line that
    names a day and hours is planned at once. `course` gives it a course when it names none
    (a line written on a course's page, D202)."""
    now = now or datetime.now(UTC)
    item, task = _todo_line(subscription, line, now)
    if course and not item["course"] and item["kind"] != "heading":
        known = {c.casefold(): c for c in course_names(subscription)}
        item["course"] = known.get(course.strip().casefold(), "")
        if task is not None:
            task = replace(task, course=item["course"])
    items = _mind(subscription)
    at = _mind_index(items, after) + 1 if after else len(items)
    if item["indent"] and not any(x["kind"] == "todo" for x in items[:at]):
        item["indent"] = 0  # a step needs a line above it
    items.insert(at, item)
    changed = _with_mind(subscription, _kept(items))
    return add_task(changed, task, now=now) if task is not None else changed


def add_template(
    subscription: Subscription, key: str, course: str = "", *, now: datetime | None = None
) -> Subscription:
    """D202: a template's heading and lines at the end of the list, for a course if one is
    chosen; the lines are the student's to change like any other."""
    templates = _todo_templates()
    if key not in templates:
        raise InvalidInput(_("choose one of the templates"))
    known = {c.casefold(): c for c in course_names(subscription)}
    course = known.get(str(course or "").strip().casefold(), "")
    title, lines = templates[key]
    items = _mind(subscription)
    heading = f"{title} · {course}" if course else title
    items.append({"id": secrets.token_hex(4), "text": heading[:MIND_TEXT], "day": "", "kind": "heading",
                  "done": False, "indent": 0, "task": "", "course": course, "links": []})  # fmt: skip
    for text in lines:
        kind = "question" if text.endswith("?") else "todo"
        items.append({"id": secrets.token_hex(4), "text": text, "day": "", "kind": kind, "done": False,
                      "indent": 0, "task": "", "course": course, "links": []})  # fmt: skip
    return _with_mind(subscription, _kept(items))


def _todo_task_done(subscription: Subscription, item: dict, done: bool, now: datetime) -> Subscription:
    names = {t["name"].casefold() for t in subscription.options.get("tasks", ())}
    if item.get("task") and item["task"].casefold() in names:
        return set_task_done(subscription, item["task"], done, now=now)
    return subscription


def _next_time(item: Mapping, today: date) -> date:
    """D202: the day a line that comes back comes back: the next such day after its own day
    (or today, if that is later)."""
    base = max(date.fromisoformat(item["day"]) if item.get("day") else today, today)
    every = int(item["every"])
    if every == 7:
        return base + timedelta(days=1)
    return base + timedelta(days=(every - base.weekday() - 1) % 7 + 1)


def tick_todo(
    subscription: Subscription, item: str, done: bool, *, now: datetime | None = None
) -> Subscription:
    """A line done, or not; its planned task with it. A line that comes back (D202) is
    written again on its next day, once, with its own task if it had hours."""
    now = now or datetime.now(UTC)
    items = _mind(subscription)
    i = _mind_index(items, item)
    if items[i]["kind"] not in TODO_TICKED:
        raise InvalidInput(_("only a to-do is ticked off"))
    items[i] = {**items[i], "done": bool(done)}
    changed = _todo_task_done(_with_mind(subscription, items), items[i], bool(done), now)
    x = items[i]
    if not done or x.get("every") is None or any(y["id"] == x.get("next") for y in items):
        return changed
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    day = _next_time(x, today)
    again = {**x, "id": secrets.token_hex(4), "done": False, "day": day.isoformat(), "task": ""}
    again.pop("next", None)
    again.pop("at", None)
    planned = [t for t in subscription.options.get("tasks", ()) if x.get("task") and t["name"] == x["task"]]
    hours = planned[0]["hours"] if planned else None
    items = _mind(changed)
    i = _mind_index(items, item)
    items[i] = {**items[i], "next": again["id"]}
    task = None
    if hours:
        again["task"] = _free_task_name(changed, f"{x['text']} ({format_date(day, 'day_short')})")
        task = TaskSpec(again["task"], f"{day:%Y-%m-%d} 23:59", float(hours), x.get("course", ""))
    items.insert(i + 1, again)
    changed = _with_mind(changed, _kept(items))
    return add_task(changed, task, now=now) if task is not None else changed


def edit_todo(subscription: Subscription, item: str, text: str) -> Subscription:
    """A line's words changed; an empty line is removed."""
    items = _mind(subscription)
    i = _mind_index(items, item)
    words = " ".join(str(text or "").split())[:MIND_TEXT]
    if not words:
        del items[i]
    else:
        items[i] = {**items[i], "text": words}
    return _with_mind(subscription, items)


def _move_task_due(subscription: Subscription, name: str, day: date, now: datetime) -> Subscription:
    tasks = _task_specs(subscription)
    i = _find_task(subscription, name)
    when = datetime.fromisoformat(str(tasks[i].due).replace(" ", "T"))
    tasks[i] = replace(tasks[i], due=f"{datetime.combine(day, when.time()):%Y-%m-%d %H:%M}")
    return revise_subscription(subscription, tasks=tasks, now=now, strict=False)


def change_todo(
    subscription: Subscription, item: str, how: str, *, to: str = "", now: datetime | None = None
) -> Subscription:
    """Move a line up or down or to a place (`to`, the index it takes), make it a step or
    not, change its kind, move it to tomorrow (its task's day with it, D202), or remove it
    (its planned task with it)."""
    now = now or datetime.now(UTC)
    items = _mind(subscription)
    i = _mind_index(items, item)
    if how in ("up", "down"):
        j = i - 1 if how == "up" else i + 1
        if 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
    elif how == "to":
        try:
            j = max(0, min(len(items) - 1, int(to)))
        except ValueError:
            raise InvalidInput(_("move the line, make it a step, change its kind, or remove it")) from None
        items.insert(j, items.pop(i))
    elif how in ("indent", "outdent"):
        items[i] = {**items[i], "indent": 1 if how == "indent" and i > 0 else 0}
    elif how in TODO_KINDS:
        # A heading is never a step; a heading or a note is never done.
        done = items[i]["done"] and how in TODO_TICKED
        indent = 0 if how == "heading" else items[i]["indent"]
        items[i] = {**items[i], "kind": how, "done": done, "indent": indent}
    elif how == "tomorrow":
        today = now.astimezone(_zone(subscription.options["tz"])).date()
        was = date.fromisoformat(items[i]["day"]) if items[i]["day"] else today
        day = max(was, today) + timedelta(days=1)
        items[i] = {**items[i], "day": day.isoformat()}
        items[i].pop("at", None)
        changed = _with_mind(subscription, items)
        names = {t["name"].casefold() for t in subscription.options.get("tasks", ())}
        if items[i].get("task") and items[i]["task"].casefold() in names:
            return _move_task_due(changed, items[i]["task"], day, now)
        return changed
    elif how == "delete":
        gone = items.pop(i)
        changed = _with_mind(subscription, items)
        names = {t["name"].casefold() for t in subscription.options.get("tasks", ())}
        if gone.get("task") and gone["task"].casefold() in names:
            return delete_task(changed, gone["task"], now=now)
        return changed
    else:
        raise InvalidInput(_("move the line, make it a step, change its kind, or remove it"))
    return _with_mind(subscription, items)


def clear_done(subscription: Subscription) -> Subscription:
    """The lines done, taken off the page (their tasks stay done in the plan's history)."""
    return _with_mind(subscription, [x for x in _mind(subscription) if not x["done"]])


def _note_card(text: str) -> tuple[str, str] | None:
    """D202: the flashcard a note gives, read by the rules that read notes (D84): "term:
    definition", a question and its answer; None for any other note."""
    found = _cardmaker.read(text)
    return (found[0].front, found[0].back) if found else None


def todo_card(
    store: str | os.PathLike, subscription: Subscription, item: str, now: datetime | None = None
) -> Subscription:
    """D202: a note's flashcard, in its course's deck and this week; the note says so after."""
    now = now or datetime.now(UTC)
    items = _mind(subscription)
    i = _mind_index(items, item)
    pair = _note_card(items[i]["text"]) if items[i]["kind"] == "note" else None
    if pair is None:
        raise InvalidInput(_("write the note as a term, a colon and its definition"))
    if items[i].get("card"):
        return subscription
    local = now.astimezone(_zone(subscription.options["tz"])).date()
    monday = local - timedelta(days=local.weekday())
    week = monday.isoformat() if items[i]["course"] else None
    course = items[i]["course"]
    made = _flash(lambda: flashcards.add(_store(store), subscription.token, course, [pair], now, week))
    items[i] = {**items[i], "card": made[0].id}
    return _with_mind(subscription, items)


def _todo_remaining(subscription: Subscription) -> dict[str, float]:
    """Each task's hours still to do: its hours less the share of its blocks done."""
    hours = {t["name"]: float(t["hours"]) for t in subscription.options.get("tasks", ())}
    if subscription.plan is None:
        return hours
    plan = PlanReport.from_dict(subscription.plan)
    worked: dict[str, int] = {}
    for x in plan.history:
        if x.kind == "task" and x.outcome != "skipped":
            worked[x.title] = worked.get(x.title, 0) + 1
    out = {}
    for t in plan.tasks:
        whole = hours.get(t.name, 0.0)
        out[t.name] = 0.0 if t.done else whole * max(0, t.blocks - worked.get(t.name, 0)) / (t.blocks or 1)
    return out


def _free_left_today(subscription: Subscription, now: datetime, tasks: Collection[str]) -> float:
    """D202: the hours left today between now (or getting up) and going to bed, less the
    classes, tests, busy times and the plan's study still to come, the sessions of `tasks`
    apart (they are the work being weighed)."""
    day = day_view(subscription)
    up = _hours_of(day["wake"], _("Get up"))
    down = _hours_of(day["bed"], _("Go to bed"))
    if down <= up:
        down += 24
    local = now.astimezone(_zone(subscription.options["tz"]))
    hour = max(up, local.hour + local.minute / 60)
    if hour >= down:
        return 0.0
    taken = 0.0
    for x in day_agenda(subscription, now):
        if x["kind"] == "study" and x["label"] in tasks:
            continue
        start, end = _hours_of(x["start"], ""), _hours_of(x["end"], "")
        if end <= start:
            end += 24
        taken += max(0.0, min(end, down) - max(start, hour))
    return max(0.0, down - hour - taken)


def todo_view(subscription: Subscription, now: datetime | None = None, course: str = "") -> dict:
    """The to-do page: its lines in order, each with its day said in words, whether it is
    late, its planned task's hours and its course's colour, its words in formatted pieces,
    its links as labels, a note's flashcard; the lines done apart; the plan's other tasks
    (a learning platform's deadlines, those added before D195); and D202's board by day,
    templates and today's fit. `course` keeps a course's lines only (its page, D202)."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    local = now.astimezone(zone)
    today = local.date()
    colours = {}
    if subscription.plan is not None:
        colours = {
            name.casefold(): index
            for name, index in course_colours(
                PlanReport.from_dict(subscription.plan), drawn_courses(subscription)
            )
        }
    tasks = {t["name"].casefold(): t for t in subscription.options.get("tasks", ())}
    remaining = _todo_remaining(subscription)

    def when(day: str) -> tuple[str, bool]:
        if not day:
            return "", False
        d = date.fromisoformat(day)
        if d == today:
            return _("today"), False
        if d == today + timedelta(days=1):
            return _("tomorrow"), False
        if d < today:
            return _("since {day}", day=_i18n.format_date(d, "short")), True
        return _i18n.format_date(d, "short"), False

    def again(x: Mapping) -> str:
        every = x.get("every")
        if every is None:
            return ""
        if int(every) == 7:
            return _("every day")
        return _("every {day}", day=_i18n.weekday_name(date(2026, 1, 5) + timedelta(days=int(every))))

    rows: list[dict] = []
    done: list[dict] = []
    parent_done = False  # a step of a line done goes with it
    items = list(enumerate(_mind(subscription)))  # each line's place in the whole list, for a drag
    if course:
        items = [(pos, x) for pos, x in items if x["course"].casefold() == course.casefold()]
    for pos, x in items:
        if not x["indent"]:
            parent_done = x["done"] and x["kind"] in TODO_TICKED
        label, late = when(x["day"])
        task = tasks.get(x["task"].casefold()) if x["task"] else None
        # A card belongs to a course's deck: a note without a course offers none.
        pair = _note_card(x["text"]) if x["kind"] == "note" and x["course"] else None
        row = {
            **x,
            "when": label,
            "late": late and not x["done"],
            "today": bool(x["day"]) and date.fromisoformat(x["day"]) <= today,
            "hours": task["hours"] if task else None,
            "color": colours.get(x["course"].casefold()) if x["course"] else None,
            "parts": todo_parts(x["text"]),
            "links": [_link_label(subscription, u) for u in x.get("links") or ()],
            "again": again(x),
            "at_time": x["at"][11:16] if x.get("at") else "",
            "card_pair": {"front": pair[0], "back": pair[1]} if pair else None,
            "pos": pos,
        }
        (done if parent_done else rows).append(row)
    # A heading counts the open lines under it, to the next heading (steps apart).
    for i, r in enumerate(rows):
        if r["kind"] == "heading":
            under = []
            for x in rows[i + 1 :]:
                if x["kind"] == "heading":
                    break
                under.append(x)
            r["open"] = sum(
                1 for x in under if x["kind"] in TODO_TICKED and not x["indent"] and not x["done"]
            )
    linked = {x["task"].casefold() for x in _mind(subscription) if x["task"]}
    others = []
    for t in subscription.options.get("tasks", ()):
        if t["name"].casefold() in linked or t.get("done"):
            continue
        if course and str(t.get("course", "")).casefold() != course.casefold():
            continue
        try:
            due = datetime.fromisoformat(str(t["due"]).replace(" ", "T")).date()
        except ValueError:
            continue
        label, late = when(due.isoformat())
        t_course = str(t.get("course", ""))
        others.append(
            {"name": t["name"], "when": label, "late": late, "hours": t["hours"], "course": t_course,
             "color": colours.get(t_course.casefold()), "due": due}
        )  # fmt: skip
    # D202: the board, the open lines by day.
    sunday = today + timedelta(days=6 - today.weekday())
    columns = [
        ("late", _("Late")),
        ("today", _("Today")),
        ("tomorrow", _("Tomorrow")),
        ("week", _("This week")),
        ("later", _("Later")),
        ("none", _("No day")),
    ]
    board: dict[str, list[dict]] = {key: [] for key, _label in columns}
    for r in rows:
        if r["kind"] not in TODO_TICKED or r["done"] or r["indent"]:
            continue
        d = date.fromisoformat(r["day"]) if r["day"] else None
        key = (
            "none"
            if d is None
            else "late"
            if d < today
            else "today"
            if d == today
            else "tomorrow"
            if d == today + timedelta(days=1)
            else "week"
            if d <= sunday
            else "later"
        )
        board[key].append(r)
    drop = {
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "week": sunday.isoformat() if sunday > today + timedelta(days=1) else "",
        "later": (sunday + timedelta(days=1)).isoformat(),
        "none": "none",
        "late": "",
    }
    # D202: today's work against today's free time: the hours left of the tasks of the lines
    # due today or before, and the free hours left, the sessions of those tasks apart.
    due_today = [r for r in rows if r["kind"] == "todo" and not r["done"] and r["today"]]
    names = [r["task"] for r in due_today if r["task"]]
    work = round(sum(remaining.get(n, 0.0) for n in names), 1)
    fit = None
    if due_today:
        free = round(_free_left_today(subscription, now, set(names)), 1) if subscription.plan else None
        over = round(work - free, 1) if free is not None and work > free else 0.0
        move = next((r for r in reversed(due_today) if r["task"]), None) if over else None
        fit = {
            "lines": len(due_today),
            "work": format_number(work),
            "free": format_number(free) if free is not None else None,
            "over": format_number(over) if over else "",
            "percent": min(100, round(100 * work / free)) if free else (100 if work else 0),
            "move": {"id": move["id"], "text": move["text"]} if move else None,
        }
    return {
        "rows": rows,
        "done": done,
        "others": sorted(others, key=lambda o: o["due"]),
        "open": sum(1 for r in rows if r["kind"] in TODO_TICKED and not r["done"]),
        "board": [
            {"key": key, "label": label, "rows": board[key], "drop": drop[key]} for key, label in columns
        ],
        "fit": fit,
        "templates": [{"key": k, "title": t} for k, (t, _lines) in _todo_templates().items()],
        "courses": course_names(subscription),
    }


def mind_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """The list on Today: the items with a day first, in their days' order (today's and
    those left from before marked), then the notes, newest first; and whether to ask
    "Close the day": from three hours before bed until getting up, once an evening."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_zone(subscription.options["tz"]))
    today = local.date()
    evening = _evening_of(subscription, local)
    items = [x for x in _mind(subscription) if x["kind"] == "todo" and not x["done"] and not x["indent"]]
    planned = sorted((x for x in items if x["day"]), key=lambda x: x["day"])
    notes = [x for x in reversed(items) if not x["day"]]
    rows = []
    for x in [*planned, *notes]:
        day = date.fromisoformat(x["day"]) if x["day"] else None
        rows.append(
            {
                "id": x["id"],
                "text": x["text"],
                "day": x["day"],
                "when": ""
                if day is None
                else _("today")
                if day == today
                else _("tomorrow")
                if day == today + timedelta(days=1)
                else _("since {day}", day=_i18n.format_date(day, "short"))
                if day < today
                else _i18n.format_date(day, "short"),
                "today": day is not None and day <= today,
                "left": day is not None and day < today,
            }
        )
    bed = _hours_of(day_view(subscription)["bed"], _("Go to bed"))
    wake = _hours_of(day_view(subscription)["wake"], _("Get up"))
    hour = local.hour + local.minute / 60
    from_hour = (bed - CLOSE_BEFORE_BED.total_seconds() / 3600) % 24
    # Between `from_hour` and getting up, across midnight when bed is late.
    late = hour >= from_hour or hour < wake if from_hour > wake else from_hour <= hour < wake
    asked = late and subscription.options.get("closed") != evening.isoformat()
    # Its evidence a tap away: the bedtime list in the evening, the plan otherwise.
    key = "bedtime-list" if late else "plan-it"
    tip = next(t for t in _tips.TIPS if t.key == key)
    return {
        "entries": rows,
        "today_iso": today.isoformat(),
        "tip": {"key": tip.key, "topic": tip.topic} if tips_on(subscription) else None,
        "today": sum(1 for r in rows if r["today"]),
        "close": asked,
        "closed": subscription.options.get("closed") == evening.isoformat(),
        "firsts": CLOSE_FIRSTS,
    }


# The workspace on Today (D140; the owner, 2026-11-18: "there could be external links to
# moodle where the material is, so students can manage the workspace from there").
WORKSPACE_COURSES = 6  # the courses shown on Today; the rest are on Courses
WORKSPACE_LINKS = 4  # a course's links shown on Today
_MOODLE_EXPORT = re.compile(
    r"^(https?://[^/?#\s]+(?:/[^?#\s]*?)?)/calendar/export_execute\.php", re.IGNORECASE
)


# AUDIT 138: a Moodle page pasted where its calendar's link goes. The owner met "the link does
# not lead to a calendar" with no word of what to copy instead: the likeliest slip is the
# address bar of the Export calendar page itself, after its button, rather than the link
# Moodle shows below the button. Told before anything is fetched, in Moodle's own words.
_MOODLE_PAGE = re.compile(
    r"^(https?://[^/?#\s]+(?:/[^?#\s]*?)?)/(calendar/export\.php|calendar/[a-z_]+\.php|course/view\.php|"
    r"my/(?:courses\.php|index\.php)?|mod/[a-z]+/view\.php|user/[a-z]+\.php|login/index\.php)(?:[?#]|$)",
    re.IGNORECASE,
)


# AUDIT 139: Moodle's words for the way to the key, from Moodle 4.5's language packs
# (lang/*/moodle.php "preferences" and "reset", webservice.php "securitykeys"). Since Moodle
# 4.3 the Security keys page lists keys by name only: a key is shown once, at the top of the
# page, just after "Reset" makes a new one (user/managetoken.php).
MOODLE_KEY_WORDS: dict[str, dict[str, str]] = {
    "en": {"preferences": "Preferences", "keys": "Security keys", "reset": "Reset"},
    "fr": {"preferences": "Préférences", "keys": "Clefs de sécurité", "reset": "Réinitialiser"},
    "it": {"preferences": "Preferenze", "keys": "Chiave di sicurezza", "reset": "Reset"},
    "es": {"preferences": "Preferencias", "keys": "Claves de seguridad", "reset": "Reiniciar"},
}


def moodle_key_words() -> dict[str, str]:
    """Moodle's words for its Security keys page and its Reset, in the app's language."""
    return MOODLE_KEY_WORDS.get(current_language(), MOODLE_KEY_WORDS["en"])


def moodle_export_words() -> dict[str, str]:
    """Moodle's words for the way to its calendar's link (D154), in the app's language."""
    return MOODLE_EXPORT_WORDS.get(current_language(), MOODLE_EXPORT_WORDS["en"])


def moodle_page_refusal(url: str) -> str | None:
    """Why a Moodle page's address is not its calendar's link, and where the link is; None
    for any other address (AUDIT 138)."""
    found = _MOODLE_PAGE.match(url.strip())
    if not found or _MOODLE_EXPORT.match(url.strip()):
        return None
    words = moodle_export_words()
    if found.group(2).lower() == "calendar/export.php":
        return _(
            "this is the address of the page «{export}», not the calendar's link: on that page, tap "
            "«{button}» and copy the link Moodle then shows below the button (it contains "
            "export_execute.php), not the address bar's",
            export=words["export"],
            button=words["button"],
        )
    return _(
        "this is the address of a Moodle page, not of its calendar: open {page}, choose «{events}» "
        "and «{period}», tap «{button}» and copy the link Moodle then shows below the button",
        page=f"{found.group(1)}/calendar/export.php",
        events=words["events"],
        period=words["period"],
        button=words["button"],
    )


def learning_platform(subscription: Subscription) -> str | None:
    """The learning platform's own page: a student's dashboard is at `<site>/my/` on every
    Moodle site, in a subfolder too. The site is the student's Moodle (`moodle_site`,
    D163), first known from the calendar link they gave for its deadlines (D92). None
    without one: the app does not guess an address."""
    site = moodle_site(subscription)
    return f"{site}/my/" if site else None


# --------------------------------------------------------------------------- #
# Moodle within reach (DECISIONS.md D163)
# --------------------------------------------------------------------------- #

# A course's pages on Moodle, each its standard address with the course's number, as
# Moodle 4.5's own files read it (`required_param('id', PARAM_INT)`, the course):
# course/view.php, course/resources.php (every file and page of the course),
# mod/assign/index.php (its assignments), grade/report/user/index.php (the student's
# marks). They open in the student's browser, where Moodle asks for its own sign-in:
# the app keeps addresses, never a password or a key (D92).
MOODLE_PAGES = (
    ("course", "/course/view.php?id={id}"),
    ("materials", "/course/resources.php?id={id}"),
    ("assignments", "/mod/assign/index.php?id={id}"),
    ("grades", "/grade/report/user/index.php?id={id}"),
)
# A course's address as a student copies it from Moodle: its page, or one of the pages
# above, the course's number as `id`, in a subfolder too (`https://x.edu/moodle/...`).
_MOODLE_COURSE = re.compile(
    r"^(https?://[^/?#\s@]+(?:/[^?#\s]*?)?)"
    r"/(?:course/view|course/resources|mod/assign/index|grade/report/user/index)\.php"
    r"\?(?:[^#\s]*?&)?id=(\d{1,10})(?:[&#][^\s]*)?$",
    re.IGNORECASE,
)
# The site alone (`moodle.example.edu`, with or without https://, or a page at its root
# such as its dashboard), so "Find it on Moodle" can search it before any course is linked.
_MOODLE_HOME = re.compile(
    r"^(?:(https?)://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+(?::\d{1,5})?)"
    r"((?:/[^?#\s/]+){0,2}?)/?(?:/(?:my(?:/courses\.php|/index\.php)?|index\.php|login/index\.php)/?)?$",
    re.IGNORECASE,
)


def moodle_site(subscription: Subscription) -> str | None:
    """The student's Moodle (D163): the one they gave, else the site of their deadlines'
    calendar link (D92), else the site of a course they linked. None without one."""
    options = subscription.options
    if options.get("moodle_site"):
        return str(options["moodle_site"])
    found = _deadlines_site(options)
    if found:
        return found
    for entry in (options.get("moodle_courses") or {}).values():
        return str(entry["site"])
    return None


def read_moodle_address(text: str) -> tuple[str, int | None]:
    """A Moodle address as a student pastes it: a course's page (its site and number), or
    the site alone (its number None). Anything else is refused: the app builds every
    address it opens from these two, never from the text."""
    text = text.strip()
    found = _MOODLE_COURSE.match(text)
    if found:
        return found.group(1).rstrip("/"), int(found.group(2))
    home = _MOODLE_HOME.match(text)
    if home and len(text) <= 300:
        scheme = (home.group(1) or "https").lower()
        return f"{scheme}://{home.group(2).lower()}{home.group(3)}", None
    raise InvalidInput(
        _("a course's address on Moodle, such as https://moodle.example.edu/course/view.php?id=123")
    )


def _host(site: str | None) -> str:
    return (urlsplit(site).hostname or "") if site else ""


def moodle_course(subscription: Subscription, course: str) -> dict:
    """A course on the student's Moodle (D163): once linked, its four pages; before, the
    address of Moodle's own course search with the course's name, when the site is
    known."""
    entry = (subscription.options.get("moodle_courses") or {}).get(course_key(course))
    if entry:
        site, number = str(entry["site"]), int(entry["id"])
        return {
            "linked": True,
            "host": _host(site),
            "id": number,
            "pages": {key: site + path.format(id=number) for key, path in MOODLE_PAGES},
            "find": None,
        }
    known = moodle_site(subscription)
    return {
        "linked": False,
        "host": _host(known),
        "id": None,
        "pages": {},
        "find": f"{known}/course/search.php?{urlencode({'search': course})}" if known else None,
    }


def moodle_home(subscription: Subscription) -> dict | None:
    """The student's Moodle for the sidebar and You (D163): the dashboard and every
    course; None until the site is known."""
    site = moodle_site(subscription)
    if not site:
        return None
    return {"dashboard": f"{site}/my/", "courses": f"{site}/my/courses.php", "host": _host(site)}


def link_moodle_course(subscription: Subscription, index: int, form: Mapping[str, str]) -> Subscription:
    """A course linked to its page on Moodle, from the address pasted (D163). The site
    alone is kept as the student's Moodle, so its course search can find the course."""
    course = _topic_course(subscription, index)
    text = str(form.get("address") or "").strip()
    if len(text) > 500:
        raise InvalidInput(_("at most {n} characters here", n=500))
    site, number = read_moodle_address(text)
    options = dict(subscription.options)
    if number is None:
        options["moodle_site"] = site
    else:
        options["moodle_courses"] = {
            **(options.get("moodle_courses") or {}),
            course_key(course): {"site": site, "id": number},
        }
    return replace(subscription, options=options)


def unlink_moodle_course(subscription: Subscription, index: int) -> Subscription:
    course = _topic_course(subscription, index)
    linked = dict(subscription.options.get("moodle_courses") or {})
    linked.pop(course_key(course), None)
    return replace(subscription, options={**subscription.options, "moodle_courses": linked})


# D154: Moodle's own words for the way to its calendar's link, in the app's languages,
# read from Moodle 4.5's strings (lang/en/calendar.php and its French, Italian and Spanish
# language packs): the calendar page's button to its subscriptions ("managesubscriptions"), the
# export page ("exportcalendar"), its two choices ("eventsall", "recentupcoming") and the
# button that shows the link ("generateurlbutton"). The export page is the same address
# on every Moodle site, `<site>/calendar/export.php` (calendar/export.php).
MOODLE_EXPORT_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "calendar": "Calendar",
        "manage": "Import or export calendars",
        "export": "Export calendar",
        "events": "All events",
        "period": "Recent and next 60 days",
        "button": "Get calendar URL",
    },
    "fr": {
        "calendar": "Calendrier",
        "manage": "Importer ou exporter des calendriers",
        "export": "Exporter le calendrier",
        "events": "Tous les événements",
        "period": "Les 60 prochains jours",
        "button": "URL du calendrier",
    },
    "it": {
        "calendar": "Calendario",
        "manage": "Importa o esporta calendari",
        "export": "Esporta calendario",
        "events": "Tutti gli eventi",
        "period": "Recenti e dei prossimi 60 giorni",
        "button": "URL del calendario",
    },
    # D201: Moodle 4.5's Spanish pack (lang/es/calendar.php).
    "es": {
        "calendar": "Calendario",
        "manage": "Importar o exportar calendarios",
        "export": "Exportar calendario",
        "events": "Todos los eventos",
        "period": "60 días recientes y próximos",
        "button": "Obtener URL del calendario",
    },
}


def deadlines_preview(
    url: str,
    tz: str,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
    most: int = 5,
) -> dict:
    """What a learning platform's link would bring (D154), before it is saved: the
    deadlines still ahead, the first `most` of them, and how many in all; or why the link
    gives none. Nothing is kept."""
    now = now or datetime.now(UTC)
    zone = _zone(tz)
    refusal = moodle_page_refusal(url)  # AUDIT 138: said before anything is fetched
    if refusal:
        return {"found": [], "count": 0, "error": refusal}
    try:
        found = [a for a in find_assignments(decode_ics(fetch(url.strip())), tz) if a.due > now]
    except ServiceError as error:
        return {"found": [], "count": 0, "error": str(error)}
    except ValueError:
        return {"found": [], "count": 0, "error": _("this link does not give a calendar")}
    return {
        "found": [
            {"name": a.name, "course": a.course, "due": a.due.astimezone(zone).strftime("%Y-%m-%d %H:%M")}
            for a in found[:most]
        ],
        "count": len(found),
        "error": None,
    }


# D154: setting up shows the plan's defaults as one sentence, each value a choice. The
# choices are around where most students' days fall; a value set elsewhere is added, so
# the sentence never shows what the plan does not do.
WAKE_CHOICES = ("06:30", "07:00", "07:30", "08:00", "08:30", "09:00")
BED_CHOICES = ("22:00", "22:30", "23:00", "23:30", "00:00", "00:30")
LUNCH_START_CHOICES = ("12:00", "12:30", "13:00")
SESSION_CHOICES = (1, 2, 3, 4)
MINUTE_CHOICES = (45, 60, 90, 120)
# The busy times a student adds in a tap, each with the hours it starts at; the days are
# theirs to pick. Training and a job are ROUTINE_QUESTIONS' kinds (D42).
QUICK_BUSY: tuple[tuple[str, str, str, str], ...] = (
    ("training", "Training", "18:00", "20:00"),
    ("work", "Work", "09:00", "13:00"),
    ("other", "Busy", "17:00", "18:00"),
)
# D172 (the owner: "one may have more jobs and shifts so it needs to be more flexible"):
# each busy time added in setting up's sentence holds up to this many shifts, each its own
# days and hours, and its own name if it is another job.
QUICK_SHIFTS = 4


def routine_rows(answers: Iterable[Mapping]) -> list[dict]:
    """Weekly busy times from answers about the week (D42): one row per day chosen. An
    answer left empty is none; one half given says what is missing."""
    kinds = {k for k, _ in ACTIVITY_KINDS} - {"class"}
    rows: list[dict] = []
    for answer in answers:
        kind = str(answer.get("kind") or "")
        kind = kind if kind in kinds else "other"
        label = str(answer.get("label") or "").strip()[:60]
        days = list(dict.fromkeys(str(d) for d in answer.get("days") or () if str(d).strip()))
        start, end = (str(answer.get(k) or "").strip() for k in ("start", "end"))
        if not (label or days or start or end):
            continue
        name = label or _(_ROUTINE_NAMES.get(kind, "Busy"))
        if not days:
            raise InvalidInput(_("{name}: choose the days", name=name))
        if not start or not end:
            raise InvalidInput(_("{name}: give the hours", name=name))
        if start == end:
            # Ending earlier than it starts is a night shift, ending the next day (D172);
            # ending when it starts would be a whole day taken.
            raise InvalidInput(_("{name}: it ends when it starts", name=name))
        rows += [
            {"label": name, "kind": kind, "weekday": day, "date": None, "start": start, "end": end}
            for day in days
        ]
    return rows


def busy_groups(activities: Iterable[Mapping]) -> dict:
    """Settings' busy times as setting up adds them (D173, after D172): the weekly ones one
    card each, by name, kind and hours, with their days in the week's order (a night shift
    marked, ending the next day); the ones on a date as they are, one row each."""
    weekly: dict[tuple, dict] = {}
    dated = []
    for a in activities:
        day = str(a.get("weekday") or "")
        if a.get("date") or day not in WEEKDAY_NAMES:
            dated.append(dict(a))
            continue
        key = (
            str(a.get("label") or ""),
            str(a.get("kind") or "other"),
            str(a.get("start") or ""),
            str(a.get("end") or ""),
        )
        group = weekly.setdefault(
            key, {"label": key[0], "kind": key[1], "start": key[2], "end": key[3], "days": []}
        )
        if day not in group["days"]:
            group["days"].append(day)
    for group in weekly.values():
        group["days"].sort(key=WEEKDAY_NAMES.index)
        group["overnight"] = bool(group["start"] and group["end"]) and group["end"] <= group["start"]
    return {"weekly": list(weekly.values()), "dated": dated}


def quick_busy_view() -> dict:
    """The busy times a student can add with their shifts (D172), for Settings (D173)."""
    return {
        "kinds": [{"kind": k, "name": n, "start": a, "end": b} for k, n, a, b in QUICK_BUSY],
        "shifts": QUICK_SHIFTS,
    }


def setup_sentence(values: Mapping) -> dict:
    """The sentence setting up shows (D154), from `setup_view`'s values (or the form's,
    sent back with a mistake): each value's choices, the current one among them, the busy
    times other than classes grouped by name and hours, the deadlines, and Moodle's words
    in the student's language."""
    day = values["day"]

    def among(choices: Iterable, current: object) -> list:
        out = list(choices)
        return out if current in out or current in ("", None) else sorted([*out, current], key=str)

    wake = str(day["wake"])
    bed = str(day["bed"])
    busy: dict[tuple, list[str]] = {}
    for a in values["activities"]:
        if a.get("kind") == "class" or not a.get("weekday"):
            continue
        busy.setdefault((a.get("label") or "", a.get("start"), a.get("end")), []).append(_(a["weekday"]))
    words = moodle_export_words()
    site = values.get("deadlines_site") or None
    # Each choice as (value, words), for the radio and for the sentence.
    return {
        "wake": [(t, t) for t in among(WAKE_CHOICES, wake)],
        "bed": [(t, t) for t in among(BED_CHOICES, bed)],
        "lunch": [(t, t) for t in among(LUNCH_START_CHOICES, str(day["lunch_start"]))]
        + [("study", _("any time (I can study then)"))],
        "lunch_now": "study" if day["lunch"] == "study" else str(day["lunch_start"]),
        "sessions": [(n, str(n)) for n in among(SESSION_CHOICES, int(values["blocks_per_day"]))],
        "minutes": [(m, _("{n} min", n=m)) for m in among(MINUTE_CHOICES, int(values["block_minutes"]))],
        "commute": values["commute_choices"],
        "busy": [
            # A shift that ends earlier than it starts ends the next day (D172).
            {"label": label, "days": days, "start": start, "end": end, "overnight": str(end) <= str(start)}
            for (label, start, end), days in busy.items()
        ],
        "quick": [
            {"kind": kind, "name": name, "start": start, "end": end} for kind, name, start, end in QUICK_BUSY
        ],
        "shifts": QUICK_SHIFTS,
        "moodle": words,
        # The export page of the Moodle whose link was given, to give it again; else the
        # student finds it by Moodle's own words (the page has the same address on every
        # site, but which site is theirs the app does not guess).
        "export_page": f"{site}/calendar/export.php" if site else None,
    }


def workspace_view(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Each course with the links the student keeps for it (D88: its page on the learning
    platform, slides, a folder), this week's first, then the course's own; each opens
    where it lives, and adding one is a tap away. The learning platform itself when it
    is known. None without courses."""
    courses = topic_courses(subscription)
    if not courses:
        return None
    now = now or datetime.now(UTC)
    monday = _progress.monday(now.astimezone(_zone(subscription.options["tz"])).date()).isoformat()
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    colours = {name.casefold(): index for name, index in course_colours(plan)} if plan else {}
    rows = []
    for index, name in enumerate(courses[:WORKSPACE_COURSES]):
        links = [
            x
            for x in _links(subscription, name)
            if x.get("week") == monday or (not x.get("week") and not x.get("source"))
        ]
        links.sort(key=lambda x: x.get("week") != monday)
        rows.append(
            {
                "index": index,
                "name": name,
                "color": colours.get(name.casefold()),
                "links": [
                    {
                        **x,
                        "host": (urlsplit(x["url"]).hostname or "").removeprefix("www."),
                        "this_week": x.get("week") == monday,
                    }
                    for x in links[:WORKSPACE_LINKS]
                ],
                "more": max(0, len(links) - WORKSPACE_LINKS),
            }
        )
    return {
        "platform": learning_platform(subscription),
        "courses": rows,
        "more": max(0, len(courses) - WORKSPACE_COURSES),
    }


# Today's two tips (D140): one to study (how to study, how the brain learns, motivation),
# one to live well (sleep, movement and coffee, breaks, habits), taking turns day by day.
STUDY_TOPICS = ("studying", "brain", "motivation")
LIVE_TOPICS = ("body", "breaks", "habits")


def tips_for_today(
    subscription: Subscription, now: datetime | None = None, *, besides: str = ""
) -> list[dict]:
    """A tip to study and a tip to live well, each a different one every day and the same
    for everyone on a date, each with how strong its evidence is and a link to it; never
    `besides`, the tip the day's message already gives. Empty when tips are off (D62)."""
    if not tips_on(subscription):
        return []
    now = now or datetime.now(UTC)
    day = now.astimezone(_zone(subscription.options["tz"])).date().toordinal()
    out = []
    for kind, topics in (("study", STUDY_TOPICS), ("live", LIVE_TOPICS)):
        pool = [t for t in _tips.TIPS if t.topic in topics and t.key != besides]
        tip = pool[day % len(pool)]
        out.append(
            {
                "kind": kind,
                "key": tip.key,
                "topic": tip.topic,
                "topic_name": _(_tips.TOPICS[tip.topic]),
                "advice": _(tip.advice),
                "strength": _(_tips.STRENGTHS[tip.strength][0]),
            }
        )
    return out


BANKED_FOR = timedelta(hours=3)  # how long an answered self-test's result stays on Today


def _day_done(
    cards: Sequence[dict],
    moments: Sequence[tuple[datetime, datetime]],
    gains: Mapping[str, dict],
    local: datetime,
) -> dict | None:
    """Done for today (D125): once every session planned today is reported, none is
    running and at least one was kept, how many were kept and the memory days the
    day's answered self-tests added (the net, shown when positive). None on a day
    with nothing planned: there is nothing to be done with."""
    todays = [c for c, (start, end) in zip(cards, moments, strict=True) if start.date() == local.date()]
    running = any(start <= local < end for start, end in moments)
    kept = [c for c in todays if c["reported"] and c["reported"] != "skipped"]
    if not todays or running or not kept or any(not c["reported"] for c in todays):
        return None
    added = sum(gains[c["id"]]["added"] for c in kept if c["memory"] and c["id"] in gains)
    return {"kept": len(kept), "of": len(todays), "added": max(0, round(added))}


def _just_banked(
    cards: Sequence[dict], moments: Sequence[tuple[datetime, datetime]], local: datetime
) -> dict | None:
    """The latest self-test answered today whose session ended within `BANKED_FOR`:
    its card, with the verdict, for Today to show the moment's result (D125)."""
    recent = [
        c
        for c, (start, end) in zip(cards, moments, strict=True)
        if c["memory"] and start.date() == local.date() and start <= local and local - end <= BANKED_FOR
    ]
    return recent[-1] if recent else None


def _exam_rows(plan: PlanReport, now: datetime, colours: Mapping[str, int]) -> list[dict]:
    """The exams still to come, soonest first, with how many of their topics are on
    track."""
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    return [
        {
            "name": x.name,
            "when": _when(datetime.fromisoformat(x.exam).astimezone(zone), local),
            "days": (datetime.fromisoformat(x.exam) - now) / timedelta(days=1),
            "status": subject_status(x),
            "ready": x.ready,
            "topics": x.topics,
            "topics_ready": x.topics_ready if x.topics > 1 else int(x.ready),
            "percent": round(100 * (x.topics_ready if x.topics > 1 else int(x.ready)) / max(x.topics, 1)),
            "color": colours.get(x.name.casefold()),
        }
        for x in sorted(plan.subjects, key=lambda x: x.exam)
        if datetime.fromisoformat(x.exam) > now
    ]


def _task_rows(
    subscription: Subscription, plan: PlanReport, now: datetime, colours: Mapping[str, int]
) -> list[dict]:
    """Every task of the plan as the pages show it: open ones by due date, then the
    finished ones, most recent first."""
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    hours = {t["name"]: t["hours"] for t in subscription.options.get("tasks", ())}
    imported = set(subscription.options.get("imported", {}).values())
    worked: dict[str, int] = {}
    for x in plan.history:
        if x.kind == "task" and x.outcome != "skipped":
            worked[x.title] = worked.get(x.title, 0) + 1
    coming: dict[str, list[SessionView]] = {}
    for x in plan.sessions:
        if x.kind == "task":
            coming.setdefault(x.title, []).append(x)
    rows = []
    for t in plan.tasks:
        due = datetime.fromisoformat(t.due).astimezone(zone)
        ahead = coming.get(t.name, [])
        done_blocks = worked.get(t.name, 0)
        if t.done:
            status = "done"
        elif due < local:
            status = "late"
        elif t.at_risk:
            status = "at risk"
        else:
            status = "on track"
        nxt = datetime.fromisoformat(ahead[0].start).astimezone(zone) if ahead else None
        progress = min(done_blocks / t.blocks, 1.0) if t.blocks else 1.0
        rows.append(
            {
                "name": t.name,
                "course": t.course,
                "color": colours.get(t.course.casefold()),
                "due": _when(due, local),
                "due_value": f"{due:%Y-%m-%dT%H:%M}",
                "days": (due - local) / timedelta(days=1),
                "hours": hours.get(t.name, 0.0),
                "blocks": t.blocks,
                "sessions_done": done_blocks,
                "sessions_planned": len(ahead),
                "next": _when(nxt, local) if nxt else "",
                "percent": 100 if t.done else round(100 * progress),
                "finish": _when(datetime.fromisoformat(t.finish).astimezone(zone), local) if t.finish else "",
                "at_risk": t.at_risk,
                "past": due < local,
                "finished": t.done,
                "status": status,
                "imported": t.name in imported,
                "steps": _step_rows(subscription, t.name),
            }
        )
    rows.sort(key=lambda r: (r["finished"], -r["days"] if r["finished"] else r["days"]))
    return rows


def tasks_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """What the Tasks page shows: every deadline with where its work stands, and
    a count of what needs attention."""
    now = now or datetime.now(UTC)
    view: dict = {"planned": subscription.plan is not None, "error": subscription.error, "rows": []}
    if subscription.plan is not None:
        plan = PlanReport.from_dict(subscription.plan)
        colours = {name.casefold(): index for name, index in course_colours(plan)}
        view["rows"] = _task_rows(subscription, plan, now, colours)
    else:
        view["rows"] = [
            {
                "name": t["name"],
                "course": t.get("course", ""),
                "color": None,
                "due": str(t["due"])[:16].replace("T", " "),
                "due_value": str(t["due"])[:16].replace(" ", "T"),
                "days": 0.0,
                "hours": t["hours"],
                "blocks": 0,
                "sessions_done": 0,
                "sessions_planned": 0,
                "next": "",
                "percent": 100 if t.get("done") else 0,
                "finish": "",
                "at_risk": False,
                "past": False,
                "finished": bool(t.get("done")),
                "status": "done" if t.get("done") else "not planned",
                "imported": False,
                "steps": _step_rows(subscription, t["name"]),
            }
            for t in subscription.options.get("tasks", ())
        ]
    rows = view["rows"]
    view["open"] = sum(not r["finished"] for r in rows)
    view["at_risk"] = sum(r["status"] in ("at risk", "late") for r in rows)
    view["this_week"] = sum(not r["finished"] and 0 <= r["days"] < 7 for r in rows)
    view["finished"] = sum(r["finished"] for r in rows)
    view["week"] = task_week(subscription, now)
    view["platform"] = {
        "linked": bool(subscription.options.get("deadlines_url")),
        "error": subscription.options.get("deadlines_error"),
        "brought": len(set(subscription.options.get("imported", {}).values())),
        "site": learning_platform(subscription),
    }
    return view


def task_week(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """D162: the time the plan gives tasks this week, Monday to Sunday in the plan's time
    zone, and the part of it done: past sessions count unless reported skipped, as a
    task's own progress counts them (`_task_rows`); the next one to come. None without
    a plan."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = (now or datetime.now(UTC)).astimezone(zone)
    monday = datetime.combine(local.date() - timedelta(days=local.weekday()), time(0, 0), zone)
    sunday = monday + timedelta(days=7)
    planned = done = 0.0
    upcoming: list[tuple[datetime, str]] = []
    for s, past in [(x, True) for x in plan.history] + [(x, False) for x in plan.sessions]:
        if s.kind != "task":
            continue
        start = datetime.fromisoformat(s.start).astimezone(zone)
        if not past and start >= local:
            upcoming.append((start, s.title))
        if not monday <= start < sunday:
            continue
        minutes = (datetime.fromisoformat(s.end) - datetime.fromisoformat(s.start)) / timedelta(minutes=1)
        planned += minutes
        if past and s.outcome != "skipped":
            done += minutes
    first = min(upcoming, default=None)
    return {
        "planned": planned / 60,
        "done": done / 60,
        "percent": min(100, round(100 * done / planned)) if planned else 0,
        "next": {"task": first[1], "when": _when(first[0], local)} if first else None,
    }


# --------------------------------------------------------------------------- #
# Progress, the streak and the weekly review (DECISIONS.md, D8 and D9)


def progress_sessions(subscription: Subscription) -> list[_progress.Session]:
    """Every session, past and to come, as `progress` counts them: local start,
    length, kind, course, and what the student reported. Past semesters' come
    first (`start_next_semester`, D36), then the current plan's."""
    zone = _zone(subscription.options["tz"])
    past = [
        _progress.Session(
            start=datetime.fromisoformat(p["start"]).astimezone(zone),
            minutes=int(p["minutes"]),
            kind=str(p["kind"]),
            course=str(p["course"]),
            report=p.get("report"),
            sid=str(p.get("sid", "")),
        )
        for p in subscription.options.get("past", ())
    ]
    return past + _plan_sessions(subscription)


def _plan_sessions(subscription: Subscription) -> list[_progress.Session]:
    """The current plan's sessions, done and to come, as `progress` counts them."""
    if subscription.plan is None:
        return []
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    out = []
    for s in plan.history + plan.sessions:
        start = datetime.fromisoformat(s.start).astimezone(zone)
        end = datetime.fromisoformat(s.end).astimezone(zone)
        out.append(
            _progress.Session(
                start=start,
                minutes=round((end - start) / timedelta(minutes=1)),
                kind=s.kind,
                course=s.subject,
                report=subscription.outcomes.get(session_id(s)),
                sid=session_id(s),
            )
        )
    return out


MILESTONES = (
    ("first", "First session done"),
    ("streak_3", "3 days in a row"),
    ("streak_7", "A week in a row"),
    ("full_week", "A full week: every session done"),
    ("streak_14", "Two weeks in a row"),
    ("streak_30", "30 days in a row"),
)


def _milestones(best: int, sessions_done: int, full_weeks: int) -> list[dict]:
    reached = {
        "first": sessions_done >= 1,
        "streak_3": best >= 3,
        "streak_7": best >= 7,
        "full_week": full_weeks >= 1,
        "streak_14": best >= 14,
        "streak_30": best >= 30,
    }
    return [{"key": key, "label": _(label), "reached": reached[key]} for key, label in MILESTONES]


# Crests (DECISIONS.md D189): the badge shelf of D41 became a collection of badges that
# climb four tiers (`crests.FAMILIES`) and four rare ones, each counted from something
# the app already keeps; every tier adds to the level, with the sessions' points (D158).
# Derived from what the student did, every time, as progress is (D8, D41).
# The tiers that D41's badges, and D8's milestones, were celebrated under keep their key,
# so none is celebrated twice.
# Each badge's icon (D189), for the pages' set of icons shipped (`web/icons.py`).
CREST_ICONS = frozenset({f.icon for f in _crests.FAMILIES} | {r.icon for r in _crests.RARES})

_OLD_CELEBRATIONS = {
    ("streak", 1): "milestone-streak_3",
    ("streak", 2): "milestone-streak_7",
    ("full_week", 1): "milestone-full_week",
    ("recall", 1): "badge-self_tests",
    ("practice", 1): "badge-practice",
    ("focus", 1): "badge-focus",
    ("cards", 1): "badge-cards",
    ("teacher", 1): "badge-teacher",
    ("seasons", 1): "badge-semester",
    ("deep_focus", 1): "badge-focus_30",
    ("deep_focus", 2): "badge-focus_60",
    ("deep_focus", 3): "badge-focus_105",
    ("deep_focus", 4): "badge-focus_150",
}


def _crest_events(
    store: str | os.PathLike,
    subscription: Subscription,
    now: datetime,
    logged: Sequence[_progress.Session] = (),
    together: Mapping | None = None,
) -> tuple[dict[str, list[date | None]], dict[str, date | None], dict[str, int]]:
    """Each badge family's tiers' days, each rare badge's day, and where each family
    stands (a count, or a best: the longest run, the longest timed session), from what
    the student did. `together` is `together_view`'s answer when the page made it already."""
    zone = _zone(subscription.options["tz"])
    local = now.astimezone(zone)
    today = local.date()
    where = _store(store)
    token = subscription.token
    options = subscription.options
    family = {f.key: f for f in _crests.FAMILIES}

    counts: dict[str, int] = {}

    def counted(key: str, days: Iterable[date]) -> list[date | None]:
        listed = list(days)
        counts[key] = len(listed)
        return _crests.tiers_from_events(listed, family[key].thresholds)

    sessions = sorted(_progress.merge_logged(progress_sessions(subscription), logged), key=lambda x: x.start)
    done = [x for x in sessions if x.confirmed and x.planned]
    out: dict[str, list[date | None]] = {}
    # Habit
    if sessions:
        first = min(sessions[0].start.date(), today)
        paused = _progress.pause_days(pauses_of(options.get("preferences", {})))
        repaired = {date.fromisoformat(d) for d in options.get("repairs", {})}
        run = _progress.streak(sessions, first, today, paused, repaired)
        out["streak"] = _crests.tiers_from_best(
            _progress.first_runs(run, family["streak"].thresholds), family["streak"].thresholds
        )
        counts["streak"] = run.best
    out["full_week"] = counted("full_week", _progress.full_week_ends(sessions, today))
    starts, timed, _tests = _insight_inputs(store, subscription, local)
    on_time = _insights.ON_TIME / timedelta(minutes=1)
    out["on_time"] = counted("on_time", (s.started.date() for s in starts if s.late <= on_time))
    out["comeback"] = counted(
        "comeback", (date.fromisoformat(d) for d in options.get("repairs", {}).values())
    )
    # Seasons: each semester after the first, on the day it began (D36).
    begun = sorted(date.fromisoformat(d) for d in options.get("semesters", ()))
    if not begun and options.get("past"):
        begun = [date.fromisoformat(options["start"])]  # a plan carried over before D189
    out["seasons"] = _crests.tiers_from_events(begun, [t - 1 for t in family["seasons"].thresholds])
    counts["seasons"] = 1 + len(begun)
    # Memory
    out["recall"] = counted("recall", (x.start.date() for x in done if x.kind in _REVIEW_KINDS))
    out["practice"] = counted("practice", (x.start.date() for x in done if x.kind == "practice"))
    rare: dict[str, date | None] = {"first": done[0].start.date() if done else None}
    month_days: list[date] = []
    odds_days: list[date] = []
    clean: date | None = None
    if subscription.plan is not None:
        plan = PlanReport.from_dict(subscription.plan)
        gains = session_gains(plan)
        weeks: dict[date, list[str]] = {}
        for s in plan.history:
            if s.kind not in _REVIEW_KINDS or s.grade not in _GRADES:
                continue
            day = datetime.fromisoformat(s.start).astimezone(zone).date()
            weeks.setdefault(day - timedelta(days=day.weekday()), []).append(s.grade)
            gain = gains.get(session_id(s), {})
            if s.grade == "again" or "chance" not in gain or gain.get("same_day"):
                continue
            if gain["chance"] < BEATEN_BELOW:
                odds_days.append(day)
            after = gain.get("stability", 0.0)
            if after - gain.get("added", 0.0) < MONTH_DAYS <= after:
                month_days.append(day)
        this_monday = today - timedelta(days=today.weekday())
        clean = next(
            (
                monday + timedelta(days=6)
                for monday in sorted(weeks)
                if monday < this_monday
                and len(weeks[monday]) >= RECALL_WEEK_LEAST
                and "again" not in weeks[monday]
            ),
            None,
        )
    out["long_memory"] = counted("long_memory", month_days)
    rare["odds"] = min(odds_days, default=None)
    rare["sweep"] = clean
    with where.connection() as db:
        answers = db.execute(
            "SELECT at FROM card_reviews WHERE token = ? ORDER BY at LIMIT ?",
            (token, family["cards"].thresholds[-1]),
        ).fetchall()
        helpful = db.execute(
            "SELECT k.created FROM kudos k JOIN posts p ON p.id = k.post"
            " WHERE p.token = ? AND p.kind = 'notes' AND k.token != ? ORDER BY k.created",
            (token, token),
        ).fetchall()
        checked = db.execute(
            "SELECT c.created FROM checks c JOIN posts p ON p.id = c.post WHERE c.token = ? AND p.token != ?",
            (token, token),
        ).fetchall()
    local_day = lambda stamp: datetime.fromisoformat(stamp).astimezone(zone).date()  # noqa: E731
    out["cards"] = counted("cards", (local_day(r[0]) for r in answers))
    passed = [
        local_day(r["at"])
        for r in (options.get("exam_results") or {}).values()
        if _RESULT_RANK.get(str(r.get("result")), 0) >= 1 and r.get("at")
    ]
    out["exams"] = counted("exams", passed)
    # Focus
    out["focus"] = counted("focus", (x.start.date() for x in sessions if not x.planned))
    out["deep_focus"] = _crests.tiers_from_best(
        {
            n: min((t.day for t in timed if t.minutes >= n), default=None)
            for n in family["deep_focus"].thresholds
        },
        family["deep_focus"].thresholds,
    )
    counts["deep_focus"] = max((t.minutes for t in timed), default=0)
    finished = [
        _crests.UNDATED
        for steps in _steps(subscription).values()
        if len(steps) >= 2 and all(s.get("done") for s in steps)
    ]
    out["steps"] = counted("steps", finished)
    out["explorer"] = counted("explorer", (date.fromisoformat(d) for d in options.get("place_sessions", ())))
    # Together
    pairs = (together if together is not None else together_view(store, subscription, now))["pairs"]
    best_pair = max((p["best"] for p in pairs), default=0)
    out["buddy"] = [_crests.UNDATED if best_pair >= t else None for t in family["buddy"].thresholds]
    counts["buddy"] = best_pair
    out["teacher"] = counted(
        "teacher", (local_day(p.created) for p in social.posts_of(where, token, "explain"))
    )
    out["notes"] = counted("notes", (local_day(r[0]) for r in helpful))
    out["checker"] = counted("checker", (local_day(r[0]) for r in checked))
    out["cheers"] = counted("cheers", (date.fromisoformat(d) for d in options.get("cheers_sent", ())))
    out["team"] = counted("team", _team_weeks(store, subscription, now))
    rare["champion"] = min(
        (
            date.fromisoformat(r["week"]) + timedelta(days=6)
            for r in options.get("league", {}).get("results", ())
            if r.get("rank") == 1 and r.get("size", 0) >= _crests.LEAGUE_LEAST
        ),
        default=None,
    )
    return out, rare, counts


def _team_weeks(store: str | os.PathLike, subscription: Subscription, now: datetime) -> list[date]:
    """The weeks before this one in which a study group the student is in met its goal,
    each on its Sunday: the group's hours against its goal, as the group's card counts
    them (D28), only the weeks since the student joined."""
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    this_monday = _progress.monday(today)
    out: list[date] = []
    for group in social.groups_of(where, subscription.token):
        people = [m for m in social.members(where, group.id) if m.joined is not None]
        mine = next((m for m in people if m.token == subscription.token), None)
        if mine is None or mine.joined is None or not group.goal:
            continue
        began = datetime.fromisoformat(group.created).astimezone(zone).date()
        mondays = [m for m in _trends.mondays(min(began, today), today, None) if m < this_monday]
        if not mondays:
            continue
        joined = _progress.monday(datetime.fromisoformat(mine.joined).astimezone(zone).date())
        totals = [0.0] * len(mondays)
        for m in people:
            since = _progress.monday(datetime.fromisoformat(m.joined or mine.joined).astimezone(zone).date())
            hours = _member_hours(store, m.token, mondays, now)
            totals = [
                t + (h if monday >= since else 0.0)
                for t, h, monday in zip(totals, hours, mondays, strict=True)
            ]
        out += [
            monday + timedelta(days=6)
            for monday, total in zip(mondays, totals, strict=True)
            if monday >= joined and total >= group.goal / 60
        ]
    return out


def _session_points_total(store: str | os.PathLike, subscription: Subscription) -> tuple[int, list]:
    """Every point the student's sessions earned (D158), and each session's points by
    day, for the level and the league: what was reported done, and what was proven.
    D189: a session of the diary outside the plan (timed with Focus or typed) earns its
    +10 too, at most `crests.LOGGED_A_DAY` a day, so a student who studies without the
    plan's sessions plays as well; one timed from a planned session counts as that one."""
    sessions = [s for s in progress_sessions(subscription) if s.planned and s.sid]
    items = [{"id": s.sid, "at": s.start.isoformat(), "course": s.course} for s in sessions]
    outcomes = {s.sid: s.report for s in sessions if s.report}
    days = [(s.start.date(), s.sid) for s in sessions]
    per_day: dict[date, int] = {}
    for k, s in enumerate(logged_sessions(store, subscription)):
        day = s.start.date()
        if s.sid or per_day.get(day, 0) >= _crests.LOGGED_A_DAY:
            continue
        per_day[day] = per_day.get(day, 0) + 1
        lid = f"logged-{k}"
        items.append({"id": lid, "at": s.start.isoformat(), "course": s.course})
        outcomes[lid] = "done"
        days.append((day, lid))
    points = session_points(items, outcomes, proof_posts(store, subscription.token))
    return sum(p["earned"] for p in points.values()), [(day, points[sid]) for day, sid in days]


def crest_of(store: str | os.PathLike, subscription: Subscription) -> dict:
    """The student's crest as anyone in their league sees it: the level, from every
    point their sessions earned and the badges' points last counted (`crests_view`
    keeps them, `options["crest"]`), and the league they play in."""
    points, _by_day = _session_points_total(store, subscription)
    bonus = int(subscription.options.get("crest", {}).get("bonus", 0))
    level = _crests.level_of(points + bonus)
    tier = int(subscription.options.get("league", {}).get("tier", 0))
    return {**level, "name": _(level["name"]), "next_name": _(level["next_name"]), **_league_words(tier)}


def _league_words(tier: int) -> dict:
    key = _crests.LEAGUES[tier]
    names = {
        "bronze": _("Bronze league"),
        "silver": _("Silver league"),
        "gold": _("Gold league"),
        "ice": _("Ice league"),
        "aurora": _("Aurora league"),
    }
    return {"league": key, "league_name": names[key], "tier": tier}


def crests_view(
    store: str | os.PathLike,
    subscription: Subscription,
    now: datetime | None = None,
    logged: Sequence[_progress.Session] = (),
    together: Mapping | None = None,
) -> dict:
    """Progress's collection (D189): the level, then every badge family with its tier,
    its ladder and how far to the next, and the rare ones. `bonus` is the badges' points,
    which the page keeps for the league (`keep_crest`)."""
    now = now or datetime.now(UTC)
    tiers, rare, counts = _crest_events(store, subscription, now, logged, together)
    groups = []
    earned = bonus = 0
    for gkey, gname, gline in _crests.GROUPS:
        items = []
        for f in _crests.FAMILIES:
            if f.group != gkey:
                continue
            days = tiers.get(f.key, [None] * 4)
            tier = _crests.tier_of(days)
            reached = days[tier - 1] if tier else None
            earned += tier
            bonus += tier * _crests.TIER_POINTS
            nxt = f.thresholds[tier] if tier < 4 else None
            have = counts.get(f.key, 0)
            if nxt is None:
                status = _("All four tiers")
            elif f.key == "deep_focus":
                status = _("Next: one session of {length}", length=_minutes_words(nxt))
            else:
                status = _("{n} of {next}", n=format_number(min(have, nxt)), next=format_number(nxt))
            items.append(
                {
                    "key": f.key,
                    "name": _(f.name),
                    "how": _(f.how),
                    "icon": f.icon,
                    "ladder": _ladder_words(f),
                    "tier": tier,
                    "metal": _crests.TIERS[tier - 1] if tier else "none",
                    "roman": _crests.TIER_ROMAN[tier - 1] if tier else "",
                    "next": nxt,
                    "status": status,
                    "earned": format_date(reached, "day")
                    if reached is not None and reached != _crests.UNDATED
                    else None,
                    "celebrate": _OLD_CELEBRATIONS.get((f.key, tier), f"crest-{f.key}-{tier}")
                    if tier
                    else "",
                }
            )
        groups.append({"key": gkey, "name": _(gname), "line": _(gline), "badges": items})
    rares = []
    for r in _crests.RARES:
        day = rare.get(r.key)
        if day is not None:
            bonus += _crests.RARE_POINTS
        rares.append(
            {
                "key": r.key,
                "name": _(r.name),
                "how": _(r.how),
                "icon": r.icon,
                "earned": format_date(day, "day") if day is not None and day != _crests.UNDATED else None,
                "got": day is not None,
                "celebrate": "milestone-first" if r.key == "first" else f"crest-rare-{r.key}",
            }
        )
    points, _by_day = _session_points_total(store, subscription)
    level = _crests.level_of(points + bonus)
    return {
        "level": {**level, "name": _(level["name"]), "next_name": _(level["next_name"])},
        "league": _league_words(int(subscription.options.get("league", {}).get("tier", 0))),
        "groups": groups,
        "rare": rares,
        "earned": earned + sum(1 for r in rares if r["got"]),
        "total": 4 * len(_crests.FAMILIES) + len(_crests.RARES),
        "families": len(_crests.FAMILIES),
        "bonus": bonus,
        "session_points": points,
        "tier_points": _crests.TIER_POINTS,
    }


def _ladder_words(family: _crests.Family) -> str:
    numbers = " · ".join(format_number(t) for t in family.thresholds)
    if family.key == "deep_focus":
        return " · ".join(_minutes_words(t) for t in family.thresholds)
    if family.unit == "{n} days":
        return _("{numbers} days", numbers=numbers)
    if family.unit == "{n} weeks":
        return _("{numbers} weeks", numbers=numbers)
    return numbers


def _minutes_words(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if not hours:
        return _("{n} min", n=rest)
    return _("{h} h {m}", h=hours, m=f"{rest:02d}") if rest else _("{h} h", h=hours)


def keep_crest(subscription: Subscription, crests: Mapping) -> Subscription:
    """The badges' points as `crests_view` last counted them, kept for the level others
    see in the league (`crest_of`): the same subscription when nothing changed."""
    kept = {"bonus": int(crests["bonus"]), "tiers": int(crests["earned"])}
    if subscription.options.get("crest") == kept:
        return subscription
    return replace(subscription, options={**subscription.options, "crest": kept})


def crest_kept(subscription: Subscription, crests: Mapping) -> bool:
    return subscription.options.get("crest") == {
        "bonus": int(crests["bonus"]),
        "tiers": int(crests["earned"]),
    }


# -- the friends' league (D189) ----------------------------------------------------


def league_on(subscription: Subscription) -> bool:
    """Whether the student plays the weekly league: on unless they left it."""
    return not subscription.options.get("league_off")


def set_league(subscription: Subscription, on: bool) -> Subscription:
    """Play the weekly league with friends, or leave it (their points, level and league
    are then seen by no one, and they see no table)."""
    options = {k: v for k, v in subscription.options.items() if k != "league_off"}
    if not on:
        options["league_off"] = True
    return replace(subscription, options=options)


def _league_players(
    store: str | os.PathLike, subscription: Subscription
) -> list[tuple[social.Profile, Subscription]]:
    """The friends who play: people the student follows and who follow them back, who have
    not left the league and with no block either way, up to `LEAGUE_SIZE` with the student."""
    where = _store(store)
    token = subscription.token
    out = []
    for other in social.mutual_follows(where, token):
        if social.blocked_between(where, token, other):
            continue
        found = social.get_profile(where, other)
        theirs = load_subscription(store, other)
        if found is None or theirs is None or not league_on(theirs):
            continue
        out.append((found, theirs))
    out.sort(key=lambda p: p[0].handle.casefold())
    return out[: _crests.LEAGUE_SIZE - 1]


def _week_points(store: str | os.PathLike, subscription: Subscription, monday: date) -> tuple[int, int]:
    """A student's league points for the week from `monday`, and the days they studied:
    +10 a session done (one of the plan's reported, or one of the diary's outside it, at
    most `crests.LOGGED_A_DAY` a day), and +20 for a vouched one, once a day
    (`PROOF_A_DAY`), so two friends cannot vouch each other to the top."""
    _total, by_day = _session_points_total(store, subscription)
    end = monday + timedelta(days=7)
    points = 0
    proven: dict[date, int] = {}
    days: set[date] = set()
    for day, p in by_day:
        if not monday <= day < end or not p["earned"]:
            continue
        days.add(day)
        points += POINTS_DONE
        if p["state"] == "proven" and proven.get(day, 0) < _crests.PROOF_A_DAY:
            proven[day] = proven.get(day, 0) + 1
            points += POINTS_PROOF
    return points, len(days)


def _monday_of(subscription: Subscription, now: datetime) -> date:
    return _progress.monday(now.astimezone(_zone(subscription.options["tz"])).date())


def league_due(subscription: Subscription, now: datetime | None = None) -> bool:
    """Whether a week of the league has ended since the student last looked."""
    if not league_on(subscription):
        return False
    now = now or datetime.now(UTC)
    week = subscription.options.get("league", {}).get("week")
    return week != _monday_of(subscription, now).isoformat()


LEAGUE_RESULTS_KEPT = 12  # weeks of results kept, for the champion's badge and Today


def settle_league(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> Subscription:
    """The weeks of the league ended since the student last looked, settled: their rank
    among the friends who play, and the league they move to (`crests.league_move`).
    A first look starts them in Bronze this week. At most eight weeks are settled at
    once; the friends are today's, each week's points theirs that week."""
    if not league_due(subscription, now):
        return subscription
    now = now or datetime.now(UTC)
    this_monday = _monday_of(subscription, now)
    state = dict(subscription.options.get("league", {}))
    results = list(state.get("results", ()))
    tier = int(state.get("tier", 0))
    if state.get("week"):
        week = date.fromisoformat(state["week"])
        weeks = []
        while week < this_monday:
            weeks.append(week)
            week += timedelta(days=7)
        players = _league_players(store, subscription)
        me = social.get_profile(_store(store), subscription.token)
        for monday in weeks[-8:]:
            mine, days = _week_points(store, subscription, monday)
            points = {subscription.token: mine}
            # Ties are told apart by handle, as the table shows them (`league_view`).
            names = {subscription.token: me.handle if me else ""}
            for profile, theirs in players:
                points[profile.token] = _week_points(store, theirs, monday)[0]
                names[profile.token] = profile.handle
            order = _crests.ranking(points, names)
            rank = order.index(subscription.token) + 1
            move = _crests.league_move(rank, len(order), days)
            results.append(
                {
                    "week": monday.isoformat(),
                    "rank": rank,
                    "size": len(order),
                    "points": mine,
                    "from": tier,
                    "to": _crests.next_league(tier, move),
                }
            )
            tier = _crests.next_league(tier, move)
    state.update({"tier": tier, "week": this_monday.isoformat(), "results": results[-LEAGUE_RESULTS_KEPT:]})
    return replace(subscription, options={**subscription.options, "league": state})


def league_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """This week's league (D189): the student and the friends who play, each with their
    avatar, their level on a small crest and their points this week, in order; who moves
    up and who may move down; the days left. `enough` is False below `LEAGUE_LEAST`,
    when the table moves no one yet."""
    now = now or datetime.now(UTC)
    if not league_on(subscription):
        return {"on": False}
    where = _store(store)
    monday = _monday_of(subscription, now)
    local = now.astimezone(_zone(subscription.options["tz"]))
    me = social.get_profile(where, subscription.token)
    rows = []
    points: dict[str, int] = {}
    names: dict[str, str] = {}
    days_of: dict[str, int] = {}
    people = [(me, subscription), *_league_players(store, subscription)] if me else [(None, subscription)]
    for profile, sub in people:
        key = sub.token
        points[key], days_of[key] = _week_points(store, sub, monday)
        names[key] = profile.handle if profile else ""
        rows.append((key, profile, sub))
    order = _crests.ranking(points, names)
    size = len(order)
    enough = size >= _crests.LEAGUE_LEAST
    table = []
    for rank, key in enumerate(order, start=1):
        _key, profile, sub = next(r for r in rows if r[0] == key)
        crest = crest_of(store, sub)
        move = _crests.league_move(rank, size, days_of[key]) if enough else 0
        public = profile.public() if profile else {"handle": "", "initial": "?", "hue": "c0", "face": None}
        if key == subscription.token and not public.get("face"):
            public = {**public, "face": avatar_of(sub)}  # their own avatar, shared or not
        table.append(
            {
                **public,
                "me": key == subscription.token,
                "rank": rank,
                "points": points[key],
                "level": crest["n"],
                "metal": crest["league"],
                "zone": "up" if enough and rank <= _crests.LEAGUE_MOVE else "down" if move < 0 else "stay",
                "days": days_of[key],
            }
        )
    tier = int(subscription.options.get("league", {}).get("tier", 0))
    days_left = 7 - local.weekday()
    return {
        "on": True,
        "profile": me is not None,
        **_league_words(tier),
        "next_league": _league_words(min(tier + 1, len(_crests.LEAGUES) - 1))["league_name"],
        "lower_league": _league_words(max(tier - 1, 0))["league_name"],
        "top": tier == len(_crests.LEAGUES) - 1,
        "rows": table,
        "enough": enough,
        "least": _crests.LEAGUE_LEAST,
        "move": _crests.LEAGUE_MOVE,
        "floor": _crests.LEAGUE_FLOOR_DAYS,
        "days_left": days_left,
        "rule": {"done": POINTS_DONE, "proof": POINTS_PROOF},
    }


LEAGUE_SAID_DAYS = 3  # Today says last week's result on the new week's first three days


def league_result(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Last week's result, for Today on the first `LEAGUE_SAID_DAYS` days of the week
    after it (D189), so it does not hold Today's one message all week: the rank, the
    table's size and the league it led to; None when there is none, or the table was
    too small to move anyone."""
    if not league_on(subscription):
        return None
    now = now or datetime.now(UTC)
    if now.astimezone(_zone(subscription.options["tz"])).weekday() >= LEAGUE_SAID_DAYS:
        return None
    last = _monday_of(subscription, now) - timedelta(days=7)
    found = next(
        (
            r
            for r in subscription.options.get("league", {}).get("results", ())
            if r.get("week") == last.isoformat()
        ),
        None,
    )
    if found is None or found.get("size", 0) < _crests.LEAGUE_LEAST:
        return None
    to = int(found["to"])
    return {
        "rank": int(found["rank"]),
        "size": int(found["size"]),
        "points": int(found.get("points", 0)),
        "up": to > int(found["from"]),
        "down": to < int(found["from"]),
        **_league_words(to),
    }


def record_cheer(subscription: Subscription, now: datetime | None = None) -> Subscription:
    """A cheer sent (D178), counted for the Cheerleader badge (D189): its day, the last
    `_CHEERS_KEPT` kept (the badge's top tier)."""
    now = now or datetime.now(UTC)
    day = now.astimezone(_zone(subscription.options["tz"])).date().isoformat()
    sent = [*subscription.options.get("cheers_sent", ()), day][-_CHEERS_KEPT:]
    return replace(subscription, options={**subscription.options, "cheers_sent": sent})


_CHEERS_KEPT = 250  # the Cheerleader's last tier
_PLACE_SESSIONS_KEPT = 150  # the Explorer's last tier


def _place_session(options: Mapping, now: datetime) -> list[str]:
    """A session at a place on the study map (D133), counted for the Explorer (D189)."""
    day = now.astimezone(_zone(options["tz"])).date().isoformat()
    return [*options.get("place_sessions", ()), day][-_PLACE_SESSIONS_KEPT:]


def _hours(minutes: int) -> float:
    return round(minutes / 60, 1)


# --------------------------------------------------------------------------- #
# Repair and pause (DECISIONS.md D126): a missed day repaired by a make-up session
# of focus within two days; a pause that keeps the streak and leaves its days free
# --------------------------------------------------------------------------- #

REPAIR_MINUTES = 25  # the make-up session's length: the timer's shortest goal
PAUSE_MOST_DAYS = 90  # the longest pause, from today
PAUSE_CHOICES = ("today", "monday", "week")  # besides a date: today only, until Monday, a week


def _pause_rows(preferences: Mapping) -> list[tuple[date, date, str]]:
    """The pauses as kept, each with its days and, for a holiday the student chose to
    rest (D184), the holiday's first day ("" for a pause they set), in order."""
    out = []
    for p in preferences.get("pauses") or ():
        try:
            first, last = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
            out.append((first, last, str(p.get("holiday") or "")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return sorted(out)


def pauses_of(preferences: Mapping) -> list[tuple[date, date]]:
    """The student's pauses, as (first day, last day) pairs, in order: those they set
    and the holidays they chose to rest (D184), whose days are left free alike."""
    return [(a, b) for a, b, _holiday in _pause_rows(preferences)]


def _pause_dicts(pauses: Sequence[tuple[date, date, str]]) -> list[dict]:
    return [
        {"from": a.isoformat(), "to": b.isoformat(), **({"holiday": h} if h else {})} for a, b, h in pauses
    ]


def _pause_view(pause: tuple[date, date] | None, holidays: Collection[tuple[date, date]] = ()) -> dict | None:
    """A pause as the pages show it: its days, ISO, its first and last days in words,
    and whether it is a holiday the student chose to rest (D184)."""
    if pause is None:
        return None
    return {
        "from": pause[0].isoformat(),
        "to": pause[1].isoformat(),
        "since": format_date(pause[0], "short"),
        "until": format_date(pause[1], "short"),
        "holiday": pause in holidays,
    }


def active_pause(pauses: Sequence[tuple[date, date]], today: date) -> tuple[date, date] | None:
    return next((p for p in pauses if p[0] <= today <= p[1]), None)


def next_pause(pauses: Sequence[tuple[date, date]], today: date) -> tuple[date, date] | None:
    """The pause set ahead, if any: one begins after today."""
    return next((p for p in pauses if p[0] > today), None)


def pause_until(choice: str, today: date) -> date:
    """The last day of a pause asked for as "today", "monday" (through Sunday),
    "week" (seven days) or a day (ISO), from `today`."""
    if choice == "today":
        return today
    if choice == "monday":
        return today + timedelta(days=6 - today.weekday())
    if choice == "week":
        return today + timedelta(days=6)
    try:
        last = date.fromisoformat(choice)
    except ValueError:
        raise InvalidInput(_("say until when: today, until Monday, a week, or a day")) from None
    if last < today:
        raise InvalidInput(_("a pause ends today or later"))
    if (last - today).days >= PAUSE_MOST_DAYS:
        raise InvalidInput(_("a pause lasts at most {n} days", n=PAUSE_MOST_DAYS))
    return last


def set_pause(subscription: Subscription, choice: str, *, now: datetime | None = None) -> Subscription:
    """Pause the streak from today until `choice` says (D126): its days neither
    extend nor break the streak, and the plan leaves them free. One pause at a
    time: a pause under way or ahead is replaced; past ones are kept, for the
    days they explain. The plan is made again at once."""
    if subscription.plan is None:
        raise InvalidInput(_("there is no plan to pause yet"))
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    last = pause_until(choice, today)
    prefs = subscription.options.get("preferences", {})
    # A holiday the student chose to rest (D184) is kept: it is an answer of its own.
    kept = [p for p in _pause_rows(prefs) if p[1] < today or p[2]]
    prefs = {**prefs, "pauses": _pause_dicts([*kept, (today, last, "")])}
    changed = replace(subscription, options={**subscription.options, "preferences": prefs})
    return refresh_subscription(changed, now=now, reread=False)


def _resumed(rows: Sequence[tuple[date, date, str]], today: date) -> list[tuple[date, date, str]]:
    """`rows` after resuming today: a pause under way ends yesterday (or is dropped,
    begun today), one ahead is dropped, past ones are kept."""
    out = []
    for a, b, h in rows:
        if b < today:
            out.append((a, b, h))
        elif a < today:
            out.append((a, today - timedelta(days=1), h))
    return out


def end_pause(subscription: Subscription, *, now: datetime | None = None) -> Subscription:
    """Resume: the pause under way ends yesterday (or is dropped, begun today), and
    one set ahead is dropped; a holiday ahead the student chose to rest stays (D184).
    The plan is made again at once."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    prefs = subscription.options.get("preferences", {})
    rows = _pause_rows(prefs)
    pauses = sorted([*_resumed(rows, today), *(p for p in rows if p[2] and p[0] > today)])
    changed = replace(
        subscription,
        options={
            **subscription.options,
            "preferences": {**prefs, "pauses": _pause_dicts(pauses)},
        },
    )
    return refresh_subscription(changed, now=now, reread=False)


# --------------------------------------------------------------------------- #
# Holidays (DECISIONS.md D184): a week or more without classes between two days of
# classes, asked about once on Today: rest those days, or keep studying
# --------------------------------------------------------------------------- #

HOLIDAY_DAYS = 7  # the fewest days in a row without classes asked about
# Asked from two weeks before the holiday begins: enough for the plan to move its sessions.
# Four weeks crowded a semester's first week, whose first holiday is often a month in (D184).
HOLIDAY_ASK_DAYS = 14
HOLIDAY_ANSWERS = ("rest", "study")


def holidays_in(
    report: CalendarReport, courses: Iterable[str], exams: Iterable[datetime] = ()
) -> list[tuple[date, date]]:
    """The holidays of a timetable, as (first day, last day): `HOLIDAY_DAYS` days or
    more in a row with no class of `courses` nor of a course with an exam in the
    timetable, between two days with one. A stretch with an exam in it, of the
    timetable or of `exams`, is an exam period, not a holiday. Courses of neither
    kind (the gym, a job) do not count as classes: their weeks go on through a
    holiday. The time after the last class is the revision the plan is for, never
    a holiday."""
    zone = _zone(report.tz)
    names = {c.casefold() for c in courses} | {a.subject.casefold() for a in report.assessments}
    days = sorted(
        {
            datetime.fromisoformat(x.start).astimezone(zone).date()
            for x in report.lectures
            if x.course.casefold() in names
        }
    )
    tested = {datetime.fromisoformat(a.when).astimezone(zone).date() for a in report.assessments}
    tested |= {e.astimezone(zone).date() for e in exams}
    out = []
    for before, after in pairwise(days):
        first, last = before + timedelta(days=1), after - timedelta(days=1)
        if (last - first).days + 1 >= HOLIDAY_DAYS and not any(first <= d <= last for d in tested):
            out.append((first, last))
    return out


def _holidays_found(plan: PlanReport) -> list[tuple[date, date]]:
    out = []
    for h in plan.settings.preferences.get("holidays") or ():
        try:
            out.append((date.fromisoformat(h["from"]), date.fromisoformat(h["to"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def holiday_ask(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Today's question about the next holiday (D184), from two weeks before it
    begins to its last day, until answered: its days, ISO and in words, how many,
    and whether it has begun. None when there is nothing to ask."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(plan.settings.tz)).date()
    answered = subscription.options.get("holidays") or {}
    for first, last in _holidays_found(plan):
        if first.isoformat() in answered or last < today or (first - today).days > HOLIDAY_ASK_DAYS:
            continue
        return {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "first": format_date(first, "day_short"),
            "last": format_date(last, "day_short"),
            "days": (last - first).days + 1,
            "under_way": first <= today,
        }
    return None


def answer_holiday(
    subscription: Subscription, first: str, answer: str, *, now: datetime | None = None
) -> Subscription:
    """The student's answer about the holiday beginning on `first` (D184). "rest"
    leaves its days from today on free, as a pause does (D126): nothing is planned
    and the streak waits. "study" keeps the plan's sessions on them; said of a
    holiday chosen for rest, its days from today on are planned again. The plan is
    made again at once."""
    if answer not in HOLIDAY_ANSWERS:
        raise InvalidInput(_("rest or keep studying, not {value}", value=repr(answer)))
    if subscription.plan is None:
        raise InvalidInput(_("there is no plan yet"))
    plan = PlanReport.from_dict(subscription.plan)
    found = {a.isoformat(): (a, b) for a, b in _holidays_found(plan)}
    options = subscription.options
    prefs = options.get("preferences", {})
    rows = _pause_rows(prefs)
    mine = [p for p in rows if p[2] == first]
    others = [p for p in rows if p[2] != first]
    # Studying after all is allowed for a holiday rested before the timetable changed.
    if first not in found and not (answer == "study" and mine):
        raise InvalidInput(_("this holiday is no longer in your timetable"))
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(plan.settings.tz)).date()
    if answer == "rest":
        last = found[first][1]
        mine = [p for p in mine if p[1] < today]
        if last >= today:
            mine.append((max(found[first][0], today), last, first))
    else:
        mine = _resumed(mine, today)
    changed = replace(
        subscription,
        options={
            **options,
            "holidays": {**(options.get("holidays") or {}), first: answer},
            "preferences": {**prefs, "pauses": _pause_dicts(sorted([*others, *mine]))},
        },
    )
    return refresh_subscription(changed, now=now, reread=False)


def repair_offer(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """Today's offer to repair a missed day (D126): the day, how it is named, the
    make-up session's length and the course to start it on; None when no day of
    the last two is missed, or the student said "Not now" today."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    if subscription.options.get("repair_later") == today.isoformat():
        return None
    view = progress_view(subscription, now, logged=logged_sessions(store, subscription))
    if not view.get("repair"):
        return None
    missed = date.fromisoformat(view["repair"])
    planned = [s for s in progress_sessions(subscription) if s.start.date() == missed and s.planned]
    return {
        "day": missed.isoformat(),
        "yesterday": (today - missed).days == 1,
        "label": format_date(missed, "short"),
        "minutes": REPAIR_MINUTES,
        "course": planned[0].course if planned else "",
    }


def repair_later(subscription: Subscription, *, now: datetime | None = None) -> Subscription:
    """ "Not now" on the repair offer: not again today."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    return replace(subscription, options={**subscription.options, "repair_later": today.isoformat()})


def progress_view(
    subscription: Subscription, now: datetime | None = None, logged: Sequence[_progress.Session] = ()
) -> dict:
    """What the Progress page shows, and the strip on Today: the streak, this week's
    sessions done of planned, the last twelve weeks day by day, milestones, exams
    and tasks. Derived from the plan and the reports every time (D8)."""
    now = now or datetime.now(UTC)
    view: dict = {"planned": subscription.plan is not None, "error": subscription.error}
    if subscription.plan is None:
        return view
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    today = local.date()
    sessions = _progress.merge_logged(progress_sessions(subscription), logged)
    first = min([plan.settings.start, *(s.start.date() for s in sessions)])
    # The student's pauses and the missed days they repaired (D126), the holidays
    # they chose to rest among them (D184).
    rows = _pause_rows(subscription.options.get("preferences", {}))
    pauses = [(a, b) for a, b, _h in rows]
    holidays = {(a, b) for a, b, h in rows if h}
    paused = _progress.pause_days(pauses)
    repaired = {date.fromisoformat(d) for d in subscription.options.get("repairs", {})}
    run = _progress.streak(sessions, first, today, paused, repaired)
    pause = active_pause(pauses, today)
    # The weeks since the plan began, at most twelve; a younger plan shows its weeks
    # to come too, four in all, so it is not a sliver.
    weeks = max(4, min(12, (_progress.monday(today) - _progress.monday(first)).days // 7 + 1))
    this_week = _progress.week(sessions, _progress.monday(today), local)
    done = [s for s in sessions if s.confirmed and s.planned]
    full = _progress.full_weeks(sessions, today)
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    tasks = _task_rows(subscription, plan, now, colours)
    if run.today_done:
        today_line = _("Today counts. See you tomorrow.")
    elif pause is not None and pause in holidays:
        today_line = _("Holidays until {day}: your streak waits.", day=format_date(pause[1], "short"))
    elif pause is not None:
        today_line = (
            _("Paused today: your streak waits.")
            if pause[1] == today
            else _("Paused until {day}: your streak waits.", day=format_date(pause[1], "short"))
        )
    elif run.today_planned:
        today_line = _n(
            "One session today makes it {n} day in a row.",
            "One session today makes it {n} days in a row.",
            run.current + 1,
        )
    else:
        today_line = _("Nothing planned today: a rest day keeps your streak as it is.")
    view.update(
        {
            "streak": {
                "current": run.current,
                "best": run.best,
                "goal": run.goal,
                "to_go": run.goal - run.current,
                "today_planned": run.today_planned,
                "today_done": run.today_done,
                "line": today_line,
                # The pause under way or ahead (D126), and its last day.
                "pause": _pause_view(pause, holidays),
                "ahead": None if pause is not None else _pause_view(next_pause(pauses, today), holidays),
            },
            # The missed day a make-up session can still repair (D126), or None.
            "repair": (repair_day := _progress.repairable(run, today)) and repair_day.isoformat(),
            "week": {
                "first": _progress.monday(today).isoformat(),
                "planned": this_week.planned,
                "done": this_week.done,
                "skipped": this_week.skipped,
                "waiting": this_week.waiting,
                "percent": this_week.percent,
                "hours_done": _hours(this_week.minutes_done),
                "hours_planned": _hours(this_week.minutes_planned),
                "logged": this_week.logged,
                "hours_logged": _hours(this_week.minutes_logged),
            },
            "grid": [
                [
                    {
                        "date": d.day.isoformat(),
                        "label": format_date(d.day, "short"),
                        "state": d.state,
                        "level": d.level,
                        "planned": d.planned,
                        "done": d.done,
                        "today": d.day == today,
                    }
                    for d in row
                ]
                for row in _progress.grid(
                    sessions, first, today, weeks=weeks, paused=paused, repaired=repaired
                )
            ],
            "since": format_date(_progress.monday(first), "day"),
            "totals": {
                "sessions": len(done),
                "hours": _hours(sum(s.minutes for s in done)),
                "logged": sum(not s.planned for s in sessions),
                "hours_logged": _hours(sum(s.minutes for s in sessions if not s.planned)),
                "days": sum(d.state == "studied" for d in run.days),
                "tasks_finished": sum(t["finished"] for t in tasks),
            },
            "milestones": _milestones(run.best, len(done), full),
            "exams": _exam_rows(plan, now, colours),
            "taken": exam_outcomes(subscription, now),
        }
    )
    return view


# Which suggestion a weekly review makes: the first that applies, in this order.
SUGGESTIONS = ("report", "time_of_day", "lighter", "exam", "all_done", "steady")


def weekly_review(
    subscription: Subscription,
    now: datetime | None = None,
    weeks_back: int = 1,
    logged: Sequence[_progress.Session] = (),
) -> dict:
    """A week in numbers, one suggestion and a paragraph saying both (the template
    that the AI version replaces when it is on, D12). By default the week before
    this one; `weeks_back=0` is this week so far."""
    now = now or datetime.now(UTC)
    if subscription.plan is None:
        return {"planned": False}
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    sessions = _progress.merge_logged(progress_sessions(subscription), logged)
    first_day = _progress.monday(local.date()) - timedelta(weeks=weeks_back)
    last_day = first_day + timedelta(days=6)
    week_end = datetime.combine(last_day + timedelta(days=1), time(0, 0), tzinfo=zone)
    w = _progress.week(sessions, first_day, min(local, week_end))
    start = min([plan.settings.start, *(s.start.date() for s in sessions)])
    run = _progress.streak(sessions, start, min(local.date(), last_day))
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    due = [
        t
        for t in _task_rows(subscription, plan, now, colours)
        if first_day <= date.fromisoformat(t["due_value"][:10]) <= last_day
    ]
    met = [t for t in due if t["finished"] or t["percent"] >= 100]
    soon = [e for e in _exam_rows(plan, week_end, colours) if 0 <= e["days"] <= 14]
    skipped_when: dict[str, int] = {}
    for x in w.sessions:
        if x.report == "skipped":
            part = _progress.part_of_day(x.start)
            skipped_when[part] = skipped_when.get(part, 0) + 1
    worst = max(skipped_when.items(), key=lambda kv: kv[1], default=("", 0))
    share = w.done / w.planned if w.planned else 1.0
    past = sum(x.start <= min(local, week_end) for x in w.sessions)
    if w.planned and past and w.waiting / past > 0.5:
        key = "report"
        suggestion = _(
            "Most sessions were not reported. "
            "A tap on Done after each one keeps your plan and your streak true."
        )
    elif worst[1] >= 2:
        key = "time_of_day"
        part = {"morning": _("morning"), "afternoon": _("afternoon"), "evening": _("evening")}[worst[0]]
        suggestion = _(
            "{n} {part} sessions were skipped. "
            "If that time does not work, change the hours you study in Settings.",
            n=worst[1],
            part=part,
        )
    elif w.planned >= 4 and share < 0.5:
        key = "lighter"
        suggestion = _(
            "A week you keep beats a heavy one you skip: try a weekly limit near the {hours} h you did, "
            "in Settings.",
            hours=max(1, round(w.minutes_done / 60)),
        )
    elif soon:
        key = "exam"
        exam = soon[0]
        suggestion = _n(
            "{name} is in {n} day: exam practice is in your plan.",
            "{name} is in {n} days: exam practice is in your plan.",
            max(1, round(exam["days"])),
            name=exam["name"],
        )
    elif w.planned and w.done >= w.planned:
        key = "all_done"
        suggestion = _("Every session done. The same again this week.")
    else:
        key = "steady"
        suggestion = _("Keep the rhythm: one session at a time.")
    if not w.planned:
        numbers = _("Nothing was planned that week.")
    else:
        numbers = _n(
            "You did {done} of {n} planned session ({hours} h).",
            "You did {done} of {n} planned sessions ({hours} h).",
            w.planned,
            done=w.done,
            hours=format_number(w.minutes_done / 60),
        )
    focus_line = (
        _n(
            "You also logged {n} focus session ({hours} h).",
            "You also logged {n} focus sessions ({hours} h).",
            w.logged,
            hours=format_number(w.minutes_logged / 60),
        )
        if w.logged
        else ""
    )
    streak_line = (
        _n("Your streak is {n} day.", "Your streak is {n} days.", run.current) if run.current else ""
    )
    deadlines = (
        _n(
            "{met} of {n} deadline met.",
            "{met} of {n} deadlines met.",
            len(due),
            met=len(met),
        )
        if due
        else ""
    )
    return {
        "planned": True,
        "week": first_day.isoformat(),
        "label": _("Week of {day}", day=format_date(first_day, "day")),
        "sessions_planned": w.planned,
        "sessions_done": w.done,
        "skipped": w.skipped,
        "waiting": w.waiting,
        "percent": w.percent,
        "hours_done": _hours(w.minutes_done),
        "hours_planned": _hours(w.minutes_planned),
        "days_studied": w.days_studied,
        "streak": run.current,
        "deadlines": len(due),
        "deadlines_met": len(met),
        "exams_soon": [{"name": e["name"], "days": round(e["days"])} for e in soon],
        "suggestion_key": key,
        "suggestion": suggestion,
        "logged": w.logged,
        "hours_logged": _hours(w.minutes_logged),
        "text": " ".join(x for x in (numbers, focus_line, streak_line, deadlines, suggestion) if x),
        "by": "rules",
    }


# The front ends translate through these (they import nothing but this module).
translate = _
translate_plural = _n
LANGUAGES = _i18n.LANGUAGES
FRONT_LANGUAGES = _i18n.FRONT_LANGUAGES  # the front page's, the app's since D201
# The model is told the plan's language by its English name (D12, D201).
_LANGUAGE_NAMES = {"fr": "French", "it": "Italian", "es": "Spanish"}
activate_language = _i18n.activate
deactivate_language = _i18n.deactivate
pick_language = _i18n.pick
format_day = _i18n.format_date


DAY_WORDS = {
    "studied": "studied",
    "forgiven": "forgiven",
    "repaired": "repaired",
    "paused": "paused",
    "missed": "not reported",
    "rest": "rest",
    "today": "today",
    "future": "to come",
    "before": "before your plan",
}


def day_word(state: str) -> str:
    """A day's state in the grid, in words, for screen readers."""
    return _(DAY_WORDS.get(state, state))


def review_for_today(
    subscription: Subscription, now: datetime | None = None, logged: Sequence[_progress.Session] = ()
) -> dict | None:
    """Last week's review, for the Today page on Monday and Tuesday, unless the
    student hid it or there was nothing planned."""
    now = now or datetime.now(UTC)
    if subscription.plan is None:
        return None
    local = now.astimezone(_zone(subscription.options["tz"]))
    if local.weekday() > 1:
        return None
    review = weekly_review(subscription, now, logged=logged)
    if not (review.get("sessions_planned") or review.get("logged")):
        return None
    if subscription.options.get("review_seen") == review["week"]:
        return None
    return review


THEMES = ("auto", "light", "dark")


def theme_of(subscription: Subscription | None) -> str:
    """How a plan's pages look (D21): "auto" follows the device."""
    theme = (subscription.options.get("theme") if subscription else None) or "auto"
    return theme if theme in THEMES else "auto"


def set_theme(subscription: Subscription, theme: str) -> Subscription:
    if theme not in THEMES:
        raise InvalidInput(_("choose Automatic, Light or Dark"))
    return replace(subscription, options={**subscription.options, "theme": theme})


# D156: themes and an accent (the owner, 2026-11-19: themes for light and dark, and "to allow the
# user to pick from a colour palette, maintaining as primary objective to make frictionless using
# the app, comfortable to navigate the sections and look at the screen"). A light theme and a dark
# theme, each used when its mode is (Appearance's Automatic follows the device), and one accent for
# both. The sky (D73, D76) stays the start; the others are flat. Their colours are in style.css.
LIGHT_THEMES: tuple[tuple[str, str, str], ...] = (
    ("sky", "Sky", "The sky of the hour behind glass cards."),
    ("paper", "Paper", "A flat grey page and white cards, no sky."),
    ("softday", "Soft day", "Less glare: warm grey and off-white."),
)
DARK_THEMES: tuple[tuple[str, str, str], ...] = (
    ("night", "Night sky", "The night's sky behind glass cards."),
    ("calm", "Calm night", "A flat page, lighter cards, clear edges."),
    ("graphite", "Graphite", "Neutral greys, as a phone's own dark mode."),
    ("dim", "Dim", "Warm and dim, for late evenings."),
    ("contrast", "High contrast", "Black, white and outlined edges."),
)
ACCENTS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic"),
    ("blue", "Blue"),
    ("teal", "Teal"),
    ("green", "Green"),
    ("amber", "Amber"),
    ("rose", "Rose"),
    ("violet", "Violet"),
)
PALETTE_START = {"light": "sky", "dark": "night", "accent": "auto"}
_PALETTE_CHOICES = {
    "light": [k for k, _n, _l in LIGHT_THEMES],
    "dark": [k for k, _n, _l in DARK_THEMES],
    "accent": [k for k, _n in ACCENTS],
}


def palette_of(subscription: Subscription | None) -> dict[str, str]:
    """A plan's light theme, dark theme and accent (D156); the sky and its accent of the hour
    until the student chooses."""
    chosen = (subscription.options.get("palette") if subscription else None) or {}
    out = dict(PALETTE_START)
    for part, names in _PALETTE_CHOICES.items():
        value = chosen.get(part)
        if isinstance(value, str) and value in names:
            out[part] = value
    return out


def set_palette(subscription: Subscription, part: str, value: str) -> Subscription:
    """One of the three choices (D156): a theme for light, one for dark, or the accent."""
    if value not in _PALETTE_CHOICES.get(part, ()):
        raise InvalidInput(_("choose one of the themes or colours shown"))
    return replace(
        subscription, options={**subscription.options, "palette": {**palette_of(subscription), part: value}}
    )


def palette_view(subscription: Subscription) -> dict:
    """Appearance's three rows of swatches (D156), each choice with its name and line."""
    now = palette_of(subscription)

    def themes(part: str, table: tuple[tuple[str, str, str], ...]) -> list[dict]:
        return [{"key": k, "name": _(name), "line": _(line), "on": now[part] == k} for k, name, line in table]

    return {
        "light": themes("light", LIGHT_THEMES),
        "dark": themes("dark", DARK_THEMES),
        "accent": [{"key": k, "name": _(name), "on": now["accent"] == k} for k, name in ACCENTS],
    }


MOTIONS = ("auto", "reduce")


def motion_of(subscription: Subscription | None) -> str:
    """How much a plan's pages move (D30): "auto" animates unless the device asks
    for reduced motion; "reduce" never animates."""
    motion = (subscription.options.get("motion") if subscription else None) or "auto"
    return motion if motion in MOTIONS else "auto"


# -- Spark's tour (D77) -------------------------------------------------------------------

# Today's tour, three steps (D97): the next step, how it went, where the rest is; once
# after setting up, until finished or skipped. Each other section says what it is for in
# one line under its title the first time it is opened (`section_hint`), and since D141
# Spark shows it around on that same visit (`SECTION_TOURS`; the owner, 2026-11-18:
# "Spark's tour is important in every section"). The line and the tour are remembered
# together in "tours_seen" as the page goes out: a section shows its tour once, even if
# it is left half way, and Settings show them all again. "week" (D77) keeps its name, so
# a plan that saw it is read as before.
TOURS = (
    "week",
    "today",
    "tasks",
    "courses",
    "focus",
    "community",
    "progress",
    "calendar",
    "you",
    "cards",
    "tips",
)
SECTION_HINTS = {
    "calendar": "Drag a study session to move it. Drag across empty time, or tap it on a phone, to keep "
    "that time free: the plan works around it.",
    "courses": "Each course week by week: what was taught, what you reviewed, what comes next.",
    "community": "Choose a handle to join your classmates. Each post says who sees it; your plan "
    "stays private.",
    "you": "Your progress, flashcards, tasks, tips and settings, all in one place.",
    "focus": "Choose how long, then start. Studied without the timer? Write it in the diary below.",
    "progress": "Your week, your streak and how ready each exam is, then the detail.",
    "tasks": "Everything that is due, soonest first. Tick a task when it is done.",
    "cards": "Your own cards, each one back shortly before you would forget it.",
    "tips": "What helps you learn, how sure the research is, and where it comes from.",
}
# Community's line for a student who already has a handle (AUDIT 82): the one above asks
# them to choose one, and since D102 the feed is the first screen such a student sees.
JOINED_HINT = (
    "What your classmates studied, explained and shared. Each post says who sees it; your plan stays private."
)


def tours_seen(subscription: Subscription) -> list[str]:
    seen = subscription.options.get("tours_seen") or []
    return [name for name in seen if name in TOURS]


# The sections with a tour of their own (D141): Settings has none (it is a list of six
# rows, each its own page), nor the pages reached from a section (a post, a group).
SECTION_TOURS = ("calendar", "courses", "community", "you", "focus", "progress", "tasks", "cards", "tips")


def tour_for(
    subscription: Subscription | None, tab: str | None, *, setup: bool = False, new: bool = False
) -> str | None:
    """The tour a page shows: Today's, once, after setting up (D77, D97), and each
    section's the first time it is opened (D141); none while setting up, nor while a
    first run's steps are ahead (D107), from the moment it reaches Today (D170)."""
    if subscription is None or setup or new or not _touring(subscription):
        return None
    if tab != "today" and tab not in SECTION_TOURS:
        return None
    return None if tab in tours_seen(subscription) else tab


def section_hint(subscription: Subscription | None, tab: str | None, *, joined: bool = False) -> str | None:
    """The line a section shows under its title the first time it is opened (D97);
    Community's says what the feed is to a student who has joined (AUDIT 82)."""
    if subscription is None or tab not in SECTION_HINTS or tab in tours_seen(subscription):
        return None
    if not _touring(subscription):
        return None  # not recorded as visited: its tour shows once tours may (D170)
    return _(JOINED_HINT) if tab == "community" and joined else _(SECTION_HINTS[tab])


def see_tour(subscription: Subscription, name: str) -> Subscription:
    """A tour finished or skipped: it is not shown again (D77)."""
    if name not in TOURS:
        raise InvalidInput(_("There is no such tour."))
    seen = tours_seen(subscription)
    if name in seen:
        return subscription
    return replace(subscription, options={**subscription.options, "tours_seen": [*seen, name]})


def reset_tours(subscription: Subscription) -> Subscription:
    """Every tour shown again, each on its section's next visit (Settings, D77)."""
    options = {k: v for k, v in subscription.options.items() if k != "tours_seen"}
    return replace(subscription, options=options)


# -- the sky and the garden behind a plan's pages (D73) ---------------------------------

SKIES = ("morning", "afternoon", "evening", "night")
# The garden in the margins grows with the streak: a seedling, two leaves, a vine,
# a flower. Days of streak from which each stage shows.
GARDEN = ((14, 4), (7, 3), (3, 2), (0, 1))


def sky_at(hour: int) -> str:
    """The part of the day the background shows (D73): morning from 5, afternoon
    from 12, evening from 18, night from 22."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def garden_stage(streak: int) -> int:
    """How far the garden in the margins has grown (D73): 1 to 4, with the streak."""
    return next(stage for days, stage in GARDEN if streak >= days)


def look_of(store: str | os.PathLike, subscription: Subscription | None, now: datetime | None = None) -> dict:
    """The sky and the garden behind a plan's pages (D73), the day of the week the
    app's icon shows (1 Monday ... 7 Sunday, D74), and where the sun is in the plan's
    day (`daylight`, 0 to 4, D76): decoration only, worked out each time from the
    plan's time zone and the streak as Today counts it (D8, logged focus sessions
    included). `sky` names the part of the day by the clock (the greeting's);
    `daylight` follows the real sun, which the colours are drawn from."""
    now = now or datetime.now(UTC)
    if subscription is None or subscription.plan is None:
        sun = _daylight.day_position(*_daylight.place("UTC", now), now)
        return {"sky": sky_at(now.hour), "garden": 1, "weekday": now.isoweekday(), "daylight": round(sun, 2)}
    plan = PlanReport.from_dict(subscription.plan)
    local = now.astimezone(_zone(plan.settings.tz))
    sun = _daylight.day_position(*_daylight.place(plan.settings.tz, now), now)
    sessions = _progress.merge_logged(progress_sessions(subscription), logged_sessions(store, subscription))
    first = min([plan.settings.start, *(s.start.date() for s in sessions)])
    run = _progress.streak(sessions, first, local.date())
    return {
        "sky": sky_at(local.hour),
        "garden": garden_stage(run.current),
        "weekday": local.isoweekday(),
        "daylight": round(sun, 2),
    }


def set_motion(subscription: Subscription, motion: str) -> Subscription:
    if motion not in MOTIONS:
        raise InvalidInput(_("choose Automatic or Reduced"))
    return replace(subscription, options={**subscription.options, "motion": motion})


# The glass (D93, D132): "auto" tints it, more opaque than D73's clear glass, and turns
# it solid when the device asks for less transparency or blurs too slowly (motion.js
# measures it as a page arrives); "clear" keeps D73's see-through glass; "solid" never
# blurs, the lightest for a slow device.
GLASSES = ("auto", "clear", "solid")


def glass_of(subscription: Subscription | None) -> str:
    glass = (subscription.options.get("glass") if subscription else None) or "auto"
    return glass if glass in GLASSES else "auto"


def set_glass(subscription: Subscription, glass: str) -> Subscription:
    if glass not in GLASSES:
        raise InvalidInput(_("choose Automatic, See-through or Solid"))
    return replace(subscription, options={**subscription.options, "glass": glass})


# -- the student's avatar (D115) -------------------------------------------------------

# A student makes their own avatar from these parts; it asks the questions on the
# science and code pages (D114). Kept in the plan's options as the names chosen: a
# drawing of shapes, nothing uploaded. The first of each is where the maker starts,
# save the skin, which starts in the middle of the six.
# D137 (the owner: "more personalisation options to create the avatar"): two more skin
# tones at the ends, five more hair styles and four more colours, eyes, eyebrows and a
# mouth to choose, two more kinds of facial hair, sunglasses, something on the head,
# freckles, earrings or a beauty mark, four kinds of clothes in ten colours, and a
# background. A part added later starts at its first name for an avatar saved before.
# D150 (the owner: "add the fade haircut, the mullet, more trendy haircuts and features for
# personalisation"): nine more cuts, lashes and sleepy eyes, a slit brow, a smirk and
# lipstick, a nose ring, ear studs and dimples, a bandana, a bucket hat and a headband, a
# jacket and a turtleneck. Each is added at the end of its part, so no saved name moves.
AVATAR_PARTS: dict[str, tuple[str, ...]] = {
    "skin": ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"),
    "hair": (
        *("short", "swept", "long", "curly", "bun", "shaved", "scarf"),
        *("afro", "bob", "ponytail", "braids", "bald"),
        *("fade", "mullet", "curtains", "crop", "wolf", "locs", "spacebuns", "pixie", "cornrows"),
    ),
    "colour": (
        *("dark", "black", "brown", "auburn", "blonde", "grey", "pink", "blue"),
        *("red", "platinum", "green", "purple"),
    ),
    "eyes": ("soft", "wide", "happy", "wink", "lashes", "sleepy"),
    "brows": ("soft", "straight", "thick", "raised", "slit"),
    "mouth": ("smile", "grin", "neutral", "smirk", "lips"),
    "face": ("none", "moustache", "beard", "goatee", "stubble"),
    "glasses": ("none", "round", "square", "sun"),
    "head": (
        "none",
        "cap",
        "beanie",
        "headphones",
        "flower",
        "bandana",
        "bucket",
        "headband",
        "mortarboard",
        "laurel",
        "crown",
    ),
    "detail": ("none", "freckles", "earrings", "mole", "nosering", "studs", "dimples"),
    "outfit": ("tee", "hoodie", "shirt", "sweater", "jacket", "turtleneck"),
    "top": ("c0", "c1", "c2", "c3", "c4", "c5", "c6", "k0", "k1", "k2"),
    "bg": ("b0", "b1", "b2", "b3", "b4", "b5", "b6"),
}
AVATAR_START: dict[str, str] = {part: names[0] for part, names in AVATAR_PARTS.items()} | {"skin": "s3"}


# D143: where a student is from, a round badge on their avatar and on their initial in
# Community. The flags are flag-icons 7.6.0's square ones (MIT, in web/static/vendor/flags
# with its licence); the countries and their names in English are the same package's list,
# its ISO countries only (data/countries.json).
@functools.cache
def flag_countries() -> tuple[tuple[str, str], ...]:
    """Every country a flag can be chosen for: (code, name in English), by name."""
    rows = json.loads((Path(__file__).parent / "data" / "countries.json").read_text(encoding="utf-8"))
    return tuple((str(r["code"]), str(r["name"])) for r in rows)


def flag_name(code: str) -> str:
    return dict(flag_countries()).get(code, "")


def flag_suggestions(store: str | os.PathLike, subscription: Subscription) -> list[dict]:
    """The countries the avatar page offers first (D143): the university's, from the
    profile (D39), and the time zone's. Only suggestions: where a student is from is theirs
    to say."""
    found: list[str] = []
    profile = social.get_profile(_store(store), subscription.token)
    zone = _universities.country_of_zone(subscription.options["tz"]) or ""
    for code in (profile.country if profile else "", zone):
        if code and code.lower() not in found and flag_name(code.lower()):
            found.append(code.lower())
    return [{"code": c, "name": flag_name(c)} for c in found]


def avatar_of(subscription: Subscription | None) -> dict[str, str] | None:
    """The avatar a student made (D115), or None before they make one. A part saved
    under a name this version does not draw falls back to where the maker starts; a flag
    (D143) not on the list is no flag."""
    chosen = subscription.options.get("avatar") if subscription else None
    if not isinstance(chosen, Mapping):
        return None
    avatar = {
        part: chosen[part] if chosen.get(part) in names else AVATAR_START[part]
        for part, names in AVATAR_PARTS.items()
    }
    flag = str(chosen.get("flag") or "")
    avatar["flag"] = flag if flag_name(flag) else ""
    return avatar


def set_avatar(subscription: Subscription, chosen: Mapping[str, str], *, level: int = 1) -> Subscription:
    """Keep the avatar a student made: every part one of its names (D115), and the flag
    (D143) one on the list, or none. A country picked from the full list wins over the
    suggestion ticked, since it was picked later. An accessory needs its level (D206), unless
    it is already worn."""
    avatar = {}
    for part, names in AVATAR_PARTS.items():
        value = chosen.get(part) or AVATAR_START[part]
        if value not in names:
            raise InvalidInput(_("choose one of the options shown for each part"))
        worn = (subscription.options.get("avatar") or {}).get(part)
        if part == "head" and value != worn and level < ACCESSORY_LEVEL.get(value, 0):
            raise InvalidInput(_("unlocked at level {n}", n=ACCESSORY_LEVEL[value]))
        avatar[part] = value
    flag = str(chosen.get("flag_other") or chosen.get("flag") or "")
    if flag == "none":
        flag = ""
    if flag and not flag_name(flag):
        raise InvalidInput(_("choose a country from the list"))
    avatar["flag"] = flag
    return replace(subscription, options={**subscription.options, "avatar": avatar})


def share_flag(store: str | os.PathLike, subscription: Subscription) -> None:
    """The avatar's flag on the student's profile, for their initial in Community (D143),
    and since D153 the avatar itself, drawn on their posts in its place (the owner,
    2026-11-19: "the avatar does not pop out here"): after the avatar is saved, and when a
    profile is made after the avatar. Only its parts' names, each one of AVATAR_PARTS'."""
    avatar = avatar_of(subscription)
    flag = avatar["flag"] if avatar else ""
    parts = json.dumps({part: avatar[part] for part in AVATAR_PARTS}) if avatar else ""
    social.set_flag(_store(store), subscription.token, flag, parts)


# --------------------------------------------------------------------------- #
# D206: rewards to unlock (the owner: "unlock different backgrounds and focus rings as
# rewards of the app; accessories for the avatar as you level up"). The focus page's ring
# and backdrop come with the sessions timed with Focus, the avatar's accessories with the
# level (D189), which never goes down. Each key is drawn by a class (`ring-*`, `bd-*`,
# `hd-*`); the thresholds are choices, not measured.
# --------------------------------------------------------------------------- #

REWARD_RINGS: tuple[tuple[str, str, int], ...] = (
    ("vine", "Vine", 0),
    ("gold", "Gold", 5),
    ("aurora", "Aurora", 15),
    ("sakura", "Sakura", 40),
)
REWARD_BACKDROPS: tuple[tuple[str, str, int], ...] = (
    ("plain", "Plain", 0),
    ("dawn", "Dawn", 10),
    ("forest", "Forest", 25),
    ("night", "Starry night", 50),
)
REWARD_ACCESSORIES: tuple[tuple[str, str, int], ...] = (  # things on the head, by level
    ("mortarboard", "Graduation cap", 2),
    ("laurel", "Laurel", 4),
    ("crown", "Crown", 6),
)
ACCESSORY_LEVEL = {key: level for key, _name, level in REWARD_ACCESSORIES}


def _timed_sessions(store: str | os.PathLike, subscription: Subscription, now: datetime) -> int:
    """How many sessions the student timed with Focus and saved in the diary."""
    local = now.astimezone(_zone(subscription.options["tz"]))
    _starts, timed, _tests = _insight_inputs(store, subscription, local)
    return len(timed)


def focus_look(subscription: Subscription) -> dict[str, str]:
    """The ring and backdrop the student chose for the focus page (D206), else the first."""
    look = subscription.options.get("look") or {}
    rings = {key for key, _n, _s in REWARD_RINGS}
    backdrops = {key for key, _n, _s in REWARD_BACKDROPS}
    ring = str(look.get("ring") or "")
    backdrop = str(look.get("backdrop") or "")
    return {
        "ring": ring if ring in rings else REWARD_RINGS[0][0],
        "backdrop": backdrop if backdrop in backdrops else REWARD_BACKDROPS[0][0],
    }


def rewards_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """What there is to unlock (D206): each ring and backdrop with the sessions it needs and
    how far the student is, each accessory with its level; which are unlocked and in use."""
    now = now or datetime.now(UTC)
    sessions = _timed_sessions(store, subscription, now)
    level = crest_of(store, subscription)
    look = focus_look(subscription)
    avatar = avatar_of(subscription) or {}

    def by_sessions(kind: str, table: tuple[tuple[str, str, int], ...]) -> list[dict]:
        return [
            {
                "key": key,
                "name": _(name),
                "needs": needs,
                "unlocked": sessions >= needs,
                "left": max(0, needs - sessions),
                "percent": 100 if not needs else min(100, round(100 * sessions / needs)),
                "chosen": look[kind] == key,
            }
            for key, name, needs in table
        ]

    return {
        "sessions": sessions,
        "level": level["n"],
        "level_name": level["name"],
        "rings": by_sessions("ring", REWARD_RINGS),
        "backdrops": by_sessions("backdrop", REWARD_BACKDROPS),
        "accessories": [
            {
                "key": key,
                "name": _(name),
                "level": needs,
                "unlocked": level["n"] >= needs,
                "worn": avatar.get("head") == key,
                "spec": {**(avatar or AVATAR_START), "head": key},
            }
            for key, name, needs in REWARD_ACCESSORIES
        ],
    }


def choose_look(
    store: str | os.PathLike, subscription: Subscription, kind: str, key: str, now: datetime | None = None
) -> Subscription:
    """The focus page's ring or backdrop, among those unlocked (D206)."""
    view = rewards_view(store, subscription, now)
    pool = {"ring": view["rings"], "backdrop": view["backdrops"]}.get(kind)
    item = next((x for x in pool or () if x["key"] == key), None)
    if item is None:
        raise InvalidInput(_("choose one of the options shown"))
    if not item["unlocked"]:
        raise InvalidInput(
            _n(
                "unlocked after {n} session timed with Focus",
                "unlocked after {n} sessions timed with Focus",
                item["needs"],
            )
        )
    look = {**(subscription.options.get("look") or {}), kind: key}
    return replace(subscription, options={**subscription.options, "look": look})


def unlocks_next(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> list[str]:
    """The rings and backdrops the session being saved unlocks: those needing one more
    session than the student has timed (D206), for the end of a session (D177)."""
    view = rewards_view(store, subscription, now)
    return [x["name"] for x in (*view["rings"], *view["backdrops"]) if x["needs"] == view["sessions"] + 1]


def language_of(subscription: Subscription) -> str:
    """The language a plan's pages and calendar events are written in (D10)."""
    return pick(subscription.options.get("lang"))


def set_language(subscription: Subscription, lang: str, *, now: datetime | None = None) -> Subscription:
    """Change a plan's language. The plan is made again from the calendar last read,
    so that what each session says to do is written in the new language; what the
    student reported is kept (sessions are named by language-free ids)."""
    if lang not in _i18n.LANGUAGES:
        raise InvalidInput(_("{value} is not a language this app speaks", value=repr(lang)))
    changed = replace(subscription, options={**subscription.options, "lang": lang})
    return refresh_subscription(changed, now=now, reread=False) if subscription.plan is not None else changed


def plan_language(store: str | os.PathLike, token: str) -> str | None:
    """The language of the plan at `token`, or None if there is none (the web app
    asks before it answers a request about that plan)."""
    subscription = load_subscription(store, token)
    return language_of(subscription) if subscription is not None else None


def hide_review(subscription: Subscription, week: str) -> Subscription:
    """The student read the review of the week starting `week` (a Monday)."""
    try:
        date.fromisoformat(week)
    except ValueError:
        raise InvalidInput(_("{value} is not a week", value=repr(week))) from None
    return replace(subscription, options={**subscription.options, "review_seen": week})


# --------------------------------------------------------------------------- #
# Focus sessions and the diary (DECISIONS.md D17)

# The focus page sends a heartbeat this often; a gap longer than the grace is time
# away (the page hidden, the phone on another app, the tab closed).
FOCUS_BEAT = timedelta(seconds=30)
FOCUS_GRACE = timedelta(seconds=75)
FOCUS_LONGEST = timedelta(hours=12)


def _instant(moment: datetime) -> str:
    """A focus session's times, to the second (AUDIT item 65): kept to the minute,
    a session started 50 s into a minute showed 0:00:50 at once, and each heartbeat
    measured its gap from the minute before, often past the grace."""
    return moment.isoformat(timespec="seconds")


# How long a focus session is meant to last, chosen before starting (D67), in
# minutes; 0 is no length. Its rewards come at 30 minutes, at an hour, then every
# 45 minutes, and when the length chosen is reached; all on focused time, the time
# on the page, so leaving the app does not earn them.
FOCUS_GOALS = (25, 45, 60, 90, 120, 180)
FOCUS_LADDER = (30, 60, *range(105, 12 * 60 + 1, 45))
# How far ahead the page lists rewards: the length chosen, or six hours.
FOCUS_HORIZON = 6 * 60
_MINUTE = timedelta(minutes=1)


def focus_goal(value: object) -> int:
    """A length the student may choose, in minutes (0: none)."""
    try:
        goal = int(str(value or 0))
    except ValueError:
        goal = -1
    if goal != 0 and goal not in FOCUS_GOALS:
        raise InvalidInput(_("choose one of the lengths offered"))
    return goal


def reward_points(goal: int, horizon: int) -> list[int]:
    """The minutes at which a session with `goal` earns a reward, up to `horizon`:
    the ladder's, and the goal's own (D67)."""
    points = {m for m in FOCUS_LADDER if m <= horizon}
    if goal:
        points.add(goal)
    return sorted(points)


def rewards_earned(goal: int, minutes: int) -> int:
    """How many rewards `minutes` of focus earned, with `goal` chosen."""
    return len(reward_points(goal, minutes)) - (1 if goal > minutes else 0)


def start_focus(
    subscription: Subscription,
    course: str,
    *,
    sid: str | None = None,
    goal: int = 0,
    repair: str | None = None,
    opener: str | None = None,
    place: str | None = None,
    aim: str = "",
    rhythm: bool = False,
    now: datetime | None = None,
) -> Subscription:
    """Start the timer for `course`, or for the plan's session `sid`, meant to last
    `goal` minutes (0: no length chosen, D67). `repair` names the missed day this
    session makes up for (D126): one of the last two days, checked when it is
    saved. `opener` is the card the session opens with (D128, `opening_card`); `place`
    the place on the map it is studied at (D133), checked by the caller. D178: `aim` is
    what the student means to finish, in a line; with `rhythm`, a 5-minute pause comes by
    itself after every 25 minutes of work (`_rhythm`)."""
    now = now or datetime.now(UTC)
    goal = focus_goal(goal)
    aim = " ".join(str(aim or "").split())[:FOCUS_AIM_MOST]
    if sid:
        session = find_session(subscription, sid)
        if session is None:
            raise InvalidInput(_("this session is no longer in your plan; the plan has changed since"))
        course = course or (session.title if session.kind == "task" else session.subject)
    course = " ".join(str(course or "").split())[: social.LIMITS["course"]]
    if not course:
        raise InvalidInput(_("say what you studied"))
    if repair:
        today = now.astimezone(_zone(subscription.options["tz"])).date()
        try:
            missed = date.fromisoformat(repair)
        except ValueError:
            raise InvalidInput(_("{value} is not a day", value=repr(repair))) from None
        if not 1 <= (today - missed).days <= _progress.REPAIR_WITHIN:
            raise InvalidInput(_("a missed day can be repaired the next day or the one after"))
    focus = {
        "course": course,
        "sid": sid or "",
        "started": _instant(now),
        "last": _instant(now),
        "away": 0,
        "interruptions": 0,
        "goal": goal,
        "repair": repair or "",
        "opener": opener or "",
        "place": place or "",
        "aim": aim,
        "steps": [],
        "rhythm": bool(rhythm),
        "block": 0,
        "rest": 0,
        "rounds": 0,
        "pause_from": "",
    }
    focus["beats"] = 0
    options = {k: v for k, v in subscription.options.items() if k != "focus_draft"}
    if place:
        options["places_recent"] = _remember_place(options, place)
    # D179: the rhythm chosen is the student's next one too, where a session starts in one tap.
    return replace(subscription, options={**options, "focus": focus, "focus_rhythm": bool(rhythm)})


# D196 (the owner, 2026-11-25: "on focus you must block social media apps such as Instagram,
# TikTok, Facebook and WhatsApp"). A web page cannot block another app, nor another site: no
# browser lets it, on a phone or a computer. What can is the device's own: an iPhone's Focus
# and Screen Time, Android's Focus mode (Digital Wellbeing), which pauses the apps chosen. So
# the focus page shows the steps for the device the page is opened on, set up once, and on an
# iPhone a shortcut the student made ("Set Focus: toggle Study") is opened in one tap when a
# session starts and when it ends. Blocking from the app itself would need an app of its own
# for each phone (Apple's Screen Time API, Android's accessibility or usage access): the
# owner's choice, not taken here.
LOCK_APPS = ("Instagram", "TikTok", "Facebook", "WhatsApp")
LOCK_SHORTCUT = "Study"  # the shortcut's name when the student keeps the one the steps say
LOCK_NAME = re.compile(r"[\w .'-]{1,40}")


def device_of(user_agent: str) -> str:
    """The kind of device a page is opened on, from its browser's User-Agent: "iphone"
    (an iPhone or an iPad), "android", "mac", "windows" or "other". A guess for showing
    the right steps first; every kind's steps stay a tap away."""
    ua = user_agent or ""
    if re.search(r"iPhone|iPad|iPod", ua):
        return "iphone"
    if "Android" in ua:
        return "android"
    if "Macintosh" in ua:
        return "mac"
    if "Windows" in ua:
        return "windows"
    return "other"


def lock_view(subscription: Subscription, device: str) -> dict:
    """What keeps the distracting apps away during a session: the steps for each kind of
    device, the one the page is on first, and the student's shortcut (an iPhone's), opened
    in one tap from the focus page once set."""
    apps = ", ".join(LOCK_APPS)
    steps = {
        "iphone": [
            _("Settings › Focus › + › Custom: call it Study, and under Apps silence {apps}.", apps=apps),
            _(
                "For apps that stay locked: Settings › Screen Time › Downtime, at your study hours, "
                "with only the apps you allow."
            ),
            _(
                "In Shortcuts, a new shortcut called Study with one action: Set Focus, Toggle, "
                "Study. Then one tap here turns it on and off."
            ),
        ],
        "android": [
            _(
                "Settings › Digital Wellbeing & parental controls › Focus mode: choose {apps}. While "
                "it is on, they cannot be opened.",
                apps=apps,
            ),
            _("Swipe down, tap the pencil and drag the Focus mode tile in: one tap turns it on."),
            _("Before a session, swipe down and tap Focus mode; tap it again when you finish."),
        ],
        "mac": [
            _("System Settings › Focus silences notifications while you study."),
            _("System Settings › Screen Time › App Limits can limit {apps} and their websites.", apps=apps),
        ],
        "windows": [
            _("Focus in the Clock app silences notifications; it does not close sites, so close their tabs."),
        ],
    }
    first = device if device in steps else "iphone"
    lock = subscription.options.get("focus_lock") or {}
    name = str(lock.get("shortcut") or "")
    return {
        "device": device,
        "apps": apps,
        "first": first,
        "steps": [{"device": first, "lines": steps[first]}]
        + [{"device": d, "lines": lines} for d, lines in steps.items() if d != first],
        "names": {"iphone": "iPhone", "android": "Android", "mac": "Mac", "windows": "Windows"},
        "shortcut": name,
        "link": f"shortcuts://run-shortcut?name={quote(name)}" if name and device == "iphone" else "",
        "default_name": LOCK_SHORTCUT,
    }


# D198 (the owner, 2026-11-25: "we block social media, not the user"). On a computer the
# app's browser extension (`extension/`) blocks these sites while a session runs: every page
# of a plan says until when (`focus_until`), and the extension reads nothing else. A session
# without a length is taken to last FOCUS_BLOCK_OPEN; one with a length, that length with its
# pauses (D178) and FOCUS_BLOCK_SLACK more. Leaving the session, or ending it, ends the block
# the next time a page of the plan is opened.
LOCK_SITES = ("instagram.com", "tiktok.com", "facebook.com", "messenger.com", "web.whatsapp.com")
FOCUS_BLOCK_OPEN = timedelta(hours=3)
FOCUS_BLOCK_SLACK = timedelta(minutes=15)


def focus_until(subscription: Subscription | None, now: datetime | None = None) -> str:
    """When the session under way is taken to end, as an ISO instant, for the extension that
    blocks the distracting sites meanwhile; "" with no session, or one past that time."""
    focus = (subscription.options.get("focus") or {}) if subscription is not None else {}
    if not focus.get("started"):
        return ""
    now = now or datetime.now(UTC)
    started = datetime.fromisoformat(focus["started"])
    goal = focus.get("goal")
    if goal:
        length = timedelta(minutes=int(goal))
        if focus.get("rhythm"):
            length += timedelta(seconds=RHYTHM_PAUSE) * int(length.total_seconds() // RHYTHM_WORK)
        end = started + length + FOCUS_BLOCK_SLACK
    else:
        end = started + FOCUS_BLOCK_OPEN
    return end.astimezone(UTC).isoformat(timespec="seconds") if end > now else ""


def set_focus_shortcut(subscription: Subscription, name: str) -> Subscription:
    """The name of the shortcut that toggles the student's Focus on their iPhone, or "" to
    forget it."""
    name = " ".join(str(name or "").split())
    if name and not LOCK_NAME.fullmatch(name):
        raise InvalidInput(_("a shortcut's name, as Shortcuts shows it, up to 40 characters"))
    lock = {**(subscription.options.get("focus_lock") or {}), "shortcut": name}
    return replace(subscription, options={**subscription.options, "focus_lock": lock})


# D207: sounds to concentrate by, on the focus page (the owner: "nature, rain, binaural, sea,
# cosy café hum, jazz, classic, fireplace"). Each is made by the browser as it plays
# (`static/sound.js`, the Web Audio API): no recording, no file to fetch, nothing to license, and
# it plays offline. Jazz and classical are generated too: a soft swing on four chords, and an
# arpeggio on the chords of Pachelbel's Canon (1690s, in the public domain) with its upper line.
# Each key is the script's name for it; the icon is Lucide's.
# D210: the owner found them not what they were meant to be, and asked for lo-fi: every sound but
# binaural is now a recording in the public domain (`focussounds`), fetched from the app's own
# files when it is first played; the browser's own version plays in its place before that, when
# the device is offline.
FOCUS_SOUNDS: tuple[tuple[str, str, str], ...] = (
    ("nature", "Nature", "trees"),
    ("rain", "Rain", "cloud-rain"),
    ("binaural", "Binaural", "headphones"),
    ("sea", "Sea", "waves"),
    ("cafe", "Café", "coffee"),
    ("lofi", "Lo-fi", "cassette-tape"),
    ("jazz", "Jazz", "music-2"),
    ("classical", "Classical", "piano"),
    ("fireplace", "Fireplace", "flame"),
)
FOCUS_PAD = _focussounds.PAD
FOCUS_VOLUME = 50  # out of 100, the first time


def focus_sound(subscription: Subscription) -> dict:
    """The sound the student last chose on the focus page (D207), its name and volume, and the
    whole list: none chosen until one is tapped; a name this version does not make, none."""
    chosen = subscription.options.get("focus_sound") or {}
    key = str(chosen.get("key") or "")
    names = {k: _(name) for k, name, _icon in FOCUS_SOUNDS}
    try:
        volume = max(0, min(100, int(chosen.get("volume", FOCUS_VOLUME))))
    except (TypeError, ValueError):
        volume = FOCUS_VOLUME
    return {
        "key": key if key in names else "",
        "name": names.get(key, ""),
        "volume": volume,
        "pad": FOCUS_PAD,
        "sounds": [
            {
                "key": k,
                "name": names[k],
                "icon": icon,
                # D210: the recording's loop and where it comes from; None for binaural.
                "loop": _focussounds.RECORDED[k].loop if k in _focussounds.RECORDED else None,
                "credit": _focussounds.RECORDED.get(k),
            }
            for k, _name, icon in FOCUS_SOUNDS
        ],
    }


def set_focus_sound(subscription: Subscription, key: str, volume: object = None) -> Subscription:
    """Keep the sound tapped and its volume (D207), saved as they change (D165); an empty key
    keeps the volume and forgets the sound."""
    if key and key not in {k for k, _name, _icon in FOCUS_SOUNDS}:
        raise InvalidInput(_("choose one of the options shown"))
    kept = focus_sound(subscription)["volume"]
    if volume not in (None, ""):
        try:
            kept = max(0, min(100, int(str(volume))))
        except ValueError:
            raise InvalidInput(
                _("{what} is a number from {low} to {high}", what=_("Volume"), low=0, high=100)
            ) from None
    return replace(
        subscription, options={**subscription.options, "focus_sound": {"key": key, "volume": kept}}
    )


def focus_rhythm_default(subscription: Subscription) -> bool:
    """Whether a session started in one tap (Today, Join, a session's card) keeps the 25 + 5
    rhythm (D179): the student's last choice on the focus page, on until they turn it off."""
    return bool(subscription.options.get("focus_rhythm", True))


def _beat(focus: dict, now: datetime) -> dict:
    last = datetime.fromisoformat(focus["last"])
    gap = now - last
    away, interruptions = focus["away"], focus["interruptions"]
    added = 0
    if focus["beats"] and gap > FOCUS_GRACE:
        # D178: a pause is time off the page by design: its part of the gap is not away.
        paused = timedelta(0)
        if focus.get("pause_from"):
            begun = datetime.fromisoformat(focus["pause_from"])
            ends = begun + timedelta(seconds=RHYTHM_PAUSE)
            paused = max(timedelta(0), min(now, ends) - max(last, begun))
        added = max(0, round((gap - FOCUS_BEAT - paused).total_seconds()))
        if added:
            away += added
            interruptions += 1
    if focus.get("rhythm"):
        focus = _rhythm(focus, now, away=added)
    return {
        **focus,
        "last": _instant(now),
        "away": away,
        "interruptions": interruptions,
        "beats": focus["beats"] + 1,
    }


# D178 (the owner's 3A, "ritmo 25 + 5, da solo"): the session's rhythm, 25 minutes of work
# and a 5-minute pause that comes by itself; the clock stops while the pause counts down,
# leaving the page during it is not time away, and the work starts again when it ends or
# when the student taps to go on. 25 and 5 are the usual "Pomodoro" lengths, a convention
# rather than a measured optimum; the breaks tip's study used 6 minutes every 24 (D62).
RHYTHM_WORK = 25 * 60  # seconds of work before a pause
RHYTHM_PAUSE = 5 * 60  # seconds of a pause


def _rhythm(focus: dict, now: datetime, away: int = 0) -> dict:
    """A session's rhythm brought from its last heartbeat to `now` (D178): the work block so
    far (`block`, seconds), the pauses ended (`rest`, seconds), the pause going on
    (`pause_from`, an instant, or ""), and the work blocks done (`rounds`). `away` is the
    time away measured since the last heartbeat, which is not work. Pure: a heartbeat keeps
    what it returns; a page only shows it."""
    if not focus.get("rhythm"):
        return focus
    moment = datetime.fromisoformat(focus["last"])
    block, rest, rounds = int(focus.get("block", 0)), int(focus.get("rest", 0)), int(focus.get("rounds", 0))
    pause = str(focus.get("pause_from") or "")
    left_away = max(0, int(away))
    while True:
        if pause:
            ends = datetime.fromisoformat(pause) + timedelta(seconds=RHYTHM_PAUSE)
            if now < ends:
                break
            rest += RHYTHM_PAUSE
            moment, pause = max(moment, ends), ""
            continue
        available = (now - moment).total_seconds() - left_away
        room = RHYTHM_WORK - block
        if available < room:
            block += max(0, round(available))
            break
        moment += timedelta(seconds=room + left_away)
        left_away, block, rounds, pause = 0, 0, rounds + 1, _instant(moment)
    return {**focus, "block": block, "rest": rest, "rounds": rounds, "pause_from": pause}


def _paused_now(focus: dict, now: datetime) -> int:
    """Seconds of the pause going on at `now` (D178); 0 at work."""
    if not focus.get("pause_from"):
        return 0
    return max(
        0, min(RHYTHM_PAUSE, round((now - datetime.fromisoformat(focus["pause_from"])).total_seconds()))
    )


def resume_focus(subscription: Subscription, *, now: datetime | None = None) -> Subscription:
    """Back to work before the pause ends (D178): the pause counts as long as it lasted."""
    now = now or datetime.now(UTC)
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    focus = _rhythm(focus, now)
    if focus.get("pause_from"):
        focus = {**focus, "rest": int(focus["rest"]) + _paused_now(focus, now), "pause_from": "", "block": 0}
    focus = {**focus, "last": _instant(now)}
    return replace(subscription, options={**subscription.options, "focus": focus})


# D178 (the owner's 1A and 1B): what the session means to finish, in a line, and the steps
# it is cut into while the timer runs, ticked as they are done; at the end the diary asks
# whether it was finished, its answer already given by the steps ticked.
FOCUS_AIM_MOST = 120  # characters
FOCUS_STEPS_MOST = 5
FOCUS_STEP_MOST = 80  # characters
FOCUS_FINISHED = ("yes", "partly", "no")


def add_focus_step(subscription: Subscription, text: str) -> Subscription:
    """A step of the running session (D178), at most `FOCUS_STEPS_MOST`."""
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    text = " ".join(str(text or "").split())[:FOCUS_STEP_MOST]
    if not text:
        raise InvalidInput(_("say what the step is"))
    steps = list(focus.get("steps") or [])
    if len(steps) >= FOCUS_STEPS_MOST:
        raise InvalidInput(_("at most {n} steps", n=FOCUS_STEPS_MOST))
    steps.append({"text": text, "done": False})
    return replace(subscription, options={**subscription.options, "focus": {**focus, "steps": steps}})


def tick_focus_step(subscription: Subscription, index: int, done: bool) -> Subscription:
    """A step done, or not after all (D178)."""
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    steps = [dict(x) for x in focus.get("steps") or []]
    if not 0 <= index < len(steps):
        raise InvalidInput(_("that step is not in this session"))
    steps[index]["done"] = bool(done)
    return replace(subscription, options={**subscription.options, "focus": {**focus, "steps": steps}})


def _finished_guess(steps: Sequence[Mapping]) -> str:
    """The diary's answer the steps already give (D178): every step ticked, yes; some, in
    part; none, no; no steps, none yet."""
    if not steps:
        return ""
    done = sum(1 for x in steps if x.get("done"))
    return "yes" if done == len(steps) else "partly" if done else "no"


def focus_beat(subscription: Subscription, *, now: datetime | None = None) -> Subscription:
    """The focus page is open and visible. A gap since the last heartbeat longer
    than `FOCUS_GRACE` counts as time away, once per gap."""
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    return replace(
        subscription, options={**subscription.options, "focus": _beat(focus, now or datetime.now(UTC))}
    )


def finish_focus(subscription: Subscription, *, now: datetime | None = None) -> Subscription:
    """Stop the timer; what it measured becomes the draft of the session's log.
    Without a single heartbeat (a browser without the script), the time is the time
    between start and finish, and the log says the focus was not checked."""
    now = now or datetime.now(UTC)
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    focus = _beat(focus, now) if focus["beats"] else _rhythm(focus, now)
    started = datetime.fromisoformat(focus["started"])
    elapsed = min(now - started, FOCUS_LONGEST)
    # D178: the rhythm's pauses are not studied time.
    paused = timedelta(seconds=int(focus.get("rest", 0)) + _paused_now(focus, now))
    focused = max(elapsed - timedelta(seconds=focus["away"]) - paused, timedelta(minutes=1))
    steps = [dict(x) for x in focus.get("steps") or []]
    draft = {
        "course": focus["course"],
        "sid": focus["sid"],
        "started": focus["started"],
        "ended": _instant(now),
        "minutes": max(1, round(focused / timedelta(minutes=1))),
        "away_minutes": round(focus["away"] / 60),
        "interruptions": focus["interruptions"],
        "checked": bool(focus["beats"]),
        "timed": True,
        "goal": int(focus.get("goal", 0)),
        "repair": str(focus.get("repair") or ""),
        "opener": str(focus.get("opener") or ""),
        # D178: what it meant to finish, its steps, and the answer they already give; the
        # aim is the diary's "What did you do?" unless the student writes otherwise.
        "aim": str(focus.get("aim") or ""),
        "title": str(focus.get("aim") or ""),
        "steps": steps,
        "finished": _finished_guess(steps),
        "rounds": int(focus.get("rounds", 0)),
    }
    options = {k: v for k, v in subscription.options.items() if k != "focus"}
    if focus.get("place"):
        # A session at a place on the study map, for the Explorer badge (D189).
        options["place_sessions"] = _place_session(options, now)
    return replace(subscription, options={**options, "focus_draft": draft})


# A curious start (D128): a planned self-test opens with one question from its week's
# own cards (D87), its answer kept for the end. Curiosity before information helped
# memory of it a day later (Gruber, Gelman and Ranganath 2014); the question asked
# is the card the model gives the least chance of recall now among those answered
# before (the one with most to gain from being asked), else the first written.
def opening_card(
    store: str | os.PathLike, subscription: Subscription, sid: str | None, now: datetime | None = None
) -> str | None:
    """The card a session `sid` opens with, or None: for anything but a self-test of
    a week of lectures, and for a week without cards."""
    if not sid or subscription.plan is None:
        return None
    session = find_session(subscription, sid)
    if session is None or session.kind not in ("review", "first review") or not session.topic:
        return None
    now = now or datetime.now(UTC)
    start = datetime.fromisoformat(session.start).astimezone(_zone(subscription.options["tz"]))
    week = _topic_monday(session.topic.removeprefix(f"{session.subject} · "), start.date())
    if week is None:
        return None
    kept = _store(store)
    found = flashcards.of_week(kept, subscription.token, session.subject, week)
    if not found:
        return None
    model = flashcards.model_for(kept, subscription.token)

    def chance(card: flashcards.Card) -> float:
        state = card.state()
        if state is None or card.last is None:
            return 2.0  # never answered: after every card answered before
        elapsed = max((now - datetime.fromisoformat(card.last)).total_seconds() / 86400, 0.0)
        return model.retrievability(elapsed, state.stability)

    return min(found, key=chance).id  # the first written among equals


def skip_opener(subscription: Subscription) -> Subscription:
    """The running session's opening question set aside: no answer at the end."""
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    return replace(subscription, options={**subscription.options, "focus": {**focus, "opener": ""}})


def opener_view(store: str | os.PathLike, subscription: Subscription, card_id: str | None) -> dict | None:
    """The opening question as the pages show it, or None when there is none or the
    card has since been deleted."""
    if not card_id:
        return None
    card = flashcards.get(_store(store), subscription.token, card_id)
    if card is None:
        return None
    return {"id": card.id, "front": card.front, "back": card.back, "course": card.course, "week": card.week}


def cancel_focus(subscription: Subscription) -> Subscription:
    options = {k: v for k, v in subscription.options.items() if k not in ("focus", "focus_draft")}
    return replace(subscription, options=options)


def focus_view(subscription: Subscription, now: datetime | None = None, *, brief: bool = False) -> dict:
    """What the focus page shows: the running session, or what can be started
    (the courses, and the plan's session now or next). `brief` leaves out the
    session's stakes (D128), which replay the plan: for the page's heartbeat."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    focus = subscription.options.get("focus")
    view: dict = {
        "running": bool(focus),
        "courses": course_names(subscription),
        "suggested": None,
        "goals": [{"minutes": g, "label": duration_words(g)} for g in FOCUS_GOALS]
        + [{"minutes": 0, "label": _("No limit")}],
        "goal": 45,
    }
    if focus:
        started = datetime.fromisoformat(focus["started"])
        goal = int(focus.get("goal", 0))
        # D178: the rhythm brought to now; its pauses stop the clock.
        rhythm = _rhythm(focus, now)
        pausing = _paused_now(rhythm, now)
        elapsed = round((now - started).total_seconds()) - int(rhythm.get("rest", 0)) - pausing
        focused = max(0, elapsed - focus["away"])
        steps = [dict(x) for x in focus.get("steps") or []]
        known = {course_key(c): c for c in topic_courses(subscription)}
        running = known.get(course_key(str(focus["course"])))
        view.update(
            # D163: the course's pages on Moodle beside the clock, once it is linked.
            moodle=moodle_course(subscription, running) if running else None,
            materials=week_materials(subscription, running, now) if running else [],  # D174
            study=week_study(subscription, running, now) if running else None,  # D208
            course=focus["course"],
            started=_iso(started),
            started_local=f"{started.astimezone(zone):%H:%M}",
            elapsed=elapsed,
            # D179: the clock shows the minutes on the page, the stem's and the diary's.
            focused=focused,
            grace=round(FOCUS_GRACE.total_seconds()),
            away=focus["away"],
            away_minutes=round(focus["away"] / 60),
            interruptions=focus["interruptions"],
            beat=round(FOCUS_BEAT.total_seconds()),
            goal=goal,
            goal_words=duration_words(goal) if goal else "",
            rewards=_focus_rewards(goal, focused),
            opener=str(focus.get("opener") or ""),
            # D178: what the session means to finish, its steps, the stem of its rewards,
            # and its rhythm: at work or pausing, the seconds left of a pause, the work
            # block so far, the blocks done and those its length makes.
            aim=str(focus.get("aim") or ""),
            steps=steps,
            steps_done=sum(1 for x in steps if x.get("done")),
            steps_most=FOCUS_STEPS_MOST,
            stem=_focus_stem(goal, focused),
            rhythm=bool(focus.get("rhythm")),
            pausing=bool(rhythm.get("pause_from")),
            pause_left=RHYTHM_PAUSE - pausing if rhythm.get("pause_from") else 0,
            block=int(rhythm.get("block", 0)),
            work=RHYTHM_WORK,
            pause=RHYTHM_PAUSE,
            round=int(rhythm.get("rounds", 0)) + (0 if rhythm.get("pause_from") else 1),
            rounds=-(-goal * 60 // RHYTHM_WORK) if goal else 0,
            goal_reached=bool(goal) and focused >= goal * 60,
        )
        if focus.get("sid") and not brief:
            card = session_card(subscription, focus["sid"], now)
            view["stakes"] = card["stakes"] if card else None
    elif subscription.plan is not None:
        today = today_view(subscription, now)
        card = today.get("now") or (today.get("next") or [None])[0]
        if card is not None:
            session = find_session(subscription, card["id"])
            length = (
                round((datetime.fromisoformat(session.end) - datetime.fromisoformat(session.start)) / _MINUTE)
                if session is not None
                else 45
            )
            view["suggested"] = {
                "id": card["id"],
                "kind": session.kind if session is not None else "",
                "title": card["title"],
                "label": card["label"],
                "when": card["when"],
                # The length offered first: the one nearest the session's own.
                "goal": min(FOCUS_GOALS, key=lambda g: (abs(g - length), g)),
                "stakes": card.get("stakes"),
                # D169: what the sentence names, the task's title or the course.
                "course": (session.title if session.kind == "task" else session.subject)
                if session is not None
                else card["title"],
            }
    if not focus:
        # D169: the sentence's choices (the plan's session first), and for each length the
        # rewards staying earns, as the page shows them before the timer starts.
        view["goal"] = view["suggested"]["goal"] if view["suggested"] else view["goal"]
        kind = view["suggested"]["kind"] if view["suggested"] else ""
        view["verb"] = _FOCUS_VERBS.get(kind, _FOCUS_VERBS[""])
        view["ahead"] = {g["minutes"]: focus_ahead(g["minutes"]) for g in view["goals"]}
    return view


# D169: the focus page's sentence, by the kind of the plan's session; `{course}` and
# `{length}` are the student's to change with a tap.
_FOCUS_VERBS = {
    "review": "I test myself on {course} for {length}.",
    "first review": "I test myself on {course} for {length}.",
    "practice": "I practise exam questions in {course} for {length}.",
    "task": "I work on {course} for {length}.",
    "": "I study {course} for {length}.",
}
FOCUS_AHEAD_MOST = 3  # rewards shown ahead of a session: the first two and the last
# D178: a few aims to tap, by the kind of the plan's session, the field always the student's.
_AIM_IDEAS = {
    "review": ("10 flashcards", "the week's summary", "the exercises I got wrong"),
    "first review": ("10 flashcards", "the week's summary", "the exercises I got wrong"),
    "practice": ("a past paper", "3 exam questions", "the exercises I got wrong"),
    "task": ("the first step", "a first draft", "the last part"),
    "": ("the exercises", "a chapter", "the summary"),
}


def aim_ideas(suggested: Mapping | None) -> list[str]:
    """What the focus page offers to finish in one tap (D178), by the plan's session's kind."""
    kind = str((suggested or {}).get("kind") or "")
    return [_(x) for x in _AIM_IDEAS.get(kind, _AIM_IDEAS[""])]


# D178 (the owner's 2B): the week's garden on the focus page, a plant a day from Monday. The
# states rank from nothing to a flower; a day shows its best session's.
FOCUS_GARDEN = ("seed", "wilt", "sprout", "leaf", "bloom")


def _garden_state(post: social.Post) -> str:
    """A saved session's plant: in flower once it reached the length chosen, drooping when it
    stopped before half of it, leafy otherwise (and when no length was chosen)."""
    minutes = _minutes_of(post)
    try:
        goal = max(0, int(post.data.get("goal") or 0))
    except (TypeError, ValueError):
        goal = 0
    if goal and minutes >= goal:
        return "bloom"
    if goal and 2 * minutes < goal:
        return "wilt"
    return "leaf"


def focus_garden(
    store: Path,
    subscription: Subscription,
    now: datetime | None = None,
    running: Mapping | None = None,
) -> dict:
    """The week's garden (D178, the owner's 2B): from Monday, a plant for each day from the
    sessions timed with Focus that day, in the course's colour, the best of the day kept; a
    seed on a day without one, and on the days ahead. Today's is also the running session's
    (`focus_view`): a sprout, in flower once its length is reached."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    monday = today - timedelta(days=today.weekday())
    rank = {state: i for i, state in enumerate(FOCUS_GARDEN)}
    best: dict[str, tuple[str, str]] = {}

    def plant(day: str, state: str, course: str) -> None:
        if rank[state] > rank[best.get(day, ("seed", ""))[0]]:
            best[day] = (state, course)

    for post in social.posts_of(_store(store), subscription.token, "session"):
        if post.data.get("timed") and monday.isoformat() <= post.day <= today.isoformat():
            plant(post.day, _garden_state(post), str(post.data.get("course") or ""))
    if running and running.get("running"):
        plant(
            today.isoformat(),
            "bloom" if running.get("goal_reached") else "sprout",
            str(running.get("course")),
        )
    names = {"seed": _("no session"), "wilt": _("drooping"), "sprout": _("growing now")}
    names |= {"leaf": _("grown"), "bloom": _("in flower")}
    days = []
    for k in range(7):
        day = monday + timedelta(days=k)
        state, course = best.get(day.isoformat(), ("seed", ""))
        index = _course_index(subscription, course) if course else None
        days.append(
            {
                "label": _("today") if day == today else _i18n.weekday_name(day, short=True),
                "name": _i18n.weekday_name(day),
                "state": state,
                "said": names[state],
                "colour": f"c{index % 7}" if index is not None else "cn",
                "today": day == today,
                "ahead": day > today,
            }
        )
    return {"days": days, "flowers": sum(1 for d in days if d["state"] == "bloom")}


# D178 (the owner's 2C): the running session's rewards as a stem whose buds open as they are
# reached, drawn to the length chosen or an hour, whichever is longer; its leaves come out as
# the stem grows, at these shares of it.
# D179: round the clock, a leaf every twelfth of the ring.
FOCUS_STEM_LEAVES = tuple(round((k + 0.5) / 12, 3) for k in range(12))


def _focus_stem(goal: int, focused: int) -> dict:
    """The stem of a running session (D178): how far it has grown (`share`, 0 to 1), each
    reward's bud by its share of the stem, open once reached, and the minutes to the next."""
    horizon = max(goal, 60) * 60
    share = min(1.0, focused / horizon)
    buds = [
        {
            "at": m * 60,
            "share": m * 60 / horizon,
            "label": duration_words(m),
            "goal": bool(goal) and m == goal,
            "open": focused >= m * 60,
        }
        for m in reward_points(goal, horizon // 60)
    ]
    ahead = next((int(str(b["at"])) for b in buds if not b["open"]), None)
    return {
        "share": share,
        "percent": round(100 * share),
        "buds": buds,
        "leaves": [{"share": f, "out": share >= f, "side": i % 2} for i, f in enumerate(FOCUS_STEM_LEAVES)],
        "next": -(-(ahead - focused) // 60) if ahead is not None else 0,
        "horizon": horizon,
    }


def focus_ahead(goal: int) -> list[dict]:
    """The rewards a session of `goal` minutes earns if the student stays (D67), as the
    focus page shows them before it starts (D169): at most three, the first two and the
    goal (or, with no length, the ladder's first three)."""
    points = reward_points(goal, goal or FOCUS_LADDER[2])
    if len(points) > FOCUS_AHEAD_MOST:
        points = [*points[: FOCUS_AHEAD_MOST - 1], points[-1]]
    return [{"minutes": m, "label": duration_words(m), "goal": bool(goal) and m == goal} for m in points]


def focus_day(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """What the student has done today, for the focus page (D169): the minutes studied
    (sessions reported done and sessions logged) and the streak, as Progress counts it
    (D8, D126). None without a plan."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    sessions = _progress.merge_logged(progress_sessions(subscription), logged_sessions(store, subscription))
    first = min([PlanReport.from_dict(subscription.plan).settings.start, *(x.start.date() for x in sessions)])
    paused = _progress.pause_days(pauses_of(subscription.options.get("preferences", {})))
    repaired = {date.fromisoformat(d) for d in subscription.options.get("repairs", {})}
    run = _progress.streak(sessions, first, today, paused, repaired)
    minutes = sum(x.minutes for x in sessions if x.confirmed and x.start.date() == today)
    return {
        "minutes": minutes,
        "words": duration_words(minutes) if minutes else "",
        "streak": run.current,
        "today_done": run.today_done,
    }


def session_done_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """The end of a timed session, celebrated (D177, the owner: "quando finisci una sessione
    non ti dà la celebrazione a modo di Duolingo"): what the timer measured, the streak with
    today counted (the session counts once its log is saved, the button under it), whether
    the length chosen was reached and whether the student stayed on the page throughout,
    and the points a session of the plan gives (D158). Only what the timer and the plan
    hold; the key makes the confetti fly once per session. None without a timed session."""
    draft = subscription.options.get("focus_draft")
    if not draft or not draft.get("timed"):
        return None
    day = focus_day(store, subscription, now)
    minutes = int(draft.get("minutes") or 0)
    goal = int(draft.get("goal") or 0)
    streak = (day["streak"] + (0 if day["today_done"] else 1)) if day else 0
    return {
        "key": f"session-{draft.get('started', '')}",
        "minutes": minutes,
        "course": str(draft.get("course") or ""),
        "streak": streak,
        "streak_new": day is not None and not day["today_done"],
        "goal": duration_words(goal) if goal and minutes >= goal else "",
        "whole": bool(draft.get("checked")) and not int(draft.get("interruptions") or 0),
        "points": POINTS_DONE if draft.get("sid") else 0,
        "proof": POINTS_PROOF,
        "unlocks": unlocks_next(store, subscription, now),  # D206
    }


def _focus_rewards(goal: int, focused: int) -> list[dict]:
    """The rewards a running session lists (D67): each at its focused second, with
    what the spark says when it comes; those reached, and the next, are shown."""
    rewards = []
    shown_next = False
    for minutes in reward_points(goal, max(goal, FOCUS_HORIZON)):
        reached = focused >= minutes * 60
        words = duration_words(minutes)
        rewards.append(
            {
                "at": minutes * 60,
                "label": words,
                "goal": minutes == goal,
                "reached": reached,
                "shown": reached or not shown_next,
                "cheer": _("Goal reached: {time} of focus.", time=words)
                if minutes == goal
                else _("{time} of focus!", time=words),
            }
        )
        shown_next = shown_next or not reached
    for reward, following in zip(rewards, [*rewards[1:], None], strict=True):
        reward["next"] = _("Next: {time}.", time=following["label"]) if following else ""
    return rewards


def log_draft(subscription: Subscription, now: datetime | None = None, sid: str = "") -> dict:
    """The log form's starting values: the session just timed; or a session of the plan
    that has started, shared from the calendar to prove it (D158), its course, length and
    day; or an empty one."""
    draft = subscription.options.get("focus_draft")
    if draft:
        return {**draft}
    empty = {
        "course": "",
        "sid": "",
        "minutes": 60,
        "timed": False,
        "checked": False,
        "away_minutes": 0,
        "interruptions": 0,
        "goal": 0,
    }
    found = find_session(subscription, sid) if sid else None
    if found is not None:
        zone = _zone(subscription.options["tz"])
        start, end = _local(found.start, zone), _local(found.end, zone)
        if start <= (now or datetime.now(UTC)).astimezone(zone):
            minutes = max(1, round((end - start).total_seconds() / 60))
            empty.update(course=found.subject, sid=sid, minutes=minutes, day=start.date().isoformat())
    return empty


def _sharing(store: str | os.PathLike, token: str, chosen: str | None, default: str) -> str:
    """Who a post goes to. Sharing needs a handle (D16): without a profile nobody
    could follow the author or tell who wrote it, so a post with no choice made
    stays private, and a choice to share is refused with the way to fix it."""
    has_profile = social.get_profile(_store(store), token) is not None
    visibility = str(chosen or (default if has_profile else "me"))
    if visibility != "me" and not has_profile:
        raise InvalidInput(
            _("to share with others, choose a handle in your profile first; or keep it for yourself")
        )
    return visibility


def log_session(
    store: str | os.PathLike,
    subscription: Subscription,
    form: Mapping[str, str],
    photos: Sequence[bytes] = (),
    *,
    now: datetime | None = None,
) -> tuple[Subscription, social.Post]:
    """Keep a study session in the diary, and publish it to whom the student
    chose. A timed session keeps what the timer measured unless the student
    changes the minutes, and then it no longer says it was timed. A session started
    from the plan reports that session done."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    # A session of the plan shared from the calendar (D158) names itself in the form.
    draft = log_draft(subscription, now, sid=str(form.get("sid") or "")[:32])
    minutes = str(form.get("minutes") or draft["minutes"])
    timed = draft["timed"] and minutes == str(draft["minutes"])
    data = {
        "course": form.get("course") or draft["course"],
        "title": form.get("title", ""),
        "note": form.get("note", ""),
        "simple": form.get("simple", ""),
        "effort": form.get("effort", ""),
        "progress": form.get("progress", ""),
        "minutes": minutes,
        "timed": timed,
        "checked": timed and draft["checked"],
        "away_minutes": draft["away_minutes"] if timed else 0,
        "interruptions": draft["interruptions"] if timed else 0,
        "goal": draft.get("goal", 0) if timed else 0,
        "sid": draft.get("sid") or "",
        # When the timer started, for "starting on time" (D80); kept from 2026-10-21.
        "started": draft.get("started", "") if timed else "",
    }
    # D178: what the session meant to finish, its steps, and whether it was finished.
    if draft.get("aim"):
        finished = str(form.get("finished") or draft.get("finished") or "")
        data["aim"] = draft["aim"]
        data["finished"] = finished if finished in FOCUS_FINISHED else ""
        data["steps"] = [{"text": x["text"], "done": bool(x.get("done"))} for x in draft.get("steps") or []]
    visibility = _sharing(store, subscription.token, form.get("visibility"), "followers")
    day = (
        datetime.fromisoformat(draft["started"]).astimezone(zone).date()
        if draft.get("started")
        else date.fromisoformat(draft["day"])
        if draft.get("day")
        else now.astimezone(zone).date()
    )
    try:
        post = social.create_post(
            _store(store),
            subscription.token,
            "session",
            day=day.isoformat(),
            visibility=visibility,
            data=data,
            photos=photos,
            now=now,
        )
    except social.SocialError as error:
        raise InvalidInput(str(error)) from None
    changed = replace(
        subscription, options={k: v for k, v in subscription.options.items() if k != "focus_draft"}
    )
    # A make-up session repairs the missed day it was started for (D126), when the
    # timer measured at least its length: minutes typed in repair nothing (D67).
    repair = str(draft.get("repair") or "") if timed else ""
    if repair and int(minutes) >= REPAIR_MINUTES:
        repairs = {**changed.options.get("repairs", {}), repair: day.isoformat()}
        changed = replace(changed, options={**changed.options, "repairs": repairs})
    sid = draft.get("sid")
    if sid and subscription.outcomes.get(sid) is None:
        # If the plan has changed since, the diary keeps the session anyway.
        with contextlib.suppress(InvalidInput):
            changed = report_session(changed, sid, "done", now=now)
    return changed, post


def logged_sessions(store: str | os.PathLike, subscription: Subscription) -> list[_progress.Session]:
    """The diary's sessions as `progress` counts them: each makes its day studied."""
    zone = _zone(subscription.options["tz"])
    out = []
    for post in social.posts_of(_store(store), subscription.token, "session"):
        day = date.fromisoformat(post.day)
        out.append(
            _progress.Session(
                start=datetime.combine(day, time(12, 0), tzinfo=zone),
                minutes=int(post.data.get("minutes", 0)),
                kind="logged",
                course=post.data.get("course", ""),
                report="done",
                sid=str(post.data.get("sid") or ""),
            )
        )
    return out


EFFORT_WORDS = {1: "very easy", 3: "easy", 5: "steady", 7: "hard", 9: "very hard", 10: "all out"}
PROGRESS_WORDS = {1: "stuck", 2: "a little", 3: "steady", 4: "good", 5: "a breakthrough"}
# D100: what kind of work a diary session was, read from the words of its title, for
# its picture. The first kind with a word in the title wins (a plural too); none of
# them, "study". English, French, Italian, Spanish and German words; a picture only,
# so a word misread costs a different drawing, nothing else.
WORK_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "paper",
        (
            "paper",
            "exam",
            "mock",
            "annale",
            "examen",
            "esame",
            "esami",
            "simulazione",
            "partiel",
            "klausur",
            "prüfung",
        ),
    ),
    ("cards", ("flashcard", "card", "carte", "tarjeta", "karteikarte")),
    (
        "exercises",
        (
            "exercise",
            "problem",
            "sheet",
            "worksheet",
            "homework",
            "exercice",
            "td",
            "devoir",
            "esercizio",
            "esercizi",
            "problema",
            "problemi",
            "compiti",
            "ejercicio",
            "übung",
            "aufgabe",
        ),
    ),
    (
        "summary",
        (
            "summary",
            "note",
            "notes",
            "résumé",
            "fiche",
            "riassunto",
            "riassunti",
            "appunti",
            "schema",
            "resumen",
            "apuntes",
            "zusammenfassung",
            "mitschrift",
        ),
    ),
    (
        "reading",
        (
            "reading",
            "read",
            "chapter",
            "book",
            "article",
            "chapitre",
            "livre",
            "lettura",
            "capitolo",
            "capitoli",
            "libro",
            "lectura",
            "kapitel",
        ),
    ),
    (
        "lecture",
        (
            "lecture",
            "video",
            "recording",
            "class",
            "cours",
            "lezione",
            "lezioni",
            "registrazione",
            "clase",
            "vorlesung",
        ),
    ),
    (
        "project",
        (
            "project",
            "lab",
            "code",
            "coding",
            "programming",
            "projet",
            "progetto",
            "laboratorio",
            "proyecto",
            "projekt",
        ),
    ),
)
_WORK = tuple(
    (kind, re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")(?:s|es)?\b", re.IGNORECASE))
    for kind, words in WORK_WORDS
)


def work_kind(title: str) -> str:
    """The kind of work a session's title names (`WORK_WORDS`), "study" if none."""
    return next((kind for kind, pattern in _WORK if pattern.search(title or "")), "study")


def duration_words(minutes: int) -> str:
    """ "45 min", "1 h 20", "2 h": how long a session lasted, as a person says it."""
    if minutes < 60:
        return _("{n} min", n=minutes)
    hours, rest = divmod(minutes, 60)
    return _("{h} h {m}", h=hours, m=f"{rest:02d}") if rest else _("{h} h", h=hours)


def post_card(post: social.Post, *, viewer: str | None, now: datetime, zone: ZoneInfo) -> dict:
    """A post as the pages draw it, for `viewer` (a token, or None): its photos'
    addresses carry the viewer's page, which is how the server knows who asks."""
    d = post.data
    when = date.fromisoformat(post.day)
    prefix = f"/p/{viewer}/m/" if viewer else "/m/"
    card = {
        **post.to_dict(),
        "mine": viewer == post.token,
        "when": format_date(when, "long"),
        "photos": [prefix + name for name in d.get("photos", [])],
    }
    if post.kind == "notes":
        card["body"] = d.get("body", "")
        # D95: a set's PDFs, each fetched from the viewer's page, as the photos are.
        card["pdfs"] = [{**f, "src": prefix + f["name"]} for f in d.get("pdfs", [])]
        card["cards_from"] = bool(d.get("body") or d.get("pdf_text")) and viewer is not None
    if post.kind == "session":
        effort = int(d.get("effort", 0))
        card["work"] = work_kind(str(d.get("title", "")))
        card["effort_word"] = _(EFFORT_WORDS[max(k for k in EFFORT_WORDS if k <= effort)]) if effort else ""
        card["progress_word"] = _(PROGRESS_WORDS.get(int(d.get("progress", 0)), ""))
        card["hours"] = format_number(int(d.get("minutes", 0)) / 60)
        card["duration"] = duration_words(int(d.get("minutes", 0)))
        if d.get("timed") and d.get("checked"):
            goal, minutes = int(d.get("goal", 0)), int(d.get("minutes", 0))
            card["rewards"] = rewards_earned(goal, minutes)
            card["goal"] = (
                (
                    _("goal reached: {time}", time=duration_words(goal))
                    if minutes >= goal
                    else _("goal: {time}", time=duration_words(goal))
                )
                if goal
                else ""
            )
            card["focus"] = (
                _n(
                    "left the app {n} time ({minutes} min)",
                    "left the app {n} times ({minutes} min)",
                    d.get("interruptions", 0),
                    minutes=d.get("away_minutes", 0),
                )
                if d.get("interruptions")
                else _("focused the whole time")
            )
        elif d.get("timed"):
            card["focus"] = _("timed, focus not checked")
        else:
            card["focus"] = _("not timed")
    return card


def diary_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """The diary: one's own sessions and explanations, newest first, with this
    week's focus time."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    posts = social.posts_of(_store(store), subscription.token)
    monday = _progress.monday(now.astimezone(zone).date())
    week = [p for p in posts if p.kind == "session" and date.fromisoformat(p.day) >= monday]
    efforts = [int(p.data["effort"]) for p in week]
    cards = [post_card(p, viewer=subscription.token, now=now, zone=zone) for p in posts]
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    colours = {name.casefold(): index for name, index in course_colours(plan)} if plan else {}
    for card in cards:  # D100: a session in its course's colour
        card["color"] = colours.get(str(card.get("course", "")).strip().casefold())
    return {
        "posts": cards,
        "weeks": _diary_weeks(cards, monday),
        "week": {
            "sessions": len(week),
            "hours": format_number(sum(int(p.data["minutes"]) for p in week) / 60),
            "effort": format_number(sum(efforts) / len(efforts)) if efforts else "",
        },
    }


def _diary_weeks(cards: Sequence[dict], monday: date) -> list[dict]:
    """D100: the diary week by week, newest first, to look back on: each week's
    sessions and hours, the best of how its sessions went (1 to 5, 0 without one),
    and the week with the most hours marked once there are two weeks. This week and
    the last are open; so is the latest, if older."""
    by_week: dict[date, dict] = {}
    for card in cards:
        first = _progress.monday(date.fromisoformat(card["day"]))
        week = by_week.setdefault(
            first, {"first": first, "posts": [], "minutes": 0, "sessions": 0, "best": 0}
        )
        week["posts"].append(card)
        if card["kind"] == "session":
            week["sessions"] += 1
            week["minutes"] += int(card.get("minutes", 0))
            week["best"] = max(week["best"], int(card.get("progress", 0)))
    weeks = sorted(by_week.values(), key=lambda w: w["first"], reverse=True)
    most = max(weeks, key=lambda w: w["minutes"], default=None) if len(weeks) > 1 else None
    for i, week in enumerate(weeks):
        first = week.pop("first")
        week["name"] = (
            _("This week")
            if first == monday
            else _("Last week")
            if first == monday - timedelta(days=7)
            else _("Week of {day}", day=format_date(first, "short"))
        )
        week["open"] = i == 0 or first >= monday - timedelta(days=7)
        week["hours"] = format_number(week["minutes"] / 60)
        week["biggest"] = week is most and week["minutes"] > 0
    return weeks


def delete_post(store: str | os.PathLike, token: str, post_id: str) -> bool:
    return social.delete_post(_store(store), token, post_id)


def set_post_visibility(store: str | os.PathLike, token: str, post_id: str, visibility: str) -> bool:
    visibility = _sharing(store, token, visibility, "me")
    try:
        return social.set_visibility(_store(store), token, post_id, visibility)
    except social.SocialError as error:
        raise InvalidInput(str(error)) from None


def photo_for(store: str | os.PathLike, viewer: str | None, name: str) -> tuple[bytes, str] | None:
    """A post's photo, for someone allowed to see the post (D18)."""
    where = _store(store)
    post = social.photo_post(where, name)
    if post is None or not social.may_see(where, viewer, post):
        return None
    path = where.photos / name
    if not path.is_file():
        return None
    return path.read_bytes(), _MEDIA.get(path.suffix, "image/jpeg")


_MEDIA = {".png": "image/png", ".pdf": "application/pdf"}  # a post's files, by their ending (D95)


def pdf_title(store: str | os.PathLike, name: str) -> str:
    """The name a set of notes' PDF was uploaded under (D95), to save it under."""
    post = social.photo_post(_store(store), name)
    for f in post.data.get("pdfs", []) if post else []:
        if f["name"] == name:
            return str(f["title"])
    return "notes"


# --------------------------------------------------------------------------- #
# The study network (DECISIONS.md D16, D19)


def _social(call: Callable[[], Any]) -> Any:
    """`call`, with the network's refusals as the service's own."""
    try:
        return call()
    except social.SocialError as error:
        raise InvalidInput(str(error)) from None


def profile_of(store: str | os.PathLike, token: str) -> dict | None:
    profile = social.get_profile(_store(store), token)
    return profile.public() if profile else None


# The years of study, as the profile's form offers them (D39).
YEAR_WORDS = {
    "1": "1st year",
    "2": "2nd year",
    "3": "3rd year",
    "4": "4th year or later",
    "master": "Master's",
    "phd": "PhD",
    "other": "Other",
}


def save_profile(
    store: str | os.PathLike, token: str, form: Mapping[str, str], *, tz: str | None = None
) -> dict:
    """Create or change the network profile; a university written as the list
    writes it is kept with its country (D39)."""
    profile = _social(
        lambda: social.set_profile(
            _store(store),
            token,
            handle=str(form.get("handle", "")),
            university=str(form.get("university", "")),
            programme=str(form.get("programme", "")),
            bio=str(form.get("bio", "")),
            year=str(form.get("year", "")),
            country=_universities.country_of_zone(tz) if tz else None,
            old_enough=form.get("old_enough") == "1",
        )
    )
    return profile.public()


def university_suggestions(query: str, tz: str | None = None, limit: int = 8) -> list[dict]:
    """Universities for the profile's field, as the student types (D39): the
    student's own country first."""
    country = _universities.country_of_zone(tz) if tz else None
    return [
        {"name": u.name, "flag": u.flag, "domain": u.domains[0] if u.domains else ""}
        for u in _universities.search(query, country, limit)
    ]


def year_words(year: str) -> str:
    return _(YEAR_WORDS[year]) if year in YEAR_WORDS else ""


def _cards(
    store: Store, viewer: str, posts: Sequence[social.Post], now: datetime, zone: ZoneInfo
) -> list[dict]:
    """Posts as cards, with their authors, kudos and comment counts, and, for notes,
    their place in the month's top of their course (D26)."""
    kudos = social.kudos_of(store, [p.id for p in posts], viewer)
    reactions = social.reactions_of(store, [p.id for p in posts if p.kind == "session"], viewer)
    comments = social.comment_counts(store, [p.id for p in posts])
    checked = [p.id for p in posts if p.kind in social.CHECKED]  # peer checks (D141)
    # A session that says or shows what was done is vouched for (D158), with the same mark.
    vouched = [p.id for p in posts if p.kind in social.VOUCHED and social.says_what_was_done(p)]
    checks = social.checks_of(store, checked + vouched, viewer)
    fixes = social.fixes_of(store, checked)
    tops = social.top_notes(store, _month_start(now)) if any(p.kind == "notes" for p in posts) else {}
    authors: dict[str, dict | None] = {}
    out = []
    for post in posts:
        if post.token not in authors:
            found = social.get_profile(store, post.token)
            authors[post.token] = found.public() if found else None
        card = post_card(post, viewer=viewer, now=now, zone=zone)
        if post.data.get("group"):  # a note shared with a study group (D85)
            group = social.get_group(store, post.data["group"])
            card["group"] = {"id": group.id, "name": group.name} if group else None
        count, given = kudos.get(post.id, (0, False))
        card.update(
            author=authors[post.token],
            kudos=count,
            kudos_given=given,
            comments=comments.get(post.id, 0),
            top=tops.get(post.id),
        )
        if post.id in checks:  # "It's right" and the fixes suggested (D141)
            count, mine = checks[post.id]
            suggested, accepted = fixes.get(post.id, (0, 0))
            card.update(checks=count, checked=mine, fixes=suggested, fixes_accepted=accepted)
        if post.id in reactions:  # a session's reactions (D108)
            these = reactions[post.id]
            card["reactions"] = [
                {
                    "kind": kind,
                    "emoji": REACTION_WORDS[kind][0],
                    "label": _(REACTION_WORDS[kind][1]),
                    "count": these["counts"][kind],
                    "mine": these["mine"] == kind,
                }
                for kind in social.REACTIONS
            ]
        out.append(card)
    return out


def _month_start(now: datetime) -> str:
    """The first moment of `now`'s month, in UTC, as the network's tables write times."""
    first = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.isoformat(timespec="seconds")


def front_door(store: str | os.PathLike, subscription: Subscription) -> str:
    """Where the app opens: "today", the plan (the day's panel beside the calendar),
    for everyone (D124). D102 had opened on the feed for a student with a handle; the
    one door, `/p/<plan>/open`, stays, so an installed app and every link to it keep
    working, and so the choice has one place to change."""
    return "today"


FEED_FIRST = 10  # posts a feed shows at first; "Show more" adds as many (D106)
FEED_MOST = 60  # and no more than this on one page


def community_view(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    tab: str = "following",
    filters: Mapping[str, str] | None = None,
    shown: int = FEED_FIRST,
    now: datetime | None = None,
) -> dict:
    """The network's two feeds: people one follows, and Explore (everyone's posts
    across universities, by university, programme, course and kind). The newest
    `shown` posts (D106: the feed the app opens on was 12 screens long on a phone);
    `more` is how many the next "Show more" asks for, `next` the first post it adds,
    None when there are no more."""
    now = now or datetime.now(UTC)
    shown = max(FEED_FIRST, min(FEED_MOST, int(shown)))
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    token = subscription.token
    filters = {
        k: str(v).strip() for k, v in (filters or {}).items() if k in ("uni", "prog", "course", "kind")
    }
    if tab == "explore":
        posts = social.feed_explore(
            where,
            token,
            university=filters.get("uni", ""),
            programme=filters.get("prog", ""),
            course=filters.get("course", ""),
            kind=filters.get("kind", ""),
            limit=shown + 1,
        )
    else:
        tab = "following"
        posts = social.feed_following(where, token, limit=shown + 1)
    me = social.get_profile(where, token)
    further = len(posts) > shown and shown < FEED_MOST
    return {
        "tab": tab,
        "filters": filters,
        "posts": _cards(where, token, posts[:shown], now, zone),
        "more": min(FEED_MOST, shown + FEED_FIRST) if further else None,
        "next": posts[shown].id if further else None,
        "profile": me.public() if me else None,
        "requests": len(social.relations(where, token)["requests"]) if me else 0,
        "invitations": len(social.invitations_of(where, token)),
        "studying": studying_now_view(store, subscription, now) if me else [],
        "focusing": bool(subscription.options.get("focus")),
        "join_minutes": JOIN_MINUTES,
        **community_context(store, subscription, now),
    }


CLASSMATES_BESIDE = 3  # classmates not followed yet, beside the feed (D162)


def community_context(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict:
    """D162: what sits beside the feed on a wide screen, to act on it: the student's
    groups with their week, and a few people at their university they neither follow
    nor have asked to (`social.classmates`, same programme and year first). Nothing
    without a profile."""
    now = now or datetime.now(UTC)
    where = _store(store)
    token = subscription.token
    me = social.get_profile(where, token)
    if me is None:
        return {"groups": [], "classmates": [], "university": ""}
    zone = _zone(subscription.options["tz"])
    circle = social.relations(where, token)
    known = {p.token for p in circle["following"] + circle["asked"]}
    mates = [p.public() for p in social.classmates(where, token) if p.token not in known]
    return {
        "groups": [_group_card(store, token, g, now, zone) for g in social.groups_of(where, token)],
        "classmates": mates[:CLASSMATES_BESIDE],
        "university": me.university,
    }


def post_view(
    store: str | os.PathLike, subscription: Subscription, post_id: str, now: datetime | None = None
) -> dict | None:
    """One post with its comments, if the student may see it."""
    now = now or datetime.now(UTC)
    where = _store(store)
    post = social.get_post(where, post_id)
    if post is None or not social.may_see(where, subscription.token, post):
        return None
    zone = _zone(subscription.options["tz"])
    card = _cards(where, subscription.token, [post], now, zone)[0]
    comments = []
    for c in social.comments_on(where, subscription.token, post):
        author = social.get_profile(where, c.token)
        comments.append(
            {
                "id": c.id,
                "body": c.body,
                "hidden": c.hidden,
                "author": author.public() if author else None,
                "when": format_date(datetime.fromisoformat(c.created).astimezone(zone).date(), "short"),
                "can_delete": subscription.token in (c.token, post.token),
                "mine": c.token == subscription.token,
                "kind": c.kind,  # "comment" or "fix" (D141)
                "accepted": c.accepted,
                "can_accept": c.kind == "fix" and post.token == subscription.token and not c.hidden,
            }
        )
    # A fix the author accepted comes first: readers see the correction before the rest.
    comments.sort(key=lambda c: not c["accepted"])
    can_comment = social.get_profile(where, subscription.token) is not None
    can_fix = can_comment and post.kind in social.CHECKED and post.token != subscription.token
    return {"post": card, "comments": comments, "can_comment": can_comment, "can_fix": can_fix}


def person_view(
    store: str | os.PathLike, subscription: Subscription, handle: str, now: datetime | None = None
) -> dict | None:
    """Someone's profile as the student sees it: what they show, and the posts the
    student may see. No follower counts (D16)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    person = social.profile_by_handle(where, handle)
    if person is None or social.blocked_between(where, subscription.token, person.token):
        return None
    zone = _zone(subscription.options["tz"])
    posts = social.posts_by(where, subscription.token, person.token)
    return {
        "person": person.public(),
        "me": person.token == subscription.token,
        "follow_state": social.follow_status(where, subscription.token, person.token),
        "follows_me": social.follow_status(where, person.token, subscription.token) == "accepted",
        # Studying together (D127): the pair's state, and whether one more fits.
        "together": together_with(store, subscription.token, person.token),
        "together_room": len(social.buddies_of(where, subscription.token)) < social.BUDDIES_EACH,
        "notes": dict(zip(("shared", "helpful"), social.notes_recognition(where, person.token), strict=True)),
        "posts": _cards(where, subscription.token, posts, now, zone),
    }


def people_view(store: str | os.PathLike, subscription: Subscription, query: str = "") -> dict:
    """The student's own circle (requests, followers, following), a search, and the
    people at the same university (D39)."""
    where = _store(store)
    token = subscription.token
    found = social.search_profiles(where, token, query) if query else []
    circle = social.relations(where, token)
    me = social.get_profile(where, token)
    return {
        "query": query,
        "university": me.university if me else "",
        "classmates": [p.public() for p in social.classmates(where, token)],
        "found": [p.public() for p in found],
        "blocked": [p.public() for p in social.blocked_by(where, token)],
        **{name: [p.public() for p in people] for name, people in circle.items()},
    }


def leave_network(store: str | os.PathLike, token: str) -> None:
    """Leave the network; the diary stays, visible only to the student."""
    social.leave(_store(store), token)


# --------------------------------------------------------------------------- #
# Studying together (DECISIONS.md D127): a pair's days in a row both studied
# --------------------------------------------------------------------------- #


def _day_states(
    store: str | os.PathLike, subscription: Subscription | None, now: datetime
) -> tuple[dict[date, str], date | None]:
    """Every day of a student's plan up to their today, as their streak judges it
    (D9, D126), in their own time zone, and that today; nothing without a plan."""
    if subscription is None or subscription.plan is None:
        return {}, None
    plan = PlanReport.from_dict(subscription.plan)
    today = now.astimezone(_zone(plan.settings.tz)).date()
    sessions = _progress.merge_logged(progress_sessions(subscription), logged_sessions(store, subscription))
    first = min([plan.settings.start, *(s.start.date() for s in sessions)])
    paused = _progress.pause_days(pauses_of(subscription.options.get("preferences", {})))
    repaired = {date.fromisoformat(d) for d in subscription.options.get("repairs", {})}
    run = _progress.streak(sessions, first, today, paused, repaired)
    return {d.day: d.state for d in run.days}, today


def together_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """The student's pairs (D127): with each, the days in a row both studied and
    whether each studied today; the asks waiting for their answer and the ones they
    sent. Never anyone's hours or sessions: whether a day was studied is all a pair
    shares, as a group's members do (D28)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    token = subscription.token
    mine, my_today = _day_states(store, subscription, now)
    zone = _zone(subscription.options["tz"])
    pairs: list[dict] = []
    waiting: list[dict] = []
    sent: list[dict] = []
    every = social.buddies_of(where, token)
    for pair in every:
        found = social.get_profile(where, pair.other(token))
        if found is None:
            continue
        if pair.accepted is None:
            (waiting if pair.asked == token else sent).append(found.public())
            continue
        theirs, their_today = _day_states(store, load_subscription(store, found.token), now)
        start = datetime.fromisoformat(pair.accepted).astimezone(zone).date()
        run, best = _progress.together(mine, theirs, start, my_today or now.astimezone(zone).date())
        pairs.append(
            {
                **found.public(),
                "days": run,
                "best": best,
                "me_today": my_today is not None and mine.get(my_today) == "studied",
                "them_today": their_today is not None and theirs.get(their_today) == "studied",
            }
        )
    return {
        "pairs": pairs,
        "waiting": waiting,
        "sent": sent,
        "room": max(0, social.BUDDIES_EACH - len(every)),
        "profile": social.get_profile(where, token) is not None,
    }


def ask_together(store: str | os.PathLike, token: str, handle: str, now: datetime | None = None) -> None:
    _social(lambda: social.ask_buddy(_store(store), token, handle, now=now))


def answer_together(
    store: str | os.PathLike, token: str, handle: str, accept: bool, now: datetime | None = None
) -> None:
    where = _store(store)
    person = social.profile_by_handle(where, handle)
    if person is None:
        raise InvalidInput(_("this request is not, or no longer, there"))
    _social(lambda: social.answer_buddy(where, token, person.token, accept, now=now))


def end_together(store: str | os.PathLike, token: str, handle: str) -> None:
    where = _store(store)
    person = social.profile_by_handle(where, handle)
    if person is not None:
        social.end_buddy(where, token, person.token)


def together_with(store: str | os.PathLike, token: str, other: str) -> str | None:
    """The pair between the student and `other` as the person page shows it:
    "together", "asked" (the student asked), "asks" (they asked), or None."""
    pair = social.buddy_between(_store(store), token, other)
    if pair is None:
        return None
    if pair.accepted is not None:
        return "together"
    return "asked" if pair.asker == token else "asks"


# --------------------------------------------------------------------------- #
# Study groups (D28): a shared weekly goal, nobody ranked
# --------------------------------------------------------------------------- #

GROUP_WEEKS = 8  # the weeks a group's page shows


def _member_hours(
    store: str | os.PathLike, token: str, mondays: Sequence[date], now: datetime
) -> list[float]:
    """A member's hours studied in each week, from their own plan and diary, in
    their own time zone, as Trends counts them (each session once, AUDIT 47)."""
    member = load_subscription(store, token)
    if member is None:
        return [0.0] * len(mondays)
    zone = _zone(member.options["tz"])
    weeks = _trends.weekly(
        progress_sessions(member), _diary_work(store, member), mondays, now.astimezone(zone)
    )
    return [w.hours for w in weeks]


def _group_card(
    path: str | os.PathLike, viewer: str, group: social.Group, now: datetime, zone: ZoneInfo
) -> dict:
    """A group as its members see it: this week's hours against the goal, the weeks
    in a row it was met, the weeks before, and who studied this week. Never one
    member's hours: the group's total is the number (D28)."""
    store = _store(path)
    today = now.astimezone(zone).date()
    began = datetime.fromisoformat(group.created).astimezone(zone).date()
    mondays = _trends.mondays(min(began, today), today, None)
    people = social.members(store, group.id)
    totals = [0.0] * len(mondays)
    memory = 0  # the memory days the members' reviews added this week (D82, D127)
    shown, hidden = [], 0
    for m in people:
        if m.joined is None:
            continue
        since = _progress.monday(datetime.fromisoformat(m.joined).astimezone(zone).date())
        hours = [
            h if monday >= since else 0.0
            for h, monday in zip(_member_hours(path, m.token, mondays, now), mondays, strict=True)
        ]
        totals = [a + b for a, b in zip(totals, hours, strict=True)]
        member = load_subscription(path, m.token)
        learnt = learning_view(path, member, now) if member is not None else None
        memory += max(0, learnt["added_week"]) if learnt else 0
        if m.token != viewer and social.blocked_between(store, viewer, m.token):
            hidden += 1  # counted in the total, left out of the list (D20)
            continue
        found = social.get_profile(store, m.token)
        shown.append(
            {
                **(found.public() if found else {"handle": "", "university": "", "programme": ""}),
                "me": m.token == viewer,
                "owner": m.token == group.owner,
                "studied": hours[-1] > 0,
            }
        )
    goal = group.goal / 60
    met = [t >= goal for t in totals]
    run = 0
    for i in range(len(met) - 1, -1, -1):
        if met[i]:
            run += 1
        elif i < len(met) - 1:  # this week, not met yet, does not break the run
            break
    this_week = totals[-1]
    invited = []
    for m in people:
        if m.joined is None and not social.blocked_between(store, viewer, m.token):
            found = social.get_profile(store, m.token)
            if found:
                invited.append(found.public())
    recent = list(zip(mondays, totals, met, strict=True))[-GROUP_WEEKS:]
    return {
        "id": group.id,
        "name": group.name,
        "goal": goal,
        "owner": group.owner == viewer,
        "week": {
            "hours": this_week,
            "goal": goal,
            "left": max(0.0, goal - this_week),
            "percent": min(100, round(100 * this_week / goal)) if goal else 0,
            "met": this_week >= goal,
            "studied": sum(x["studied"] for x in shown),
            "memory": memory,
        },
        "run": run,
        "members": shown,
        "hidden": hidden,
        "invited": invited,
        "size": sum(m.joined is not None for m in people),
        "weeks": [
            {
                "label": format_date(monday, "day_short"),
                "first": format_date(monday, "short"),
                "monday": monday.isoformat(),
                "hours": hours,
                "met": done,
                "current": monday == _progress.monday(today),
            }
            for monday, hours, done in recent
        ],
    }


def groups_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """The student's groups, the invitations waiting, and whether they can make
    another (D28)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    token = subscription.token
    me = social.get_profile(where, token)
    mine = social.groups_of(where, token)
    invitations = []
    for group, member in social.invitations_of(where, token):
        by = social.get_profile(where, member.invited_by)
        invitations.append(
            {
                "id": group.id,
                "name": group.name,
                "goal": group.goal / 60,
                "by": by.handle if by else "",
                "size": sum(m.joined is not None for m in social.members(where, group.id)),
            }
        )
    return {
        "profile": me.public() if me else None,
        "groups": [_group_card(store, token, g, now, zone) for g in mine],
        "invitations": invitations,
        "can_create": me is not None and len(mine) < social.GROUPS_EACH,
        "most": social.GROUPS_EACH,
        "size": social.GROUP_SIZE,
    }


def group_view(
    store: str | os.PathLike, subscription: Subscription, group_id: str, now: datetime | None = None
) -> dict | None:
    """One group's page, for its members only."""
    now = now or datetime.now(UTC)
    where = _store(store)
    group = social.get_group(where, group_id)
    mine = social.membership(where, subscription.token, group_id) if group else None
    if group is None or mine is None or mine.joined is None:
        return None
    zone = _zone(subscription.options["tz"])
    card = _group_card(store, subscription.token, group, now, zone)
    card["room"] = social.GROUP_SIZE - len(social.members(where, group_id))
    card["invite"] = social.current_invite(where, group_id)
    # D85: the notes its members shared with it, for the members only.
    card["notes"] = _cards(
        where, subscription.token, social.group_notes(where, subscription.token, group_id), now, zone
    )
    card["list"] = group_list_view(store, subscription, group_id, now)  # D204
    return card


def note_groups(store: str | os.PathLike, token: str) -> list[dict]:
    """The study groups a student can share notes with (D85)."""
    return [{"id": g.id, "name": g.name} for g in social.groups_of(_store(store), token)]


def create_group(
    store: str | os.PathLike, token: str, form: Mapping[str, str], now: datetime | None = None
) -> social.Group:
    where = _store(store)
    group: social.Group = _social(
        lambda: social.create_group(where, token, form.get("name", ""), form.get("goal", ""), now=now)
    )
    return group


def invite_to_group(
    store: str | os.PathLike, token: str, group_id: str, handle: str, now: datetime | None = None
) -> dict:
    where = _store(store)
    person: social.Profile = _social(lambda: social.invite(where, token, group_id, handle, now=now))
    return person.public()


def answer_group(
    store: str | os.PathLike, token: str, group_id: str, accept: bool, now: datetime | None = None
) -> bool:
    where = _store(store)
    return bool(_social(lambda: social.answer_invitation(where, token, group_id, accept, now=now)))


def leave_group(store: str | os.PathLike, token: str, group_id: str) -> None:
    social.leave_group(_store(store), token, group_id)


def group_invite_code(store: str | os.PathLike, token: str, group_id: str, *, renew: bool = False) -> str:
    """The code of the group's invitation link (D40)."""
    where = _store(store)
    code: str = _social(lambda: social.invite_code(where, token, group_id, renew=renew))
    return code


def invitation_view(store: str | os.PathLike, code: str) -> dict | None:
    """What an invitation link shows anyone who opens it: the group's name, how many
    are in it, its weekly goal. Never who they are."""
    where = _store(store)
    group = social.group_by_code(where, code)
    if group is None:
        return None
    joined = sum(m.joined is not None for m in social.members(where, group.id))
    return {"code": code, "name": group.name, "size": joined, "goal": group.goal / 60, "id": group.id}


def join_group_by_code(store: str | os.PathLike, token: str, code: str, now: datetime | None = None) -> str:
    """Join the group of an invitation link; its id."""
    where = _store(store)
    group: social.Group = _social(lambda: social.join_by_code(where, token, code.strip(), now=now))
    return group.id


def invite_code_from(text: str) -> str:
    """The code in what a student pasted: the code itself or the whole link."""
    found = re.search(r"(?:^|/g/)([A-Za-z0-9_-]{12})(?:[/?#]|$)", text.strip())
    return found.group(1) if found else ""


def remove_from_group(store: str | os.PathLike, token: str, group_id: str, handle: str) -> None:
    where = _store(store)
    _social(lambda: social.remove_member(where, token, group_id, handle))


def change_group(store: str | os.PathLike, token: str, group_id: str, form: Mapping[str, str]) -> None:
    where = _store(store)
    _social(lambda: social.set_group(where, token, group_id, form.get("name", ""), form.get("goal", "")))


# --------------------------------------------------------------------------- #
# D204: a study group's list, the to-do page's proposal 12
# --------------------------------------------------------------------------- #


def group_list_view(
    store: str | os.PathLike, subscription: Subscription, group_id: str, now: datetime | None = None
) -> dict | None:
    """The group's list as one member sees it: each line's words drawn as on the to-do page
    (D202's marks, its links by their names), its day in words, who wrote it and who ticked
    it off. Someone blocked writes without a name, as they are left off the members (D20)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    token = subscription.token
    group = social.get_group(where, group_id)
    mine = social.membership(where, token, group_id) if group else None
    if group is None or mine is None or mine.joined is None:
        return None
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    names: dict[str, str] = {}

    def who(person: str | None) -> str:
        if not person:
            return ""
        if person not in names:
            found = social.get_profile(where, person)
            hidden = person != token and social.blocked_between(where, token, person)
            names[person] = found.handle if found and not hidden else ""
        return names[person]

    lines = []
    for line in social.group_lines(where, token, group_id):
        words, links = _todo_links(line.text)
        d = date.fromisoformat(line.day) if line.day else None
        when = (
            ""
            if d is None
            else _("today")
            if d == today
            else _("tomorrow")
            if d == today + timedelta(days=1)
            else _i18n.format_date(d, "short")
        )
        lines.append(
            {
                "id": line.id,
                "text": line.text,
                "parts": todo_parts(words),
                "links": [_link_label(subscription, u) for u in links],
                "day": line.day,
                "when": when,
                "late": d is not None and d < today and line.done is None,
                "by": who(line.token),
                "mine": line.token == token,
                "done": line.done is not None,
                "done_by": who(line.done_by),
                "can_drop": token in (line.token, group.owner),
            }
        )
    open_lines = [x for x in lines if not x["done"]]
    return {
        "id": group.id,
        "name": group.name,
        "lines": open_lines,
        "done": [x for x in lines if x["done"]],
        "room": social.GROUP_LINES - len(lines),
        "most": social.GROUP_LINE,
    }


def add_group_line(
    store: str | os.PathLike, token: str, group_id: str, form: Mapping[str, str], now: datetime | None = None
) -> str:
    """A line on the group's list; its id."""
    where = _store(store)
    line: social.GroupLine = _social(
        lambda: social.add_group_line(
            where, token, group_id, form.get("text", ""), form.get("day", ""), now=now
        )
    )
    return line.id


def change_group_line(
    store: str | os.PathLike, token: str, group_id: str, line_id: str, what: str, now: datetime | None = None
) -> None:
    """Tick a line of the group's list off (or open it again), or take it off."""
    where = _store(store)
    if what == "tick":
        _social(lambda: social.tick_group_line(where, token, group_id, line_id, now=now))
    elif what == "drop":
        _social(lambda: social.drop_group_line(where, token, group_id, line_id))
    else:
        raise InvalidInput(_("this cannot be done to a line"))


def clear_group_lines(store: str | os.PathLike, token: str, group_id: str) -> int:
    where = _store(store)
    cleared: int = _social(lambda: social.clear_group_lines(where, token, group_id))
    return cleared


def share_todo(
    store: str | os.PathLike,
    subscription: Subscription,
    item: str,
    group_id: str,
    now: datetime | None = None,
) -> None:
    """A line of one's own to-do list written on a group's list too, with its day and links;
    a heading or a step's own words go as they are. The two lines then live apart."""
    items = _mind(subscription)
    x = items[_mind_index(items, item)]
    text = " ".join([x["text"], *(x.get("links") or ())])[: social.GROUP_LINE]
    form = {"text": text, "day": x["day"] if x["kind"] in TODO_TICKED else ""}
    add_group_line(store, subscription.token, group_id, form, now=now)


def todo_groups(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> list[dict]:
    """Beside the to-do page (D204): each of the student's groups, its open lines counted
    and the first three, the group's page a tap away."""
    where = _store(store)
    out = []
    for group in social.groups_of(where, subscription.token):
        view = group_list_view(store, subscription, group.id, now)
        if view is None:
            continue
        out.append(
            {"id": group.id, "name": group.name, "open": len(view["lines"]), "first": view["lines"][:3]}
        )
    return out


# --------------------------------------------------------------------------- #
# Flashcards (D29): the student's own, scheduled by FSRS
# --------------------------------------------------------------------------- #

CARD_GRADES = tuple(flashcards.GRADES)


def _flash(call: Callable[[], Any]) -> Any:
    """`call`, with the cards' refusals as the service's own."""
    try:
        return call()
    except flashcards.CardError as error:
        raise InvalidInput(str(error)) from None


def wait_words(delta: timedelta) -> str:
    """ "in 10 minutes", "in 3 days": when a card comes back, as a person says it."""
    minutes = max(1, round(delta / timedelta(minutes=1)))
    if minutes < 60:
        return _n("in {n} minute", "in {n} minutes", minutes)
    hours = round(minutes / 60)
    if hours < 24:
        return _n("in {n} hour", "in {n} hours", hours)
    days = round(hours / 24)
    if days < 45:
        return _n("in {n} day", "in {n} days", days)
    months = round(days / 30)
    return _n("in {n} month", "in {n} months", months)


def _card_dict(card: flashcards.Card, now: datetime) -> dict:
    due = datetime.fromisoformat(card.due) if card.due else None
    return {
        "id": card.id,
        "course": card.course,
        "front": card.front,
        "back": card.back,
        "new": card.new,
        "reps": card.reps,
        "lapses": card.lapses,
        "due": None if due is None else (_("now") if due <= now else wait_words(due - now)),
        "week": card.week,
        "week_label": _("week of {day}", day=format_date(date.fromisoformat(card.week), "day_short"))
        if card.week
        else "",
    }


def cards_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None, course: str = ""
) -> dict:
    """The flashcards page: a deck per course with its cards due now and new, the
    cards of the course chosen, and when the next one is due."""
    now = now or datetime.now(UTC)
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    token = subscription.token
    every = flashcards.of(where, token)
    waiting = flashcards.queue(where, token, now, zone)
    decks: dict[str, dict] = {}
    for card in every:
        deck = decks.setdefault(
            card.course.casefold(), {"course": card.course, "total": 0, "due": 0, "new": 0}
        )
        deck["total"] += 1
    for card in waiting:
        deck = decks[card.course.casefold()]
        deck["new" if card.new else "due"] += 1
    later = flashcards.next_due(where, token, now)
    chosen = [c for c in every if course and c.course.casefold() == course.casefold()]
    own = flashcards.personal(where, token)
    return {
        # D53: the intervals' parameters, the student's own once fitted.
        "fitted": own.reviews if own else 0,
        "to_fit": 0 if own else max(0, fitting.MIN_REVIEWS - flashcards.scored(where, token)),
        "decks": sorted(decks.values(), key=lambda d: d["course"].casefold()),
        "waiting": len(waiting),
        "total": len(every),
        "course": chosen[0].course if chosen else course,
        "cards": [_card_dict(c, now) for c in chosen],
        "next": wait_words(later - now) if later else None,
        "today": flashcards.reviewed_today(where, token, now, zone),
        "courses": course_names(subscription),
        # D101: the courses with something to make cards from, one tap each.
        "from_courses": courses_with_materials(store, subscription),
        "new_per_day": flashcards.NEW_PER_DAY,
        # A course's weeks, for its new cards (D87): the week in course by default.
        **_week_choice(subscription, chosen[0].course if chosen else course, now),
    }


def card_review_view(
    store: str | os.PathLike,
    subscription: Subscription,
    now: datetime | None = None,
    course: str = "",
    week: str = "",
) -> dict:
    """The next card to review, with when it would come back after each answer; with
    a course and a week, the next of that week's cards to practise (D87)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    try:
        week = date.fromisoformat(week).isoformat() if week and course else ""
    except ValueError:
        week = ""
    if week:
        waiting = flashcards.week_practice(where, subscription.token, now, zone, course, week)
    else:
        waiting = flashcards.queue(where, subscription.token, now, zone, course)
    view: dict = {
        "course": course,
        "week": week,
        "week_label": _("week of {day}", day=format_date(date.fromisoformat(week), "day_short"))
        if week
        else "",
        "week_cards": len(flashcards.of_week(where, subscription.token, course, week)) if week else 0,
        "course_index": next(
            (i for i, c in enumerate(topic_courses(subscription)) if course_key(c) == course_key(course)),
            None,
        ),
        "date": now.astimezone(zone).date().isoformat(),
        "left": len(waiting),
        "today": flashcards.reviewed_today(where, subscription.token, now, zone),
        "card": None,
    }
    if waiting:
        card = waiting[0]
        view["card"] = _card_dict(card, now)
        view["options"] = []
        model = flashcards.model_for(where, subscription.token)
        for grade in CARD_GRADES:
            _after, due, _elapsed = flashcards.schedule(card, flashcards.GRADES[grade], now, model)
            view["options"].append({"grade": grade, "when": wait_words(due - now)})
    else:
        later = flashcards.next_due(where, subscription.token, now, course)
        view["next"] = wait_words(later - now) if later else None
    return view


def cards_due(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> int:
    """Cards to review now, new ones within today's allowance: for Today and Focus."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    return len(flashcards.queue(_store(store), subscription.token, now, zone))


def add_cards(
    store: str | os.PathLike, subscription: Subscription, form: Mapping[str, str], now: datetime | None = None
) -> int:
    """One card (question and answer), or several pasted one per line."""
    now = now or datetime.now(UTC)
    where = _store(store)

    def make() -> int:
        if form.get("paste", "").strip():
            pairs = flashcards.parse_paste(form["paste"])
        else:
            pairs = [(form.get("front", ""), form.get("back", ""))]
        week = form.get("week") or None
        return len(flashcards.add(where, subscription.token, form.get("course", ""), pairs, now, week))

    return int(_flash(make))


def answer_card(
    store: str | os.PathLike,
    subscription: Subscription,
    card_id: str,
    grade: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    where = _store(store)
    _flash(lambda: flashcards.answer(where, subscription.token, card_id, grade, now))


def refit_cards(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> bool:
    """Fit the student's own parameters for their cards if it is due (D53):
    enough answers and none fitted, or a quarter more since. Seconds of work, so
    the web app runs it after answering, not before. True when it fitted."""
    return flashcards.refit(_store(store), subscription.token, now or datetime.now(UTC)) is not None


def card_view(store: str | os.PathLike, subscription: Subscription, card_id: str) -> dict | None:
    card = flashcards.get(_store(store), subscription.token, card_id)
    if card is None:
        return None
    now = datetime.now(UTC)
    # The course's weeks, to move the card to another (D87).
    return {
        **_card_dict(card, now),
        "weeks": _week_choice(subscription, card.course, now, card.week or "")["weeks"],
    }


def edit_card(
    store: str | os.PathLike, subscription: Subscription, card_id: str, form: Mapping[str, str]
) -> None:
    where = _store(store)
    _flash(
        lambda: flashcards.edit(
            where,
            subscription.token,
            card_id,
            form.get("course", ""),
            form.get("front", ""),
            form.get("back", ""),
            # Its week (D87) changes only when the form has the field.
            *([form.get("week") or None] if "week" in form else []),
        )
    )


def delete_card(store: str | os.PathLike, subscription: Subscription, card_id: str) -> bool:
    return flashcards.delete(_store(store), subscription.token, card_id)


# -- flashcards from notes (D84) -------------------------------------------------------

CARDS_TEXT_MOST = _cardmaker.MAX_TEXT
CARDS_MADE_MOST = _cardmaker.MAX_CARDS
CARD_KINDS = {
    "qa": "Question and answer",
    "term": "Term and definition",
    "list": "List",
    "cloze": "Fill in the blank",
    "definition": "Definition",
    "ai": "Written by AI",
}


def _note_source(store: str | os.PathLike, subscription: Subscription, note_id: str) -> social.Post | None:
    """A note with text the student may read: theirs, their group's, or one shared
    with them; None otherwise."""
    where = _store(store)
    post = social.get_post(where, note_id) if note_id else None
    if post is None or post.kind != "notes" or not (post.data.get("body") or post.data.get("pdf_text")):
        return None
    return post if social.may_see(where, subscription.token, post) else None


def cards_by_ai(subscription: Subscription) -> bool:
    """Whether cards from notes are written by the model (D138): the server has it and
    the student said yes to AI (D12). Otherwise the rules read them."""
    return _ai.cards_from_env() is not None and ai_on(subscription)


def ai_cards_offer(subscription: Subscription) -> bool:
    """Whether to offer turning AI on for cards (D138): the server has the model and
    the student has not said yes yet."""
    return _ai.cards_from_env() is not None and not ai_on(subscription)


def _ai_check_chars() -> int:
    """The notes the check reads: as many as the cards were written from (D138)."""
    return MATERIALS_MOST


def card_maker_view(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    text: str | None = None,
    course: str = "",
    note_id: str = "",
    both: bool = False,
    use_ai: bool = False,
    now: datetime | None = None,
    provider: _ai.Provider | None = None,
    week: str | None = None,
    from_course: bool = False,
    of_week: str | None = None,
    read_note: str | None = None,
) -> dict:
    """The page that makes flashcards from notes (D84): the text (pasted, or a note's),
    and the cards read from it for the student to pick. By rules; by the model too
    when the student asks and AI is on and allowed, its cards first.

    `from_course` (D101): the course's materials instead of a text, its topics and the
    notes the student may read, one week's topics when `of_week` names it. The rules
    read the notes; the model reads all of it, and each of its cards comes with the
    week of the topic it belongs to."""
    now = now or datetime.now(UTC)
    note = _note_source(store, subscription, note_id)
    materials = course_materials(store, subscription, course, of_week or None) if from_course else None
    if materials is not None:
        text = materials["notes"]
    elif note is not None and text is None:
        # The note's text, then its PDFs' (D95).
        noted = "\n\n".join(t for t in (note.data.get("body", ""), note.data.get("pdf_text", "")) if t)
        text, course = noted, course or note.data.get("course", "")
    text = (text or "")[: _cardmaker.MAX_TEXT]
    found = _cardmaker.read(text)
    if both:
        found = _cardmaker.both_ways(found)[: _cardmaker.MAX_CARDS]
    candidates = [{"front": c.front, "back": c.back, "kind": c.kind, "week": ""} for c in found]
    used_ai = checked = ai_failed = False
    source = materials["text"] if materials is not None else text
    model = (provider or _ai.cards_from_env()) if use_ai and ai_on(subscription) and source.strip() else None
    if model is not None:
        language = _LANGUAGE_NAMES.get(language_of(subscription), "English")
        written: list[tuple[str, str, str]] = []
        if materials is not None:
            topics = materials["topics"]
            system, prompt = _ai.materials_prompt(
                materials["text"],
                language,
                course,
                [(t["id"], t["title"]) for t in topics],
                only_topics=bool(of_week and topics),
            )
            data = _ai.ask(
                _store(store),
                subscription.token,
                model,
                "cards",
                system=system,
                prompt=prompt,
                schema=_ai.MATERIALS_SCHEMA,
                max_tokens=_ai.MATERIALS_MAX_TOKENS,
                now=now,
            )
            weeks_of = {t["id"]: t["monday"] for t in topics}
            written = [
                (f, b, weeks_of.get(t, "") or (of_week or ""))
                for f, b, t in (_ai.materials_from_answer(data, list(weeks_of)) if data else [])
            ]
        else:
            system, prompt = _ai.cards_prompt(text, language, course)
            data = _ai.ask(
                _store(store),
                subscription.token,
                model,
                "cards",
                system=system,
                prompt=prompt,
                schema=_ai.CARDS_SCHEMA,
                max_tokens=_ai.CARDS_MAX_TOKENS,
                now=now,
            )
            written = [(f, b, "") for f, b in (_ai.cards_from_answer(data) if data else [])]
        if written:
            # D138: each card checked against what it was written from; kept, fixed or
            # dropped. A check that cannot be read leaves the cards as written, said so.
            pairs = [(f, b) for f, b, _w in written]
            system, prompt = _ai.check_prompt(source[: _ai_check_chars()], pairs, language)
            verdicts = _ai.ask(
                _store(store),
                subscription.token,
                model,
                "cards-check",
                system=system,
                prompt=prompt,
                schema=_ai.CHECK_SCHEMA,
                max_tokens=_ai.CHECK_MAX_TOKENS,
                now=now,
            )
            kept_ai = _ai.checked_cards(verdicts, pairs)
            if kept_ai is not None:
                checked = True
                written = [(f, b, written[i][2]) for i, f, b in kept_ai]
        if written:
            used_ai = True
            # D138: the model's cards alone once it wrote some; the rules' are the
            # fallback, not a second list of lesser cards under them.
            candidates = [{"front": f, "back": b, "kind": "ai", "week": w} for f, b, w in written][
                : _cardmaker.MAX_CARDS
            ]
        else:
            ai_failed = True
        log_event(store, subscription.token, "cardmaker", "ai" if used_ai else "rules")
    choice = _week_choice(subscription, course, now, week)
    labels = {o["value"]: o["label"] for o in choice["weeks"]}
    for c in candidates:
        c["word"] = _(CARD_KINDS[c["kind"]])
        c["week_label"] = labels.get(c["week"], "")
    return {
        "text": text,
        "course": course,
        "courses": course_names(subscription),
        "note": {"id": note.id, "title": note.data.get("title", "")} if note else None,
        "materials": materials,
        "candidates": candidates,
        "both": both,
        "read": bool(source.strip()),
        "ai_ready": _ai.cards_from_env() is not None and ai_on(subscription),
        "ai_off": _ai.cards_from_env() is not None and not ai_on(subscription),
        "ai_used": used_ai,
        # D138: the model's cards checked against the notes; or the model wrote none
        # (off, over the cap, or no usable answer) and the rules' cards are offered.
        "ai_checked": checked,
        "ai_failed": ai_failed,
        "most": _cardmaker.MAX_CARDS,
        "most_text": _cardmaker.MAX_TEXT,
        # A file of notes read (D134): what was left out, if anything.
        "read_note": read_note,
        # The course's weeks, to file the cards under one (D87): this week by default.
        **choice,
    }


# D101: what a course's materials may give the model at once, in characters: under
# Claude Haiku 5.5's price step of 100K tokens (`ai.LONG_PROMPTS`) even at two
# characters a token (dense formulas; prose runs at three to four), with the
# instructions and the topics' list besides.
MATERIALS_MOST = 150_000


def courses_with_materials(store: str | os.PathLike, subscription: Subscription) -> list[str]:
    """D101: the plan's courses with topics kept or a note with text the student may
    read, in the plan's order."""
    where = _store(store)
    token = subscription.token
    kept = {k for k, v in (subscription.options.get("topics") or {}).items() if v.get("items")}
    posts = list(social.posts_of(where, token, "notes", limit=200))
    for group in social.groups_of(where, token):
        posts += social.group_notes(where, token, group.id)
    noted = {
        course_key(p.data.get("course", "")) for p in posts if p.data.get("body") or p.data.get("pdf_text")
    }
    return [c for c in course_names(subscription) if course_key(c) in kept | noted]


def course_materials(
    store: str | os.PathLike, subscription: Subscription, course: str, week: str | None = None
) -> dict:
    """D101: what a course gives to make cards from: its topics (a week's, when `week`
    names one of its Mondays), then the notes of the course the student may read,
    theirs and their groups', newest first, each note's text and its PDFs'. Cut at
    `MATERIALS_MOST` characters, which the view says."""
    key = course_key(course)
    kept = (subscription.options.get("topics") or {}).get(key) or {}
    # A chapter that runs over several weeks (D187) belongs to each of them.
    items = [
        i for i in kept.get("items") or () if week is None or week in {m.isoformat() for m in _weeks_of(i)}
    ]
    topics = [
        {"id": f"t{n}", "title": i["title"], "monday": week or i.get("monday") or ""}
        for n, i in enumerate(items, 1)
    ]
    notes: list[str] = []
    sources: list[dict] = []
    for post in _course_note_posts(store, subscription, course):
        body = "\n\n".join(t for t in (post.data.get("body", ""), post.data.get("pdf_text", "")) if t).strip()
        if body:
            title = post.data.get("title", "") or _("Notes")
            notes.append(f"## {title}\n\n{body}")
            sources.append(
                {
                    "title": title,
                    "mine": post.token == subscription.token,
                    "chars": len(body),
                    "pdfs": len(post.data.get("pdfs", [])),
                }
            )
    listed = "\n".join(f"- {t['title']}" for t in topics)
    whole = "\n\n".join(x for x in ((f"## {_('Topics')}\n\n{listed}" if topics else ""), *notes) if x)
    return {
        "text": whole[:MATERIALS_MOST],
        "notes": "\n\n".join(notes),
        "topics": topics,
        "sources": sources,
        "chars": min(len(whole), MATERIALS_MOST),
        "cut": len(whole) > MATERIALS_MOST,
        "week": week or "",
        "index": _course_index(subscription, course),
    }


def add_made_cards(
    store: str | os.PathLike,
    subscription: Subscription,
    course: str,
    fronts: Sequence[str],
    backs: Sequence[str],
    picked: Sequence[str],
    now: datetime | None = None,
    week: str | None = None,
    weeks: Sequence[str] = (),
) -> int:
    """The cards the student ticked in the preview, added to the course's deck, and
    to one of its weeks when chosen (D87); a card with a week of its own (`weeks`,
    from the topic the model filed it under, D101) goes to that week."""
    now = now or datetime.now(UTC)
    chosen = {int(i) for i in picked if str(i).isdigit()}
    by_week: dict[str | None, list[tuple[str, str]]] = {}
    for i, (f, b) in enumerate(zip(fronts, backs, strict=False)):
        if i in chosen:
            own = weeks[i] if i < len(weeks) else ""
            by_week.setdefault(own or week or None, []).append((f, b))
    if not by_week:
        raise InvalidInput(_("tick the cards to add"))
    added = 0
    for filed, pairs in by_week.items():
        call = functools.partial(flashcards.add, _store(store), subscription.token, course, pairs, now, filed)
        added += len(_flash(call))
    return added


REPORT_REASONS = social.REPORT_REASONS


def report_view(
    store: str | os.PathLike, subscription: Subscription, kind: str, target_id: str
) -> dict | None:
    """What the report page shows: the item, in a line, and its author."""
    where = _store(store)
    found = social._target(where, kind, target_id)
    if found is None or not social.may_see(where, subscription.token, found[0]):
        return None
    post, comment = found
    author = social.get_profile(where, comment.token if comment else post.token)
    if comment:
        summary = comment.body
    elif post.kind == "explain":
        summary = post.data.get("concept", "")
    else:
        summary = " · ".join(x for x in (post.data.get("course", ""), post.data.get("title", "")) if x)
    return {
        "kind": kind,
        "target": target_id,
        "post": post.id,
        "summary": summary,
        "author": author.public() if author else None,
        "mine": subscription.token == (comment.token if comment else post.token),
    }


def report(store: str | os.PathLike, token: str, kind: str, target_id: str, reason: str) -> bool:
    """Report a post or a comment (D20); True if it is now hidden for review."""
    where = _store(store)
    if social.get_profile(where, token) is None:
        raise InvalidInput(_("choose a handle first, in your profile"))
    return bool(_social(lambda: social.report(where, token, kind, target_id, reason)))


def block(store: str | os.PathLike, token: str, handle: str) -> None:
    where = _store(store)
    _social(lambda: social.block(where, token, _person_token(where, handle)))


def unblock(store: str | os.PathLike, token: str, handle: str) -> None:
    where = _store(store)
    social.unblock(where, token, _person_token(where, handle))


def moderation_view(store: str | os.PathLike) -> list[dict]:
    """The owner's queue (D20): each reported item with what it says, its photos,
    its author's handle, and the reasons given."""
    where = _store(store)
    out = []
    for item in social.pending_reports(where):
        author = social.get_profile(where, item.author)
        d = item.post.data
        if item.comment:
            text = item.comment.body
            title = _("Comment on: {what}", what=d.get("concept") or d.get("course", ""))
        else:
            text = "\n".join(x for x in (d.get("title", ""), d.get("note", ""), d.get("text", "")) if x)
            title = d.get("concept") or d.get("course", "")
        out.append(
            {
                "kind": item.kind,
                "target": item.target,
                "title": title,
                "text": text,
                "photos": [] if item.comment else list(d.get("photos", [])),
                "author": author.handle if author else "",
                "visibility": item.post.visibility,
                "hidden": (item.comment.hidden if item.comment else item.post.hidden),
                "reasons": [
                    (_(REPORT_REASONS[r]), n) for r, n in item.reasons.items() if r in REPORT_REASONS
                ],
                "reporters": item.reporters,
                "first": item.first[:10],
            }
        )
    return out


def moderate(store: str | os.PathLike, kind: str, target_id: str, keep: bool) -> bool:
    """The owner keeps or removes a reported item; the decision goes to the event
    log, on the author's plan, for the record the Digital Services Act asks for."""
    author = social.review(_store(store), kind, target_id, keep)
    if author is None:
        return False
    log_event(store, author, "moderated", f"{kind} {'kept' if keep else 'removed'}")
    return True


def admin_photo(store: str | os.PathLike, name: str) -> tuple[bytes, str] | None:
    """A reported post's photo, for the owner's review, whoever the post is for."""
    where = _store(store)
    if social.photo_post(where, name) is None:
        return None
    path = where.photos / name
    if not path.is_file():
        return None
    return path.read_bytes(), _MEDIA.get(path.suffix, "image/jpeg")


def _person_token(store: Store, handle: str) -> str:
    person = social.profile_by_handle(store, handle)
    if person is None:
        raise InvalidInput(_("there is nobody called @{handle}", handle=handle.lstrip("@")))
    return person.token


def follow(store: str | os.PathLike, token: str, handle: str) -> str:
    where = _store(store)
    return str(_social(lambda: social.ask_to_follow(where, token, _person_token(where, handle))))


def unfollow(store: str | os.PathLike, token: str, handle: str) -> None:
    where = _store(store)
    social.unfollow(where, token, _person_token(where, handle))


def answer_follow(store: str | os.PathLike, token: str, handle: str, accept: bool) -> None:
    where = _store(store)
    social.answer_request(where, token, _person_token(where, handle), accept)


def remove_follower(store: str | os.PathLike, token: str, handle: str) -> None:
    where = _store(store)
    social.unfollow(where, _person_token(where, handle), token)


def _visible_post(store: Store, token: str, post_id: str) -> social.Post:
    post = social.get_post(store, post_id)
    if post is None or not social.may_see(store, token, post):
        raise InvalidInput(_("this post is not, or no longer, visible to you"))
    return post


def toggle_kudos(store: str | os.PathLike, token: str, post_id: str, now: datetime | None = None) -> bool:
    """Kudos, "I got it", or "helpful" on notes; dated, since the notes library counts
    this month's helpful marks (D26)."""
    where = _store(store)
    return bool(
        _social(lambda: social.toggle_kudos(where, token, _visible_post(where, token, post_id), now=now))
    )


def react(
    store: str | os.PathLike, token: str, post_id: str, kind: object, now: datetime | None = None
) -> str | None:
    """A reaction to a session (D108), or taking it back; the reaction kept."""
    where = _store(store)
    post = _social(lambda: _visible_post(where, token, post_id))
    return _social(lambda: social.react(where, token, post, str(kind), now=now))


def add_comment(
    store: str | os.PathLike, token: str, post_id: str, body: str, kind: object = "comment"
) -> None:
    """A comment, or a fix suggested to an explanation or notes (D141)."""
    where = _store(store)
    what = str(kind or "comment")
    _social(lambda: social.add_comment(where, token, _visible_post(where, token, post_id), body, kind=what))


def toggle_check(store: str | os.PathLike, token: str, post_id: str, now: datetime | None = None) -> bool:
    """A reader's "It's right" on an explanation or notes, or taking it back (D141)."""
    where = _store(store)
    return bool(
        _social(lambda: social.toggle_check(where, token, _visible_post(where, token, post_id), now=now))
    )


def accept_fix(store: str | os.PathLike, token: str, comment_id: str) -> tuple[bool, str]:
    """The author accepts a fix suggested to their post, or takes it back (D141):
    whether it is accepted now, and the post's id."""
    where = _store(store)
    accepted = bool(_social(lambda: social.accept_fix(where, token, comment_id)))
    with where.connection() as db:
        row = db.execute("SELECT post FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return accepted, str(row[0]) if row else ""


# -- Studying now (D108) -------------------------------------------------------------

# What a reaction looks like and says (D108), in `social.REACTIONS`' order.
REACTION_WORDS = {
    "kudos": ("👏", "Kudos"),
    "fire": ("🔥", "On fire"),
    "strong": ("💪", "Keep going"),
    "brain": ("🧠", "Big brain"),
}
PRESENCE_GRACE = timedelta(minutes=10)  # a session past its goal, or without beats, still shows this long
PRESENCE_OPEN = 120  # minutes a session without a chosen length is taken to last
PRESENCE_LONGEST = timedelta(hours=4)
JOIN_MINUTES = FIRST_DAY_MINUTES  # Join starts the timer's shortest goal


def studying_now_on(store: str | os.PathLike, token: str) -> bool:
    return social.studying_now_on(_store(store), token)


def set_studying_now(store: str | os.PathLike, token: str, on: bool) -> None:
    """The yes to Studying now (D108): off by default; for a student with a handle."""
    _social(lambda: social.set_studying_now(_store(store), token, on))


def sync_presence(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> None:
    """Studying now follows the focus timer (D108): shown while it runs, for the
    session's goal and ten minutes more (two hours with no goal), longer while the
    page's heartbeat says it goes on, never past four hours; gone when it stops."""
    where = _store(store)
    focus = subscription.options.get("focus")
    if not focus:
        social.clear_presence(where, subscription.token)
        return
    now = now or datetime.now(UTC)
    started = datetime.fromisoformat(focus["started"])
    planned = started + timedelta(minutes=int(focus.get("goal") or PRESENCE_OPEN)) + PRESENCE_GRACE
    until = min(max(planned, now + PRESENCE_GRACE), started + PRESENCE_LONGEST)
    if until <= now:
        social.clear_presence(where, subscription.token)
        return
    social.mark_present(
        where, subscription.token, focus["course"], started, until, place=focus.get("place") or None
    )


# -- the study map (D133) ----------------------------------------------------------------
# Where students study: public places they add, and, at each, who is there now. The
# people a student follows show by name, everyone else as a count (the owner's choice,
# 2026-11-16); only students who said yes to Studying now (D108) are ever at a place,
# and only at the place they chose for a session. The map is Leaflet over OpenStreetMap's
# tiles (the owner approved both); its address is given to the page, not written in the
# script, as the tile policy recommends.
MAP_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_HOST = "https://tile.openstreetmap.org"
MAP_SPAN = 0.25  # degrees around the map's centre whose places are listed (about 28 km)
PLACES_RECENT = 5


def _remember_place(options: Mapping, place_id: str) -> list[str]:
    """The places a student studied at, latest first, to offer them again."""
    return [place_id, *(p for p in options.get("places_recent", []) if p != place_id)][:PLACES_RECENT]


def place_info(store: str | os.PathLike, place_id: str | None) -> dict | None:
    """A place by its id, as the pages name it; None for none or an unknown id."""
    place = social.get_place(_store(store), place_id)
    return {"id": place.id, "name": place.name, "lat": place.lat, "lon": place.lon} if place else None


def _who_words(named: Sequence[tuple], others: int, me: bool) -> str:
    """What a place's line says: the people followed by handle, then how many others."""
    names = ", ".join(f"@{profile.handle}" for profile, _course in named)
    if me and not names and not others:
        return _("Just you")
    if names and others:
        return _n("{names} and {n} other", "{names} and {n} others", others, names=names)
    if names:
        return names
    if others:
        return _n("{n} person studying", "{n} people studying", others)
    return _("Nobody yet")


def map_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None, at: str | None = None
) -> dict:
    """The study map (D133): its centre (the place `at`, the student's own place now, the
    last one they studied at, else their time zone's main city), the places around it
    and wherever someone they follow studies, each with who is there as the student may
    see it, the busiest first; whether the student can be seen (a handle and Studying
    now's yes), and the place of their own session, if it has one."""
    now = now or datetime.now(UTC)
    where = _store(store)
    token = subscription.token
    focus = subscription.options.get("focus") or {}
    mine = str(focus.get("place") or "")
    recent = [
        p for p in (social.get_place(where, i) for i in subscription.options.get("places_recent", [])) if p
    ]
    who = social.who_is_where(where, token, now)
    anchor = social.get_place(where, at) or social.get_place(where, mine) or (recent[0] if recent else None)
    if anchor is not None:
        lat, lon, zoom = anchor.lat, anchor.lon, 15
    else:
        lat, lon = _daylight.place(subscription.options["tz"], now)
        zoom = 13
    shown = {p.id: p for p in social.places_around(where, lat, lon, MAP_SPAN)}
    for place in recent:
        shown.setdefault(place.id, place)
    for place_id, here in who.items():
        if (here["named"] or here["me"]) and place_id not in shown:
            found = social.get_place(where, place_id)
            if found is not None:
                shown[found.id] = found
    rows = []
    for place in shown.values():
        here = who.get(place.id, {"named": [], "others": 0, "me": False})
        count = len(here["named"]) + here["others"] + int(here["me"])
        rows.append(
            {
                "id": place.id,
                "name": place.name,
                "lat": place.lat,
                "lon": place.lon,
                "count": count,
                "named": [{"handle": p.handle, "course": course} for p, course in here["named"]],
                "others": here["others"],
                "me": here["me"] or place.id == mine,
                "line": _who_words(here["named"], here["others"], here["me"] or place.id == mine),
                "distance": (place.lat - lat) ** 2 + ((place.lon - lon) * math.cos(math.radians(lat))) ** 2,
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["distance"]))
    for r in rows:
        del r["distance"]
    return {
        "center": [round(lat, 5), round(lon, 5)],
        "zoom": zoom,
        "places": rows,
        "studying": sum(r["count"] for r in rows),
        "mine": mine,
        "mine_name": next((r["name"] for r in rows if r["id"] == mine), ""),
        "focusing": bool(focus),
        "profile": social.get_profile(where, token) is not None,
        "on": social.studying_now_on(where, token),
        "tiles": MAP_TILES,
        "at": at or "",
    }


def choose_place(store: str | os.PathLike, subscription: Subscription, place_id: str) -> Subscription:
    """The running session's place on the map (D133): seen there by the student's
    followers, counted by everyone else. It needs Studying now's yes (D108)."""
    if social.get_place(_store(store), place_id) is None:
        raise InvalidInput(_("this place is no longer on the map"))
    if not social.studying_now_on(_store(store), subscription.token):
        raise InvalidInput(
            _("turn on Studying now in your profile first: the map shows only those who said yes")
        )
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    options = {
        **subscription.options,
        "focus": {**focus, "place": place_id},
        "places_recent": _remember_place(subscription.options, place_id),
    }
    return replace(subscription, options=options)


def leave_place(subscription: Subscription) -> Subscription:
    """The running session goes on, at no place."""
    focus = subscription.options.get("focus")
    if not focus:
        raise InvalidInput(_("there is no session running"))
    return replace(subscription, options={**subscription.options, "focus": {**focus, "place": ""}})


def add_place(
    store: str | os.PathLike, subscription: Subscription, form: Mapping[str, str], now: datetime | None = None
) -> dict:
    """A public place to study, from its name and the point tapped on the map (D133)."""
    try:
        place = social.add_place(
            _store(store), subscription.token, form.get("name", ""), form.get("lat"), form.get("lon"), now
        )
    except social.SocialError as error:
        raise InvalidInput(str(error)) from None
    return {"id": place.id, "name": place.name}


def studying_now_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> list[dict]:
    """Who of the people the student follows is studying now (D108): each with the
    course, the minutes so far, and the course of the student's own a Join starts
    (the same name if they have it, else their first)."""
    now = now or datetime.now(UTC)
    mine = course_names(subscription)
    by_name = {c.casefold(): c for c in mine}
    out = []
    for profile, course, started in social.present_followed(_store(store), subscription.token, now):
        out.append(
            {
                **profile.public(),
                "course": course,
                "minutes": max(1, round((now - started).total_seconds() / 60)),
                "join": by_name.get(course.casefold()) or (mine[0] if mine else course),
            }
        )
    return out


def cheer_classmate(
    store: str | os.PathLike, subscription: Subscription, handle: str, now: datetime | None = None
) -> bool:
    """A cheer to a classmate studying now, by handle (D178): False when one was sent to them
    a moment ago."""
    where = _store(store)
    target = social.profile_by_handle(where, str(handle or "").lstrip("@")[:40])
    if target is None:
        raise InvalidInput(_("they are not studying now"))
    try:
        return social.cheer(where, subscription.token, target.token, now)
    except social.SocialError as error:
        raise InvalidInput(str(error)) from None


def cheers_view(store: str | os.PathLike, subscription: Subscription) -> list[dict]:
    """Who cheered the student during the session running (D178), the latest first."""
    focus = subscription.options.get("focus")
    if not focus:
        return []
    since = datetime.fromisoformat(focus["started"])
    return [p.public() for p in social.cheers_for(_store(store), subscription.token, since)]


def delete_comment(store: str | os.PathLike, token: str, comment_id: str) -> bool:
    return social.delete_comment(_store(store), token, comment_id)


LIBRARY_SORTS = ("helpful", "new")


def post_notes(
    store: str | os.PathLike,
    subscription: Subscription,
    form: Mapping[str, str],
    photos: Sequence[bytes] = (),
    *,
    pdfs: Sequence[tuple[str, bytes]] = (),
    now: datetime | None = None,
) -> social.Post:
    """Notes for the library (D26): the student's own, by course, with the pages as
    photos or PDFs (D95), or written as text (D85); shared with everyone by default,
    since the library is for others, or with one of their study groups only (D85)."""
    now = now or datetime.now(UTC)
    where = _store(store)
    visibility = _sharing(store, subscription.token, form.get("visibility"), "everyone")
    day = now.astimezone(_zone(subscription.options["tz"])).date().isoformat()
    data = {
        "course": _shared_course(form),
        "title": form.get("title", ""),
        "note": form.get("note", ""),
        "body": form.get("body", ""),
        "group": form.get("group", ""),
        "own_work": form.get("own_work") == "1",
    }
    post: social.Post = _social(
        lambda: social.create_post(
            where,
            subscription.token,
            "notes",
            day=day,
            visibility=visibility,
            data=data,
            photos=photos,
            pdfs=pdfs,
            now=now,
        )
    )
    return post


def library_view(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    filters: Mapping[str, str] | None = None,
    sort: str = "helpful",
    now: datetime | None = None,
) -> dict:
    """The notes library: notes by course, university and programme, the month's
    most helpful first (or the newest), each with its helpful marks and its place
    in its course's top of the month."""
    now = now or datetime.now(UTC)
    where = _store(store)
    zone = _zone(subscription.options["tz"])
    sort = sort if sort in LIBRARY_SORTS else "helpful"
    filters = {k: str(v).strip()[:80] for k, v in (filters or {}).items() if k in ("course", "uni", "prog")}
    found = social.notes_library(
        where,
        subscription.token,
        month_start=_month_start(now),
        course=filters.get("course", ""),
        university=filters.get("uni", ""),
        programme=filters.get("prog", ""),
        sort=sort,
    )
    cards = _cards(where, subscription.token, [n.post for n in found], now, zone)
    for card, note in zip(cards, found, strict=True):
        card.update(month=note.month)
    me = social.get_profile(where, subscription.token)
    return {
        "notes": cards,
        "filters": filters,
        "sort": sort,
        "profile": me.public() if me else None,
        "courses": course_names(subscription),
        "invitations": len(social.invitations_of(where, subscription.token)),
    }


def _shared_course(form: Mapping[str, str]) -> str:
    """The course of a share form (D141): a course typed under "Another course" wins
    over the chip ticked, since typing it was the later act."""
    return str(form.get("course_other") or "").strip() or str(form.get("course") or "").strip()


def share_course(subscription: Subscription, now: datetime | None = None) -> str:
    """The course a share form starts on (D141), so sharing what was just studied takes
    no typing: the course of the session under way, else of the last one ended today,
    else of the next one. "" when none is a course of the plan."""
    if subscription.plan is None:
        return ""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    names = set(course_names(subscription))
    plan = PlanReport.from_dict(subscription.plan)
    sessions = sorted((s for s in plan.history + plan.sessions if s.subject in names), key=lambda s: s.start)
    local = now.astimezone(zone)
    today = local.date()

    def at(stamp: str) -> datetime:
        moment = datetime.fromisoformat(stamp)
        return moment if moment.tzinfo else moment.replace(tzinfo=zone)

    under_way = [s for s in sessions if at(s.start) <= now < at(s.end)]
    ended = [s for s in sessions if at(s.end) <= now and at(s.end).astimezone(zone).date() == today]
    coming = [s for s in sessions if at(s.start) > now]
    for found in (under_way[:1], ended[-1:], coming[:1]):
        if found:
            return found[0].subject
    return ""


def post_explanation(
    store: str | os.PathLike,
    subscription: Subscription,
    form: Mapping[str, str],
    photos: Sequence[bytes] = (),
    *,
    now: datetime | None = None,
) -> social.Post:
    """An "explain it simply" post (D19): a concept, its course, an explanation
    for someone who studies something else."""
    now = now or datetime.now(UTC)
    where = _store(store)
    visibility = _sharing(store, subscription.token, form.get("visibility"), "everyone")
    day = now.astimezone(_zone(subscription.options["tz"])).date().isoformat()
    data = {
        "concept": form.get("concept", ""),
        "course": _shared_course(form),
        "text": form.get("text", ""),
    }
    post: social.Post = _social(
        lambda: social.create_post(
            where,
            subscription.token,
            "explain",
            day=day,
            visibility=visibility,
            data=data,
            photos=photos,
            now=now,
        )
    )
    return post


# --------------------------------------------------------------------------- #
# Tips with their sources (DECISIONS.md D62)


# The words under a tip's picture (D160), by the unit of its figure (`tips.FIGURES`).
FIGURE_WORDS = {
    "studies": ("{n} study", "{n} studies"),
    "experiments": ("{n} experiment", "{n} experiments"),
    "trials": ("{n} trial", "{n} trials"),
    "tests": ("{n} test", "{n} tests"),
    "people": ("{n} person", "{n} people"),
    "students": ("{n} student", "{n} students"),
}


def tip_figure(key: str) -> str:
    """The number under a tip's picture (D160), in the page's language: "138 studies",
    "18 to 254 days"; "" for a tip whose study gives no one number."""
    figure = _tips.FIGURES.get(key)
    if figure is None:
        return ""
    if len(figure) == 3:
        return _("{low} to {high} days", low=figure[1], high=figure[2])
    one, many = FIGURE_WORDS[figure[0]]
    return _n(one, many, figure[1])


def _tip(tip: _tips.Tip) -> dict:
    label, meaning = _tips.STRENGTHS[tip.strength]
    return {
        "key": tip.key,
        "figure": tip_figure(tip.key),
        "topic": tip.topic,
        "strength": tip.strength,
        "strength_label": _(label),
        "strength_meaning": _(meaning),
        "advice": _(tip.advice),
        "why": _(tip.why),
        "limits": _(tip.limits),
        "in_app": _(tip.in_app) if tip.in_app else "",
        "sources": [
            {
                "cite": source.cite,
                "venue": source.venue,
                "url": source.url,
                "checked": f"{format_date(source.checked, 'day_short')} {source.checked.year}",
            }
            for source in (_tips.SOURCES[key] for key in tip.sources)
        ],
    }


def tips_view(topic: str | None = None) -> dict:
    """The tips page (D62) by topic (D91): every topic with what it is about and how
    many tips it holds, and the chosen topic's tips, in the plan's language, with
    what their evidence is worth and where it comes from. No topic chosen: the list
    of topics alone."""
    chosen = topic if topic in _tips.TOPICS else None
    return {
        "strengths": [
            {"key": key, "label": _(label), "meaning": _(meaning)}
            for key, (label, meaning) in _tips.STRENGTHS.items()
        ],
        "topics": [
            {
                "key": key,
                "name": _(name),
                "note": _(_tips.TOPIC_NOTES[key]),
                "count": sum(1 for t in _tips.TIPS if t.topic == key),
                "current": key == chosen,
            }
            for key, name in _tips.TOPICS.items()
        ],
        "topic": chosen,
        "name": _(_tips.TOPICS[chosen]) if chosen else "",
        "note": _(_tips.TOPIC_NOTES[chosen]) if chosen else "",
        "tips": [_tip(t) for t in _tips.TIPS if t.topic == chosen] if chosen else [],
    }


# How the spark looks beside each tip (D66): thinking beside a way to study, eyes
# closed beside sleep and breaks, cheering beside exercise and a habit, waving
# beside going to class, as it is beside coffee.
MOODS = ("idle", "think", "rest", "cheer", "wave")
TIP_MOODS = {
    "self-test": "think",
    "spacing": "think",
    "class-quizzes": "think",
    "interleave": "think",
    "pretest": "think",
    "attend": "wave",
    "sleep-after": "rest",
    "sleep-regular": "rest",
    "exercise": "cheer",
    "caffeine": "idle",
    "breaks": "rest",
    "track": "think",
    "if-then": "think",
    "habit": "cheer",
    # The clear-your-mind list's (D145)
    "plan-it": "think",
    "bedtime-list": "rest",
    # How the brain learns, motivation and reward (D91)
    "recall-strengthens": "think",
    "surprise": "think",
    "spacing-cells": "think",
    "curiosity": "think",
    "reward-memory": "cheer",
    "rewards": "cheer",
    "choice": "wave",
    "effort-feels": "think",
    "effort-reward": "cheer",
    "mindset": "idle",
}

TIP_SETTINGS = ("on", "off")


# D155: Today's widgets, each on or off, as a phone's home screen lets a person choose (the
# owner's choice 2-B, 2026-11-19, after "this page is very overwhelming i do not feel in
# control"). Five are on to start; the order is the page's own, most urgent first, so
# nothing moves under a student's thumb. What asks for an answer (a session to report,
# an answered self-test, the day's one message) is always shown: it is not a widget.
# D177 (the owner, 2026-11-20: "la prima cosa che hai è il focus ... ci aggiungerei
# immediatamente sotto la comunità, così ti incoraggia a studiare"): Community comes right
# after the next step, and is on to start; seeing classmates study is the nudge.
TODAY_WIDGETS: tuple[tuple[str, str, str], ...] = (
    ("next", "Up next", "The session going on, or the next one, with Focus a tap away."),
    ("friends", "Community", "Who is studying now, and what classmates shared."),
    ("day", "Today", "Your classes, sessions and busy times today."),
    ("focus", "Focus", "Time anything besides the plan's next session, in one tap."),
    ("tasks", "Tasks", "What is due next, and how far each has come."),
    ("exams", "Exams", "Each exam, how far off, and how many topics are on track."),
    ("mind", "On your mind", "A list to empty your head into, a day or a note each."),
    ("time", "Your time and attention", "The hours left today, and how long you focused this week."),
    ("tips", "Tips for today", "A tip to study and one to live well, with their evidence."),
    ("work", "Workspace", "Each course's links, and your learning platform."),
    ("week", "This week", "The week's sessions and your days in a row."),
    ("cards", "Flashcards", "The cards waiting for you today."),
)
# D171 (the owner's A): the next step's band starts any session, so Focus gave way to On
# your mind; a student who chose keeps their choice.
TODAY_START = ("next", "friends", "day", "tasks", "exams", "mind")
# D165: each widget's picture, the one its head on Today shows (`web/icons.ART`'s names),
# so a switch on Edit Today and its widget look alike; no two alike, to tell rows apart.
TODAY_PICTURES = {
    "next": "target",
    "day": "calendar",
    "focus": "focus",
    "tasks": "tasks",
    "exams": "cap",
    "mind": "summary",
    "time": "daytime",
    "tips": "tips",
    "work": "reading",
    "week": "progress",
    "cards": "cards",
    "friends": "group",
}


def today_widgets(subscription: Subscription) -> tuple[str, ...]:
    """The widgets a student keeps on Today (D155), in the page's order; the ones to start
    (`TODAY_START`, six since D177) until they choose."""
    chosen = subscription.options.get("today_widgets")
    keys = [k for k, _name, _line in TODAY_WIDGETS]
    if not isinstance(chosen, list):
        return TODAY_START
    return tuple(k for k in keys if k in chosen)


def set_today_widgets(subscription: Subscription, chosen: Iterable[str]) -> Subscription:
    """Keep the widgets chosen for Today (D155); a name Today does not draw is refused."""
    keys = [k for k, _name, _line in TODAY_WIDGETS]
    wanted = set(chosen)
    if wanted - set(keys):
        raise InvalidInput(_("choose among Today's widgets"))
    return replace(
        subscription, options={**subscription.options, "today_widgets": [k for k in keys if k in wanted]}
    )


def today_widgets_view(subscription: Subscription) -> list[dict]:
    """Edit Today's list (D155): each widget, what it shows, and whether it is on."""
    shown = today_widgets(subscription)
    return [
        {
            "key": key,
            "name": _(name),
            "line": _(line),
            "on": key in shown,
            "start": key in TODAY_START,
            "picture": TODAY_PICTURES[key],
        }
        for key, name, line in TODAY_WIDGETS
    ]


def tips_on(subscription: Subscription) -> bool:
    """Whether Today and Focus show tips (D63); the tips page is always there."""
    return subscription.options.get("tips", "on") != "off"


def set_tips(subscription: Subscription, value: str) -> Subscription:
    if value not in TIP_SETTINGS:
        raise InvalidInput(_("choose On or Off"))
    return replace(subscription, options={**subscription.options, "tips": value})


def _tip_moments(subscription: Subscription, local: datetime) -> tuple[list, list]:
    """What in the plan today a tip speaks to (D63), as (tip key, why now) pairs:
    first what cannot wait (an exam or a test close, a session or a class skipped,
    caffeine before an evening session), then what is ordinary (a lecture, training,
    a self-test, the evening after studying). Only facts the plan holds; the tip
    itself says what the research found."""
    urgent: list[tuple[str, str]] = []
    usual: list[tuple[str, str]] = []
    if subscription.plan is None:
        return urgent, usual
    plan = PlanReport.from_dict(subscription.plan)
    zone = local.tzinfo
    today = local.date()

    def at(stamp: str) -> datetime:
        return datetime.fromisoformat(stamp).astimezone(zone)

    exams = [(at(s.exam), s.name) for s in plan.subjects]
    exams += [(at(e.start), test_label("partial", e.summary)) for e in plan.events if e.source == "partial"]
    soon = sorted(x for x in exams if local < x[0] < local + timedelta(days=4))
    if soon:
        urgent.append(
            ("sleep-regular", _("Exam soon: {name}, {when}.", name=soon[0][1], when=_when(soon[0][0], local)))
        )
    tests = sorted(
        (at(e.start), test_label(e.source, e.summary))
        for e in plan.events
        if e.source in ("quiz", "mock") and local < at(e.start) < local + timedelta(days=7)
    )
    if tests:
        urgent.append(
            (
                "class-quizzes",
                _("A test is coming: {name}, {when}.", name=tests[0][1], when=_when(tests[0][0], local)),
            )
        )
    if any(s.outcome == "skipped" and at(s.start).date() == today - timedelta(days=1) for s in plan.history):
        urgent.append(("habit", _("A session was skipped yesterday.")))
    for change in subscription.options.get("lecture_changes", ()):
        if change.get("action") != "skip":
            continue  # a catch-up is planned, or the class is a test
        course = course_of(change["summary"]) or short_title(change["summary"])
        if "at" in change and abs((at(change["at"]).date() - today).days) < 7:
            day = format_date(at(change["at"]).date(), "short")
            urgent.append(
                ("attend", _("A class skipped this week: {course}, {day}.", course=course, day=day))
            )
            break
        if "weekday" in change and date.fromisoformat(change["from"]) < today + timedelta(days=7):
            urgent.append(("attend", _("A class you skip every week: {course}.", course=course)))
            break
    sessions = [s for s in plan.history + plan.sessions if at(s.start).date() == today]
    evening = [at(s.start) for s in sessions if at(s.start) > local and at(s.start).hour >= 18]
    if evening and local.hour >= 12:
        urgent.append(("caffeine", _("You study this evening at {time}.", time=f"{min(evening):%H:%M}")))

    if not subscription.options.get("warm_up"):
        lectures = _lectures_ahead_today(plan, local)
        if lectures:
            first = lectures[0]
            usual.append(
                (
                    "pretest",
                    _(
                        "A lecture later today: {course} at {time}.",
                        course=first["course"],
                        time=first["when"],
                    ),
                )
            )
    for row in subscription.busy_rows:
        weekly = row.get("weekday") is not None and int(row["weekday"]) == today.weekday()
        if row.get("kind") == "training" and (weekly or row.get("date") == today.isoformat()):
            usual.append(
                (
                    "exercise",
                    _(
                        "Training today: {name} at {time}.",
                        name=row.get("label", ""),
                        time=str(row.get("start", ""))[:5],
                    ),
                )
            )
            break
    tests_today = [s for s in sessions if s.kind in ("review", "first review") and at(s.end) > local]
    if tests_today:
        usual.append(("self-test", _("A self-test today: {subject}.", subject=tests_today[0].subject)))
    studied = [s for s in sessions if at(s.end) <= local and s.outcome != "skipped"]
    if studied and local.hour >= 20:
        usual.append(("sleep-after", _("You studied today.")))
    return urgent, usual


def tip_of_the_day(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """Today's tip (D63): the one the plan's day calls for, with why now; when
    several ordinary moments apply, they take turns day by day; when none does, the
    tips take turns (D62). None when the student turned tips off."""
    if not tips_on(subscription):
        return None
    now = now or datetime.now(UTC)
    local = now.astimezone(_zone(subscription.options["tz"]))
    urgent, usual = _tip_moments(subscription, local)
    day = local.date().toordinal()
    if urgent or usual:
        key, reason = urgent[0] if urgent else usual[day % len(usual)]
        tip = next(t for t in _tips.TIPS if t.key == key)
    else:
        tip, reason = _tips.of_the_day(local.date()), ""
    return {
        "key": tip.key,
        "topic": tip.topic,
        "advice": _(tip.advice),
        "reason": reason,
        "mood": TIP_MOODS[tip.key],
    }


def focus_tip(subscription: Subscription) -> dict | None:
    """The tip the focus page shows (D63): plan breaks before starting, and take one
    after `BREAK_AFTER` of a running session, the study's 24-minute blocks
    [ref:biwer2023]. The spark waves before, and rests at the break (D66). None
    when tips are off."""
    if not tips_on(subscription):
        return None
    tip = next(t for t in _tips.TIPS if t.key == "breaks")
    return {
        "key": tip.key,
        "topic": tip.topic,
        "advice": _(tip.advice),
        "break_after": int(BREAK_AFTER.total_seconds()),
        "mood": "wave",
        "break_mood": "rest",
    }


BREAK_AFTER = timedelta(minutes=24)


# --------------------------------------------------------------------------- #
# Tips in context (DECISIONS.md D135)

# The pages that show a tip beside what they are about, each its own offset so that two
# pages seen the same day turn through their tips apart.
TIP_PLACES = ("focus", "review", "cards", "make", "calendar", "week", "course", "tasks", "progress")
TIP_EXAM_DAYS = 21  # a course's page speaks of its exam from three weeks before


def _days_list(days: Sequence[date]) -> str:
    """Days as a sentence says them: "Tue 3 Nov", "Tue 3 Nov and Fri 6 Nov"."""
    words = [format_date(d, "short") for d in days]
    return (
        words[0] if len(words) == 1 else _("{first} and {last}", first=", ".join(words[:-1]), last=words[-1])
    )


def _tip_options(
    subscription: Subscription,
    place: str,
    local: datetime,
    *,
    course: str = "",
    suggested: dict | None = None,
) -> list[tuple[str, str]]:
    """The tips a page may show, as (tip key, why here) pairs, from what the page is about
    and the facts the plan holds (D135). The tip says what the research found; the line
    says only what is true of this plan."""
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan is not None else None
    zone = local.tzinfo

    def at(stamp: str) -> datetime:
        return datetime.fromisoformat(stamp).astimezone(zone)

    if place == "focus":
        out = []
        if suggested and suggested.get("kind") in ("review", "first review"):
            out.append(
                (
                    "recall-strengthens",
                    _("This session is a self-test: recall what you can before you look at your notes."),
                )
            )
        out += [
            ("breaks", _("Before you start: decide when your breaks will be.")),
            (
                "if-then",
                _("Your session has a day and a time; choosing the place too makes it an if-then plan."),
            ),
            (
                "effort-feels",
                _("If recalling feels harder than rereading, that effort is part of how it works."),
            ),
        ]
        return out
    if place == "review":
        return [
            (
                "recall-strengthens",
                _("Say the answer to yourself before you show it: the recall is what strengthens it."),
            ),
            (
                "effort-feels",
                _("A card you struggle with is not wasted: struggling is not a sign of learning less."),
            ),
        ]
    if place == "cards":
        return [
            ("spacing", _("Each card comes back after a gap that grows every time you remember it.")),
            ("self-test", _("Flashcards are practice tests: one of the two techniques rated most useful.")),
            ("spacing-cells", _("Why the gaps: spacing shows even in cells, in a laboratory.")),
        ]
    if place == "make":
        return [("self-test", _("Cards made from your notes turn rereading into testing yourself."))]
    if place == "tasks":
        return [("if-then", _("Give each task a day and a time: an if-then plan helps it get done."))]
    if place == "progress":
        return [
            ("track", _("Reporting each session, as counted here, is keeping track of what you did.")),
            ("habit", _("Your streak forgives a missed day: one miss does not undo a habit.")),
            ("rewards", _("What you see here is progress, not points to collect.")),
        ]
    if place == "week":
        return [
            ("spacing", _("The same hours spread over more days hold longer than in a few long sittings.")),
            ("habit", _("A day off does not undo a habit: in the study, missing one day did not matter.")),
            (
                "sleep-regular",
                _("Leave room for sleep: the weeks before an exam counted, not only the night before."),
            ),
        ]
    if plan is None:
        return []
    if place == "calendar":
        out = []
        ahead = [s for s in plan.sessions if local < at(s.start) < local + timedelta(days=21)]
        by_course: dict[str, list[date]] = {}
        for s in sorted(ahead, key=lambda s: s.start):
            days = by_course.setdefault(s.subject, [])
            if at(s.start).date() not in days:
                days.append(at(s.start).date())
        spaced = next(((c, d[:3]) for c, d in by_course.items() if len(d) >= 3), None)
        if spaced is not None:
            out.append(
                (
                    "spacing",
                    _(
                        "Your plan comes back to {course} on {days}.",
                        course=spaced[0],
                        days=_days_list(spaced[1]),
                    ),
                )
            )
        out += [
            ("if-then", _("Every session here has a day and a time: an if-then plan, ready made.")),
            ("choice", _("Drag a session to another time and your plan adapts around it.")),
        ]
        return out
    if place == "course":
        out = []
        exam = next((s for s in plan.subjects if s.name == course), None)
        if exam is not None and local < at(exam.exam) < local + timedelta(days=TIP_EXAM_DAYS):
            day = format_date(at(exam.exam).date(), "short")
            out.append(
                (
                    "sleep-regular",
                    _(
                        "{course}'s exam is on {day}: sleep over the weeks before it counts.",
                        course=course,
                        day=day,
                    ),
                )
            )
        folded = course.casefold()
        tests = sorted(
            at(e.start)
            for e in plan.events
            if e.source in ("quiz", "mock")
            and local < at(e.start) < local + timedelta(days=14)
            and (course_of(e.summary) or short_title(e.summary)).casefold() == folded
        )
        if tests:
            out.append(
                (
                    "class-quizzes",
                    _(
                        "A test is coming in {course}, {when}: it is study too.",
                        course=course,
                        when=_when(tests[0], local),
                    ),
                )
            )
        out += [
            (
                "self-test",
                _(
                    "Practise {course} by recalling: its self-tests and cards are practice tests.",
                    course=course,
                ),
            ),
            (
                "interleave",
                _(
                    "When you practise {course}'s problems, mix their kinds instead of one kind at a time.",
                    course=course,
                ),
            ),
        ]
        return out
    return []


def tip_here(
    subscription: Subscription,
    place: str,
    now: datetime | None = None,
    *,
    course: str = "",
    suggested: dict | None = None,
) -> dict | None:
    """The tip a page shows beside what it is about (D135): the advice, why here, how
    strong its evidence is, what it does not show and its sources, each page turning
    through its tips day by day. The first of a page's moments that only its plan holds
    (a self-test about to start, an exam close) comes first. None when tips are off or
    the page has none."""
    if not tips_on(subscription) or place not in TIP_PLACES:
        return None
    now = now or datetime.now(UTC)
    local = now.astimezone(_zone(subscription.options["tz"]))
    options = _tip_options(subscription, place, local, course=course, suggested=suggested)
    if not options:
        return None
    pinned = {"focus": "recall-strengthens", "course": "sleep-regular"}.get(place)
    if pinned and options[0][0] == pinned:
        key, here = options[0]
    else:
        key, here = options[(local.date().toordinal() + TIP_PLACES.index(place)) % len(options)]
    tip = next(t for t in _tips.TIPS if t.key == key)
    return {**_tip(tip), "here": here, "mood": TIP_MOODS[key], "topic_name": _(_tips.TOPICS[tip.topic])}


def _plan_start(subscription: Subscription) -> date:
    return date.fromisoformat(str(subscription.options["start"])[:10])


def _note(note: _weekly.Note, week: int, read: Iterable[str]) -> dict:
    return {
        "key": note.key,
        "week": week,
        "title": _(note.title),
        "paragraphs": [_(p) for p in note.paragraphs],
        "for_you": _(note.for_you),
        "limits": _(note.limits),
        "unread": note.key not in set(read),
        "mood": "think",
        "sources": [
            {
                "cite": s.cite,
                "venue": s.venue,
                "url": s.url,
                "checked": f"{format_date(s.checked, 'day_short')} {s.checked.year}",
            }
            for s in (_tips.SOURCES[k] for k in note.sources)
        ],
    }


def weekly_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """The weekly notes a plan has so far (D64), this week's first, and when the
    next one comes."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    start = _plan_start(subscription)
    count = _weekly.unlocked(start, today)
    read = subscription.options.get("weekly_read", ())
    notes = [_note(n, i + 1, read) for i, n in enumerate(_weekly.NOTES[:count])]
    following = _weekly.monday(today) + timedelta(days=7)
    return {
        "notes": list(reversed(notes)),
        "next": format_date(following, "long") if count < len(_weekly.NOTES) else "",
    }


def weekly_note(subscription: Subscription, key: str, now: datetime | None = None) -> dict | None:
    """One note, if this plan has it yet (D64)."""
    return next((n for n in weekly_view(subscription, now)["notes"] if n["key"] == key), None)


def mark_note_read(subscription: Subscription, key: str) -> Subscription:
    read = list(subscription.options.get("weekly_read", ()))
    if key in read:
        return subscription
    return replace(subscription, options={**subscription.options, "weekly_read": [*read, key]})


def note_of_the_week(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """This week's note, for Today, until it is read (D64)."""
    notes = weekly_view(subscription, now)["notes"]
    return {"key": notes[0]["key"], "title": notes[0]["title"]} if notes and notes[0]["unread"] else None


def log_tips(store: str | os.PathLike, token: str) -> None:
    """The tips page was opened (once a day per plan), for the pilot's measures:
    whether students read the tips decides the AI coach (D62)."""
    _store(store).log_daily(token, "tips")


# --------------------------------------------------------------------------- #
# The coach (DECISIONS.md D65)

COACH_MAX = 400  # characters in a question
# The rules' coach: words of a question (English, French, Italian) and the tips
# they lead to. A pattern starts at a word's start; short words are whole words.
_SLEEP = (
    r"\bsleep",
    r"\bnap\b",
    r"\ball[- ]nighter",
    r"\bdormir",
    r"\bsommeil",
    r"\bnuit blanche",
    r"\bsieste",
    r"\bdormire",
    r"\bsonno",
    r"\bnotte in bianco",
)
_COACH_WORDS = {
    "self-test": (
        r"\bre-?read",
        r"\bhighlight",
        r"\btest(ing)? myself",
        r"\bmemori",
        r"\bremember",
        r"\bretain",
        r"\bforget",
        r"\brelir",
        r"\brelis",
        r"\brelecture",
        r"\bsurlign",
        r"\bmémoris",
        r"\bretenir",
        r"\boubli",
        r"\brilegg",
        r"\brilettur",
        r"\bsottolin",
        r"\bmemorizz",
        r"\bricord",
        r"\bdimentic",
    ),
    "spacing": (
        r"\bcram",
        r"\blast[- ]minute",
        r"\bnight before",
        r"\bspread",
        r"\bspac(e|ing)\b",
        r"\bbachot",
        r"\bdernière minute",
        r"\bla veille",
        r"\bétaler",
        r"\bultimo momento",
        r"\bnotte prima",
        r"\bdistribuir",
    ),
    "class-quizzes": (
        r"\bquiz",
        r"\bmidterm",
        r"\binterro",
        r"\bpartiel",
        r"\bcompitin",
        r"\bprov[ae] intermedi",
    ),
    "interleave": (
        r"\bmix",
        r"\binterleav",
        r"\bexercises\b",
        r"\bproblem",
        r"\bmaths?\b",
        r"\bmélang",
        r"\bexercices\b",
        r"\bproblème",
        r"\bmescol",
        r"\balternar",
        r"\besercizi",
        r"\bproblemi",
        r"\bmatematic",
    ),
    "pretest": (r"before (the )?(lecture|class)", r"avant (le|la) cours", r"prima della lezione"),
    "attend": (
        r"\bskip(ping)? (a |the )?(class|lecture)",
        r"\battend",
        r"\bgo(ing)? to (class|lectures?)",
        r"\bsécher",
        r"\baller en cours",
        r"\bassiduité",
        r"\bsaltare (la |le )?lezion",
        r"\bfrequenz",
        r"\bandare a lezione",
    ),
    "sleep-after": _SLEEP,
    "sleep-regular": _SLEEP,
    "exercise": (
        r"\bsport",
        r"\bgym\b",
        r"\brunning\b",
        r"\bexercise\b",
        r"\bworkout",
        r"\bcourir",
        r"\bpalestra",
        r"\bcorrere",
        r"\ballenament",
    ),
    "caffeine": (
        r"\bcoffee",
        r"\bcaffein",
        r"\btea\b",
        r"\benergy drink",
        r"\btheanin",
        r"\bcafé",
        r"\bthé\b",
        r"\bthéanin",
        r"\bcaffè",
        r"\btè\b",
        r"\bteanin",
    ),
    "breaks": (
        r"\bbreaks?\b",
        r"\bpomodoro",
        r"\btired",
        r"\bfocus",
        r"\bconcentr",
        r"\bdistract",
        r"\bpauses?\b",
        r"\bfatigu",
        r"\bpausa\b",
        r"\bstanc",
        r"\bdistra",
    ),
    "track": (r"\btrack", r"\bprogress", r"\bmonitor", r"\bprogrès", r"\bprogressi", r"\btraccia"),
    "if-then": (
        r"\bprocrastin",
        r"\bget started",
        r"\bstart studying",
        r"\bmotiv",
        r"\bcommencer",
        r"\biniziare",
        r"\bcominciare",
    ),
    "habit": (
        r"\bhabit",
        r"\broutine",
        r"\bstreak",
        r"\bevery day",
        r"\bconsistent",
        r"\bhabitude",
        r"\btous les jours",
        r"\babitudin",
        r"\bogni giorno",
        r"\bcostan",
    ),
}
COACH_WORDS = {key: re.compile("|".join(words)) for key, words in _COACH_WORDS.items()}


def _coach_rules(question: str) -> list[str]:
    """The tips a question's words lead to, the most matched first, at most two."""
    text = question.casefold()
    scores = {key: len(pattern.findall(text)) for key, pattern in COACH_WORDS.items()}
    order = [t.key for t in _tips.TIPS]
    ranked = sorted((k for k, n in scores.items() if n), key=lambda k: (-scores[k], order.index(k)))
    return ranked[:2]


def coach(
    store: str | os.PathLike,
    subscription: Subscription,
    question: str,
    *,
    now: datetime | None = None,
    provider: _ai.Provider | None = None,
) -> dict:
    """A question about studying, answered from the tips (D65): by the model when AI
    is on and allowed, and its answer keeps the rules (`ai.coach_from_answer`);
    otherwise by matching the question's words to the tips. The question is not
    kept; that one was asked, and how, is logged for the pilot's measures.

    D176: the model is Claude Haiku unless the server names another, within the
    student's answers of the day (`coach_allowance`); past them the rules answer and
    `spent` says why. The model is given what the plan says about now
    (`_coach_plan`), so its advice can name an exam, a topic or today's session."""
    now = now or datetime.now(UTC)
    question = " ".join(str(question).split())
    if len(question) < 3:
        raise InvalidInput(_("ask a question in a few words"))
    if len(question) > COACH_MAX:
        raise InvalidInput(_("at most {n} characters, please", n=COACH_MAX))
    by_key = {t.key: t for t in _tips.TIPS}
    model = (provider or _ai.coach_from_env()) if ai_on(subscription) else None
    spent = model is not None and coach_allowance(store, subscription, now)["left"] <= 0
    if model is not None and not spent:
        language = _LANGUAGE_NAMES.get(language_of(subscription), "English")
        library = [{"key": t.key, "advice": t.advice, "why": t.why, "limits": t.limits} for t in _tips.TIPS]
        system, prompt = _ai.coach_prompt(question, language, library, _coach_plan(subscription, now))
        data = _ai.ask(
            _store(store),
            subscription.token,
            model,
            "coach",
            system=system,
            prompt=prompt,
            schema=_ai.COACH_SCHEMA,
            max_tokens=_ai.COACH_MAX_TOKENS,
            now=now,
        )
        answer = _ai.coach_from_answer(data, list(by_key)) if data else None
        if answer is not None:
            log_event(store, subscription.token, "coach", "ai")
            return {
                "by": "ai",
                "answer": answer["answer"],
                "ask": answer["question"],
                "declined": answer["kind"] == "declined",
                "tips": [_tip(by_key[k]) for k in answer["tips"]],
                "mood": "idle" if answer["kind"] == "declined" else "think",
                "spent": False,
            }
    matched = _coach_rules(question)
    log_event(store, subscription.token, "coach", "rules")
    return {
        "by": "rules",
        "answer": _("Here is what the research in the tips says about that.")
        if matched
        else _("No tip matches your question yet. All the tips are on the tips page."),
        "ask": "",
        "declined": False,
        "tips": [_tip(by_key[k]) for k in matched],
        "mood": "think" if matched else "idle",
        "spent": spent,
    }


# The coach's answers with AI a day (D176): five for everyone, the owner paying for the
# model during the pilot (a university project); two hundred with premium, which the
# owner gives by a code made on the admin page (nothing is sold yet). A day is the
# student's own, from midnight in the plan's time zone. Every call counts, a failed one
# too: it may have cost. The month's cap (`ai.monthly_cap`) still stops every call.
COACH_FREE_A_DAY = 5
COACH_PREMIUM_A_DAY = 200


def premium(subscription: Subscription) -> dict | None:
    """The plan's premium (D176): since when, and which code gave it; None without."""
    kept = subscription.options.get("premium")
    return kept if isinstance(kept, dict) else None


def _day_began(subscription: Subscription, now: datetime) -> datetime:
    zone = _zone(subscription.options.get("tz") or "UTC")
    return datetime.combine(now.astimezone(zone).date(), time(0, 0), tzinfo=zone)


def coach_allowance(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict:
    """The coach's answers with AI today (D176): how many a day, how many asked, how
    many left, and whether the plan has premium."""
    now = now or datetime.now(UTC)
    most = COACH_PREMIUM_A_DAY if premium(subscription) else COACH_FREE_A_DAY
    used = _ai.calls_today(
        _store(store), subscription.token, now, "coach", since=_day_began(subscription, now)
    )
    return {
        "most": most,
        "used": min(used, most),
        "left": max(most - used, 0),
        "premium": bool(premium(subscription)),
    }


_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
COACH_PLAN_EXAMS = 4  # the next exams the coach is told of
COACH_PLAN_TOPICS = 5  # the taught topics with the least chance of recall now


def _coach_plan(subscription: Subscription, now: datetime) -> dict | None:
    """What the coach is told of the plan (D176), and nothing else: today's date and
    weekday; the next exams, each course's first one ahead, with the days left; the
    taught topics the memory model gives the least chance of recall today (FSRS with
    average weights, rounded to 5 %); today's study sessions, their times, kind and
    course or task, and whether each is over. Never the timetable's other events, a
    place, a note, a classmate or the plan's address. None without a plan."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    exams: dict[str, datetime] = {}
    for spec in plan.specs:
        if not spec.get("exam"):
            continue
        when = datetime.fromisoformat(spec["exam"]).astimezone(zone)
        course = spec.get("subject", spec["name"])
        if when.date() >= local.date() and (course not in exams or when < exams[course]):
            exams[course] = when
    ahead = sorted(exams.items(), key=lambda kv: kv[1])[:COACH_PLAN_EXAMS]
    model = model_of(plan.settings)
    today = _days_after(plan.settings.start, local)
    _snapshots, _gains, state = _memory_timeline(plan, model, [today])
    syllabus = _syllabus_index(subscription)
    taught = []
    for spec in plan.specs:
        if spec.get("available_day", 0.0) > today or spec["name"] not in state:
            continue
        memory, last = state[spec["name"]]
        course = spec.get("subject", spec["name"])
        recall = model.retrievability(max(today - last, 0.0), memory.stability)
        taught.append((recall, course, spec["name"]))
    taught.sort()
    sessions = [
        {
            "time": f"{i['start']}-{i['end']}",
            "kind": i["what"],
            "of": i["label"],
            "over": i["state"] == "past",
        }
        for i in day_agenda(subscription, now)
        if i["kind"] == "study"
    ]
    return {
        "today": f"{local.date().isoformat()} ({_WEEKDAY_NAMES[local.weekday()]})",
        "exams": [
            {
                "course": course,
                "date": when.date().isoformat(),
                "days_left": (when.date() - local.date()).days,
            }
            for course, when in ahead
        ],
        "weakest_topics": [
            {
                "course": course,
                "topic": _topic_label(name, course),
                # The course program's topics for that week, when the student gave it (D81).
                "covers": _syllabus_of_name(name, course, syllabus)[:4],
                "recall_now": f"{5 * round(20 * recall)} %",
            }
            for recall, course, name in taught[:COACH_PLAN_TOPICS]
        ],
        "today_sessions": sessions,
    }


# Premium codes (D176): made by the owner, shown once, kept as a digest. A code may be
# used by as many plans as the owner says (one student, or a class); closing it stops
# new uses and leaves the plans that used it as they are.
PREMIUM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0, O, 1 or I to misread
PREMIUM_MOST_USES = 1000


def _premium_digest(code: str) -> str:
    return hashlib.sha256(re.sub(r"[^A-Z0-9]", "", str(code).upper()).encode()).hexdigest()


def make_premium_code(store: str | os.PathLike, label: str, most: int, now: datetime | None = None) -> str:
    """A new code, in three groups of four (60 bits), for `most` plans; the owner's
    `label` says for whom. Only its digest is kept: the code is shown once."""
    label = " ".join(str(label).split())[:80]
    if not label:
        raise InvalidInput(_("say who the code is for"))
    if not 1 <= int(most) <= PREMIUM_MOST_USES:
        raise InvalidInput(_("between 1 and {n} uses", n=PREMIUM_MOST_USES))
    code = "-".join("".join(secrets.choice(PREMIUM_ALPHABET) for _ in range(4)) for _ in range(3))
    with _store(store).connection() as db:
        db.execute(
            "INSERT INTO premium_codes (digest, label, uses, most, created) VALUES (?, ?, 0, ?, ?)",
            (
                _premium_digest(code),
                label,
                int(most),
                (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
            ),
        )
    return code


def premium_codes_view(store: str | os.PathLike, now: datetime | None = None) -> dict:
    """The owner's codes, newest first, each with its uses; and the month's AI spending
    against the cap, with the coach's answers this month."""
    now = now or datetime.now(UTC)
    with _store(store).connection() as db:
        rows = db.execute(
            "SELECT digest, label, uses, most, created FROM premium_codes ORDER BY created DESC"
        ).fetchall()
        (coach_calls,) = db.execute(
            "SELECT COUNT(*) FROM ai_usage WHERE feature = 'coach' AND created >= ?", (_ai._month_start(now),)
        ).fetchone()
    return {
        "codes": [
            {
                "id": str(digest)[:12],
                "label": label,
                "uses": int(uses),
                "most": int(most),
                "open": int(uses) < int(most),
                "created": format_date(datetime.fromisoformat(created).date(), "long"),
            }
            for digest, label, uses, most, created in rows
        ],
        "spent": format_number(round(_ai.spent_this_month(_store(store), now), 2)),
        "cap": format_number(round(_ai.monthly_cap(), 2)),
        "coach_calls": int(coach_calls),
        "free": COACH_FREE_A_DAY,
        "premium": COACH_PREMIUM_A_DAY,
    }


def close_premium_code(store: str | os.PathLike, ident: str) -> bool:
    """No more uses of the code whose digest starts with `ident`; the plans that used
    it keep their premium."""
    if not re.fullmatch(r"[0-9a-f]{12}", ident):
        return False
    with _store(store).connection() as db:
        return bool(
            db.execute(
                "UPDATE premium_codes SET most = uses WHERE substr(digest, 1, 12) = ?", (ident,)
            ).rowcount
        )


def redeem_premium(
    store: str | os.PathLike, token: str, code: str, now: datetime | None = None
) -> Subscription | None:
    """A student's code (D176): one use of it claimed, then premium kept with the plan.
    A plan with premium already uses none. None if the plan does not exist."""
    now = now or datetime.now(UTC)
    current = load_subscription(store, token)
    if current is None:
        return None
    if premium(current):
        return current
    plain = re.sub(r"[^A-Z0-9]", "", str(code).upper())
    if not 8 <= len(plain) <= 40:
        raise InvalidInput(_("that code is not known"))
    digest = _premium_digest(plain)
    with _store(store).connection() as db:
        if db.execute("SELECT 1 FROM premium_codes WHERE digest = ?", (digest,)).fetchone() is None:
            raise InvalidInput(_("that code is not known"))
        claimed = db.execute(
            "UPDATE premium_codes SET uses = uses + 1 WHERE digest = ? AND uses < most", (digest,)
        ).rowcount
    if not claimed:
        raise InvalidInput(_("that code has been used as many times as it can be"))
    given = {"since": now.astimezone(UTC).isoformat(timespec="seconds"), "code": digest[:12]}
    return update_subscription(
        store, token, lambda s: s if premium(s) else replace(s, options={**s.options, "premium": given})
    )


def premium_view(subscription: Subscription) -> dict:
    """What Settings says of premium (D176)."""
    kept = premium(subscription)
    since = None
    if kept and kept.get("since"):
        with contextlib.suppress(ValueError):
            since = format_date(datetime.fromisoformat(kept["since"]).date(), "long")
    return {"on": bool(kept), "since": since, "free": COACH_FREE_A_DAY, "most": COACH_PREMIUM_A_DAY}


# --------------------------------------------------------------------------- #
# AI, opt-in and capped (DECISIONS.md D12)


def ai_available() -> bool:
    """Whether this server can ask a model at all: a key, the SDK, a priced model
    (the tasks' and the review's, or the coach's, D176)."""
    return _ai.from_env() is not None or _ai.coach_from_env() is not None


def ai_on(subscription: Subscription) -> bool:
    return bool(subscription.options.get("ai"))


def set_ai(subscription: Subscription, on: bool) -> Subscription:
    """Turn AI on or off for a plan. Off is the default: what a student types is
    sent to a processor in the United States only after they say yes."""
    return replace(subscription, options={**subscription.options, "ai": bool(on)})


def understand_task(
    store: str | os.PathLike,
    subscription: Subscription,
    words: str,
    *,
    now: datetime | None = None,
    provider: _ai.Provider | None = None,
) -> dict:
    """A task typed in one line, as the new-task form's fields, to check before
    adding: read by rules, and by the model when AI is on and allowed. Where the
    model leaves a field empty, the rules' reading stays."""
    now = now or datetime.now(UTC)
    words = " ".join(str(words).split())[:200]
    if not words:
        raise InvalidInput(_("type the task in a few words"))
    tz = subscription.options["tz"]
    local = now.astimezone(_zone(tz))
    courses = course_names(subscription)
    guess = _ai.read_task(words, today=local.date(), courses=courses)
    by = "rules"
    model = (provider or _ai.from_env()) if ai_on(subscription) else None
    if model is not None:
        system, prompt = _ai.task_prompt(words, local, tz, courses)
        data = _ai.ask(
            _store(store),
            subscription.token,
            model,
            "task",
            system=system,
            prompt=prompt,
            schema=_ai.TASK_SCHEMA,
            max_tokens=1500,
            now=now,
        )
        read = _ai.task_from_answer(data, today=local.date(), courses=courses) if data else None
        if read is not None:
            guess = _ai.TaskGuess(
                name=read.name,
                due=read.due or guess.due,
                hours=read.hours or guess.hours,
                course=read.course or guess.course,
            )
            by = "ai"
    note = (
        _("Read by AI: check it before adding.")
        if by == "ai"
        else _("Read from your words: check it before adding.")
    )
    if not guess.due:
        note += " " + _("No date found: choose one.")
    return {**guess.as_dict(), "by": by, "note": note}


def review_with_ai(
    store: str | os.PathLike,
    subscription: Subscription,
    review: dict | None,
    *,
    now: datetime | None = None,
    provider: _ai.Provider | None = None,
) -> dict | None:
    """The weekly review with its paragraph written by the model, when AI is on and
    allowed; else as the rules wrote it. A paragraph is kept with the plan, keyed by
    the week, the language and the numbers it was written from, so a page view does
    not ask again, and a late report (new numbers) gets a new paragraph."""
    if not review or not review.get("planned") or not ai_on(subscription):
        return review
    now = now or datetime.now(UTC)
    lang = language_of(subscription)
    system, prompt = _ai.review_prompt(review, _LANGUAGE_NAMES.get(lang, "English"))
    key = f"{review['week']}:{lang}:{hashlib.sha256(prompt.encode()).hexdigest()[:12]}"
    kept = subscription.options.get("review_ai") or {}
    if key in kept:
        return {**review, "text": kept[key], "by": "ai"}
    model = provider or _ai.from_env()
    if model is None:
        return review
    data = _ai.ask(
        _store(store),
        subscription.token,
        model,
        "review",
        system=system,
        prompt=prompt,
        schema=_ai.REVIEW_SCHEMA,
        max_tokens=1500,
        now=now,
    )
    text = _ai.paragraph_from_answer(data) if data else None
    if text is None:
        return review

    def keep(current: Subscription) -> Subscription:
        recent = {k: v for k, v in (current.options.get("review_ai") or {}).items() if k >= review["week"]}
        return replace(current, options={**current.options, "review_ai": {**recent, key: text}})

    with contextlib.suppress(ServiceError):
        update_subscription(store, subscription.token, keep)
    return {**review, "text": text, "by": "ai"}


# --------------------------------------------------------------------------- #
# The pilot's measures (DECISIONS.md D15; docs/STRATEGY.md "How we will know")


def calibration(store: str | os.PathLike) -> dict:
    """How well predicted recall matched what students reported (Gate C of the
    engine review; the pilot reads it, D15): the mean log loss over every answer
    that tested a prediction, lower is better, None with no answers.

    - cards: each flashcard answer a day or more after the card's previous one,
      predicted by FSRS-4.5's defaults and by FSRS-6's (D52);
    - own: the answers given after a student's own parameters were fitted (D53),
      predicted by them and by FSRS-6's defaults: out of sample, as the fit never
      saw them;
    - self_tests: each self-test answered with how much was recalled (D25),
      against the recall the plan predicted for it;
    - exams: how the exams went (D56), beside the recall predicted for them, for
      the students who agreed to share it."""
    where = _store(store)
    six, old = MODELS["fsrs-6"], MODELS["fsrs-4.5"]
    sums: dict[str, list] = {"cards": [0.0, 0.0, 0, 0], "own": [0.0, 0.0, 0, 0], "self_tests": [0.0, 0, 0]}
    exams: dict[str, Any] = {"shared": [], "private": 0, "skipped": 0}
    for token in where.tokens():
        log = flashcards.answer_log(where, token, flashcards.MAX_CARDS)
        if log:
            histories = [[(e, g) for _, e, g in card] for card in log]
            a, n = fitting.losses(old, histories)
            b, _ = fitting.losses(six, histories)
            if n:
                c = sums["cards"]
                c[0], c[1], c[2], c[3] = c[0] + a, c[1] + b, c[2] + n, c[3] + 1
            own = flashcards.personal(where, token)
            if own is not None:
                after = [[at > own.at for at, _, _ in card] for card in log]
                a, n = fitting.losses(own.model, histories, after)
                b, _ = fitting.losses(six, histories, after)
                if n:
                    c = sums["own"]
                    c[0], c[1], c[2], c[3] = c[0] + a, c[1] + b, c[2] + n, c[3] + 1
        subscription = load_subscription(store, token)
        if subscription is None or subscription.plan is None:
            continue
        for record in subscription.options.get("exam_results", {}).values():
            if record.get("skipped"):
                exams["skipped"] += 1
            elif record.get("consent"):
                exams["shared"].append(
                    {
                        "predicted": record.get("predicted"),
                        "result": record["result"],
                        "mark": record["mark"],
                        "helped": record.get("helped", ""),
                    }
                )
            else:
                exams["private"] += 1
        answered = [
            s for s in PlanReport.from_dict(subscription.plan).history if s.kind in _REVIEW_KINDS and s.grade
        ]
        for s in answered:
            r = min(max(s.recall, 1e-4), 1 - 1e-4)
            sums["self_tests"][0] -= math.log(r) if s.grade != "again" else math.log(1 - r)
        if answered:
            sums["self_tests"][1] += len(answered)
            sums["self_tests"][2] += 1

    def mean(total: float, count: int) -> float | None:
        return total / count if count else None

    c, o, st = sums["cards"], sums["own"], sums["self_tests"]
    return {
        "cards": {
            "answers": c[2],
            "students": c[3],
            "fsrs-4.5": mean(c[0], c[2]),
            "fsrs-6": mean(c[1], c[2]),
        },
        "own": {"answers": o[2], "students": o[3], "own": mean(o[0], o[2]), "fsrs-6": mean(o[1], o[2])},
        "self_tests": {"answers": st[1], "students": st[2], "predicted": mean(st[0], st[1])},
        # After the exam (D56): the answers shared, without plan or course, by the
        # recall predicted; those kept private and those not given are only counted.
        "exams": {
            **exams,
            "shared": sorted(exams["shared"], key=lambda e: (e["predicted"] is None, e["predicted"] or 0.0)),
        },
    }


def engagement(store: str | os.PathLike, now: datetime | None = None) -> dict:
    """What the pilot reads week by week, from the event log, the plans and the
    network's tables, on the server: nothing runs in a student's browser.

    - plans: set up in all, and those set up in the last 30 days;
    - active: plans opened (a `visit`) in the last 7 and 30 days;
    - tips: plans that opened the tips page in the last 7 and 30 days (D62);
    - weekly: plans that opened a weekly note in the last 7 and 30 days (D64);
    - coach: plans that asked the coach in the last 7 days, and the questions by who answered (D65);
    - north star: plans with a session confirmed (done or hard, or a focus session
      logged) in the last 7 days, as a share of plans set up;
    - confirmed: planned sessions of the last 7 days reported done or hard, of
      those planned (unreported ones count as not confirmed);
    - return: of the plans set up at least 7 (30) days ago, those opened on the
      7th (30th) day after;
    - streaks: current streaks, and how many reach 3 and 7 days;
    - guardrail: plans whose last 7 days held more study (done, hard and logged)
      than their own weekly limit, which should stay at zero;
    - network, focus and AI: what was shared, cheered and spent."""
    now = now or datetime.now(UTC)
    where = _store(store)
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)
    events = where.all_events((now - timedelta(days=400)).isoformat(timespec="seconds"))
    started = {t: datetime.fromisoformat(at) for t, at, kind, _detail in events if kind == "start"}
    visits: dict[str, set[date]] = {}
    tips: dict[str, set[date]] = {}  # the tips page opened (D62)
    notes: dict[str, set[date]] = {}  # a weekly note opened (D64)
    asked: dict[str, set[date]] = {}  # a question to the coach (D65)
    questions = {"ai": 0, "rules": 0}  # in the last 7 days, by who answered
    for t, at, kind, detail in events:
        days = {"visit": visits, "tips": tips, "weekly": notes, "coach": asked}.get(kind)
        if days is not None:
            days.setdefault(t, set()).add(datetime.fromisoformat(at).date())
        if kind == "coach" and detail in questions and datetime.fromisoformat(at) >= week_ago:
            questions[detail] += 1
    tokens = where.tokens()

    def active(since: datetime, days: dict[str, set[date]] = visits) -> int:
        return sum(1 for t in tokens if any(d >= since.date() for d in days.get(t, ())))

    def returned(days: int) -> tuple[int, int]:
        eligible = [t for t in tokens if t in started and started[t] <= now - timedelta(days=days)]
        back = [t for t in eligible if (started[t] + timedelta(days=days)).date() in visits.get(t, set())]
        return len(back), len(eligible)

    planned = confirmed = over_limit = 0
    north = 0
    streaks = []
    for token in tokens:
        subscription = load_subscription(store, token)
        if subscription is None:
            continue
        sessions = progress_sessions(subscription)
        logged = [
            x for x in _progress.merge_logged(sessions, logged_sessions(store, subscription)) if not x.planned
        ]
        recent = [x for x in sessions if week_ago <= x.start <= now]
        planned += len(recent)
        done = [x for x in recent if x.confirmed]
        confirmed += len(done)
        logged_recent = [x for x in logged if x.start.date() >= week_ago.date()]
        if done or logged_recent:
            north += 1
        limit = {**DEFAULT_PREFERENCES, **subscription.options.get("preferences", {})}["weekly_hours"]
        studied = sum(x.minutes for x in done + logged_recent) / 60
        if limit and studied > limit:
            over_limit += 1
        if subscription.plan is not None:
            first = min(
                [date.fromisoformat(subscription.options["start"]), *(x.start.date() for x in logged)]
            )
            zone = _zone(subscription.options["tz"])
            streaks.append(_progress.streak(sessions + logged, first, now.astimezone(zone).date()).current)
    with where.connection() as db:

        def count(sql: str, *args: object) -> int:
            return int(db.execute(sql, args).fetchone()[0])

        since = week_ago.astimezone(UTC).isoformat()
        network = {
            "profiles": count("SELECT COUNT(*) FROM profiles"),
            "follows": count("SELECT COUNT(*) FROM follows WHERE status = 'accepted'"),
            "sessions_shared": count(
                "SELECT COUNT(*) FROM posts WHERE kind = 'session' AND visibility != 'me' AND created >= ?",
                since,
            ),
            "sessions_private": count(
                "SELECT COUNT(*) FROM posts WHERE kind = 'session' AND visibility = 'me' AND created >= ?",
                since,
            ),
            "explanations": count(
                "SELECT COUNT(*) FROM posts WHERE kind = 'explain' AND created >= ?", since
            ),
            "notes": count("SELECT COUNT(*) FROM posts WHERE kind = 'notes' AND created >= ?", since),
            # Flashcards (D29): answers in the last 7 days, and the students who gave them.
            "card_reviews": count("SELECT COUNT(*) FROM card_reviews WHERE at >= ?", since),
            "card_students": count("SELECT COUNT(DISTINCT token) FROM card_reviews WHERE at >= ?", since),
            # Groups with at least two members: a group of one is a goal, not a group (D28).
            "groups": count(
                "SELECT COUNT(*) FROM (SELECT grp FROM group_members WHERE joined IS NOT NULL "
                "GROUP BY grp HAVING COUNT(*) >= 2)"
            ),
            "kudos": count("SELECT COUNT(*) FROM kudos WHERE created >= ?", since),
            "comments": count("SELECT COUNT(*) FROM comments WHERE created >= ?", since),
            "reports_open": count("SELECT COUNT(DISTINCT target) FROM reports WHERE resolved = 0"),
            "timed": count(
                "SELECT COUNT(*) FROM posts WHERE kind = 'session' AND created >= ? "
                "AND json_extract(data, '$.timed')",
                since,
            ),
            "focus_checked": count(
                "SELECT COUNT(*) FROM posts WHERE kind = 'session' AND created >= ? "
                "AND json_extract(data, '$.checked')",
                since,
            ),
        }
    month = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    streaks.sort()
    return {
        "at": now.astimezone(UTC).isoformat(timespec="minutes"),
        "plans": len(tokens),
        "new_30": sum(1 for t in tokens if t in started and started[t] >= month_ago),
        "active_7": active(week_ago),
        "active_30": active(month_ago),
        "tips_7": active(week_ago, tips),
        "tips_30": active(month_ago, tips),
        "weekly_7": active(week_ago, notes),
        "weekly_30": active(month_ago, notes),
        "coach_7": active(week_ago, asked),
        "coach_questions_7": questions,
        "north_star": north,
        "planned_7": planned,
        "confirmed_7": confirmed,
        "return_7": returned(7),
        "return_30": returned(30),
        "streaks": {
            "median": streaks[len(streaks) // 2] if streaks else 0,
            "longest": streaks[-1] if streaks else 0,
            "at_least_3": sum(1 for x in streaks if x >= 3),
            "at_least_7": sum(1 for x in streaks if x >= 7),
        },
        "over_limit": over_limit,
        "network": network,
        "ai": {**_ai.usage(where, month), "cap": _ai.monthly_cap(), "available": ai_available()},
        "calibration": calibration(store),
    }


# --------------------------------------------------------------------------- #
# The exam forecast and the trajectory (DECISIONS.md D22 to D24)

_REVIEW_KINDS = ("review", "first review")


def _five(share: float) -> int:
    """A model's estimate as a percentage rounded to 5: no false precision (D24)."""
    return int(5 * round(100 * max(0.0, min(1.0, share)) / 5))


def _recall_at_exam(
    members: Sequence[dict], states: Mapping[str, tuple[MemoryState, float]], model: Model
) -> float:
    values = [
        model.retrievability(max(m["exam_day"] - states[m["name"]][1], 0.0), states[m["name"]][0].stability)
        for m in members
        if m["name"] in states
    ]
    return sum(values) / len(values) if values else 0.0


def session_gains(plan: PlanReport) -> dict[str, dict]:
    """For each self-test, its topic's predicted recall on exam day without it and
    with it, if no other review followed (D24). The memory states are carried
    through the whole timeline: reported sessions as reported (an answered self-test
    with the grade its answer gives, D25), the rest as planned, so a session under
    way (already in the plan's history) has its gain too."""
    exam_day = {s["name"]: s["exam_day"] for s in plan.specs}
    model = model_of(plan.settings)
    states = {
        s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in plan.specs
    }
    # Each topic's planned sessions, for "it comes back on …" (D125).
    coming: dict[str, list[SessionView]] = {}
    for session in sorted(plan.sessions, key=lambda x: x.start_day):
        coming.setdefault(session.title, []).append(session)
    out: dict[str, dict] = {}
    for session in sorted(plan.history + plan.sessions, key=lambda x: x.start_day):
        if session.title not in states or session.outcome in _NO_MEMORY:
            continue
        memory, last = states[session.title]
        elapsed = max(session.start_day - last, 0.0)
        exam = exam_day[session.title]
        after = model.review(memory, elapsed, _grade(session))
        if session.kind in _REVIEW_KINDS and session.start_day < exam:
            following = next(
                (x for x in coming.get(session.title, ()) if x.start_day > session.start_day), None
            )
            out[session_id(session)] = {
                "without": _five(model.retrievability(max(exam - last, 0.0), memory.stability)),
                "with": _five(model.retrievability(max(exam - session.start_day, 0.0), after.stability)),
                # The memory loop (D125): the chance of recalling it as the session
                # starts, whether it is a second pass on the same day, the memory
                # days it adds (stability after less before, as D82 counts them),
                # the day it then holds 90 % until, and when the topic comes back.
                "chance": _five(model.retrievability(elapsed, memory.stability)),
                "same_day": elapsed < 1.0,
                "added": after.stability - memory.stability,
                "holds": plan.settings.start + timedelta(days=session.start_day + after.stability),
                "next": following.start if following else None,
                # The exam's day, for the session's stakes (D128).
                "exam": plan.settings.start + timedelta(days=exam),
                # How long it then holds, for a topic's first month (D130).
                "stability": after.stability,
            }
        states[session.title] = (after, session.start_day)
    return out


def forecast(plan: PlanReport, now: datetime, weeks: Sequence[date] = ()) -> list[dict]:
    """Each exam still to come: its topics' predicted recall on exam day if the plan
    is followed and if nothing more were done, and, for each Monday in `weeks`, what
    the sessions reported by the end of that week had built (D24)."""
    zone = _zone(plan.settings.tz)
    start = plan.settings.start
    groups: dict[str, list[dict]] = {}
    for spec in plan.specs:
        groups.setdefault(spec.get("subject", spec["name"]), []).append(spec)
    model = model_of(plan.settings)
    current = _replay(plan.specs, plan.history, model)
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    out = []
    for subject in sorted(plan.subjects, key=lambda x: x.exam):
        exam = datetime.fromisoformat(subject.exam)
        members = groups.get(subject.name, [])
        if exam <= now or not members:
            continue
        path = []
        for first in weeks:
            end_of_week = datetime.combine(first + timedelta(days=7), time(0, 0), tzinfo=zone)
            if end_of_week > exam + timedelta(days=7):
                break
            day = _days_after(start, min(end_of_week, now.astimezone(zone)))
            states = _replay(plan.specs, [h for h in plan.history if h.start_day <= day], model)
            path.append(_five(_recall_at_exam(members, states, model)))
        out.append(
            {
                "name": subject.name,
                "when": format_date(exam.astimezone(zone).date(), "short"),
                "days": max(0, (exam.astimezone(zone).date() - now.astimezone(zone).date()).days),
                "with_plan": _five(subject.recall_at_exam),
                "if_stopped": _five(_recall_at_exam(members, current, model)),
                # self-tests behind with no report: not counted until answered (D44)
                "unknown": sum(
                    1 for h in plan.history if h.outcome == "unknown" and h.subject == subject.name
                ),
                "path": path,
                "topics": len(members),
                "color": colours.get(subject.name.casefold()),
            }
        )
    return out


RANGES = {"4": 4, "12": 12, "all": None}


def _diary_work(store: str | os.PathLike, subscription: Subscription) -> list[_trends.Logged]:
    zone = _zone(subscription.options["tz"])
    out = []
    for post in social.posts_of(_store(store), subscription.token, "session"):
        d = post.data
        out.append(
            _trends.Logged(
                start=datetime.combine(date.fromisoformat(post.day), time(12, 0), tzinfo=zone),
                minutes=int(d.get("minutes", 0)),
                effort=int(d.get("effort", 0)),
                progress=int(d.get("progress", 0)),
                course=str(d.get("course", "")),
                timed=bool(d.get("timed")),
                checked=bool(d.get("checked")),
                interruptions=int(d.get("interruptions", 0)),
                sid=str(d.get("sid") or ""),
            )
        )
    return out


def trends_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None, span: str = "12"
) -> dict:
    """The Trends page (D22): this week against the student's own 4-week average,
    the exam forecast, and week-by-week hours, sessions kept and study load, with
    hours per course, the share kept by part of day and focus over the range."""
    now = now or datetime.now(UTC)
    span = span if span in RANGES else "12"
    zone = _zone(subscription.options["tz"])
    local = now.astimezone(zone)
    sessions = progress_sessions(subscription)
    work = _diary_work(store, subscription)
    first = min([date.fromisoformat(subscription.options["start"]), *(x.start.date() for x in work)])
    # The averages need the weeks before the range too.
    length = RANGES[span]
    shown = _trends.mondays(first, local.date(), length)
    wider = _trends.mondays(first, local.date(), None if length is None else length + _trends.AVERAGE_WEEKS)
    weeks_all = _trends.weekly(sessions, work, wider, local)
    hours_avg = _trends.rolling([w.hours for w in weeks_all])
    load_avg = _trends.rolling([float(w.load) for w in weeks_all])
    offset = len(weeks_all) - len(shown)
    weeks = weeks_all[offset:]
    prefs = {**DEFAULT_PREFERENCES, **subscription.options.get("preferences", {})}
    hours_now, hours_before = _trends.against_average(weeks_all, "hours")
    load_now, load_before = _trends.against_average(weeks_all, "load")
    done_weeks = weeks_all[:-1]
    kept_recent = [w for w in done_weeks[-_trends.AVERAGE_WEEKS :] if w.planned_past]
    kept_earlier = [
        w for w in done_weeks[-2 * _trends.AVERAGE_WEEKS : -_trends.AVERAGE_WEEKS] if w.planned_past
    ]

    def share(ws: Sequence[_trends.Week]) -> int | None:
        planned = sum(w.planned_past for w in ws)
        return round(100 * sum(w.done for w in ws) / planned) if planned else None

    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    known = [name for name, _i in course_colours(plan)] if plan else []
    colours = {name.casefold(): index for name, index in course_colours(plan)} if plan else {}
    in_range = [x for x in work if x.start.date() >= shown[0]] if shown else []
    words = {"morning": _("morning"), "afternoon": _("afternoon"), "evening": _("evening")}
    run = _progress.streak(
        _progress.merge_logged(
            sessions, [_progress.Session(x.start, x.minutes, "logged", x.course, "done", x.sid) for x in work]
        ),
        first,
        local.date(),
    )
    return {
        "span": span,
        "spans": [("4", _("4 weeks")), ("12", _("12 weeks")), ("all", _("Semester"))],
        "since": format_date(shown[0], "short") if shown else "",
        "limit": prefs["weekly_hours"],
        "tiles": {
            "hours": {"value": hours_now, "average": hours_before},
            "load": {"value": load_now, "average": load_before},
            "kept": {"value": share(kept_recent), "before": share(kept_earlier)},
            "streak": {"value": run.current, "best": run.best},
            "direction": _trends.direction(
                [w.hours for w in done_weeks[-_trends.AVERAGE_WEEKS :]],
                [w.hours for w in done_weeks[-2 * _trends.AVERAGE_WEEKS : -_trends.AVERAGE_WEEKS]],
            ),
        },
        "weeks": [
            {
                "label": format_date(w.first, "day_short"),
                "first": format_date(w.first, "short"),
                "current": w.current,
                "hours": w.hours,
                "hours_avg": hours_avg[offset + i],
                "kept": w.kept,
                "done": w.done,
                "planned_past": w.planned_past,
                "load": w.load,
                "load_avg": load_avg[offset + i],
                "logged": w.logged,
                "effort": w.effort,
                "progress": w.progress,
                "answers": w.answers,
                "recall": w.recall,
            }
            for i, w in enumerate(weeks)
        ],
        "courses": [
            {"name": name, "hours": hours, "color": colours.get(name.casefold())}
            for name, hours in _trends.by_course(sessions, work, shown[0], local, known)
        ]
        if shown
        else [],
        "parts": [
            {
                "part": words[part],
                "done": done,
                "past": past,
                "kept": round(100 * done / past) if past else None,
            }
            for part, done, past in _trends.by_part_of_day(sessions, shown[0], local)
        ]
        if shown
        else [],
        "focus": {
            "logged": len(in_range),
            "timed": sum(x.timed for x in in_range),
            "checked": sum(x.checked for x in in_range),
            "uninterrupted": sum(x.checked and not x.interruptions for x in in_range),
            "effort": sum(x.effort for x in in_range) / len(in_range) if in_range else None,
        },
        "forecast": forecast(plan, now, shown) if plan else [],
    }


def climb_view(trend: dict) -> dict | None:
    """D100: the climb on Progress, from the Trends page's weeks over the whole
    semester (`trends_view(..., "all")`): the hours added up week by week, which only
    ever rise, so a short week reads as a step up, never as a drop; the flags passed
    on the way (`trends.MILESTONES`), the next one, and the biggest week. A flag passed
    this week is celebrated once. None before there is a week."""
    weeks = trend["weeks"]
    if not weeks:
        return None
    hours = [float(w["hours"]) for w in weeks]
    totals = _trends.climb(hours)
    passed = _trends.flags(totals)
    now = weeks[-1]["current"]
    fresh = [m for m, at in passed if now and at > len(weeks) - 1]
    biggest = max(range(len(hours)), key=lambda i: (hours[i], i))
    return {
        "total": totals[-1],
        "since": trend["since"],
        "this_week": hours[-1] if now else 0.0,
        "totals": totals,
        "passed": passed,
        "flags": len(passed),
        "next": _trends.next_milestone(totals[-1]),
        "new": fresh[-1] if fresh else None,
        "biggest": (
            {
                "hours": hours[biggest],
                "first": weeks[biggest]["first"],
                "now": now and biggest == len(weeks) - 1,
            }
            if len(weeks) > 1 and hours[biggest] > 0
            else None
        ),
        "texts": [
            _(
                "Week of {day}: {hours} h, {total} h in all",
                day=w["first"],
                hours=format_number(h),
                total=format_number(t),
            )
            for w, h, t in zip(weeks, hours, totals, strict=True)
        ],
    }


# -- insights (DECISIONS.md D80) ----------------------------------------------------


def _insight_inputs(
    store: str | os.PathLike, subscription: Subscription, local: datetime
) -> tuple[list[_insights.Start], list[_insights.Timed], list[_insights.SelfTest]]:
    """What the insights are computed from: the diary's timed sessions (and, for those
    started from the plan, the planned start), and the self-tests answered."""
    zone = local.tzinfo
    planned = {s.sid: s.start for s in progress_sessions(subscription) if s.sid}
    starts, timed = [], []
    for post in social.posts_of(_store(store), subscription.token, "session"):
        d = post.data
        if not d.get("timed") or not d.get("checked"):
            continue
        timed.append(
            _insights.Timed(
                day=date.fromisoformat(post.day),
                minutes=int(d.get("minutes", 0)),
                away=int(d.get("away_minutes", 0)),
                interruptions=int(d.get("interruptions", 0)),
            )
        )
        sid, started = str(d.get("sid") or ""), d.get("started")
        if sid in planned and started:
            starts.append(_insights.Start(planned[sid], datetime.fromisoformat(started).astimezone(zone)))
    tests = []
    if subscription.plan is not None:
        for s in PlanReport.from_dict(subscription.plan).history:
            if s.kind in _REVIEW_KINDS and s.grade in _insights.GRADE_SCORE:
                tests.append(
                    _insights.SelfTest(datetime.fromisoformat(s.start).astimezone(zone), s.recall, s.grade)
                )
    return starts, timed, tests


def insights_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """The Insights on Progress (D80): the pace first, then four measures, each in
    plain words, its line over the weeks, and how it is measured; a measure without
    enough data yet says how many more it needs. `today` is the one worth a line on
    Today, if any: a day off due or a pace above the limit, else an insight just
    unlocked or a record just set."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    local = now.astimezone(zone)
    today = local.date()
    starts, timed, tests = _insight_inputs(store, subscription, local)
    mondays = [
        _progress.monday(today) - timedelta(weeks=k) for k in range(_insights.SERIES_WEEKS - 1, -1, -1)
    ]
    sessions = progress_sessions(subscription)
    work = _diary_work(store, subscription)
    weeks = _trends.weekly(sessions, work, mondays, local)
    logged = [_progress.Session(x.start, x.minutes, "logged", x.course, "done", x.sid) for x in work]
    studied = {s.start.date() for s in _progress.merge_logged(sessions, logged) if s.confirmed}
    prefs = {**DEFAULT_PREFERENCES, **subscription.options.get("preferences", {})}
    pace = _insights.pace(weeks, prefs.get("weekly_hours"), _insights.days_in_a_row(studied, today))
    measures = [
        _insights.on_time(starts, mondays),
        _insights.focus(timed, mondays, today),
        _insights.recall_by_part(tests),
        _insights.against_forecast(tests),
    ]
    # When each measure's data came: an insight unlocked in the last two days is new.
    recent = today - timedelta(days=1)
    came = {
        "on_time": [x.started.date() for x in starts],
        "focus": [x.day for x in timed],
        "recall_time": [x.start.date() for x in tests],
        "forecast": [x.start.date() for x in tests],
    }
    cards = [_pace_card(pace, subscription.token)]
    for m in measures:
        card = _insight_card(m)
        if m["unlocked"]:
            fresh = sum(d >= recent for d in came[m["key"]])
            card["new"] = m["have"] - fresh < _insights.NEED[m["key"]]
        cards.append(card)
    first = None
    if pace["state"] in ("rest", "above"):
        first = cards[0]
    else:
        first = next((c for c in cards[1:] if c.get("record")), None) or next(
            (c for c in cards[1:] if c.get("new")), None
        )
    return {"cards": cards, "today": first}


def _pace_card(pace: dict, token: str) -> dict:
    hours = format_number(round(pace["hours"], 1))
    usual = format_number(round(pace["usual"], 1)) if pace["usual"] is not None else ""
    card = {
        "key": "pace",
        "title": _("Your pace"),
        "unlocked": True,
        "value": _("{n} h", n=hours),
        "caption": _("this week"),
        "state": pace["state"],
        "how": _(
            "Hours done this week (sessions reported done and sessions logged) against your weekly limit and "
            "the mean of your last four weeks, and days of study in a row. It never asks for more."
        ),
    }
    if pace["state"] == "rest":
        card["says"] = _n(
            "{n} day of study in a row. Plan a day off: a day with nothing planned keeps your streak.",
            "{n} days of study in a row. Plan a day off: a day with nothing planned keeps your streak.",
            pace["in_a_row"],
        )
        card["link"] = {"href": f"/p/{token}/settings/week#your-week", "words": _("Plan a day off")}
    elif pace["state"] == "above" and pace["limit"] and pace["hours"] > pace["limit"]:
        card["says"] = _(
            "Above your limit of {limit} h. Your plan keeps to it: the extra hours are yours to rest.",
            limit=format_number(pace["limit"]),
        )
    elif pace["state"] == "above":
        card["says"] = _("Well above your usual {usual} h. Keep an evening free this week.", usual=usual)
    elif pace["state"] == "start":
        card["says"] = _("Your usual pace shows after your first full week.")
    elif pace["hours"] < (pace["usual"] or 0):
        card["says"] = _("So far this week, against your usual {usual} h.", usual=usual)
    else:
        card["says"] = _("Close to your usual {usual} h: a pace you can keep.", usual=usual)
    return card


_INSIGHT_TITLES = {
    "on_time": "Starting on time",
    "focus": "Focus",
    "recall_time": "When you remember best",
    "forecast": "Your memory against the forecast",
}
_INSIGHT_HOW = {
    "on_time": (
        "Planned sessions started with the focus timer: on time is at most 10 minutes after the planned "
        "start; early counts too."
    ),
    "focus": (
        "Timed sessions: the time on the page against the time the timer ran. Time away is time off the "
        "page, not proof of distraction."
    ),
    "recall_time": (
        "Self-tests answered with how much you recalled, by the time they started. A part of the day is "
        "compared once it has 3 answers."
    ),
    "forecast": (
        "How often your self-tests found something remembered (any answer but nothing), against how often "
        "the plan expected. The plan predicts with an average memory, so a gap is a hint, not a grade."
    ),
}
_PARTS = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"}
_BEST_PART = {
    "morning": "Your self-tests go best in the morning.",
    "afternoon": "Your self-tests go best in the afternoon.",
    "evening": "Your self-tests go best in the evening.",
}


def _insight_card(m: dict) -> dict:
    key = m["key"]
    card: dict[str, Any] = {
        "key": key,
        "title": _(_INSIGHT_TITLES[key]),
        "unlocked": m["unlocked"],
        "how": _(_INSIGHT_HOW[key]),
    }
    if not m["unlocked"]:
        more = m["need"] - m["have"]
        card["percent"] = round(100 * m["have"] / m["need"])
        card["says"] = {
            "on_time": _n(
                "Start {n} more planned session with the timer to see this.",
                "Start {n} more planned sessions with the timer to see this.",
                more,
            ),
            "focus": _n("Time {n} more session to see this.", "Time {n} more sessions to see this.", more),
            "recall_time": _n(
                "Answer {n} more self-test to see this.", "Answer {n} more self-tests to see this.", more
            ),
            "forecast": _n(
                "Answer {n} more self-test to see this.", "Answer {n} more self-tests to see this.", more
            ),
        }[key]
        return card
    if key == "on_time":
        card["value"] = f"{m['share']} %"
        card["caption"] = _("started on time")
        card["says"] = (
            _("Most of your sessions start when you planned them.")
            if m["share"] >= 80 or not m["late_by"]
            else _(
                "When you start late, it is by about {n} min. Try opening Focus as the session begins.",
                n=m["late_by"],
            )
        )
    elif key == "focus":
        card["value"] = f"{m['share']} %"
        card["caption"] = _("of your timed study on the page")
        card["says"] = (
            _("Longest without leaving the page: {time}.", time=duration_words(m["longest"]))
            if m["longest"]
            else ""
        )
        if m["record"]:
            card["record"] = _(
                "New record: {time} without leaving the page.", time=duration_words(m["longest"])
            )
            card["celebrate"] = f"record-focus-{m['longest']}"
    elif key == "recall_time":
        parts = m["parts"]
        if m["best"]:
            card["value"] = _(_PARTS[m["best"]])
            card["caption"] = _("your self-tests' best time")
        card["rows_caption"] = _("Recalled on average, by part of the day")
        card["says"] = (
            _(_BEST_PART[m["best"]])
            + " "
            + _("The plan already puts sessions at the time you chose; you can change it in Settings.")
            if m["best"]
            else _("No clear difference yet between the parts of the day.")
        )
        card["rows"] = [
            {"part": _(_PARTS[p]), "percent": v["percent"], "n": v["n"]} for p, v in parts.items()
        ]
    elif key == "forecast":
        gap = m["gap"]
        card["value"] = f"+{gap}" if gap > 0 else ("≈" if gap == 0 else f"−{-gap}")
        card["caption"] = _("points against the forecast")
        said = _(
            "You remembered something in {r} % of your self-tests; the plan expected {e} %.",
            r=m["remembered"],
            e=m["expected"],
        )
        if gap > 0:
            said += " " + _("Your memory holds better than the plan assumes.")
        elif gap < 0:
            said += " " + _("Lower answers bring your next self-test sooner.")
        card["says"] = said
    series = [v for v in m.get("series", []) if v is not None]
    if len(series) >= 2:
        card["series"] = series
        card["trend"] = m.get("trend")
    return card


def agenda(subscription: Subscription, week: int, now: datetime | None = None) -> dict:
    """One week as a list of days, each with its busy events and sessions in time
    order: what a phone shows instead of a grid. Week 0 is the one holding `now`."""
    if subscription.plan is None:
        raise InvalidInput(_("there is no plan yet: add an exam or a deadline in the settings"))
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    first = local.date() + timedelta(days=7 * week)
    days = [first + timedelta(days=d) for d in range(7)]
    entries: dict[date, list[dict]] = {d: [] for d in days}

    def add(
        start: datetime, end: datetime, label: str, kind: str, sid: str | None = None, **extra: Any
    ) -> None:
        start, end = start.astimezone(zone), end.astimezone(zone)
        if start.date() in entries:
            entries[start.date()].append(
                {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}", "label": label, "kind": kind, "id": sid}
                | extra
            )

    exams = {x.exam for x in plan.subjects}
    changes = tuple(subscription.options.get("lecture_changes", ()))
    for e in plan.events:
        kind = (
            "exam"
            if e.start in exams and e.source not in ("commute", "lunch", "skipped")
            else e.source
            if e.source in ("typed", "commute", "lunch", "skipped", *TEST_KINDS)
            else "calendar"
        )
        label = (
            e.summary
            if kind == "exam"
            else test_label(kind, e.summary)
            if kind in TEST_KINDS
            else short_title(e.summary)
        )
        begin = datetime.fromisoformat(e.start)
        extra: dict[str, Any] = {}
        if kind in ("calendar", "skipped", *TEST_KINDS):
            # A class can be skipped from here too, without the calendar's script (D60).
            change = _lecture_change(changes, BusyEvent(begin, begin, e.summary), zone) or {}
            extra = {
                "at": e.start,
                "ref": lecture_ref(e.summary),
                "upcoming": begin > now,
                "weekly": not change.get("at"),
            }
        add(begin, datetime.fromisoformat(e.end), label, kind, **extra)
    for s in plan.history + plan.sessions:
        begin, end = datetime.fromisoformat(s.start), datetime.fromisoformat(s.end)
        add(begin, end, session_label(s), "study", session_id(s))
    return {
        "week": week,
        "days": [
            {
                "label": _when(datetime.combine(d, time(0, 0), tzinfo=zone), local).removesuffix(" 00:00"),
                "entries": sorted(entries[d], key=lambda x: x["start"]),
            }
            for d in days
        ],
    }


# --------------------------------------------------------------------------- #
# The assistant: deadlines, a weekly budget, days off, exam practice
# --------------------------------------------------------------------------- #

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Exam practice sized by the course (D46): 1.5 hours for every four weeks it was
# taught, at least 3; a twelve-week course keeps the 4.5 hours every exam had
# before. A course with no lectures in the timetable gets those 4.5 hours.
PRACTICE_HOURS_PER_WEEKS = (1.5, 4)
PRACTICE_MIN_HOURS = 3.0
PRACTICE_UNSIZED_HOURS = 4.5
MAX_PRACTICE_HOURS = 40.0
PRACTICE_CHOICES = (0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.0, 12.0)  # offered in Settings
# How much an exam weighs (D49): only between topics fading together.
EXAM_WEIGHTS = ((0.5, "Weighs less"), (1.0, "Usual weight"), (2.0, "Weighs more"))
_WEIGHT_VALUES = {w for w, _ in EXAM_WEIGHTS}


def weeks_taught(report: CalendarReport, course: str, exam: datetime) -> int:
    """The weeks (Monday to Sunday, local time) with a lecture of `course` in the
    timetable before its exam, including those before the plan starts."""
    zone = _zone(report.tz)
    mondays = set()
    for x in report.lectures:
        end = datetime.fromisoformat(x.end)
        if x.course.casefold() == course.casefold() and end < exam:
            local = end.astimezone(zone).date()
            mondays.add(local - timedelta(days=local.weekday()))
    return len(mondays)


def practice_hours_for(weeks: int) -> float:
    """Exam practice for a course taught `weeks` weeks (D46)."""
    if weeks <= 0:
        return PRACTICE_UNSIZED_HOURS
    hours, per = PRACTICE_HOURS_PER_WEEKS
    return max(PRACTICE_MIN_HOURS, hours * math.ceil(weeks / per))


def make_schedule(
    report: CalendarReport,
    subjects: Sequence[SubjectSpec] = (),
    *,
    tasks: Sequence[TaskSpec] = (),
    weekly_hours: float | None = None,
    rest_days: Sequence[str] = (),
    practice_days: float = 14.0,
    practice_hours: float | None = 4.5,
    practice: Mapping[str, float] | None = None,
    weights: Mapping[str, float] | None = None,
    retention: float = 0.9,
    lectures_as_topics: bool = True,
    done: Sequence[SessionView] = (),
    now: datetime | None = None,
    pins: Sequence[Mapping] = (),
    model: str = "fsrs-6",
    pauses: Sequence[Mapping] = (),
    quiz_ready: Sequence[str] = (),
) -> PlanReport:
    """The student's week, scheduled by the assistant's rules (`cps.assistant`):
    deadlines first when they get close, exam practice before each exam, self-testing
    on the taught material that is fading, work ahead otherwise, and free time
    when nothing needs it. Within `weekly_hours` a week, never on `rest_days` nor
    on the days of a pause the student set (D126).

    Exam practice is `practice_hours` before every exam, or with None sized by the
    weeks each course was taught (`practice_hours_for`, D46); `practice` gives a
    course its own hours, whichever applies otherwise. `weights` gives an exam a
    weight (`EXAM_WEIGHTS`, D49), used only between topics fading together.

    Instant, so it can be run again whenever anything changes: `done` and `now`
    continue from what already happened, as `continue_plan` does. A subject is
    "ready" when every one of its topics is predicted at `retention` or more at the
    exam.
    """
    if not subjects and not tasks:
        raise InvalidInput(_("add a subject with an exam, or a task with a deadline"))
    if weekly_hours is not None and weekly_hours <= 0:
        raise InvalidInput(_("the weekly hours must be positive, or left out for no limit"))
    unknown = [d for d in rest_days if d not in WEEKDAY_NAMES]
    if unknown:
        raise InvalidInput(
            _(
                "days off must be among {days}, not {wrong}",
                days=", ".join(WEEKDAY_NAMES),
                wrong=", ".join(unknown),
            )
        )
    if len(set(rest_days)) >= 7:
        raise InvalidInput(_("leave at least one day of the week for studying"))
    chosen = {str(k): v for k, v in (practice or {}).items()}
    for hours in (practice_hours, *chosen.values()):
        if hours is not None and not 0 <= float(hours) <= MAX_PRACTICE_HOURS:
            raise InvalidInput(_("exam practice must be 0 to {most} hours", most=f"{MAX_PRACTICE_HOURS:.0f}"))
    if practice_days < 0:
        raise InvalidInput(_("exam practice cannot be negative"))
    if model not in MODELS:
        raise InvalidInput(f"memory model must be one of {', '.join(MODELS)}, not {model!r}")
    if any(float(w) not in _WEIGHT_VALUES for w in (weights or {}).values()):
        raise InvalidInput(_("an exam weighs less, as usual or more"))
    names = [t.name.strip() for t in tasks]
    if any(not n for n in names) or len(set(n.casefold() for n in names)) != len(names):
        raise InvalidInput(_("every task needs a name of its own"))
    if any(not t.hours > 0 for t in tasks):
        raise InvalidInput(_("every task needs a positive number of hours"))
    zone = _zone(report.tz)
    dues = []
    for t in tasks:
        due = t.due_datetime(zone)
        day = _days_after(report.start, due)
        if day <= 0:
            raise ExamInPast(
                _(
                    "{name} is due ({when}) before the start of the plan",
                    name=t.name,
                    when=f"{due:%Y-%m-%d %H:%M}",
                )
            )
        dues.append((t, due, day))

    if subjects:
        settings, specs, report = _prepare(
            report, subjects, retention, 1, None, DEFAULT_FAILURE_PENALTY, lectures_as_topics, MODELS[model]
        )
    else:
        if not 0.5 <= retention < 1.0:
            raise InvalidInput("target recall must be at least 0.5 and below 1")
        specs = []
        settings = PlanSettings(
            start=report.start,
            tz=report.tz,
            retention=retention,
            window=1,
            seed=None,
            failure_penalty=DEFAULT_FAILURE_PENALTY,
            block_minutes=report.block_minutes,
            slots_per_day=report.slots_per_day,
            horizon_days=report.days,
        )
    latest_due = max((day for _, _, day in dues), default=0.0)
    if latest_due > report.days:
        report = _extend(report, math.ceil(latest_due - 1e-9))
    planned = {s.get("subject", s["name"]).casefold() for s in specs}
    partials = [p for p in _partials(report) if p["course"].casefold() in planned and p["exam_day"] > 0]
    settings = replace(
        settings,
        horizon_days=report.days,
        engine="assistant",
        preferences={
            "weekly_hours": weekly_hours,
            "rest_days": list(rest_days),
            "practice_days": practice_days,
            "practice_hours": practice_hours,
            "practice": {k: float(v) for k, v in chosen.items()},
            "weights": {str(k): float(v) for k, v in (weights or {}).items() if float(v) != 1.0},
            # The memory model the rules and the forecasts use (D52).
            "model": model,
            # The student's pauses (D126): their days are left free.
            "pauses": [dict(p) for p in pauses],
            # The courses with quizzes in class (D194): their recent weeks are kept ready.
            "quiz_ready": sorted({str(c) for c in quiz_ready}),
            # The weeks without classes the timetable has (D184), asked about on Today.
            "holidays": [
                {"from": a.isoformat(), "to": b.isoformat()}
                for a, b in holidays_in(report, planned, [datetime.fromisoformat(s["exam"]) for s in specs])
            ],
            # What sizing gives each course, kept for the settings page (D46); a
            # partial exam (D61) is sized like an exam, on the weeks taught before it.
            "practice_sized": {
                key: practice_hours_for(weeks_taught(report, course, datetime.fromisoformat(exam)))
                for key, (course, exam) in (
                    {
                        s.get("exam_key") or s.get("subject", s["name"]): (
                            s.get("subject", s["name"]),
                            s["exam"],
                        )
                        for s in specs
                    }
                    | {p["key"]: (p["course"], p["exam"]) for p in partials}
                ).items()
            },
            # Classes that are tests (D61): partial exams, practised for, and mock
            # exams, which count as practice.
            "partials": partials,
            "mocks": _mocks(report),
        },
    )
    views = tuple(
        TaskView(
            name=t.name.strip(),
            course=t.course.strip(),
            due=_iso(due),
            due_day=day,
            blocks=blocks_for_hours(t.hours, report.block_minutes),
            done=t.done,
        )
        for t, due, day in dues
    )
    known = {s["name"] for s in specs} | {t.name for t in views}
    known |= {f"Exam practice: {s['subject']}" for s in specs}
    known |= {f"Exam practice: {p['key']}" for p in partials}
    history = tuple(
        replace(x, index=i)
        for i, x in enumerate(sorted((x for x in done if x.title in known), key=lambda x: x.start_day))
    )
    after = _days_after(report.start, now.astimezone(zone)) if now is not None else -1.0
    return _assist(settings, specs, views, report.blocks + report.quick, report.events, history, after, pins)


def _assist(
    settings: PlanSettings,
    specs: Sequence[dict],
    tasks: Sequence[TaskView],
    blocks: tuple[BlockView, ...],
    events: tuple[EventView, ...],
    history: tuple[SessionView, ...],
    after_day: float,
    pins: Sequence[Mapping] = (),
) -> PlanReport:
    """Run the assistant on the blocks after `after_day`, from the state `history`
    leaves, and report it in the shape every front end already reads. `pins` are the
    sessions the student moved (`move_session`); the free blocks they overlap are
    left out, and a pin whose task, topic or exam is gone is dropped."""
    prefs = settings.preferences
    minutes = settings.block_minutes
    zone = _zone(settings.tz)
    # A finished task's pins go with it: nothing more is planned for it.
    names = {s["name"] for s in specs} | {t.name for t in tasks if not t.done}
    courses = {s.get("subject", s["name"]) for s in specs}
    pinned_views: list[BlockView] = []
    forced: list[Pin] = []
    for i, p in enumerate(pins):
        start, end = _local(p["start"], zone), _local(p["end"], zone)
        day = _days_after(settings.start, start)
        known = p["course"] in courses if p["kind"] == "practice" else p["title"] in names
        if day <= after_day or not known:
            continue
        origin = _days_after(settings.start, _local(p.get("from") or p["start"], zone))
        view = BlockView(slot=-1 - i, day=math.floor(day), start_day=day, start=_iso(start), end=_iso(end))
        pinned_views.append(view)
        hold = (min(origin, day), max(origin, day))
        forced.append(Pin(view.as_block(), p["kind"], p["title"], p["course"], *hold))

    def overlaps(b: BlockView) -> bool:
        lo, hi = datetime.fromisoformat(b.start), datetime.fromisoformat(b.end)
        return any(
            lo < datetime.fromisoformat(v.end) and datetime.fromisoformat(v.start) < hi for v in pinned_views
        )

    blocks = tuple(b for b in blocks if not overlaps(b))
    # A pause the student set (D126) leaves its days free: nothing is planned on
    # them, so nothing can be missed; a session they placed there themselves stays.
    paused = _progress.pause_days(pauses_of(prefs))
    if paused:
        blocks = tuple(b for b in blocks if _local(b.start, zone).date() not in paused)
    model = model_of(settings)
    states = _replay(specs, history, model)
    counted = [h for h in history if h.outcome != "skipped"]
    done_task: dict[str, int] = {}
    done_practice: dict[str, int] = {}
    for h in counted:
        if h.kind == "task":
            done_task[h.title] = done_task.get(h.title, 0) + 1
        elif h.kind == "practice":
            done_practice[h.subject] = done_practice.get(h.subject, 0) + 1
    weights = {k.casefold(): float(v) for k, v in (prefs.get("weights") or {}).items()}
    # Topics whose last self-test went poorly (forgotten, or some recalled): their
    # next self-test takes a full block (D50).
    last: dict[str, SessionView] = {}
    for h in sorted(history, key=lambda x: x.start_day):
        if h.kind in _REVIEW_KINDS and h.outcome not in _NO_MEMORY:
            last[h.title] = h
    poor = {name for name, h in last.items() if h.outcome == "lapsed" or h.grade in ("again", "hard")}
    # Courses with quizzes in class (D194): each week of lectures kept ready while recent.
    quizzed = {c.casefold() for c in prefs.get("quiz_ready") or ()}
    topics = [
        Topic(
            s["name"],
            s.get("subject", s["name"]),
            states[s["name"]][0],
            states[s["name"]][1],
            s.get("available_day", 0.0),
            s["exam_day"],
            s.get("about", s.get("subject", s["name"])),
            weights.get(s.get("subject", s["name"]).casefold(), 1.0),
            s["name"] in poor,
            s.get("available_day", 0.0) + QUIZ_READY_DAYS
            if s.get("subject", s["name"]).casefold() in quizzed
            else 0.0,
        )
        for s in specs
    ]

    def practice_blocks(course: str) -> int:
        """The student's hours for this course, else their hours for every exam,
        else the course's sized hours (D46); plans made before D46 have only the
        second."""
        chosen = {k.casefold(): v for k, v in (prefs.get("practice") or {}).items()}
        fixed = prefs.get("practice_hours")
        default = fixed if fixed is not None else (prefs.get("practice_sized") or {}).get(course)
        hours = chosen.get(course.casefold(), PRACTICE_UNSIZED_HOURS if default is None else default)
        return blocks_for_hours(hours, minutes) if hours > 0 else 0

    # A mock exam held in class (D61) is practice for its course's exam, done or to
    # come: the practice planned for that exam is shorter by its length.
    final_day = {s.get("subject", s["name"]): s["exam_day"] for s in specs if not s.get("exam_key")}
    for mock in prefs.get("mocks") or ():
        if mock["day"] < final_day.get(mock["course"], -1.0):
            course = mock["course"]
            done_practice[course] = done_practice.get(course, 0) + blocks_for_hours(mock["hours"], minutes)
    # One exam per key: a course's final, or a partial exam of it (D61).
    keyed = {s.get("exam_key") or s.get("subject", s["name"]): s["exam_day"] for s in specs}
    keyed |= {p["key"]: p["exam_day"] for p in prefs.get("partials") or ()}
    exams = {
        key: Exam(key, day, max(practice_blocks(key) - done_practice.get(key, 0), 0))
        for key, day in keyed.items()
    }
    work = [
        Task(t.name, t.due_day, 0 if t.done else max(t.blocks - done_task.get(t.name, 0), 0), t.course)
        for t in tasks
    ]
    first_weekday = settings.start.weekday()
    # The week's budget in minutes (D48): a quick self-test spends its own length.
    week_used: dict[int, int] = {}
    for h in counted:
        w = (first_weekday + h.day) // 7
        length = (datetime.fromisoformat(h.end) - datetime.fromisoformat(h.start)) / timedelta(minutes=1)
        week_used[w] = week_used.get(w, 0) + round(length)
    weekly = prefs["weekly_hours"]
    ahead = [b for b in blocks if b.start_day > after_day]
    result = assist(
        [b.as_block() for b in ahead if not b.quick],
        first_weekday=first_weekday,
        topics=topics,
        tasks=work,
        exams=list(exams.values()),
        preferences=Preferences(
            weekly_blocks=None if weekly is None else max(1, math.floor(weekly * 60 / minutes + 1e-9)),
            rest_weekdays=tuple(WEEKDAY_NAMES.index(d) for d in prefs["rest_days"]),
            practice_days=prefs["practice_days"],
            review_below=settings.retention,
            ready_below=max(QUIZ_READY_RECALL, settings.retention),
            model=model,
            weekly_minutes=None if weekly is None else max(minutes, round(weekly * 60)),
            block_minutes=minutes,
        ),
        week_used=week_used,
        pins=forced,
        quick=[Quick(b.as_block(), b.minutes, b.quick) for b in ahead if b.quick],
    )

    by_slot = {b.slot: b for b in (*blocks, *pinned_views)}
    sessions = tuple(
        SessionView(
            index=len(history) + i,
            subject=x.course or x.title,
            start=by_slot[x.block.slot].start,
            end=by_slot[x.block.slot].end,
            day=x.block.day,
            start_day=x.block.start_day,
            slot=x.block.slot,
            recall=x.recall,
            outcome="recalled",
            stability_before=x.stability_before,
            stability_after=x.stability_after,
            rationale=x.why,
            topic=x.title,
            kind=x.kind,
            detail=x.what,
            pinned=x.pinned,
        )
        for i, x in enumerate(result.sessions)
    )

    views, warnings = [], []
    groups: dict[str, list[dict]] = {}
    for s in specs:
        groups.setdefault(s.get("subject", s["name"]), []).append(s)
    for name, members in groups.items():
        finals = [result.topics[s["name"]] for s in members]
        recalls = [
            model.retrievability(max(s["exam_day"] - last, 0.0), memory.stability)
            for s, (memory, last) in zip(members, finals, strict=True)
        ]
        ok = [r >= settings.retention for r in recalls]
        ready = all(ok)
        # The subject's exam is its final, not a partial exam (D61).
        head = next((s for s in members if not s.get("exam_key")), members[0])
        if len(members) == 1:
            line = f"{'ready' if ready else 'not ready'}: {recalls[0]:.0%} predicted at the exam"
        else:
            line = (
                f"{'ready' if ready else 'not ready'}: {sum(ok)} of {len(members)} topics at "
                f"{settings.retention:.0%} or more at the exam"
            )
        views.append(
            SubjectView(
                name=name,
                exam=head["exam"],
                exam_day=head["exam_day"],
                stability=head["stability"],
                difficulty=head["difficulty"],
                prior=head["prior"],
                target=0.0,
                ready=ready,
                unreachable=False,
                recall_at_exam=sum(recalls) / len(recalls),
                stability_at_exam=min(result.topics[s["name"]][0].stability for s in members),
                first_review=next((x.start for x in history + sessions if x.subject == name), None),
                topics=len(members),
                topics_ready=sum(ok),
                status_line=line,
            )
        )
        if not ready:
            warnings.append(
                _n(
                    "{name}: {below} of {total} topics is predicted below {target} at the exam. "
                    "More hours a week, or fewer days off, would change that.",
                    "{name}: {below} of {total} topics are predicted below {target} at the exam. "
                    "More hours a week, or fewer days off, would change that.",
                    len(members) - sum(ok),
                    name=name,
                    below=len(members) - sum(ok),
                    total=len(members),
                    target=f"{settings.retention:.0%}",
                )
            )
        if result.practice_left.get(name):
            warnings.append(
                _n(
                    "{name}: {n} block of exam practice did not fit before the exam.",
                    "{name}: {n} blocks of exam practice did not fit before the exam.",
                    result.practice_left[name],
                    name=name,
                )
            )

    task_views = []
    for t in tasks:
        mine = [
            x for x in history + sessions if x.kind == "task" and x.title == t.name and x.outcome != "skipped"
        ]
        left = 0 if t.done else result.task_left.get(t.name, 0)
        task_views.append(
            replace(t, scheduled=len(mine), finish=mine[-1].start if mine else None, at_risk=left > 0)
        )
        if left:
            warnings.append(
                _n(
                    "{name}: {n} block of work does not fit before it is due ({when}). "
                    "More hours a week, fewer days off or an earlier start would change that.",
                    "{name}: {n} blocks of work do not fit before it is due ({when}). "
                    "More hours a week, fewer days off or an earlier start would change that.",
                    left,
                    name=t.name,
                    when=t.due[:16].replace("T", " "),
                )
            )
    return PlanReport(
        settings=settings,
        subjects=tuple(views),
        specs=tuple(dict(s) for s in specs),
        sessions=sessions,
        history=history,
        blocks=blocks,
        events=events,
        warnings=tuple(warnings),
        tasks=tuple(task_views),
    )


# --------------------------------------------------------------------------- #
# A course's topics, from its syllabus, shared with classmates (D81)
# --------------------------------------------------------------------------- #

# Each course's topics are kept in the plan as `options["topics"][course_key]`:
# {"course": name, "items": [{"title", "week", "lecture", "monday"}], "from": the
# classmate's list taken, if any}. "monday" is the teaching week the topic falls in,
# found in the timetable when the topics are saved.
TOPICS_PREVIEW = 3  # topics a classmate's list shows before it is taken
TOPICS_TEXT_MOST = 20_000  # characters of pasted text read: a long syllabus's program


def course_key(name: str) -> str:
    """A course's name as two timetables of one university write it alike: lower
    case, single spaces."""
    return " ".join(name.casefold().split())


def topic_courses(subscription: Subscription) -> list[str]:
    """The courses a plan keeps topics for: its subjects, in order."""
    return [str(s["name"]) for s in subscription.subjects if s.get("name")]


def _topic_course(subscription: Subscription, index: int) -> str:
    courses = topic_courses(subscription)
    if not 0 <= index < len(courses):
        raise InvalidInput(_("There is no such course in your plan."))
    return courses[index]


def _lecture_spans(subscription: Subscription, course: str) -> list[tuple[str, str]]:
    """The course's lectures in the timetable, as (start, end) in ISO, in order; none
    without a timetable file."""
    if subscription.plan is None or subscription.ics_text is None:
        return []
    settings = PlanReport.from_dict(subscription.plan).settings
    changes = tuple(subscription.options.get("lecture_changes", ()))
    return sorted(
        {
            (x.start, x.end)
            for x in _lectures(subscription.ics_text, settings.start, settings.tz, changes)
            if x.course.casefold() == course.casefold()
        }
    )


def _teaching(subscription: Subscription, course: str) -> tuple[list[date], list[float], list[int]]:
    """The course's teaching weeks in the timetable, in order: their Mondays and
    their lecture hours, and the week (from 1) of each lecture. Lectures as
    `_topics` counts them, by the day each ends; none without a timetable file."""
    taught = _lecture_spans(subscription, course)
    if not taught:
        return [], [], []
    zone = _zone(PlanReport.from_dict(subscription.plan or {}).settings.tz)
    mondays: list[date] = []
    hours: list[float] = []
    weeks: list[int] = []
    for begin, end in taught:
        first, last = datetime.fromisoformat(begin), datetime.fromisoformat(end).astimezone(zone)
        monday = last.date() - timedelta(days=last.weekday())
        if not mondays or mondays[-1] != monday:
            mondays.append(monday)
            hours.append(0.0)
        hours[-1] += (last - first) / timedelta(hours=1)
        weeks.append(len(mondays))
    return mondays, hours, weeks


def _placed(subscription: Subscription, course: str, topics: Sequence[_syllabus.Topic]) -> list[dict]:
    """The topics with the Monday of the teaching week each falls in (or None) and,
    for a topic that runs over more weeks (a chapter of a list without weeks, D187),
    the Monday of the last as "until"."""
    mondays, hours, weeks = _teaching(subscription, course)
    places = _syllabus.place(topics, hours, weeks, mondays)
    ends = _syllabus.covers(topics, places, len(mondays))
    out = []
    for t, w, end in zip(topics, places, ends, strict=True):
        item = {**t.to_dict(), "monday": mondays[w - 1].isoformat() if w is not None else None}
        if w is not None and end is not None and end > w:
            item["until"] = mondays[end - 1].isoformat()
        out.append(item)
    return out


def _weeks_of(item: Mapping, mondays: Sequence[date] = ()) -> list[date]:
    """The Mondays of the teaching weeks a saved topic is taught in: its own and, up
    to its "until" (D187), those of the teaching weeks between (`mondays`), else
    every seventh day."""
    if not item.get("monday"):
        return []
    first = date.fromisoformat(item["monday"])
    if not item.get("until"):
        return [first]
    last = date.fromisoformat(item["until"])
    between = [m for m in mondays if first <= m <= last]
    return between or [first + timedelta(weeks=k) for k in range((last - first).days // 7 + 1)]


_SECTION_NAMES = {
    "content": "course content",
    "outcomes": "learning outcomes",
    "assessment": "assessment",
    "prerequisites": "prerequisites",
    "reading": "reading",
    "methods": "teaching methods",
}


def _course_facts(text: str) -> dict:
    """What a whole course page says beside its topics (D83): which sections it has,
    its assessment's parts and weights, its ECTS credits and their hours of work."""
    facts: dict[str, Any] = {}
    found = _syllabus.sections(text)
    if found:
        facts["sections"] = [name for name in _SECTION_NAMES if name in found]
    parts = _syllabus.assessment(text)
    if parts:
        facts["assessment"] = [
            {"label": p.label, "percent": p.percent, **({"weeks": list(p.weeks)} if p.weeks else {})}
            for p in parts
        ]
    credit = _syllabus.credits(text)
    if credit is not None:
        facts["credits"] = credit
    hours = _syllabus.hours(text)
    if hours is not None:
        facts["hours"] = hours  # hours of classes in the semester (D90)
    return facts


def _facts_view(facts: Mapping) -> dict:
    """A course page's facts in the student's words."""
    out: dict[str, Any] = {}
    if facts.get("sections"):
        out["sections"] = [_(_SECTION_NAMES[n]) for n in facts["sections"] if n in _SECTION_NAMES]
    if facts.get("assessment"):
        out["assessment"] = "; ".join(f"{p['label']} {p['percent']} %" for p in facts["assessment"])
    if facts.get("credits"):
        credit = float(facts["credits"])
        low, high = _syllabus.workload(credit)
        out["credits"] = _(
            "{n} ECTS credits: about {low} to {high} hours of work in all, lectures included.",
            n=format_number(credit if credit % 1 else int(credit)),
            low=low,
            high=high,
        )
    if facts.get("hours"):
        hours = float(facts["hours"])
        out["hours"] = _n(
            "{hours} hour of classes in the semester.",
            "{hours} hours of classes in the semester.",
            round(hours),
            hours=format_number(hours if hours % 1 else int(hours)),
        )
    return out


def _topic_rows(items: Sequence[Mapping]) -> list[dict]:
    """Topics as a page lists them: the title and when it is taught."""
    rows = []
    for item in items:
        monday = date.fromisoformat(item["monday"]) if item.get("monday") else None
        until = date.fromisoformat(item["until"]) if item.get("until") else None
        when = ""
        if monday and until:  # a chapter over several weeks (D187)
            when = _(
                "Weeks of {first} to {last}",
                first=format_date(monday, "day_short"),
                last=format_date(until, "day_short"),
            )
        elif monday:
            when = _("Week of {day}", day=format_date(monday, "day_short"))
        rows.append(
            {
                "title": item["title"],
                "when": when,
                "week": item.get("week"),
                "lecture": item.get("lecture"),
                "assessment": item.get("kind") == "assessment",
                "material": item.get("material") or "",
            }
        )
    return rows


def _topics_text(items: Sequence[Mapping]) -> str:
    """Saved topics as lines the reader gives back, in the student's language."""
    return _syllabus.to_text(
        [_syllabus.Topic.from_dict(dict(i)) for i in items], week_word=_("Week"), lecture_word=_("Lecture")
    )


def _university(store: str | os.PathLike, token: str) -> str:
    profile = social.get_profile(_store(store), token)
    return profile.university if profile is not None else ""


def topics_overview(store: str | os.PathLike, subscription: Subscription) -> list[dict]:
    """Each course of the plan, with how many topics it has, whether they are
    shared, and how many classmates' lists wait for it."""
    university = _university(store, subscription.token)
    shared = social.shared_by(_store(store), subscription.token)
    kept = subscription.options.get("topics") or {}
    out = []
    for index, course in enumerate(topic_courses(subscription)):
        key = course_key(course)
        items = (kept.get(key) or {}).get("items") or []
        offers = (
            social.classmates_topics(_store(store), subscription.token, university, key) if not items else []
        )
        out.append(
            {
                "index": index,
                "course": course,
                "count": len(items),
                "shared": key in shared,
                "offers": len(offers),
            }
        )
    return out


def course_topics_view(
    store: str | os.PathLike, subscription: Subscription, index: int, text: str | None = None
) -> dict:
    """A course's topics page: what is saved, a preview of `text` read as the
    student pasted it, and the lists classmates shared for the course."""
    course = _topic_course(subscription, index)
    key = course_key(course)
    kept = (subscription.options.get("topics") or {}).get(key) or {}
    items = kept.get("items") or []
    university = _university(store, subscription.token)
    preview = None
    if text is not None:
        read = _syllabus.read(_syllabus.course_text(text))
        preview = {
            "rows": _topic_rows(_placed(subscription, course, read)),
            "count": len(read),
            "full": len(read) == _syllabus.MAX_TOPICS,
            "facts": _facts_view(_course_facts(text)),
        }
    offers = [
        {
            "id": o.id,
            "titles": [t["title"] for t in o.topics[:TOPICS_PREVIEW]],
            "more": max(0, len(o.topics) - TOPICS_PREVIEW),
            "count": len(o.topics),
            "uses": o.uses,
            "taken": kept.get("from") == o.id,
        }
        for o in social.classmates_topics(_store(store), subscription.token, university, key)
    ]
    return {
        "index": index,
        "course": course,
        "rows": _topic_rows(items),
        "text": text if text is not None else _topics_text(items),
        "preview": preview,
        "shared": key in social.shared_by(_store(store), subscription.token),
        "university": university,
        "offers": offers,
        "timetable": bool(_teaching(subscription, course)[0]),
        "facts": _facts_view(kept.get("facts") or {}),
    }


def save_course_topics(
    store: str | os.PathLike,
    subscription: Subscription,
    index: int,
    text: str,
    *,
    share: bool,
    now: datetime | None = None,
) -> Subscription:
    """Keep the topics read from `text` for a course (an empty text clears them),
    and offer them to classmates at the student's university when `share`; not
    sharing withdraws a list shared before."""
    course = _topic_course(subscription, index)
    key = course_key(course)
    read = _syllabus.read(_syllabus.course_text(text))
    before = (subscription.options.get("topics") or {}).get(key) or {}
    topics = {k: v for k, v in (subscription.options.get("topics") or {}).items() if k != key}
    if read:
        # A whole course page gives its assessment and credits too (D83); the topics'
        # own lines, saved again, keep the ones read before, and a topic kept by its
        # title keeps its schedule's day and reading (D90), which its line does not say.
        facts = _course_facts(text) or before.get("facts") or {}
        earlier = {i["title"]: i for i in before.get("items") or ()}
        read = [_kept_details(t, earlier.get(t.title)) for t in read]
        topics[key] = {
            "course": course,
            "items": _placed(subscription, course, read),
            "from": None,
            "facts": facts,
        }
    university = _university(store, subscription.token)
    if share and read and university:
        social.share_topics(
            _store(store), subscription.token, university, key, course, [t.to_dict() for t in read], now
        )
    else:
        social.unshare_topics(_store(store), subscription.token, key)
    return replace(subscription, options={**subscription.options, "topics": topics})


def _kept_details(topic: _syllabus.Topic, item: Mapping | None) -> _syllabus.Topic:
    if item is None:
        return topic
    return replace(
        topic,
        day=topic.day or item.get("day") or None,
        material=topic.material or item.get("material") or None,
    )


def take_classmates_topics(
    store: str | os.PathLike, subscription: Subscription, index: int, list_id: str
) -> dict:
    """A classmate's list for the course, taken: counted once, and placed in the
    student's own timetable, ready for `with_course_topics`."""
    course = _topic_course(subscription, index)
    kept = (subscription.options.get("topics") or {}).get(course_key(course)) or {}
    try:
        taken = social.take_topics(
            _store(store), subscription.token, list_id, counted=kept.get("from") == list_id
        )
    except social.SocialError as exc:
        raise InvalidInput(str(exc)) from exc
    read = [_syllabus.Topic.from_dict(t) for t in taken.topics][: _syllabus.MAX_TOPICS]
    social.unshare_topics(_store(store), subscription.token, course_key(course))
    return {"course": course, "items": _placed(subscription, course, read), "from": taken.id}


def with_course_topics(subscription: Subscription, entry: Mapping) -> Subscription:
    """The plan with a course's topics (`take_classmates_topics`) in place of its own."""
    topics = {**(subscription.options.get("topics") or {}), course_key(str(entry["course"])): dict(entry)}
    return replace(subscription, options={**subscription.options, "topics": topics})


# --------------------------------------------------------------------------- #
# A class's link: one student's courses, taken by the whole class (D104)
# --------------------------------------------------------------------------- #

CLASS_PREVIEW = 3  # topics a course shows on the link's page


def _class_courses(subscription: Subscription) -> list[dict]:
    """What a plan's class link offers: each of its courses that has topics, or what
    a course page said of it (assessment, credits, hours), in the plan's order."""
    kept = subscription.options.get("topics") or {}
    out = []
    for course in topic_courses(subscription):
        entry = kept.get(course_key(course)) or {}
        items = [i for i in entry.get("items") or () if i.get("title")]
        facts = entry.get("facts") or {}
        if items or facts:
            out.append({"course": course, "items": items, "facts": facts})
    return out


def _class_source(store: str | os.PathLike, code: str) -> tuple[Subscription, list[dict]] | None:
    """The plan a class link offers, read now, and its courses; None for a link that
    does not work: replaced, stopped, its plan deleted, or nothing left to offer."""
    owner = social.class_link_owner(_store(store), code)
    source = load_subscription(store, owner) if owner else None
    if source is None:
        return None
    courses = _class_courses(source)
    return (source, courses) if courses else None


def class_link_view(store: str | os.PathLike, subscription: Subscription) -> dict:
    """The class link on Courses (D104): its code once made, how many plans took
    it, and what it offers."""
    courses = _class_courses(subscription)
    current = social.current_class_link(_store(store), subscription.token)
    return {
        "code": current[0] if current else None,
        "uses": current[1] if current else 0,
        "courses": [c["course"] for c in courses],
        "topics": sum(len(c["items"]) for c in courses),
    }


def make_class_link(
    store: str | os.PathLike, subscription: Subscription, *, renew: bool = False, now: datetime | None = None
) -> str:
    """A class link for the plan's courses, or (`renew`) a new one in place of it."""
    if not _class_courses(subscription):
        raise InvalidInput(_("add a course's topics first: the link offers them"))
    return social.class_link(_store(store), subscription.token, renew=renew, now=now)


def stop_class_link(store: str | os.PathLike, subscription: Subscription) -> None:
    social.stop_class_link(_store(store), subscription.token)


def class_page_view(store: str | os.PathLike, code: str) -> dict | None:
    """What a class link shows whoever opens it (D104): its courses, each with its
    number of topics and the first few, and the university and programme its maker's
    profile names. Never who made it, nor anything of their plan but its courses."""
    found = _class_source(store, code)
    if found is None:
        return None
    source, courses = found
    profile = social.get_profile(_store(store), source.token)
    return {
        "code": code,
        "university": profile.university if profile else "",
        "programme": profile.programme if profile else "",
        "courses": [
            {
                "course": c["course"],
                "count": len(c["items"]),
                "titles": [i["title"] for i in c["items"][:CLASS_PREVIEW]],
                "more": max(0, len(c["items"]) - CLASS_PREVIEW),
                "facts": bool(c["facts"]),
            }
            for c in courses
        ],
    }


def class_take_view(store: str | os.PathLike, subscription: Subscription, code: str) -> dict | None:
    """A class link opened with a plan (D104): each of its courses matched to one of
    the plan's by the words their names share, as a semester's program is (D90),
    and ticked when that course has no topics of its own yet."""
    found = _class_source(store, code)
    shown = class_page_view(store, code)
    if found is None or shown is None:
        return None
    source, courses = found
    mine = topic_courses(subscription)
    kept = subscription.options.get("topics") or {}
    rows = []
    for i, j in enumerate(_matches([c["course"] for c in courses], mine)):
        own = (kept.get(course_key(mine[j])) or {}) if j is not None else {}
        rows.append(
            {
                **shown["courses"][i],
                "index": i,
                "mine": mine[j] if j is not None else None,
                "own_topics": len(own.get("items") or ()),
                "taken": own.get("from") == f"class:{code}",
            }
        )
    return {
        **shown,
        "rows": rows,
        "matched": sum(r["mine"] is not None for r in rows),
        "own": source.token == subscription.token,
        "profile": social.get_profile(_store(store), subscription.token) is not None,
    }


def take_class_program(
    store: str | os.PathLike, subscription: Subscription, code: str, picks: Collection[int]
) -> dict[str, dict]:
    """The picked courses of a class link (by their place on the link), each with its
    topics placed on the plan's own weeks and what its course page said: the entries
    for `with_class_program`, keyed by the plan's course. A plan's first take of a link
    counts as one use of it."""
    found = _class_source(store, code)
    if found is None:
        raise InvalidInput(_("this class link does not work any more"))
    source, courses = found
    if source.token == subscription.token:
        raise InvalidInput(_("this is your own class's link"))
    mine = topic_courses(subscription)
    kept = subscription.options.get("topics") or {}
    entries: dict[str, dict] = {}
    for i, j in enumerate(_matches([c["course"] for c in courses], mine)):
        if j is None or i not in picks:
            continue
        course, offered = mine[j], courses[i]
        read = [_syllabus.Topic.from_dict(t) for t in offered["items"]][: _syllabus.MAX_TOPICS]
        before = kept.get(course_key(course)) or {}
        entries[course_key(course)] = {
            "course": course,
            "items": _placed(subscription, course, read),
            "from": f"class:{code}",
            "facts": offered["facts"] or before.get("facts") or {},
        }
    if not entries:
        raise InvalidInput(_("tick at least one of your courses"))
    if not any((e or {}).get("from") == f"class:{code}" for e in kept.values()):
        social.count_class_use(_store(store), code)
    for key in entries:  # the topics are the class's now, no longer this plan's own list
        social.unshare_topics(_store(store), subscription.token, key)
    return entries


def with_class_program(subscription: Subscription, entries: Mapping[str, Mapping]) -> Subscription:
    """The plan with the courses a class link brought (`take_class_program`) in place
    of their own topics."""
    topics = {**(subscription.options.get("topics") or {}), **{k: dict(v) for k, v in entries.items()}}
    return replace(subscription, options={**subscription.options, "topics": topics})


# --------------------------------------------------------------------------- #
# A semester's program in one document, pasted or uploaded as a PDF (D90)
# --------------------------------------------------------------------------- #

SEMESTER_TEXT_MOST = _pdftext.MAX_CHARS  # a booklet's text: about sixty pages
PDF_MOST = _pdftext.MAX_BYTES
SEMESTER_ROWS = 6  # topics a found course shows before it is saved
_PDF_ERRORS = {
    "large": "That PDF is larger than 10 MB.",
    "not_pdf": "That file is not a PDF.",
    "locked": "That PDF is locked with a password. Open it, copy its text and paste it instead.",
    "unreadable": "That PDF could not be read. Open it, copy its text and paste it instead.",
    "empty": "That PDF has no text to read: it may be a scan. Copy its text and paste it instead.",
    "missing": "PDFs cannot be read on this server. Open it, copy its text and paste it instead.",
}
# Words a course's name shares with any other: left out when two names are compared.
_NAME_WORDS = frozenset(
    {
        *("and", "of", "the", "a", "an", "to", "for", "in", "on", "introduction", "intro"),
        *("de", "la", "le", "les", "des", "du", "et", "d", "l", "di", "del", "della", "dei", "e"),
        *("y", "en", "el", "los", "las", "und", "der", "die", "das", "van", "het", "voor"),
    }
)


def pdf_text(data: bytes) -> tuple[str, str | None]:
    """The text of an uploaded PDF (D90), and a note when only its first pages were
    read; the file itself is not kept."""
    try:
        found = _pdftext.text(data)
    except _pdftext.PdfError as exc:
        raise InvalidInput(_(_PDF_ERRORS[exc.reason])) from exc
    note = _("Only its first {n} pages were read.", n=found.read) if found.read < found.pages else None
    return found.text, note


# D134: why a file of notes gave no text, said to the student with what to do instead.
_NOTES_ERRORS = {
    "large": "That file is larger than 10 MB: send a chapter at a time.",
    "photo": "Photos of notes are not read yet: only typed text is. Send the PDF or the document instead.",
    "old_office": "That is an old Word or PowerPoint file: save it as .docx, .pptx or PDF and send it again.",
    "unknown": "That file is not one the app reads: save it as PDF, Word, PowerPoint or text.",
    "not_pdf": "That file is not one the app reads: save it as PDF, Word, PowerPoint or text.",
    "unreadable": "That file could not be read. Open it, copy its text and paste it instead.",
    "locked": _PDF_ERRORS["locked"],
    "empty": "That file has no text to read: a scan or handwriting is not read. Paste the text instead.",
    "missing": _PDF_ERRORS["missing"],
}
NOTES_FILE_MOST = _notefiles.MAX_BYTES


def notes_file_text(name: str, data: bytes) -> tuple[str, str | None]:
    """The text of a file of notes a student sends to make flashcards (D134), and a
    note when only part of it was read; read on this server, the file not kept."""
    try:
        found = _notefiles.text(name, data)
    except _notefiles.NotesError as exc:
        raise InvalidInput(_(_NOTES_ERRORS.get(exc.reason, _NOTES_ERRORS["unreadable"]))) from exc
    note = None
    if found.read < found.parts:
        if found.kind == "pdf":
            note = _("Only its first {n} pages were read.", n=found.read)
        else:
            note = _("Only its first {n} slides were read.", n=found.read)
    elif found.cut:
        note = _(
            "Long notes: the first {n} characters are read; send the rest as another file.",
            n=format_number(_notefiles.MAX_CHARS, 0),
        )
    return found.text, note


# A course's number written as a Roman numeral, "Economics I" in a program and "Economics 1"
# in the timetable (AUDIT 124): read as its figure when it is the name's last word, where a
# course's number goes; elsewhere "i" and "v" can be words ("Analisi e i modelli").
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8"}
_ROMAN |= {"ix": "9", "x": "10"}


def _stems(name: str) -> set[str]:
    words = re.findall(r"[^\W_]+", _syllabus.folded(name))
    if len(words) > 1 and words[-1] in _ROMAN:
        words[-1] = _ROMAN[words[-1]]
    return {w[:5] for w in words if w not in _NAME_WORDS}


def _matches(names: Sequence[str], courses: Sequence[str]) -> list[int | None]:
    """The plan's course each name of a program document is most likely to be, by
    the words they share ("Algebra 3" and "ALGEBRA 3 - CM"), each course given once,
    to the closest name first; None below half the words in common."""
    pairs = []
    for i, name in enumerate(names):
        a = _stems(name)
        for j, course in enumerate(courses):
            b = _stems(course)
            if a and b:
                score = len(a & b) / len(a | b)
                if score >= 0.5:
                    pairs.append((score, i, j))
    out: list[int | None] = [None] * len(names)
    taken: set[int] = set()
    for _score, i, j in sorted(pairs, key=lambda x: -x[0]):
        if out[i] is None and j not in taken:
            out[i], taken = j, taken | {j}
    return out


def program_text(text: str) -> str:
    """A program's text as the import keeps it between reading and saving: plain line
    breaks, no blank space around it, at most `SEMESTER_TEXT_MOST` characters."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()[:SEMESTER_TEXT_MOST]


def digest(text: str) -> str:
    """A short fingerprint of a program's text: a save checks it is the text read."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def program_view(
    store: str | os.PathLike, subscription: Subscription, text: str | None = None, note: str | None = None
) -> dict:
    """The semester import (D90): each course a program document holds, with its
    topics on their weeks, its assessments and its facts, and the course of the
    plan it is most likely to be."""
    courses = topic_courses(subscription)
    university = _university(store, subscription.token)
    view: dict[str, Any] = {
        "options": [{"index": i, "course": c} for i, c in enumerate(courses)],
        "text": text or "",
        "digest": digest(text) if text else "",
        "note": note,
        "university": university,
        "found": None,
    }
    if not text or not text.strip():
        return view
    found = _syllabus.courses(text, courses)
    chosen = _matches([c.name for c in found], courses)
    rows = []
    for n, (part, choice) in enumerate(zip(found, chosen, strict=True)):
        topics = _syllabus.read(_syllabus.course_text(part.text))
        placed = (
            _placed(subscription, courses[choice], topics)
            if choice is not None
            else [{**t.to_dict(), "monday": None} for t in topics]
        )
        listed = _topic_rows(placed)
        rows.append(
            {
                "n": n,
                "name": part.name,
                "choice": choice,
                "topics": sum(1 for t in topics if t.kind != "assessment"),
                "rows": listed[:SEMESTER_ROWS],
                "more": max(0, len(listed) - SEMESTER_ROWS),
                "dated": [r for r in listed if r["assessment"]],
                "facts": _facts_view(_course_facts(part.text)),
                "empty": not topics and not _course_facts(part.text),
            }
        )
    view["found"] = rows
    return view


def save_program(
    store: str | os.PathLike,
    subscription: Subscription,
    text: str,
    choices: Mapping[int, int],
    *,
    share: bool,
    now: datetime | None = None,
) -> tuple[Subscription, int]:
    """Each course of a program document saved to the course of the plan the
    student chose for it (`choices`, by its place in the document): its topics as
    `save_course_topics` keeps them; a course with no topic list keeps its own
    topics and takes the document's assessment, credits and hours. Returns the plan
    and how many courses were saved."""
    courses = topic_courses(subscription)
    found = _syllabus.courses(text, courses)
    saved = 0
    for n, part in enumerate(found):
        index = choices.get(n)
        if index is None or not 0 <= index < len(courses):
            continue
        if _syllabus.read(_syllabus.course_text(part.text)):
            subscription = save_course_topics(store, subscription, index, part.text, share=share, now=now)
            saved += 1
            continue
        facts = _course_facts(part.text)
        if facts:
            key = course_key(courses[index])
            entry = (subscription.options.get("topics") or {}).get(key) or {
                "course": courses[index],
                "items": [],
                "from": None,
            }
            topics = {**(subscription.options.get("topics") or {}), key: {**entry, "facts": facts}}
            subscription = replace(subscription, options={**subscription.options, "topics": topics})
            saved += 1
    return subscription, saved


def _syllabus_index(subscription: Subscription) -> dict[tuple[str, str], list[str]]:
    """Topic titles by course and teaching week ("05 Oct", as in a session's
    topic), and by course and "before" for the weeks begun before the plan began."""
    kept = subscription.options.get("topics") or {}
    if not kept or subscription.plan is None:
        return {}
    start = PlanReport.from_dict(subscription.plan).settings.start
    index: dict[tuple[str, str], list[str]] = {}
    for key, entry in kept.items():
        for item in entry.get("items") or ():
            if item.get("kind") == "assessment":
                continue
            # Each week a topic is taught in: a chapter's every week (D187).
            for monday in _weeks_of(item):
                index.setdefault((key, _week_label(monday)), []).append(item["title"])
                if monday < start:  # a week begun before the plan: some of it was taught before
                    before = index.setdefault((key, "before"), [])
                    if item["title"] not in before:
                        before.append(item["title"])
    return index


_WEEK_TOPIC = re.compile(r"^week of (.+?)(?: \(\d+ of \d+\))?$")


def _syllabus_of(s: SessionView, index: Mapping[tuple[str, str], list[str]]) -> list[str]:
    """The syllabus topics a session reviews, from the week of lectures it is about."""
    if not index or s.kind == "task" or not s.topic:
        return []
    return _syllabus_of_name(s.topic, s.subject, index)


def topics_nudge(store: str | os.PathLike, subscription: Subscription) -> dict | None:
    """A course without topics for which a classmate shared some: one line on
    Today, until the student takes them or adds their own."""
    for course in topics_overview(store, subscription):
        if not course["count"] and course["offers"]:
            return {"index": course["index"], "course": course["course"]}
    return None


# --------------------------------------------------------------------------- #
# Your memory: strength, the map of topics, memory days added, recall reps (D82)
# --------------------------------------------------------------------------- #


def _memory_timeline(
    plan: PlanReport, model: Model, ends: Sequence[float]
) -> tuple[list[list[float]], list[tuple[float, float]], dict[str, tuple[MemoryState, float]]]:
    """One pass over the plan's history, as `_replay` makes it: the stabilities of
    the topics taught by each day of `ends`, each review's day and the stability it
    added, and every topic's state after the last review before the last of `ends`."""
    state = {
        s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in plan.specs
    }
    taught = {s["name"]: s.get("available_day", 0.0) for s in plan.specs}
    snapshots: list[list[float]] = []
    gains: list[tuple[float, float]] = []
    reviews = sorted(
        (x for x in plan.history if x.outcome not in _NO_MEMORY and x.title in state),
        key=lambda x: x.start_day,
    )
    i = 0
    for end in ends:
        while i < len(reviews) and reviews[i].start_day <= end:
            session = reviews[i]
            memory, last = state[session.title]
            after = model.review(memory, session.start_day - last, _grade(session))
            gains.append((session.start_day, after.stability - memory.stability))
            state[session.title] = (after, session.start_day)
            i += 1
        snapshots.append([m.stability for name, (m, _) in state.items() if taught[name] <= end])
    return snapshots, gains, state


def recall_week(subscription: Subscription, now: datetime | None = None) -> dict | None:
    """The first week (Monday to Sunday) in which the student recalled every self-test
    they answered, at least `RECALL_WEEK_LEAST` of them (D130), when that week is the
    one just past; None otherwise. Said once, the week after; never counted down to."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    this_monday = local.date() - timedelta(days=local.weekday())
    weeks: dict[date, list[str]] = {}
    for session in plan.history:
        if session.kind in ("review", "first review") and session.grade in _GRADES:
            day = datetime.fromisoformat(session.start).astimezone(zone).date()
            monday = day - timedelta(days=day.weekday())
            if monday < this_monday:
                weeks.setdefault(monday, []).append(session.grade)
    for monday in sorted(weeks):
        grades = weeks[monday]
        if len(grades) >= RECALL_WEEK_LEAST and "again" not in grades:
            if monday == this_monday - timedelta(days=7):
                return {"answered": len(grades), "monday": format_date(monday, "day_short")}
            return None  # the first such week was earlier: said then, not again
    return None


def learning_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict | None:
    """Your memory on Progress (D82): the median strength of the taught topics and
    its weeks, today's map of every topic in four levels, the memory days the
    weeks' reviews added and the weeks' recall reps. None before there is a plan
    with a taught topic."""
    if subscription.plan is None:
        return None
    now = now or datetime.now(UTC)
    plan = PlanReport.from_dict(subscription.plan)
    model = model_of(plan.settings)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    start = plan.settings.start
    today = _days_after(start, local)
    mondays = _trends.mondays(start, local.date(), _learning.WEEKS)
    ends = [
        min(_days_after(start, datetime.combine(m + timedelta(days=7), time(0, 0), tzinfo=zone)), today)
        for m in mondays
    ]
    snapshots, gains, state = _memory_timeline(plan, model, ends)
    taught = [s for s in plan.specs if s.get("available_day", 0.0) <= today]
    if not taught:
        return None
    syllabus = _syllabus_index(subscription)
    topics = []
    for spec in taught:
        memory, last = state[spec["name"]]
        topics.append(
            _learning.Topic(
                course=spec.get("subject", spec["name"]),
                name=spec["name"],
                recall=model.retrievability(max(today - last, 0.0), memory.stability),
                stability=memory.stability,
            )
        )
    series = [_learning.strength(s) for s in snapshots]
    added = _learning.by_week([(start + timedelta(days=day), gain) for day, gain in gains], mondays)
    reps = [
        _learning.Rep(s.start.date(), "self-test" if s.kind in _learning.SELF_TESTS else "exercise")
        for s in progress_sessions(subscription)
        if s.confirmed and s.kind in (*_learning.SELF_TESTS, *_learning.PRACTICE) and s.start <= local
    ]
    reps += _card_reps(store, subscription, mondays, zone)
    weekly_reps = _learning.reps_by_week(reps, mondays)
    labels = [format_date(m, "day_short") for m in mondays]
    current = [i == len(mondays) - 1 for i in range(len(mondays))]
    tiles = _learning.topic_map(topics)
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    for course in tiles["courses"]:
        course["color"] = colours.get(course["course"].casefold())
        for t in course["topics"]:
            t["label"] = _topic_label(t["name"], course["course"])
            t["syllabus"] = _syllabus_of_name(t["name"], course["course"], syllabus)
    now_strength = _learning.strength([t.stability for t in topics]) or 0.0
    first = next((v for v in series if v is not None), None)
    return {
        "strength": round(now_strength),
        "milestone": _learning.milestone(now_strength),
        # The first week's strength, when the weeks shown have grown it.
        "first": format_number(round(first, 1)) if first is not None and now_strength > first else None,
        "growth": _learning.growth(series),
        "series": [round(v, 1) if v is not None else None for v in series],
        "added": [round(v) for v in added],
        "added_week": round(added[-1]) if added else 0,
        "reps": weekly_reps,
        "reps_week": weekly_reps[-1],
        "labels": labels,
        "current": current,
        "mondays": [m.isoformat() for m in mondays],
        "map": tiles,
        "topics": len(topics),
    }


def _card_reps(
    store: str | os.PathLike, subscription: Subscription, mondays: Sequence[date], zone: ZoneInfo
) -> list[_learning.Rep]:
    """A rep for each flashcard answered in the weeks shown, on its week's Monday."""
    out = []
    where = _store(store)
    for first in mondays:
        begin = datetime.combine(first, time(0, 0), tzinfo=zone)
        count = flashcards.reviews_since(where, subscription.token, begin) - flashcards.reviews_since(
            where, subscription.token, begin + timedelta(days=7)
        )
        out += [_learning.Rep(first, "card")] * count
    return out


def _topic_label(name: str, course: str) -> str:
    """A topic as a tile names it: "week of 05 Oct", or the course itself."""
    rest = name.removeprefix(f"{course} · ")
    return rest if rest != name else course


def _syllabus_of_name(name: str, course: str, index: Mapping[tuple[str, str], list[str]]) -> list[str]:
    """The syllabus topics of a plan topic, from the week of lectures it is about."""
    rest = name.removeprefix(f"{course} · ")
    week = _WEEK_TOPIC.match(rest)
    label = week.group(1) if week else ("before" if rest.startswith("taught before") else "")
    return index.get((course_key(course), label), []) if label else []


# --------------------------------------------------------------------------- #
# Courses: each course's weeks, done and to come, its workload, its cards by
# week and its materials (DECISIONS.md D86, D87, D88)
# --------------------------------------------------------------------------- #

LINKS_MOST = 30  # material links one course keeps
LINK_LIMITS = {"title": 80, "url": 500}


def _course_index(subscription: Subscription, course: str) -> int | None:
    return next(
        (i for i, c in enumerate(topic_courses(subscription)) if course_key(c) == course_key(course)), None
    )


def _week_name(monday: date, n: int) -> str:
    return _("Week {n} · {day}", n=n, day=format_date(monday, "day_short"))


def _this_week(mondays: Sequence[date], today: date) -> date | None:
    """The latest teaching week begun by `today`, or None before the first."""
    begun = [m for m in mondays if m <= today]
    return begun[-1] if begun else None


def _week_choice(
    subscription: Subscription, course: str, now: datetime, chosen: str | None = None
) -> dict[str, Any]:
    """The course's teaching weeks as a form offers them, and the one chosen: the
    one asked for, else the week in course today (D87)."""
    if _course_index(subscription, course) is None:
        return {"weeks": [], "week": ""}
    mondays = _teaching(subscription, course)[0]
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    options = [{"value": m.isoformat(), "label": _week_name(m, n)} for n, m in enumerate(mondays, start=1)]
    known = {o["value"] for o in options}
    if chosen is not None:
        week = chosen if chosen in known else ""
    else:
        current = _this_week(mondays, today)
        week = current.isoformat() if current else ""
    return {"weeks": options, "week": week}


def _credits(subscription: Subscription, course: str) -> float | None:
    """The course's ECTS credits: as the student typed them, else as its pasted
    course page said (D83)."""
    key = course_key(course)
    typed = (subscription.options.get("credits") or {}).get(key)
    if typed:
        return float(typed)
    facts = ((subscription.options.get("topics") or {}).get(key) or {}).get("facts") or {}
    return float(facts["credits"]) if facts.get("credits") else None


def set_course_credits(subscription: Subscription, index: int, value: str) -> Subscription:
    """The course's ECTS credits as the student knows them (D86); empty takes them
    off (the course page's, if any, count again)."""
    key = course_key(_topic_course(subscription, index))
    credits = dict(subscription.options.get("credits") or {})
    text = str(value or "").strip().replace(",", ".")
    if not text:
        credits.pop(key, None)
    else:
        try:
            number = float(text)
        except ValueError:
            raise InvalidInput(_("credits are a number, from 1 to 30")) from None
        if not 1 <= number <= 30:
            raise InvalidInput(_("credits are a number, from 1 to 30"))
        credits[key] = round(number * 2) / 2
    return replace(subscription, options={**subscription.options, "credits": credits})


def _study_minutes(plan: PlanReport, course: str) -> tuple[int, int]:
    """Minutes of the plan's study for a course: done (sessions past, not skipped)
    and to come."""
    done = sum(
        round((datetime.fromisoformat(x.end) - datetime.fromisoformat(x.start)) / timedelta(minutes=1))
        for x in plan.history
        if course_key(x.subject) == course_key(course) and x.outcome != "skipped"
    )
    ahead = sum(
        round((datetime.fromisoformat(x.end) - datetime.fromisoformat(x.start)) / timedelta(minutes=1))
        for x in plan.sessions
        if course_key(x.subject) == course_key(course)
    )
    return done, ahead


# A plan begun this many days or more after a course's first lecture counts the course
# from the plan's own first day (D186): the study before it was never in the plan.
LATE_START_DAYS = 7


def _late_share(plan: PlanReport, course: str, spans: Sequence[tuple[str, str]]) -> float | None:
    """For a plan begun `LATE_START_DAYS` or more after the course's first lecture
    (D186), the part of the course from the plan's first day on: the days from it to
    the course's last exam over the days from the first lecture, else the part of its
    lecture hours still to come; None for a plan begun before or soon after it."""
    if not spans:
        return None
    zone = _zone(plan.settings.tz)
    start = plan.settings.start
    first = min(datetime.fromisoformat(b).astimezone(zone).date() for b, _e in spans)
    if (start - first).days < LATE_START_DAYS:
        return None
    exams = [
        datetime.fromisoformat(s["exam"]).astimezone(zone).date()
        for s in plan.specs
        if course_key(s.get("subject", "")) == course_key(course)
    ]
    if exams and max(exams) > first:
        return min(1.0, max(0.0, (max(exams) - start).days / (max(exams) - first).days))
    hours = [(datetime.fromisoformat(e) - datetime.fromisoformat(b)) / timedelta(hours=1) for b, e in spans]
    ahead = sum(
        h
        for (b, _e), h in zip(spans, hours, strict=True)
        if datetime.fromisoformat(b).astimezone(zone).date() >= start
    )
    return ahead / sum(hours) if sum(hours) else None


def course_workload(subscription: Subscription, course: str) -> dict | None:
    """The workload check (D86): the hours a course's ECTS credits stand for (25 to
    30 each, the ECTS Users' Guide), against its lectures in the timetable and the
    plan's study for it, done and to come. Information, never a demand: the credits
    are a typical student's workload, not this one's. For a plan begun a week or
    more into the course (D186), from the plan's first day: its lectures since then,
    against the credits' share of the course that was still ahead (`_late_share`).
    None without credits or a plan."""
    credit = _credits(subscription, course)
    if credit is None or subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    low, high = _syllabus.workload(credit)
    spans = _lecture_spans(subscription, course)
    share = _late_share(plan, course, spans)
    zone = _zone(plan.settings.tz)
    if share is not None:
        low, high = round(low * share), round(high * share)
        spans = [
            (b, e)
            for b, e in spans
            if datetime.fromisoformat(b).astimezone(zone).date() >= plan.settings.start
        ]
    lectures = round(
        sum((datetime.fromisoformat(e) - datetime.fromisoformat(b)) / timedelta(hours=1) for b, e in spans)
    )
    done, ahead = _study_minutes(plan, course)
    study = round((done + ahead) / 60)
    total = lectures + study
    verdict = "below" if total < low else "above" if total > high else "within"
    since = format_date(plan.settings.start, "short") if share is not None else ""
    if since:
        words = {
            "below": _(
                "Since {day}: {total} h, {gap} h under what the credits suggest for those weeks.",
                day=since,
                total=total,
                gap=low - total,
            ),
            "within": _(
                "Since {day}: {total} h, within what the credits suggest for those weeks.",
                day=since,
                total=total,
            ),
            "above": _(
                "Since {day}: {total} h, more than the credits suggest for those weeks.",
                day=since,
                total=total,
            ),
        }[verdict]
    else:
        words = {
            "below": _(
                "{total} h in all, {gap} h under what the credits suggest.", total=total, gap=low - total
            ),
            "within": _("{total} h in all: within what the credits suggest.", total=total),
            "above": _("{total} h in all: more than the credits suggest.", total=total),
        }[verdict]
    top = max(high, total) * 1.1
    # The syllabus's hours of classes (D90): what the credits leave for the student's
    # own study, and that over the course's weeks of lectures.
    facts = ((subscription.options.get("topics") or {}).get(course_key(course)) or {}).get("facts") or {}
    own = None
    if facts.get("hours"):
        # The syllabus speaks of the whole course, whenever the plan began.
        low_all, high_all = _syllabus.workload(credit)
        classes = round(float(facts["hours"]))
        weeks = len(_teaching(subscription, course)[0])
        own_low, own_high = max(0, low_all - classes), max(0, high_all - classes)
        own = {
            "classes": classes,
            "says": _(
                "The syllabus counts {classes} h of classes: the rest, {low} to {high} h, is your own study.",
                classes=classes,
                low=own_low,
                high=own_high,
            ),
            "weekly": _(
                "About {low} to {high} h of your own a week, over its {weeks} weeks of lectures.",
                low=math.floor(own_low / weeks),
                high=math.ceil(own_high / weeks),
                weeks=weeks,
            )
            if weeks
            else "",
        }
    return {
        "own": own,
        "credits": format_number(credit if credit % 1 else int(credit)),
        "low": low,
        "high": high,
        "lectures": lectures,
        "study": study,
        "done": round(done / 60),
        "total": total,
        "verdict": verdict,
        "says": words,
        # The plan's first day, in words, for a plan begun partway through (D186).
        "since": since,
        # The bar, drawn as SVG on the server: widths in percent of the scale.
        "bar": {
            "lectures": round(100 * lectures / top, 1),
            "study": round(100 * study / top, 1),
            "low": round(100 * low / top, 1),
            "high": round(100 * high / top, 1),
        },
    }


def _week_states(
    subscription: Subscription, course: str, mondays: Sequence[date], today: date, now: datetime
) -> list[dict]:
    """Each teaching week of a course: taught or to come, its syllabus topics, the
    plan's reviews of it done and to come, and today's predicted recall."""
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    kept = (subscription.options.get("topics") or {}).get(course_key(course)) or {}
    items = kept.get("items") or []
    parts = (kept.get("facts") or {}).get("assessment") or []
    recall: dict[str, float] = {}
    if plan is not None:
        model = model_of(plan.settings)
        local = now.astimezone(_zone(plan.settings.tz))
        day = _days_after(plan.settings.start, local)
        _snap, _gains, state = _memory_timeline(plan, model, [day])
        for spec in plan.specs:
            if spec.get("available_day", 0.0) <= day:
                memory_state, last = state[spec["name"]]
                recall[spec["name"]] = model.retrievability(max(day - last, 0.0), memory_state.stability)
    out = []
    for n, monday in enumerate(mondays, start=1):
        base = f"{course} · week of {_week_label(monday)}"
        names = [name for name in recall if name == base or name.startswith(base + " (")]
        done = (
            sum(
                1
                for x in plan.history
                if x.topic.startswith(base) and x.outcome != "skipped" and x.kind != "task"
            )
            if plan
            else 0
        )
        ahead = sum(1 for x in plan.sessions if x.topic.startswith(base)) if plan else 0
        when = (
            "now" if monday <= today < monday + timedelta(days=7) else "taught" if monday < today else "ahead"
        )
        level = None
        if names:
            value = min(recall[name] for name in names)
            level = {"recall": round(100 * value), "level": _learning.level(value)}
        own = [i for i in items if i.get("monday") == monday.isoformat()]
        # A chapter begun in an earlier week and still taught in this one (D187).
        going = [
            i
            for i in items
            if i.get("until")
            and i.get("kind") != "assessment"
            and i["monday"] < monday.isoformat() <= i["until"]
        ]
        out.append(
            {
                "n": n,
                "monday": monday.isoformat(),
                "label": _week_name(monday, n),
                "state": when,
                "topics": [_("{topic} (continued)", topic=i["title"]) for i in going]
                + [i["title"] for i in own if i.get("kind") != "assessment"],
                # A schedule's mock exam or midterm in its week, and an assessment's
                # part held "in weeks 3 and 10" (D90); what the schedule says to read.
                "assessments": [i["title"] for i in own if i.get("kind") == "assessment"]
                + [p["label"] for p in parts if n in (p.get("weeks") or ())],
                "readings": list(dict.fromkeys(i["material"] for i in own if i.get("material"))),
                "done": done,
                "ahead": ahead,
                "memory": level,
            }
        )
    return out


def _exam_of(plan: PlanReport, course: str, now: datetime) -> dict | None:
    """The course's next exam, else its last one."""
    exams = sorted(
        {
            datetime.fromisoformat(s["exam"])
            for s in plan.specs
            if course_key(s.get("subject", "")) == course_key(course)
        }
    )
    if not exams:
        return None
    exam = next((e for e in exams if e >= now), exams[-1])
    local = exam.astimezone(_zone(plan.settings.tz))
    days = (local.date() - now.astimezone(local.tzinfo).date()).days
    return {
        "date": format_date(local.date(), "long"),
        "days": days,
        "says": _n("in {n} day", "in {n} days", days)
        if days > 0
        else (_("today") if days == 0 else _("done")),
    }


def _links(subscription: Subscription, course: str) -> list[dict]:
    return list(((subscription.options.get("materials") or {}).get(course_key(course))) or [])


def add_course_link(subscription: Subscription, index: int, form: Mapping[str, str]) -> Subscription:
    """A link to a course's material (D88): slides, the course on the learning
    platform, a shared folder. Kept with the plan, shown to the student only; an
    address must be http or https."""
    course = _topic_course(subscription, index)
    title = " ".join(str(form.get("title") or "").split())
    url = str(form.get("url") or "").strip()
    if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.IGNORECASE):
        raise InvalidInput(_("an address starting with https://"))
    if len(url) > LINK_LIMITS["url"] or len(title) > LINK_LIMITS["title"]:
        raise InvalidInput(
            _("at most {n} characters here", n=LINK_LIMITS["title"] if len(title) > 80 else 500)
        )
    links = _links(subscription, course)
    if len(_hand_links(subscription, course)) >= LINKS_MOST:
        raise InvalidInput(_("at most {n} links for a course", n=LINKS_MOST))
    week = str(form.get("week") or "")
    known = {m.isoformat() for m in _teaching(subscription, course)[0]}
    links.append(
        {
            "id": secrets.token_urlsafe(6),
            "title": title or url.split("//", 1)[1].split("/", 1)[0],
            "url": url,
            "week": week if week in known else "",
        }
    )
    materials = {**(subscription.options.get("materials") or {}), course_key(course): links}
    return replace(subscription, options={**subscription.options, "materials": materials})


def remove_course_link(subscription: Subscription, index: int, link_id: str) -> Subscription:
    course = _topic_course(subscription, index)
    links = [x for x in _links(subscription, course) if x.get("id") != link_id]
    materials = {**(subscription.options.get("materials") or {}), course_key(course): links}
    return replace(subscription, options={**subscription.options, "materials": materials})


# --------------------------------------------------------------------------- #
# A course's materials from where its teacher keeps them (D174)
# --------------------------------------------------------------------------- #

MATERIALS_FILE_MOST = 4 * 1024 * 1024  # bytes of a page saved from the browser
MATERIALS_MOST = 2 * _coursepages.MAX_ITEMS  # one course's materials read from pages, every source
MATERIALS_ON_FOCUS = 6  # the week's materials on the focus screen
_GOOGLE_DOC = re.compile(r"^https://docs\.google\.com/document/d/([\w-]{20,})", re.IGNORECASE)


def _hand_links(subscription: Subscription, course: str) -> list[dict]:
    """The links the student added by hand (D88), not those read from a page."""
    return [x for x in _links(subscription, course) if not x.get("source")]


def _decoded(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("latin-1")


def _material_places(
    subscription: Subscription, course: str, items: Sequence[_coursepages.Material]
) -> list[str]:
    """Each item's place: the Monday of the teaching week it falls in, else "course"
    (the whole course) or "exams" (exam practice)."""
    mondays, hours, weeks = _teaching(subscription, course)
    placed = [i for i in items if i.placed]
    weeks_of = iter(_syllabus.place([i.topic() for i in placed], hours, weeks, mondays) if placed else [])
    out = []
    for item in items:
        week = next(weeks_of) if item.placed else None
        out.append(mondays[week - 1].isoformat() if week is not None else item.shelf)
    return out


def _source_key(reading: _coursepages.Reading, address: str | None) -> str:
    if reading.source == "moodle" and reading.site:
        return f"moodle:{reading.site}#{reading.course_id or ''}"
    return f"page:{address or reading.name}"


def import_course_materials(
    subscription: Subscription,
    index: int,
    *,
    page: bytes | None = None,
    address: str | None = None,
    fetch: Callable[[str], tuple[bytes, str]] | None = None,
) -> tuple[Subscription, dict]:
    """A course's materials read from where its teacher keeps them (D174): a page saved
    from Moodle or from the teacher's site (`page`, the file's bytes), or the address of
    a public page or a shared document (`address`), which the server reads. Each item is
    placed on a teaching week of the timetable, else kept for the whole course or for
    exam practice. Reading the same source again replaces what it gave, keeping where
    the student moved an item. A Moodle page links the course to its page there (D163)
    when it was not; a Moodle address alone links it and asks for the page, which Moodle
    shows only after its own sign-in."""
    course = _topic_course(subscription, index)
    key = course_key(course)
    options = dict(subscription.options)
    if address is not None:
        address = address.strip()
        if not address:
            raise InvalidInput(_("a file, or a page's address"))
        if len(address) > 500:
            raise InvalidInput(_("at most {n} characters here", n=500))
        moodle_address = None
        with contextlib.suppress(InvalidInput):
            moodle_address = read_moodle_address(address)
        if moodle_address is not None and moodle_address[1] is not None:
            site, number = moodle_address
            options["moodle_courses"] = {
                **(options.get("moodle_courses") or {}),
                key: {"site": site, "id": number},
            }
            return replace(subscription, options=options), {"needs_file": True, "host": _host(site)}
        doc = _GOOGLE_DOC.match(address)
        target = f"https://docs.google.com/document/d/{doc.group(1)}/export?format=html" if doc else address
        try:
            body, final = (fetch or _sources.fetch_page)(target)
        except _sources.SourceError as exc:
            raise InvalidInput(str(exc)) from exc
        html, base, label = _decoded(body), final, address
    elif page is not None:
        if len(page) > MATERIALS_FILE_MOST:
            raise InvalidInput(_("the page is larger than {mb} MB", mb=MATERIALS_FILE_MOST // (1024 * 1024)))
        html, base, label = _decoded(page), None, None
    else:
        raise InvalidInput(_("a file, or a page's address"))
    if "<" not in html[:4096]:
        raise InvalidInput(_("this is not a web page: save the course's page from the browser (Ctrl+S)"))
    reading = _coursepages.read(html, base)
    if reading.source == "moodle" and not reading.items and not reading.course_id:
        raise InvalidInput(_("this is not a course's page on Moodle: open the course, then save its page"))
    return _keep_reading(subscription, options, course, reading, label)


def _keep_reading(
    subscription: Subscription,
    options: dict,
    course: str,
    reading: _coursepages.Reading,
    label: str | None,
) -> tuple[Subscription, dict]:
    """What a source gave a course's materials, placed and kept in place of what it gave
    before (D174); from a page, or since D208 from Moodle's web service."""
    key = course_key(course)
    source = _source_key(reading, label)
    places = _material_places(subscription, course, reading.items)
    old = [x for x in _links(subscription, course) if x.get("source") == source]
    moved = {x["url"]: x for x in old if x.get("moved")}
    others = [x for x in _links(subscription, course) if x.get("source") != source]
    room = MATERIALS_MOST - sum(1 for x in others if x.get("source"))
    fresh = []
    for item, where in list(zip(reading.items, places, strict=True))[: max(0, room)]:
        kept = moved.get(item.url)
        where = (kept.get("week") or kept.get("shelf") or where) if kept else where
        fresh.append(
            {
                "id": secrets.token_urlsafe(6),
                "title": item.title,
                "url": item.url,
                "kind": item.kind,
                "group": item.group,
                "week": where if where not in _coursepages.SHELVES else "",
                "shelf": where if where in _coursepages.SHELVES else "",
                "source": source,
                **({"moved": True} if kept else {}),
            }
        )
    options["materials"] = {**(options.get("materials") or {}), key: others + fresh}
    sources = dict((options.get("material_sources") or {}).get(key) or {})
    sources[source] = {"name": reading.name, "from": reading.source, "address": label or ""}
    options["material_sources"] = {**(options.get("material_sources") or {}), key: sources}
    linked = False
    if reading.site and reading.course_id and key not in (options.get("moodle_courses") or {}):
        options["moodle_courses"] = {
            **(options.get("moodle_courses") or {}),
            key: {"site": reading.site, "id": reading.course_id},
        }
        linked = True
    summary = {
        "count": len(fresh),
        "weeks": len({x["week"] for x in fresh if x["week"]}),
        "course": sum(1 for x in fresh if x["shelf"] == "course"),
        "exams": sum(1 for x in fresh if x["shelf"] == "exams"),
        "name": reading.name,
        "unresolved": reading.unresolved,
        "linked": linked,
        "needs_file": False,
    }
    return replace(subscription, options=options), summary


def move_course_material(subscription: Subscription, index: int, item_id: str, where: str) -> Subscription:
    """An item of a course's materials moved to a teaching week (its Monday), to the
    whole course or to exam practice; kept there when its page is read again."""
    course = _topic_course(subscription, index)
    known = {m.isoformat() for m in _teaching(subscription, course)[0]}
    if where not in known and where not in _coursepages.SHELVES:
        raise InvalidInput(_("Choose a week of the course."))
    links = []
    found = False
    for x in _links(subscription, course):
        if x.get("id") == item_id:
            found = True
            x = {
                **x,
                "week": where if where in known else "",
                "shelf": "" if where in known else where,
                "moved": True,
            }
        links.append(x)
    if not found:
        raise InvalidInput(_("This item is no longer there."))
    materials = {**(subscription.options.get("materials") or {}), course_key(course): links}
    return replace(subscription, options={**subscription.options, "materials": materials})


def remove_course_materials(subscription: Subscription, index: int, source: str) -> Subscription:
    """Every item one page gave, removed; the links added by hand stay."""
    course = _topic_course(subscription, index)
    key = course_key(course)
    links = [x for x in _links(subscription, course) if x.get("source") != source]
    sources = dict((subscription.options.get("material_sources") or {}).get(key) or {})
    sources.pop(source, None)
    options = {
        **subscription.options,
        "materials": {**(subscription.options.get("materials") or {}), key: links},
        "material_sources": {**(subscription.options.get("material_sources") or {}), key: sources},
    }
    return replace(subscription, options=options)


def _material_row(x: Mapping) -> dict:
    return {
        "id": x["id"],
        "title": x["title"],
        "url": x["url"],
        "kind": x.get("kind") or "link",
        "group": x.get("group") or "",
        "where": x.get("week") or x.get("shelf") or "course",
        "host": (urlsplit(x["url"]).hostname or "").removeprefix("www."),
    }


def course_materials_view(subscription: Subscription, index: int, now: datetime | None = None) -> dict:
    """A course's materials page (D174): what each page gave, week by week, then for the
    whole course and for exam practice; each item can be moved, and each page read
    again or removed."""
    now = now or datetime.now(UTC)
    course = _topic_course(subscription, index)
    key = course_key(course)
    mondays = _teaching(subscription, course)[0]
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    read = [x for x in _links(subscription, course) if x.get("source")]
    weeks = []
    for number, monday in enumerate(mondays, start=1):
        items = [_material_row(x) for x in read if x.get("week") == monday.isoformat()]
        if items:
            weeks.append(
                {
                    "monday": monday.isoformat(),
                    "number": number,
                    "label": _("Week {n}", n=number),
                    "day": format_date(monday, "day_short"),
                    "now": monday <= today < monday + timedelta(days=7),
                    "items": items,
                }
            )
    sources = (subscription.options.get("material_sources") or {}).get(key) or {}
    return {
        "index": index,
        "course": course,
        "weeks": weeks,
        "course_items": [_material_row(x) for x in read if not x.get("week") and x.get("shelf") != "exams"],
        "exam_items": [_material_row(x) for x in read if not x.get("week") and x.get("shelf") == "exams"],
        "count": len(read),
        "sources": [
            {
                "key": source,
                "name": meta.get("name") or "",
                "moodle": meta.get("from") == "moodle",
                "address": meta.get("address") or "",
                "host": _host(meta.get("address")) if meta.get("address") else "",
                "count": sum(1 for x in read if x.get("source") == source),
            }
            for source, meta in sources.items()
        ],
        "choices": [
            {"value": m.isoformat(), "label": _("Week {n} · {day}", n=k, day=format_date(m, "day_short"))}
            for k, m in enumerate(mondays, start=1)
        ]
        + [
            {"value": "course", "label": _("For the whole course")},
            {"value": "exams", "label": _("Exam practice")},
        ],
        "timetable": bool(mondays),
        "moodle": moodle_course(subscription, course),
    }


def week_materials(subscription: Subscription, course: str, now: datetime | None = None) -> list[dict]:
    """The materials of the course's teaching week now, else of the last one taught:
    for the focus screen (D174)."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    mondays = [m for m in _teaching(subscription, course)[0] if m <= today]
    if not mondays:
        return []
    week = mondays[-1].isoformat()
    return [_material_row(x) for x in _links(subscription, course) if x.get("week") == week][
        :MATERIALS_ON_FOCUS
    ]


# --------------------------------------------------------------------------- #
# D208: a course read from Moodle with the student's own key, and its study plan
# --------------------------------------------------------------------------- #
# The owner, 2026-11-26, after D92 had kept Moodle at links and saved pages: the student
# gives the course's address as before (D163); the app reads the course with the student's
# key for Moodle's mobile web service, read-only, downloads the course's files, keeps their
# text and the plan made from it, and not the files. The key is sealed as the calendar's link
# is (D175); only two functions are called, `core_webservice_get_site_info` (to check the
# key) and `core_course_get_contents` (a course's sections and files).

MOODLE_KEY = re.compile(r"^[0-9a-fA-F]{32}$")
MOODLE_KEY_PAGE = "/user/managetoken.php"  # Moodle's Security keys page, where a student copies it
moodle_call = _sources.moodle_call  # for the web app, which calls only service
MoodleError = _sources.MoodleError
fetch_file = _sources.fetch_file

# What each way to study says when the rules made the plan (the model writes its own).
PLAN_HOW_WORDS = {
    "read": "Read the week's slides once, looking for the main ideas.",
    "self-test": "Close them and write down what you remember, then check what you missed.",
    "worked-example": "Study a solved example before trying a problem alone.",
    "exercises": "Do the week's exercises before looking at any solution.",
    "summary": "Write a one-page summary from memory.",
    "exam-practice": "Work through the past paper in the time the exam gives.",
}
PLAN_HOW_NAMES = {
    "read": "Read",
    "self-test": "Test yourself",
    "worked-example": "Worked example",
    "exercises": "Exercises",
    "summary": "Summary",
    "exam-practice": "Exam practice",
}


def _moodle_refusal(error: _sources.MoodleError) -> str:
    """Moodle's no, in words a student can act on."""
    if error.code in ("invalidtoken", "invalidtokenexpired"):
        return _("Moodle did not accept this key: copy it again from its Security keys page.")
    if error.code in ("servicenotavailable", "enablewsdescription", "webservicesdisabled"):
        return _(
            "This Moodle does not let apps read courses. A course's page saved from the browser still brings its links."  # noqa: E501
        )
    if error.code in ("accessexception", "requireloginerror", "nopermissions", "errorcoursecontextnotvalid"):
        return _("Moodle refused this key access to the course: are you enrolled in it?")
    return _("Moodle said no: {message}", message=str(error))


def moodle_key_view(subscription: Subscription) -> dict:
    """The student's Moodle key for reading courses (D208): whether one is kept, for which
    site, since when, and where on their Moodle it is copied from."""
    kept = subscription.options.get("moodle_key") or {}
    site = moodle_site(subscription)
    linked = bool(kept.get("key")) and _sealed.unseal(kept.get("key")) is not None
    at = kept.get("at")
    return {
        "linked": linked,
        "host": _host(kept.get("site")) if linked else _host(site),
        "name": kept.get("name") or "",
        "since": format_date(datetime.fromisoformat(at).date(), "day_month") if linked and at else "",
        "page": f"{site}{MOODLE_KEY_PAGE}" if site else None,
        "site": bool(site),
        "words": moodle_key_words(),  # AUDIT 139
    }


def connect_moodle_key(
    subscription: Subscription,
    text: str,
    *,
    call: Callable[..., object] | None = None,
    now: datetime | None = None,
) -> Subscription:
    """Keep the student's Moodle key (D208), sealed, once Moodle has said it reads courses
    with it. Moodle's answer names the student; nothing of it is kept but the site's name."""
    now = now or datetime.now(UTC)
    key = text.strip()
    if not MOODLE_KEY.match(key):
        raise InvalidInput(
            _("A Moodle key is 32 letters and digits: copy it from Moodle's Security keys page.")
        )
    site = moodle_site(subscription)
    if not site:
        raise InvalidInput(_("First link a course to its page on Moodle, so the app knows your Moodle."))
    try:
        info = (call or _sources.moodle_call)(site, key, "core_webservice_get_site_info")
    except _sources.MoodleError as exc:
        raise InvalidInput(_moodle_refusal(exc)) from exc
    except _sources.SourceError as exc:
        raise InvalidInput(str(exc)) from exc
    info = info if isinstance(info, Mapping) else {}
    functions = {f.get("name") for f in info.get("functions") or () if isinstance(f, Mapping)}
    if "core_course_get_contents" not in functions:
        raise InvalidInput(
            _("This key cannot read courses: copy the key of the «Moodle mobile web service».")
        )
    kept = {
        "site": site,
        "key": _sealed.seal(key),
        "name": " ".join(str(info.get("sitename") or "").split())[:80],
        "at": now.astimezone(UTC).isoformat(timespec="seconds"),
    }
    return replace(subscription, options={**subscription.options, "moodle_key": kept})


def forget_moodle_key(subscription: Subscription) -> Subscription:
    options = {k: v for k, v in subscription.options.items() if k != "moodle_key"}
    return replace(subscription, options=options)


def _moodle_key(subscription: Subscription, site: str) -> str | None:
    kept = subscription.options.get("moodle_key") or {}
    if (kept.get("site") or "").rstrip("/") != site.rstrip("/"):
        return None
    return _sealed.unseal(kept.get("key"))


def _keep_texts(
    store: str | os.PathLike,
    token: str,
    course: str,
    read: Sequence[_studyplan.Read],
    items: Sequence[str],
    now: datetime,
) -> None:
    """The text of a course's files, in place of what was read before (D208)."""
    at = now.astimezone(UTC).isoformat(timespec="seconds")
    with _store(store).connection() as db:
        db.execute("DELETE FROM course_texts WHERE token = ? AND course = ?", (token, course))
        db.executemany(
            "INSERT OR REPLACE INTO course_texts (token, course, item, title, week, text, read) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(token, course, item, r.title, r.week, r.text, at) for r, item in zip(read, items, strict=True)],
        )


def course_texts(store: str | os.PathLike, subscription: Subscription, course: str) -> list[dict]:
    """The text read from a course's files on Moodle (D208), file by file."""
    with _store(store).connection() as db:
        rows = db.execute(
            "SELECT title, week, text, read FROM course_texts WHERE token = ? AND course = ? ORDER BY rowid",
            (subscription.token, course_key(course)),
        ).fetchall()
    return [{"title": t, "week": w, "text": x, "read": r} for t, w, x, r in rows]


def _plan_input(
    read: Sequence[_studyplan.Read], weeks: Sequence[tuple[str, int]]
) -> tuple[list[dict], list[dict]]:
    """The files for the model, week by week, each cut so that the course stays within
    `studyplan.TEXT_MOST` characters."""
    each = max(1500, _studyplan.TEXT_MOST // max(1, len(read)))
    out = []
    for monday, number in weeks:
        files = [{"title": r.title, "kind": r.kind, "text": r.text[:each]} for r in read if r.week == monday]
        if files:
            out.append({"week": number, "files": files})
    whole = [
        {"title": r.title, "kind": "exam" if r.week == "exams" else r.kind, "text": r.text[:each]}
        for r in read
        if r.week in ("course", "exams")
    ]
    return out, whole


def read_moodle_course(
    store: str | os.PathLike,
    subscription: Subscription,
    index: int,
    *,
    call: Callable[..., object] | None = None,
    fetch: Callable[..., bytes] | None = None,
    provider: _ai.Provider | None = None,
    now: datetime | None = None,
    ai: bool = True,
) -> tuple[Subscription, dict]:
    """A course read from Moodle with the student's key (D208): its sections and activities
    become its materials on its weeks (as a saved page's do, D174); its files are downloaded,
    read and dropped, their text kept; and a study plan is made from them, week by week, by
    the model when the student said yes to AI and the server has it, else by rules. The plan's
    topics go on the week's sessions (D81) unless the student gave the course's topics.
    `ai` False keeps to the rules whatever the student said: the demo's made-up students
    (D209), whose plans no visitor asked a model for."""
    now = now or datetime.now(UTC)
    course = _topic_course(subscription, index)
    key = course_key(course)
    entry = (subscription.options.get("moodle_courses") or {}).get(key)
    if not entry:
        raise InvalidInput(_("First link this course to its page on Moodle."))
    site, number = str(entry["site"]), int(entry["id"])
    token = _moodle_key(subscription, site)
    if token is None:
        raise InvalidInput(_("Add your Moodle key in Connections first."))
    try:
        contents = (call or _sources.moodle_call)(
            site, token, "core_course_get_contents", {"courseid": number}
        )
    except _sources.MoodleError as exc:
        raise InvalidInput(_moodle_refusal(exc)) from exc
    except _sources.SourceError as exc:
        raise InvalidInput(str(exc)) from exc
    found = _studyplan.sections(contents, site)
    if not any(sec.activities for sec in found):
        raise InvalidInput(
            _("Moodle gave nothing for this course: is it the right course, and are you enrolled in it?")
        )
    reading = _coursepages.sections(
        course,
        site,
        number,
        [(sec.name, sec.number, [(a.name, a.page, a.kind) for a in sec.activities]) for sec in found],
    )
    subscription, kept = _keep_reading(subscription, dict(subscription.options), course, reading, None)
    source = _source_key(reading, None)
    where_of = {
        x["url"]: x.get("week") or x.get("shelf") or "course"
        for x in _links(subscription, course)
        if x.get("source") == source
    }
    files, left = _studyplan.to_read(found)
    getter = fetch or _sources.fetch_file
    read: list[_studyplan.Read] = []
    items: list[str] = []
    failed = 0
    for file in files:
        try:
            body = getter(_studyplan.with_key(file.address, token), limit=_studyplan.FILE_MOST)
            extracted = _notefiles.text(file.filename, body)
        except (_sources.SourceError, _notefiles.NotesError):
            failed += 1
            continue
        several = sum(1 for f in files if f.page == file.page) > 1
        title = f"{file.activity} · {file.filename}" if several else file.activity
        read.append(
            _studyplan.Read(
                title,
                where_of.get(file.page, "course"),
                extracted.text[: _studyplan.TEXT_PER_FILE],
                _studyplan.kind_of(f"{file.activity} {file.filename}"),
            )
        )
        items.append(file.address)
    _keep_texts(store, subscription.token, key, read, items, now)
    mondays = _teaching(subscription, course)[0]
    weeks = [(m.isoformat(), n) for n, m in enumerate(mondays, start=1)]
    plan, by, order = None, "rules", ""
    model = (provider or _ai.cards_from_env()) if ai and ai_on(subscription) and read else None
    if model is not None:
        language = _LANGUAGE_NAMES.get(language_of(subscription), "English")
        by_week, whole = _plan_input(read, weeks)
        system, prompt = _ai.plan_prompt(course, language, by_week, whole)
        data = _ai.ask(
            _store(store),
            subscription.token,
            model,
            "study-plan",
            system=system,
            prompt=prompt,
            schema=_ai.PLAN_SCHEMA,
            max_tokens=_ai.PLAN_MAX_TOKENS,
            now=now,
        )
        plan = _studyplan.checked_plan(data, weeks)
        if plan is not None:
            by, order = "ai", _studyplan.clip((data or {}).get("order") or "", 400)
    if plan is None:
        plan = _studyplan.rules_plan(weeks, read)
    options = dict(subscription.options)
    options["study_plans"] = {
        **(options.get("study_plans") or {}),
        key: {
            "weeks": plan,
            "by": by,
            "order": order,
            "files": len(read),
            "failed": failed,
            "left": left,
            "read": now.astimezone(UTC).isoformat(timespec="seconds"),
            "host": _host(site),
        },
    }
    before = (options.get("topics") or {}).get(key) or {}
    if plan and (not before.get("items") or before.get("source") == "moodle"):
        topics = [_syllabus.Topic(t["title"], week=w["number"]) for w in plan for t in w["topics"]]
        options["topics"] = {
            **(options.get("topics") or {}),
            key: {
                "course": course,
                "items": _placed(subscription, course, topics[: _syllabus.MAX_TOPICS]),
                "from": None,
                "facts": before.get("facts") or {},
                "source": "moodle",
            },
        }
    log_event(store, subscription.token, "moodle_read", by)
    summary = {
        "files": len(read),
        "failed": failed,
        "left": left,
        "weeks": len(plan),
        "by": by,
        "materials": kept["count"],
    }
    return replace(subscription, options=options), summary


def study_plan_view(subscription: Subscription, index: int, now: datetime | None = None) -> dict | None:
    """A course's study plan (D208), week by week, for its page: each week's topics in
    order with their key points, what to know first, and how to study it; the current
    week marked. None before the course was read from Moodle."""
    now = now or datetime.now(UTC)
    course = _topic_course(subscription, index)
    kept = (subscription.options.get("study_plans") or {}).get(course_key(course))
    if not kept:
        return None
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    weeks = []
    for w in kept.get("weeks") or ():
        monday = date.fromisoformat(w["monday"])
        weeks.append(
            {
                "number": w["number"],
                "label": _("Week {n} · {day}", n=w["number"], day=format_date(monday, "day_short")),
                "now": monday <= today < monday + timedelta(days=7),
                "past": monday + timedelta(days=7) <= today,
                "topics": w.get("topics") or [],
                "needs": w.get("needs") or [],
                "how": [
                    {
                        "kind": h["kind"],
                        "name": _(PLAN_HOW_NAMES.get(h["kind"], h["kind"])),
                        "what": h.get("what") or _(PLAN_HOW_WORDS.get(h["kind"], "")),
                    }
                    for h in w.get("how") or ()
                ],
            }
        )
    read = datetime.fromisoformat(kept["read"])
    return {
        "weeks": weeks,
        "by_ai": kept.get("by") == "ai",
        "order": kept.get("order") or "",
        "files": kept.get("files", 0),
        "failed": kept.get("failed", 0),
        "left": kept.get("left", 0),
        "read": format_date(read.astimezone(_zone(subscription.options["tz"])).date(), "day_month"),
        "host": kept.get("host") or "",
        "ai_offer": _ai.cards_from_env() is not None and not ai_on(subscription),
    }


def week_study(subscription: Subscription, course: str, now: datetime | None = None) -> dict | None:
    """This week's part of a course's study plan (D208), for the focus screen: the week
    taught now, else the last one taught."""
    now = now or datetime.now(UTC)
    kept = (subscription.options.get("study_plans") or {}).get(course_key(course))
    if not kept:
        return None
    today = now.astimezone(_zone(subscription.options["tz"])).date()
    past = [w for w in kept.get("weeks") or () if date.fromisoformat(w["monday"]) <= today]
    if not past:
        return None
    w = past[-1]
    return {
        "number": w["number"],
        "topics": [t["title"] for t in w.get("topics") or ()][:3],
        "how": [
            {
                "name": _(PLAN_HOW_NAMES.get(h["kind"], h["kind"])),
                "what": h.get("what") or _(PLAN_HOW_WORDS.get(h["kind"], "")),
            }
            for h in w.get("how") or ()
        ][:2],
    }


# D209: the owner, 2026-11-26, about D208: "i need a tutorial, in the app too because i
# cant find it". Planning a course from Moodle takes three steps spread over two pages (a
# course's Moodle card, Connections) and one more on Moodle itself; the guide puts them on
# one page, in order, says which are done, and starts the next one from where it is.
MOODLE_GUIDE_STEPS = ("link", "key", "read")
MOODLE_EXAMPLE = "https://moodle.your-university.edu"  # an address for the pictures, no site


def moodle_guide_view(subscription: Subscription) -> dict:
    """D209: the way to a course's study plan from Moodle, step by step, and where the
    student is on it: a course linked to its page (D163), the key given once (D208), a
    course read and planned. Each course says what it still needs; `next` is the first
    step not done, None once a course has its plan."""
    options = subscription.options
    linked = options.get("moodle_courses") or {}
    plans = options.get("study_plans") or {}
    key = moodle_key_view(subscription)
    courses = [
        {
            "index": index,
            "name": name,
            "linked": course_key(name) in linked,
            "planned": course_key(name) in plans,
        }
        for index, name in enumerate(topic_courses(subscription))
    ]
    done = {
        "link": any(c["linked"] for c in courses),
        "key": key["linked"],
        "read": any(c["planned"] for c in courses),
    }
    site = moodle_site(subscription)
    first = next((c for c in courses if c["linked"] and not c["planned"]), None)
    return {
        "steps": [{"key": step, "done": done[step]} for step in MOODLE_GUIDE_STEPS],
        "done": sum(done.values()),
        "next": next((step for step in MOODLE_GUIDE_STEPS if not done[step]), None),
        "courses": courses,
        "planned": [c for c in courses if c["planned"]],
        "to_read": first,  # the first course linked and not yet read
        "key": key,
        "site": bool(site),
        "host": _host(site) or _host(MOODLE_EXAMPLE),
        "home": site or MOODLE_EXAMPLE,
        "example": f"{site or MOODLE_EXAMPLE}/course/view.php?id=",
        "ai": ai_on(subscription) and _ai.cards_from_env() is not None,
    }


# --------------------------------------------------------------------------- #
# Connections: every link a plan keeps with the outside, and its state (D175)
# --------------------------------------------------------------------------- #

moodle_public_config = _sources.moodle_public_config  # for the web app, which calls only service
MOODLE_ACCESS_DAYS = 30  # how long what a Moodle says about its sign-in is kept
MOODLE_ACCESS_RETRY = timedelta(days=1)  # how long before asking again one that did not answer


def moodle_access_due(subscription: Subscription, now: datetime | None = None) -> str | None:
    """The student's Moodle when it should be asked how it signs in: never asked, asked
    as another site, asked more than `MOODLE_ACCESS_DAYS` ago, or a day after it did not
    answer. None otherwise, and without a Moodle."""
    site = moodle_site(subscription)
    if not site:
        return None
    kept = subscription.options.get("moodle_access") or {}
    if kept.get("site") != site or not kept.get("at"):
        return site
    age = (now or datetime.now(UTC)) - datetime.fromisoformat(str(kept["at"]))
    wait = timedelta(days=MOODLE_ACCESS_DAYS) if kept.get("ok") else MOODLE_ACCESS_RETRY
    return site if age > wait else None


def ask_moodle_access(site: str, probe: Callable[[str], Mapping], now: datetime | None = None) -> dict:
    """What `site` says about its sign-in, asked through `probe`
    (`sources.moodle_public_config`), to keep as the plan's `moodle_access`. A site
    that does not answer is kept as such, and asked again a day later."""
    at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    try:
        found = probe(site)
        providers = [
            " ".join(str(p.get("name")).split())[:80]
            for p in (found.get("identityproviders") or [])
            if isinstance(p, Mapping) and p.get("name")
        ][:5]
        return {
            "site": site,
            "at": at,
            "ok": True,
            "login": int(found.get("typeoflogin") or 1),
            "apps": bool(found.get("enablewebservices")) and bool(found.get("enablemobilewebservice")),
            "providers": providers,
        }
    except (SourceError, ValueError, TypeError, AttributeError):
        return {"site": site, "at": at, "ok": False}


def keep_moodle_access(subscription: Subscription, found: Mapping) -> Subscription:
    return replace(subscription, options={**subscription.options, "moodle_access": dict(found)})


def _access_view(kept: Mapping, site: str) -> dict:
    """What the student's Moodle said about its sign-in, in the student's words, and
    what that means for what the app can read (D174, D175)."""
    if kept.get("site") != site:
        return {"kind": "unknown", "says": _("Not asked yet.")}
    if not kept.get("ok"):
        return {"kind": "unknown", "says": _("Your Moodle did not answer when asked how it signs in.")}
    if not kept.get("apps"):
        return {
            "kind": "closed",
            "says": _(
                "Your Moodle is closed to apps: this app reads the course pages you save, "
                "and the calendar link."
            ),
        }
    if int(kept.get("login") or 1) in (2, 3):
        providers = ", ".join(kept.get("providers") or ()) or _("your university's page")
        return {
            "kind": "sso",
            "says": _(
                "Your Moodle signs in through {providers}. It gives its key to its own app only, so this app "
                "reads the course pages you save; reading it by itself needs your university's agreement.",
                providers=providers,
            ),
        }
    return {
        "kind": "own",
        "says": _(
            "Your Moodle signs in with its own form. Reading it by itself would need your password, which "
            "this app never asks for: it reads the course pages you save."
        ),
    }


def _when_read(stamp: str | None, zone: ZoneInfo, now: datetime) -> str:
    if not stamp:
        return ""
    at = datetime.fromisoformat(stamp).astimezone(zone)
    if at.date() == now.astimezone(zone).date():
        return _("today at {time}", time=f"{at:%H:%M}")
    return format_date(at.date(), "day_short")


def connections_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> dict:
    """Connections (D175): every link the plan keeps with the outside, each with its
    state and the one thing to do about it: Moodle (how it signs in, its calendar link,
    each course linked and its materials, D163, D174), the timetable, the plan's own
    calendar for Google Calendar or another, the passkeys and the devices reminded."""
    now = now or datetime.now(UTC)
    options = subscription.options
    zone = _zone(options["tz"])
    site = moodle_site(subscription)
    linked = options.get("moodle_courses") or {}
    courses = [
        {
            "index": index,
            "name": name,
            "linked": course_key(name) in linked,
            "materials": sum(1 for x in _links(subscription, name) if x.get("source")),
        }
        for index, name in enumerate(topic_courses(subscription))
    ]
    calendar = {
        "linked": bool(options.get("deadlines_url")),
        "host": _deadlines_host(options),
        "read": _when_read(options.get("deadlines_read"), zone, now),
        "error": options.get("deadlines_error"),
        "brought": len(set((options.get("imported") or {}).values())),
    }
    source = subscription.source_url
    timetable = {
        "kind": "link" if source else "file" if subscription.ics_text is not None else "drawn",
        "host": (urlsplit(source).hostname or "").removeprefix("www.") if source else "",
        "read": _when_read(subscription.refreshed, zone, now) if source else "",
        "error": subscription.error if source else None,
    }
    account = account_view(store, subscription, now)
    reminders = reminders_view(store, subscription)
    return {
        "moodle": {
            "site": site or "",
            "host": _host(site),
            "access": _access_view(options.get("moodle_access") or {}, site) if site else None,
            "calendar": calendar,
            "courses": courses,
            "linked": sum(1 for c in courses if c["linked"]),
            "with_materials": sum(1 for c in courses if c["materials"]),
        },
        "timetable": timetable,
        "passkeys": len(account.get("passkeys") or []),
        "recovery": bool(account.get("recovery")),
        "devices": len(reminders.get("devices") or []),
        # What needs the student now: a link that failed to read.
        "attention": int(bool(calendar["error"])) + int(bool(timetable["error"])),
    }


def _course_note_posts(
    store: str | os.PathLike, subscription: Subscription, course: str
) -> list[social.Post]:
    """The notes of a course a student can open, newest first: their own, and their
    groups'."""
    where = _store(store)
    token = subscription.token
    found: dict[str, social.Post] = {}
    for post in social.posts_of(where, token, "notes", limit=200):
        found[post.id] = post
    for group in social.groups_of(where, token):
        for post in social.group_notes(where, token, group.id):
            found.setdefault(post.id, post)
    mine = [p for p in found.values() if course_key(p.data.get("course", "")) == course_key(course)]
    return sorted(mine, key=lambda p: p.created, reverse=True)


def _course_notes(store: str | os.PathLike, subscription: Subscription, course: str) -> list[dict]:
    """The notes of a course a student can open: their own, and their groups'."""
    where = _store(store)
    token = subscription.token
    mine = _course_note_posts(store, subscription, course)
    names = {g.id: g.name for g in social.groups_of(where, token)}
    out = []
    for post in mine[:12]:
        author = social.get_profile(where, post.token)
        out.append(
            {
                "id": post.id,
                "title": post.data.get("title", ""),
                "mine": post.token == token,
                "by": author.handle if author else "",
                "group": names.get(post.data.get("group", "")) if post.visibility == social.GROUP else "",
                "text": bool(post.data.get("body")),
                "pages": len(post.data.get("photos", [])),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# The sidebar on a laptop (DECISIONS.md D136)

SIDEBAR_COURSES = 8  # courses listed; the rest are on Courses


def sidebar_view(store: str | os.PathLike, subscription: Subscription, now: datetime | None = None) -> dict:
    """What the laptop's sidebar shows on every page of a plan (D136), as Notion
    Calendar's does: this month, each day marked if the plan studies or has an exam or a
    test on it, each a link to the calendar on that day; the courses in their colours with
    the days to their exams; how many cards wait. Light: no replay, only the plan's
    sessions, exams and tests as kept."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    first = today.replace(day=1)
    start = first - timedelta(days=first.weekday())
    following = (first + timedelta(days=32)).replace(day=1)
    weeks = -(-(following - start).days // 7)
    end = start + timedelta(days=7 * weeks)
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    study: set[date] = set()
    exams: dict[date, list[str]] = {}
    colours: dict[str, int] = {}
    if plan is not None:

        def day_of(stamp: str) -> date:
            return datetime.fromisoformat(stamp).astimezone(zone).date()

        for s in plan.history + plan.sessions:
            d = day_of(s.start)
            if start <= d < end and s.outcome != "skipped":
                study.add(d)
        for subject in plan.subjects:
            exams.setdefault(day_of(subject.exam), []).append(subject.name)
        for e in plan.events:
            if e.source in ("partial", "quiz", "mock"):
                exams.setdefault(day_of(e.start), []).append(test_label(e.source, e.summary))
        colours = {name.casefold(): index for name, index in course_colours(plan)}
    days = []
    for n in range(7 * weeks):
        d = start + timedelta(days=n)
        said = [format_date(d, "long")]
        if d in exams:
            said.append(_("exam: {names}", names=", ".join(exams[d])))
        elif d in study:
            said.append(_("study planned"))
        days.append(
            {
                "date": d.isoformat(),
                "day": d.day,
                "in_month": d.month == first.month,
                "today": d == today,
                "study": d in study,
                "exam": d in exams,
                "label": " · ".join(said),
            }
        )
    courses = []
    for index, course in enumerate(topic_courses(subscription)[:SIDEBAR_COURSES]):
        exam = _exam_of(plan, course, now) if plan is not None else None
        courses.append(
            {
                "index": index,
                "name": course,
                "color": colours.get(course.casefold()),
                "exam": exam["says"] if exam and exam["days"] >= 0 else "",
                "soon": bool(exam and 0 <= exam["days"] <= 14),
            }
        )
    return {
        "month": format_date(first, "month"),
        "weekdays": [
            {
                "letter": _i18n.weekday_name(start + timedelta(days=k), short=True)[:1].upper(),
                "name": _i18n.weekday_name(start + timedelta(days=k)),
            }
            for k in range(7)
        ],
        "weeks": [days[k : k + 7] for k in range(0, len(days), 7)],
        "courses": courses,
        "more_courses": max(0, len(topic_courses(subscription)) - SIDEBAR_COURSES),
        "moodle": moodle_home(subscription),  # D163
        "cards_due": cards_due(store, subscription, now),
    }


def courses_view(
    store: str | os.PathLike, subscription: Subscription, now: datetime | None = None
) -> list[dict]:
    """The Courses section (D88): each course of the plan with how far its teaching
    has gone, how much of it was reviewed, its next exam and its cards."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    colours = {name.casefold(): index for name, index in course_colours(plan)} if plan else {}
    kept = subscription.options.get("topics") or {}
    cards = flashcards.of(_store(store), subscription.token)
    due_stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    out = []
    for index, course in enumerate(topic_courses(subscription)):
        mondays = _teaching(subscription, course)[0]
        weeks = _week_states(subscription, course, mondays, today, now) if plan else []
        taught = [w for w in weeks if w["state"] != "ahead"]
        reviewed = [w for w in taught if w["done"]]
        mine = [c for c in cards if course_key(c.course) == course_key(course)]
        workload = course_workload(subscription, course)
        out.append(
            {
                "index": index,
                "course": course,
                "color": colours.get(course.casefold()),
                "exam": _exam_of(plan, course, now) if plan else None,
                "weeks": len(weeks),
                "taught": len(taught),
                "reviewed": len(reviewed),
                "percent": round(100 * len(taught) / len(weeks)) if weeks else 0,
                "topics": len((kept.get(course_key(course)) or {}).get("items") or []),
                "cards": len(mine),
                "due": sum(1 for c in mine if c.new or (c.due is not None and c.due <= due_stamp)),
                "workload": workload["verdict"] if workload else None,
            }
        )
    return out


def course_view(
    store: str | os.PathLike, subscription: Subscription, index: int, now: datetime | None = None
) -> dict:
    """One course's page (D88): its weeks, taught or to come, each with its syllabus
    topics, the reviews done and planned, today's recall and its own cards (D87);
    the topics without a week; the assessment and the workload check (D86); its
    notes, its deck and the links to its material."""
    now = now or datetime.now(UTC)
    course = _topic_course(subscription, index)
    key = course_key(course)
    zone = _zone(subscription.options["tz"])
    today = now.astimezone(zone).date()
    mondays = _teaching(subscription, course)[0]
    weeks = _week_states(subscription, course, mondays, today, now) if subscription.plan else []
    cards = [c for c in flashcards.of(_store(store), subscription.token) if course_key(c.course) == key]
    due_stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    for w in weeks:
        own = [c for c in cards if c.week == w["monday"]]
        w["cards"] = len(own)
        w["links"] = [x for x in _links(subscription, course) if x.get("week") == w["monday"]]
    kept = (subscription.options.get("topics") or {}).get(key) or {}
    items = kept.get("items") or []
    unplaced = [i["title"] for i in items if not i.get("monday")]
    before = [i["title"] for i in items if i.get("monday") and date.fromisoformat(i["monday"]) not in mondays]
    taught = [w for w in weeks if w["state"] != "ahead"]
    plan = PlanReport.from_dict(subscription.plan) if subscription.plan else None
    colours = {name.casefold(): i for name, i in course_colours(plan)} if plan else {}
    current = next((w for w in weeks if w["state"] == "now"), None)
    taken = [r for r in exam_outcomes(subscription, now)["exams"] if course_key(r["course"]) == key]
    return {
        "index": index,
        "course": course,
        "color": colours.get(course.casefold()),
        "exam": _exam_of(plan, course, now) if plan else None,
        # its last exam answered about, beside the forecast (D105)
        "outcome": max(taken, key=lambda r: r["at"]) if taken else None,
        "weeks": weeks,
        "now": current,
        "taught": len(taught),
        "reviewed": sum(1 for w in taught if w["done"]),
        "percent": round(100 * len(taught) / len(weeks)) if weeks else 0,
        "topics": len(items),
        "unplaced": unplaced,
        "before": before,
        "facts": _facts_view(kept.get("facts") or {}),
        "credits": format_number(_credits(subscription, course) or 0)
        if _credits(subscription, course)
        else "",
        "typed_credits": bool((subscription.options.get("credits") or {}).get(key)),
        "workload": course_workload(subscription, course),
        "cards": len(cards),
        "due": sum(1 for c in cards if c.new or (c.due is not None and c.due <= due_stamp)),
        "unfiled": sum(1 for c in cards if not c.week),
        "notes": _course_notes(store, subscription, course),
        "links": [x for x in _hand_links(subscription, course) if not x.get("week")],
        "moodle": moodle_course(subscription, course),  # D163
        "all_links": len(_hand_links(subscription, course)),
        # D174: what the pages the student brought in gave
        "materials": {
            "count": sum(1 for x in _links(subscription, course) if x.get("source")),
            "weeks": len(
                {x["week"] for x in _links(subscription, course) if x.get("source") and x.get("week")}
            ),
        },
        "links_most": LINKS_MOST,
        "week_options": _week_choice(subscription, course, now)["weeks"],
    }


# --------------------------------------------------------------------------- #
# The science behind the engine: a page for students and their teachers (D89)
# --------------------------------------------------------------------------- #

SCIENCE_DAYS = 60  # the forgetting curves' span
SCIENCE_STABILITIES = (2.0, 7.0, 30.0)  # three topics: shaky, a week, a month
SCIENCE_SPACING = 5.0  # the topic whose next review the spacing chart follows (days)
# D113: the scenes that run as the science page is scrolled through. Their readouts
# change once per step of the scroll; the draining columns pass through 8 stops.
SCIENCE_STEPS = 25
SCIENCE_STOPS = 8
# The spacing effect as a landscape: topics that held this many days (rows, the
# shakiest at the back), tested when their recall had fallen to these (columns). From
# 3 days up: FSRS-6 treats a review within the same day apart (its short-term
# stability), which a topic of 1 or 2 days reaches at 95 % and 90 %.
SCIENCE_LAND_STABILITIES = (3.0, 7.0, 15.0, 30.0, 60.0)
SCIENCE_LAND_RECALLS = (1.0, 0.95, 0.9, 0.8, 0.7)
SCIENCE_LAND_FOLLOWS = 7.0  # the topic the landscape's readout follows: one that held a week


def _curve(points: Sequence[tuple[float, float]], days: float, top: float, height: float = 100.0) -> str:
    """An SVG polyline's points in a 300-wide box: days across, values up to `top`."""
    return " ".join(f"{300 * x / days:.1f},{height - height * y / top:.1f}" for x, y in points)


# The research the page cites, each a checked entry of docs/REFERENCES.md: the tips'
# own sources (D62), and four more the engine rests on.
SCIENCE_KEYS = (
    "dunlosky2013",
    "adesope2017",
    "yang2021",
    "karpicke2009",
    "cepeda2006",
    "cepeda2008",
    "rohrer2020",
    "ye2022",
    "fsrs-wiki",
    "srs-benchmark",
    "ects2015",
)
SCIENCE_SOURCES = {
    **_tips.SOURCES,
    "ye2022": _tips.Source(
        "ye2022",
        "Ye, Su and Cao, 2022",
        "ACM SIGKDD Conference on Knowledge Discovery and Data Mining",
        "https://doi.org/10.1145/3534678.3539081",
        date(2026, 9, 28),
    ),
    "fsrs-wiki": _tips.Source(
        "fsrs-wiki",
        "open-spaced-repetition, The Algorithm, 2026",
        "awesome-fsrs wiki",
        "https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm",
        date(2026, 9, 28),
    ),
    "srs-benchmark": _tips.Source(
        "srs-benchmark",
        "open-spaced-repetition, SRS Benchmark, 2026",
        "GitHub",
        "https://github.com/open-spaced-repetition/srs-benchmark",
        date(2026, 10, 13),
    ),
    "ects2015": _tips.Source(
        "ects2015",
        "European Commission, ECTS Users' Guide, 2015",
        "Publications Office of the European Union",
        "https://doi.org/10.2766/87192",
        date(2026, 10, 21),
    ),
}


# AUDIT 102: the labels at the ends of two curves are kept a line apart, so that close
# recalls (54 % and 67 % on the code page) do not print one over the other.
LABEL_GAP = 15.0  # in the chart's units; its labels are about 12 high


def _apart(curves: list[dict]) -> None:
    """Each curve's label line (`label_y`), its end moved up or down only as far as the
    gap between neighbouring labels needs."""
    ends = sorted(curves, key=lambda c: c["end_y"])
    ys = [float(c["end_y"]) + 4.0 for c in ends]
    for i in range(1, len(ys)):
        short = LABEL_GAP - (ys[i] - ys[i - 1])
        if short > 0:
            ys[i - 1] -= short / 2
            ys[i] += short / 2
    for c, y in zip(ends, ys, strict=True):
        c["label_y"] = round(y, 1)


def science_view() -> dict:
    """The science page (D89): every number on it computed here from the model the
    product runs (FSRS-6, D52) and the app's own settings, so the page and the app
    cannot disagree. The research it cites is in docs/REFERENCES.md, checked."""
    model = FSRS_6
    weights = model.weights
    decay = getattr(weights, "decay", 0.5)
    curves = []
    for stability, level in zip(SCIENCE_STABILITIES, ("review", "good", "strong"), strict=True):
        points = [(t, model.retrievability(float(t), stability)) for t in range(SCIENCE_DAYS + 1)]
        curves.append(
            {
                "stability": format_number(stability, 0),
                "level": level,
                "points": _curve(points, SCIENCE_DAYS, 1.0),
                "at_30": round(100 * model.retrievability(30.0, stability)),
                "end_x": 300.0,
                "end_y": round(100 - 100 * points[-1][1], 1),
            }
        )
    week = 7.0
    recall_week = {d: round(100 * model.retrievability(float(d), week)) for d in (7, 14, 30)}
    state = MemoryState(stability=SCIENCE_SPACING, difficulty=5.0)
    spacing = {g: round(model.review(state, float(g), Grade.GOOD).stability, 1) for g in (0, 5, 15, 30)}
    return {
        "curves": curves,
        "days": SCIENCE_DAYS,
        "week": {k: v for k, v in recall_week.items()},
        "decay": format_number(decay, 4),
        "spacing_from": format_number(SCIENCE_SPACING, 0),
        "spacing": {k: format_number(v, 1) for k, v in spacing.items()},
        "spacing_recall": {
            g: round(100 * model.retrievability(float(g), SCIENCE_SPACING)) for g in (0, 5, 15, 30)
        },
        "target": round(100 * flashcards.RETENTION),
        "new_per_day": flashcards.NEW_PER_DAY,
        "again_minutes": round(flashcards.RELEARN / timedelta(minutes=1)),
        "own_after": fitting.MIN_REVIEWS,
        "credit_hours": f"{_syllabus.CREDIT_HOURS[0]}–{_syllabus.CREDIT_HOURS[1]}",
        "sources": [SCIENCE_SOURCES[k] for k in SCIENCE_KEYS],
        "kept": _kept_up(model),
        # D113: the scenes that run as the page is scrolled through.
        "fade": _science_fade(model),
        "land": _science_land(model),
    }


def _science_step_day(step: int) -> int:
    """The day the science page's scrolled scenes stand at, at the end of a step: a
    whole day, the one the readout names and computes."""
    return round(SCIENCE_DAYS * (step + 1) / SCIENCE_STEPS)


def _science_fade(model: Model) -> dict:
    """D113: the three topics of the forgetting curves as columns draining at their own
    pace while a day passes under the chart: each column's height at the eight stops
    (twentieths of full recall), and every topic's recall at the end of each step."""
    stops = [SCIENCE_DAYS * k / (SCIENCE_STOPS - 1) for k in range(SCIENCE_STOPS)]
    columns = []
    for stability, level in zip(SCIENCE_STABILITIES, ("review", "good", "strong"), strict=True):
        heights = [round(20 * model.retrievability(day, stability)) for day in stops]
        columns.append(
            {"stability": format_number(stability, 0), "level": level, "zq": heights, "z": heights[-1]}
        )
    readouts = []
    for step in range(SCIENCE_STEPS):
        day = float(_science_step_day(step))
        recall = [round(100 * model.retrievability(day, s)) for s in SCIENCE_STABILITIES]
        readouts.append({"step": step, "day": round(day), "recall": recall})
    shaky = SCIENCE_STABILITIES[0]
    return {
        "columns": columns,
        "readouts": readouts,
        # "The shaky one is at 90 % on day {n}": its stability, by definition of it.
        "shaky_at_90": format_number(model.interval(shaky, 0.9), 0),
        # "the solid one is still at {r} %" on day 7
        "solid_at_week": round(100 * model.retrievability(7.0, SCIENCE_STABILITIES[-1])),
        "end": [round(100 * model.retrievability(float(SCIENCE_DAYS), s)) for s in SCIENCE_STABILITIES],
    }


def _science_land(model: Model) -> dict:
    """D113: the spacing effect as a landscape. For each topic (a row) and each recall
    it was tested at (a column, the wait it took to fall there), how many times longer
    a successful recall makes it hold: one tower each, as tall as that (twentieths of
    the largest), rising column by column as the page is scrolled through."""
    cells = {}
    for stability in SCIENCE_LAND_STABILITIES:
        for recall in SCIENCE_LAND_RECALLS:
            wait = model.interval(stability, recall) if recall < 1.0 else 0.0
            after = model.review(MemoryState(stability=stability, difficulty=5.0), wait, Grade.GOOD).stability
            cells[(stability, recall)] = (wait, after)
    top = max(after / stability for (stability, _), (_, after) in cells.items())
    rows = [
        {
            "stability": format_number(stability, 0),
            "cells": [
                {
                    "column": k,
                    "times": format_number(cells[(stability, recall)][1] / stability, 1),
                    "z": max(1, round(20 * cells[(stability, recall)][1] / stability / top)),
                    "shade": min(4, int(5 * cells[(stability, recall)][1] / stability / (top + 1e-9))),
                }
                for k, recall in enumerate(SCIENCE_LAND_RECALLS)
            ],
        }
        for stability in SCIENCE_LAND_STABILITIES
    ]
    follows = SCIENCE_LAND_FOLLOWS
    columns = [
        {
            "recall": round(100 * recall),
            "wait": format_number(cells[(follows, recall)][0], 0),
            "after": format_number(cells[(follows, recall)][1], 0),
            "times": format_number(cells[(follows, recall)][1] / follows, 1),
            "risk": round(100 * (1 - recall)),
        }
        for recall in SCIENCE_LAND_RECALLS
    ]
    return {
        "rows": rows,
        "columns": columns,
        "follows": format_number(follows, 0),
        "times": {(s, r): after / s for (s, r), (_, after) in cells.items()},
        "stabilities": [format_number(s, 0) for s in SCIENCE_LAND_STABILITIES],
    }


SCIENCE_KEPT_FROM = 2.0  # the topic the "reviews keep it up" chart follows (days of stability)


def _kept_up(model: Model) -> dict:
    """D109: one topic's recall over the science page's span, reviewed each time it
    falls to the app's target, every review a success: the sawtooth spacing makes,
    computed by the model the product runs."""
    target = flashcards.RETENTION
    state = MemoryState(stability=SCIENCE_KEPT_FROM, difficulty=5.0)
    t, last, points, reviews = 0.0, 0.0, [(0.0, 1.0)], []
    held = [(0.0, SCIENCE_KEPT_FROM)]  # from which day the topic held how long (D113)
    while True:
        due = last + model.interval(state.stability, target)
        step = 0.25
        while t + step < min(due, SCIENCE_DAYS):
            t += step
            points.append((t, model.retrievability(t - last, state.stability)))
        if due >= SCIENCE_DAYS:
            points.append((float(SCIENCE_DAYS), model.retrievability(SCIENCE_DAYS - last, state.stability)))
            break
        points += [(due, target), (due, 1.0)]
        reviews.append({"day": due, "before": state.stability})
        state = model.review(state, due - last, Grade.GOOD)
        held.append((due, state.stability))
        t = last = due
    alone = [(float(t), model.retrievability(float(t), SCIENCE_KEPT_FROM)) for t in range(SCIENCE_DAYS + 1)]
    return {
        "from": format_number(SCIENCE_KEPT_FROM, 0),
        "points": _curve(points, SCIENCE_DAYS, 1.0),
        "alone": _curve(alone, SCIENCE_DAYS, 1.0),
        "alone_end": round(100 * alone[-1][1]),
        "target": round(100 * target),
        "marks": [
            {"x": round(300 * r["day"] / SCIENCE_DAYS, 1), "y": round(100 - 100 * target, 1)} for r in reviews
        ],
        "reviews": len(reviews),
        "days": [round(r["day"]) for r in reviews],
        "last": format_number(state.stability, 0),
        "target_y": round(100 - 100 * target, 1),
        # D113: the step of the scroll at which each test comes, and at the end of each
        # step the tested topic's recall and how long it holds, against the same topic
        # left alone.
        "steps": [min(SCIENCE_STEPS - 1, int(SCIENCE_STEPS * r["day"] / SCIENCE_DAYS)) for r in reviews],
        "readouts": [_kept_at(model, held, step) for step in range(SCIENCE_STEPS)],
    }


def _kept_at(model: Model, held: list[tuple[float, float]], step: int) -> dict:
    """The tested topic at the end of a step of the science page's scroll (D113)."""
    day = float(_science_step_day(step))
    since, stability = max((h for h in held if h[0] <= day + 1e-9), key=lambda h: h[0])
    return {
        "step": step,
        "day": round(day),
        "recall": round(100 * model.retrievability(day - since, stability)),
        "holds": format_number(stability, 0),
        "alone": round(100 * model.retrievability(day, SCIENCE_KEPT_FROM)),
    }


# -- How it works, the garden (D116) ------------------------------------------------------

# The plain page, for everyone: a topic is a plant, the water in its pot is how much the
# student remembers, its roots how long that lasts. Every number it shows is computed
# here by the model the product runs (FSRS-6, D52), as the science page's are.
GARDEN_STOPS = SCIENCE_STOPS  # a scene's eight stops, as the science page's columns (D113)
GARDEN_ONE_DAYS = 7.0  # chapter 1: one shaky topic's pot over a week
GARDEN_WEEK = 7.0  # chapters 3 and 5: a topic that holds a week
# Chapter 3's eight stops, in days: both pots full; the first watered at once (stop 2);
# a week passes; the second watered as it droops (stop 5); another week.
GARDEN_SPACING_DAYS = (0.0, 0.0, 0.0, 3.5, 7.0, 7.0, 10.5, 14.0)
GARDEN_SOON_AT, GARDEN_JUST_AT = 2, 5  # the stops at which each pot is watered
# Three topics, each with how long it holds and how many days ago it was last revised
# before Monday; five free evenings; the exam on Saturday. D116's week game used them; the
# opening shelf and exam day still show them after the app's week (D121's month replaced
# the game).
GARDEN_TOPICS: tuple[tuple[str, str, float, int], ...] = (
    ("t0", "Analysis", 2.0, 1),
    ("t1", "Algebra", 4.0, 2),
    ("t2", "Physics", 8.0, 3),
)
GARDEN_EVENINGS = 5
GARDEN_REST = "rest"
# D117: a plant's roots are as long as its topic holds, on one scale for the whole page:
# a month reaches the bottom of the glass.
GARDEN_ROOTS_DAYS = 30.0


def _garden_z(recall: float) -> int:
    """A pot's water, in twentieths of full: the height the glass is filled to."""
    return max(0, min(20, round(20 * recall)))


def _garden_roots(stability: float) -> int:
    """A plant's roots, in twentieths of the glass's depth: how long its topic holds,
    against a month (D117). At least a stub, so that a plant always has roots."""
    return max(1, min(20, round(20 * stability / GARDEN_ROOTS_DAYS)))


def _garden_week(model: Model, plan: Sequence[str]) -> dict:
    """The garden on exam morning after a week's plan: one topic revised each evening,
    or none ("rest"), every revision a success."""
    states = {
        key: (MemoryState(stability=s, difficulty=5.0), -float(ago)) for key, _, s, ago in GARDEN_TOPICS
    }
    for day, key in enumerate(plan, start=1):
        if key in states:
            state, last = states[key]
            states[key] = (model.review(state, day - last, Grade.GOOD), float(day))
    exam = GARDEN_EVENINGS + 1
    plants: list[dict[str, Any]] = []
    for key, name, _stability, _ago in GARDEN_TOPICS:
        state, last = states[key]
        recall = model.retrievability(exam - last, state.stability)
        plants.append(
            {
                "key": key,
                "name": name,
                "recall": round(100 * recall),
                "z": _garden_z(recall),
                "roots": _garden_roots(state.stability),
            }
        )
    return {
        "plan": list(plan),
        "plants": plants,
        "lowest": min(p["recall"] for p in plants),
        "used": sum(1 for key in plan if key != GARDEN_REST),
    }


def _garden_evenings(model: Model) -> list[dict]:
    """The app's rule for self-tests evening by evening (assistant.py, the science page's
    third rule), with what it saw (D146): each evening every topic's recall, and the one
    revised, the topic that has faded furthest once it is down to the target; None when
    every topic is above it and the evening stays free."""
    target = flashcards.RETENTION
    states = {
        key: (MemoryState(stability=s, difficulty=5.0), -float(ago)) for key, _, s, ago in GARDEN_TOPICS
    }
    evenings = []
    for day in range(1, GARDEN_EVENINGS + 1):
        recall = {k: model.retrievability(day - last, st.stability) for k, (st, last) in states.items()}
        key = min(recall, key=lambda k: recall[k])
        picked = key if recall[key] <= target else None
        if picked is not None:
            state, last = states[picked]
            states[picked] = (model.review(state, day - last, Grade.GOOD), float(day))
        evenings.append({"pick": picked, "recall": recall})
    return evenings


def _garden_app_plan(model: Model) -> list[str]:
    """The app's rule for self-tests (assistant.py, the science page's third rule): each
    evening the topic that has faded furthest, once it is down to the target; otherwise
    the evening stays free."""
    return [e["pick"] or GARDEN_REST for e in _garden_evenings(model)]


# D146: the evenings drawn as the owner chose (2026-11-18, "all choices but the plant one"):
# each day a 24-hour dial, and the week with a wire from each evening to the topic it went to.
_DIAL_AT = ((52, 72), (150, 72), (248, 72), (101, 194), (199, 194))  # five dials, three and two
DIAL_R = 40


def _dial_arc(cx: float, cy: float, r: float, h0: float, h1: float) -> str:
    """An arc of a 24-hour dial, midnight at the top, clockwise: an SVG path."""
    a0 = h0 / 24 * 2 * math.pi - math.pi / 2
    a1 = h1 / 24 * 2 * math.pi - math.pi / 2
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    large = 1 if h1 - h0 > 12 else 0
    return f"M{x0:.2f} {y0:.2f}A{r} {r} 0 {large} 1 {x1:.2f} {y1:.2f}"


def _garden_dials(
    week: dict, evenings: list[dict], names: dict[str, str], colours: dict[str, int]
) -> list[dict]:
    """Each day of the week as a dial: its sleep and lectures, the evening's block, and the
    topic the evening goes to (or none: a night off)."""
    per_hour = week["slots_per_day"] / 24
    dials = []
    for d, (cx, cy) in zip(week["days"], _DIAL_AT, strict=True):
        arcs = [
            {
                "kind": r["kind"],
                "d": _dial_arc(cx, cy, DIAL_R, r["start"] / per_hour + 0.08, r["end"] / per_hour - 0.08),
            }
            for r in d["runs"]
        ]
        block = next((b for b in week["blocks"] if b["day"] == d["day"]), None)
        evening = (
            _dial_arc(
                cx, cy, DIAL_R, block["slot"] / per_hour, (block["slot"] + week["block_slots"]) / per_hour
            )
            if block
            else ""
        )
        pick = evenings[d["day"]]["pick"] if d["day"] < len(evenings) else None
        ticks = []
        for hour in (0, 6, 12, 18):
            a = hour / 24 * 2 * math.pi - math.pi / 2
            ticks.append(
                {
                    "x1": round(cx + (DIAL_R + 8) * math.cos(a), 2),
                    "y1": round(cy + (DIAL_R + 8) * math.sin(a), 2),
                    "x2": round(cx + (DIAL_R + 12) * math.cos(a), 2),
                    "y2": round(cy + (DIAL_R + 12) * math.sin(a), 2),
                }
            )
        dials.append(
            {
                "day": d["day"],
                "cx": cx,
                "cy": cy,
                "arcs": arcs,
                "evening": evening,
                "ticks": ticks,
                "topic": names[pick] if pick else "",
                "color": colours[pick] if pick else None,
            }
        )
    return dials


def _garden_water(evenings: list[dict], names: dict[str, str], colours: dict[str, int]) -> dict:
    """The week, then the water (D146): each evening's levels (every topic's recall as the
    evening begins), the one it went to, and where that topic is after it, back to full.
    The levels are FSRS-6's with average weights, every revision a success."""
    target = flashcards.RETENTION
    rows = []
    for k, e in enumerate(evenings):
        rows.append(
            {
                "day": k,
                "pick": names[e["pick"]] if e["pick"] else "",
                "color": colours[e["pick"]] if e["pick"] else None,
                "level": round(100 * e["recall"][e["pick"]]) if e["pick"] else 0,
                "lowest": round(100 * min(e["recall"].values())),
            }
        )
    topics = []
    for key, name, _s, _ago in GARDEN_TOPICS:
        levels = [round(100 * e["recall"][key]) for e in evenings]
        topics.append(
            {
                "key": key,
                "name": name,
                "color": colours[key],
                "levels": [
                    {"day": k, "level": v, "watered": evenings[k]["pick"] == key}
                    for k, v in enumerate(levels)
                ],
            }
        )
    return {
        "rows": rows,
        "topics": topics,
        "target": round(100 * target),
        "free": sum(1 for r in rows if not r["pick"]),
    }


# The front page's story (D188): one week of lectures, "seen once and shaky" (a first
# review graded Hard, as the plan assumes of every week of lectures), prepared three
# ways for an exam four weeks later and followed twelve weeks past it. The method is
# benchmarks/figures.py's (spacing_data), with FSRS-6, the product's model since D52.
LANDING_EXAM = 28.0
LANDING_AFTER = 84.0  # twelve weeks after the exam
LANDING_CRAM = (24.0, 25.0, 26.0, 27.0)  # four sessions in the exam's last days
LANDING_WIDTH = 220  # the plot's width in its 300-unit drawing, labels and their 12-unit text around it
LANDING_HOLD = 0.8  # "holds": recall stays above this after the exam
# A course (D190): twelve weeks of lectures, its exam two weeks after the last week's,
# each week of lectures prepared by the plan's rule or crammed in the exam's last days.
LANDING_COURSE_WEEKS = 12
LANDING_COURSE_GAP = 14.0
# The badges the story shows (D189, D190), each at the tier a student partway through a
# semester might have reached; and a made-up league of the demo's classmates, as the
# crests board drew it (their avatars, `showcase.FACES`).
LANDING_CRESTS = (
    ("streak", 3),
    ("recall", 2),
    ("focus", 2),
    ("buddy", 2),
    ("cards", 1),
    ("teacher", 1),
    ("full_week", 1),
    ("deep_focus", 4),
)
LANDING_STREAK = 12  # D191: the made-up student's streak on the morning of the week shown
# Each friend's own league, on the small crest at their avatar's corner.
LANDING_LEAGUE_METALS = ("gold", "silver", "silver", "bronze", "silver", "bronze")
LANDING_LEAGUE = (
    (
        "lena",
        6,
        240,
        {
            "skin": "s1",
            "hair": "ponytail",
            "colour": "blonde",
            "glasses": "round",
            "outfit": "turtleneck",
            "top": "k1",
            "bg": "b4",
        },
    ),
    (
        "marco",
        5,
        205,
        {
            "skin": "s3",
            "hair": "curtains",
            "colour": "dark",
            "face": "stubble",
            "outfit": "jacket",
            "top": "c2",
            "bg": "b2",
        },
    ),
    ("", 4, 170, None),
    (
        "sofia",
        3,
        150,
        {
            "skin": "s4",
            "hair": "curly",
            "colour": "black",
            "detail": "earrings",
            "mouth": "grin",
            "top": "c5",
            "bg": "b1",
        },
    ),
    (
        "noah",
        2,
        120,
        {
            "skin": "s6",
            "hair": "fade",
            "colour": "black",
            "head": "headphones",
            "outfit": "hoodie",
            "top": "c4",
            "bg": "b5",
        },
    ),
    (
        "chloe",
        3,
        95,
        {
            "skin": "s2",
            "hair": "bob",
            "colour": "auburn",
            "detail": "freckles",
            "eyes": "happy",
            "top": "c1",
            "bg": "b3",
        },
    ),
)


@functools.cache
def _landing_curves() -> tuple[dict, ...]:
    """The three ways of preparing the exam, each a polyline in a box `LANDING_WIDTH` by 100 over
    the exam's four weeks and the twelve after, its sessions, and its recall at the
    exam and at the end (whole percents). Pure, so computed once per process."""
    model, horizon = FSRS_6, LANDING_EXAM + LANDING_AFTER
    start = model.initial_state(Grade.HARD)
    spaced: list[float] = []
    memory, last, t = start, 0.0, 0.0
    while t < LANDING_EXAM - 1:
        t += 0.25
        if model.retrievability(t - last, memory.stability) <= flashcards.RETENTION:
            memory, last = model.review(memory, t - last, Grade.GOOD), t
            spaced.append(t)
    out = []
    for name, days in (("spaced", spaced), ("crammed", list(LANDING_CRAM)), ("once", [])):
        memory, last = start, 0.0
        points: list[tuple[float, float]] = []
        pending = list(days)
        for step in range(int(horizon * 2) + 1):
            t = step / 2
            while pending and pending[0] <= t:
                day = pending.pop(0)
                points.append((day, model.retrievability(day - last, memory.stability)))
                memory, last = model.review(memory, day - last, Grade.GOOD), day
                points.append((day, 1.0))
            points.append((t, model.retrievability(t - last, memory.stability)))
        hold, day = 0, LANDING_EXAM
        while model.retrievability(day + 1 - last, memory.stability) > LANDING_HOLD and hold < 3 * 365:
            day += 1
            hold += 1
        if model.retrievability(LANDING_EXAM - last, memory.stability) <= LANDING_HOLD:
            hold = 0
        out.append(
            {
                "name": name,
                "holds": hold,
                "points": " ".join(
                    f"{LANDING_WIDTH * x / horizon:.1f},{100 - 100 * y:.1f}" for x, y in points
                ),
                "sessions": len(days),
                "exam": round(100 * model.retrievability(LANDING_EXAM - last, memory.stability)),
                "end": round(100 * model.retrievability(horizon - last, memory.stability)),
                "end_y": round(100 - 100 * model.retrievability(horizon - last, memory.stability), 1),
            }
        )
    return tuple(out)


@functools.cache
def _landing_course() -> dict:
    """A course of `LANDING_COURSE_WEEKS` weeks of lectures (D190), every week "seen once
    and shaky" and prepared two ways for an exam `LANDING_COURSE_GAP` days after the last:
    by the plan's rule (a self-test when recall falls to the target, as `_landing_curves`)
    or crammed, four sessions in the exam's last four days. The sessions each way, the
    most spaced self-tests in a week, and the crammed ones in a day."""
    model = FSRS_6
    exam = 7.0 * (LANDING_COURSE_WEEKS - 1) + LANDING_COURSE_GAP
    weekly: dict[int, int] = {}
    spaced = 0
    for k in range(LANDING_COURSE_WEEKS):
        taught = 7.0 * k
        memory, last, t = model.initial_state(Grade.HARD), taught, taught
        while t < exam - 1:
            t += 0.25
            if model.retrievability(t - last, memory.stability) <= flashcards.RETENTION:
                memory, last = model.review(memory, t - last, Grade.GOOD), t
                spaced += 1
                weekly[int(t // 7)] = weekly.get(int(t // 7), 0) + 1
    crammed = len(LANDING_CRAM) * LANDING_COURSE_WEEKS
    return {
        "weeks": LANDING_COURSE_WEEKS,
        "spaced": spaced,
        "spaced_week": max(weekly.values()),
        "crammed": crammed,
        "cram_days": len(LANDING_CRAM),
        "crammed_day": crammed // len(LANDING_CRAM),
    }


# D191: the front page's week, planned by the app's own rules on the sample second year
# (examples/sample-semester.ics): a plan made on the semester's first day, every session
# reported done until the Tuesday morning of its fourth week, then that Tuesday taken off
# (D126's pause). Kept as data (`data/landing_week.json`), since a server's package has no
# examples; `tests/test_landing.py` makes it again and checks it is the same, and
# `python benchmarks/landing_week.py` writes it when the planner changes.
LANDING_WEEK_START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)  # the plan is made
LANDING_WEEK_MONDAY = date(2026, 10, 19)  # the week shown, the semester's fourth
LANDING_WEEK_TZ = "Europe/Paris"
LANDING_WEEK_HOURS = (8, 18)  # the part of each day drawn
LANDING_WEEK_FILE = Path(__file__).resolve().parent / "data" / "landing_week.json"


def landing_week_compute(ics: bytes) -> dict:
    """The front page's week (D191), made by the app: the timetable's classes and the plan's
    sessions Monday to Friday, as the plan stood on Tuesday morning, then where each of
    them went once Tuesday was taken off. Each session is keyed by its topic and kind, so
    a session that moved is the same session; one the plan put after Friday is "later"."""
    zone = _zone(LANDING_WEEK_TZ)
    monday = LANDING_WEEK_MONDAY
    tuesday = datetime.combine(monday + timedelta(days=1), time(7, 0), tzinfo=zone).astimezone(UTC)
    sub = start_subscription(tz=LANDING_WEEK_TZ, ics=ics, now=LANDING_WEEK_START)
    reported: set[str] = set()
    while sub.plan is not None:
        plan = PlanReport.from_dict(sub.plan)
        due = [
            x
            for x in plan.sessions
            if datetime.fromisoformat(x.end) <= tuesday
            and session_id(x) not in sub.outcomes
            and session_id(x) not in reported
        ]
        if not due:
            break
        first = due[0]
        reported.add(session_id(first))
        answer = "all" if first.kind in _REVIEW_KINDS else "done"
        sub = report_session(
            sub,
            session_id(first),
            answer,
            now=datetime.fromisoformat(first.end).astimezone(UTC) + timedelta(minutes=5),
        )
    low, high = (h * 60 for h in LANDING_WEEK_HOURS)

    def week(subscription: Subscription) -> tuple[list[dict], dict[str, dict]]:
        view = calendar_view(subscription, first=monday, count=5, now=tuesday)
        busy: list[dict] = []
        study: dict[str, dict] = {}
        seen: Counter[str] = Counter()
        for item in view["items"]:
            start, end = max(item["start"], low), min(item["end"], high)
            if end <= start:
                continue
            if item["kind"] == "study":
                topic = f"{item['title']}|{item['session_kind']}"
                seen[topic] += 1
                study[f"{topic}|{seen[topic]}"] = {
                    "day": item["day"],
                    "start": start,
                    "end": end,
                    "course": item["course"],
                    "color": item["color"],
                    "kind": item["session_kind"],
                }
            else:
                busy.append(
                    {
                        "day": item["day"],
                        "start": start,
                        "end": end,
                        "course": item.get("course") or "",
                        "color": item.get("color", 6),
                    }
                )
        return busy, study

    busy, before = week(sub)
    _busy, after = week(set_pause(sub, "today", now=tuesday))
    sessions = []
    for key, x in sorted(before.items(), key=lambda kv: (kv[1]["day"], kv[1]["start"])):
        moved = after.get(key)
        sessions.append({**x, "to": [moved["day"], moved["start"], moved["end"]] if moved else None})
    return {
        "monday": monday.isoformat(),
        "off": 1,
        "hours": list(LANDING_WEEK_HOURS),
        "busy": sorted(busy, key=lambda b: (b["day"], b["start"])),
        "sessions": sessions,
        "new": [
            {**x, "to": [x["day"], x["start"], x["end"]]}
            for key, x in sorted(after.items(), key=lambda kv: (kv[1]["day"], kv[1]["start"]))
            if key not in before
        ],
    }


@functools.cache
def _landing_week() -> dict:
    return json.loads(LANDING_WEEK_FILE.read_text(encoding="utf-8"))


def landing_week() -> dict:
    """The front page's week (D191) as the page draws it: each day's name, each block's
    place in quarter hours from the first hour drawn and its length, each session's
    course, kind and colour, and where it went once Tuesday was taken off."""
    data = _landing_week()
    low = data["hours"][0] * 60
    monday = date.fromisoformat(data["monday"])

    def quarters(start: int, end: int) -> tuple[int, int]:
        return (start - low) // 15 + 1, max(1, (end - start) // 15)

    def block(x: dict) -> dict:
        row, span = quarters(x["start"], x["end"])
        return {
            "day": x["day"] + 1,
            "row": row,
            "span": span,
            "course": x["course"],
            "color": x["color"] % 7,
            "time": f"{x['start'] // 60}:{x['start'] % 60:02d}",
        }

    sessions = []
    for x in data["sessions"]:
        item = {**block(x), "kind": _(KIND_LABELS.get(x["kind"], "Self-test"))}
        if x["to"] is None:
            item["to"] = None
        else:
            to = block({**x, "day": x["to"][0], "start": x["to"][1], "end": x["to"][2]})
            item["to"] = to if (to["day"], to["row"]) != (item["day"], item["row"]) else "stay"
        sessions.append(item)
    moving = [s for s in sessions if s["to"] not in ("stay",)]
    for order, s in enumerate(moving, start=1):
        s["order"] = order
    off = monday + timedelta(days=data["off"])
    return {
        "days": [
            {
                "name": format_date(monday + timedelta(days=k), "short").split(" ")[0],
                "date": (monday + timedelta(days=k)).day,
            }
            for k in range(5)
        ],
        "hours": [f"{h}:00" for h in range(data["hours"][0], data["hours"][1], 2)],
        "rows": (data["hours"][1] - data["hours"][0]) * 4,
        "busy": [block(b) for b in data["busy"]],
        "sessions": sessions,
        "new": [{**block(x), "kind": _(KIND_LABELS.get(x["kind"], "Self-test"))} for x in data["new"]],
        "off": data["off"] + 1,
        "off_day": format_date(off, "long").split(" ")[0],
        "moved": len([s for s in sessions if isinstance(s["to"], dict)]),
        "later": len([s for s in sessions if s["to"] is None]),
        "count": len(sessions),
        "today": [
            {**block(x), "kind": _(KIND_LABELS.get(x["kind"], "Self-test")), "minutes": x["end"] - x["start"]}
            for x in data["sessions"]
            if x["day"] == 0
        ],
        "today_name": format_date(monday, "long"),
        "courses": [
            {"course": course, "color": color % 7}
            for course, color in dict((x["course"], x["color"]) for x in data["sessions"]).items()
        ],
    }


def landing_view() -> dict:
    """The front page's story (D188): its numbers from the app's own rules and model,
    so the pitch and the app cannot disagree. Recall twelve weeks after the exam for a
    week of lectures spaced, crammed or studied once (FSRS-6, average weights); a
    session's points (D158); the badges, some shown; the make-up session's length
    (D126); focus's rhythm (D178); how long a plan is kept (D36); the coach's free
    answers (D176)."""
    families = {f.key: f for f in _crests.FAMILIES}
    me_points = next(points for handle, _level, points, _face in LANDING_LEAGUE if not handle)
    curves = [dict(c) for c in _landing_curves()]
    # Each curve's end is labelled beside it, kept 14 units (its text and a gap) clear of
    # the label above.
    below = -14.0
    for curve in sorted(curves, key=lambda c: c["end_y"]):
        curve["label_y"] = round(max(curve["end_y"] + 4, below + 14), 1)
        below = curve["label_y"]
    return {
        "curves": curves,
        "spacing": {c["name"]: c for c in curves},
        "width": LANDING_WIDTH,
        "exam_x": round(LANDING_WIDTH * LANDING_EXAM / (LANDING_EXAM + LANDING_AFTER), 1),
        "weeks_after": round(LANDING_AFTER / 7),
        "target": round(100 * flashcards.RETENTION),
        "points_done": POINTS_DONE,
        "points_proof": POINTS_PROOF,
        "points_all": POINTS_DONE + POINTS_PROOF,
        "badge_count": len(_crests.FAMILIES),
        "rare_count": len(_crests.RARES),
        "tiers": len(_crests.TIERS),
        "tier_points": _crests.TIER_POINTS,
        "crests": [
            {
                "key": key,
                "name": _(families[key].name),
                "icon": families[key].icon,
                "metal": _crests.TIERS[tier - 1],
                "roman": _crests.TIER_ROMAN[tier - 1],
                "ladder": _ladder_words(families[key]),
            }
            for key, tier in LANDING_CRESTS
        ],
        "league": [
            {
                "handle": handle,
                "me": not handle,
                "initial": handle[:1].upper() if handle else _("You")[:1],
                "hue": "c0",
                "face": face,
                "rank": rank,
                "level": level,
                "points": points,
                "metal": LANDING_LEAGUE_METALS[rank - 1],
                "zone": "up" if rank <= _crests.LEAGUE_MOVE else "stay",
            }
            for rank, (handle, level, points, face) in enumerate(LANDING_LEAGUE, start=1)
        ],
        "league_name": _league_words(1)["league_name"],
        "league_up": _league_words(2)["league_name"],
        "league_move": _crests.LEAGUE_MOVE,
        "league_floor": _crests.LEAGUE_FLOOR_DAYS,
        "level_names": [_(n) for n in _crests.LEVEL_NAMES],
        "course": _landing_course(),
        "holds": {c["name"]: c["holds"] for c in curves},
        "hold_percent": round(100 * LANDING_HOLD),
        "holds_ratio": format_number(
            next(c["holds"] for c in curves if c["name"] == "spaced")
            / next(c["holds"] for c in curves if c["name"] == "crammed"),
            1,
        ),
        "repair": REPAIR_MINUTES,
        "rhythm": RHYTHM_WORK // 60,
        "kept_months": round(RETENTION / timedelta(days=365) * 12),
        "coach_free": COACH_FREE_A_DAY,
        "week": landing_week(),
        "streak": LANDING_STREAK,
        "scenes": _landing_scenes(),  # D203
        # D191: you climb into the top three as the story is scrolled, from the points before
        # a session done and vouched for; the reader's own tick above adds one more session.
        "climb": {
            "before": me_points - POINTS_DONE - POINTS_PROOF,
            "after": me_points,
            "tap": me_points + POINTS_DONE,
            "gain": POINTS_DONE + POINTS_PROOF,
        },
    }


# D203: the front page's scenes (the owner: "more animations like the ones that fill the
# calendar", then "even more dopaminergic and motivating"). Their numbers come from the app's
# own rules and model, and the marks scene's from one study, so the pitch and the app agree.
LANDING_SCENE_DAYS = 7  # the made-up week of the streak scene
LANDING_SCENE_MISSED = 3  # its day missed, then repaired (Thursday)
LANDING_SCENE_TOPICS = 11  # the made-up course's topics, all on track at the end
LANDING_TUBE_FROM = 0.72  # the recall a topic seen once has fallen to when it is tested
# Yang, Luo, Vadillo, Yu and Shanks (2021), docs/REFERENCES.md yang2021: 222 classroom studies,
# 48,478 students, testing raised achievement by g = 0.499.
LANDING_TESTING = (0.499, 222, 48478)
LANDING_EXAM_DAYS = (date(2027, 2, 2), date(2027, 2, 4))  # the made-up exams of the steps scene


def _thousands(n: int) -> str:
    """A whole number with its thousands marked as the language does."""
    text = f"{n:,}"
    lang = current_language()
    return (
        text.replace(",", " ") if lang == "fr" else text.replace(",", ".") if lang in ("it", "es") else text
    )


def _landing_notes() -> list[str]:
    """The notes of the cards scene, in the page's language."""
    return [
        _("Linear algebra · lecture 6"),
        # D205: mathematics in the notes, read by D84's rules and drawn as MathML (D199).
        _("Eigenvalue: a number $\\lambda$ such that $Av = \\lambda v$ for a vector $v \\neq 0$."),
        _("Eigenvectors of distinct eigenvalues are independent."),
        _("Characteristic polynomial: $p(\\lambda) = \\det(A - \\lambda I)$"),
        _("Trace: $\\mathrm{tr}\\,A = \\sum_{i=1}^{n} a_{ii}$, the sum of the eigenvalues."),
    ]


# D205: the front page's films of the avatar, Moodle and Focus. The avatar's steps are its own
# parts' names (AVATAR_PARTS); the course page is read by `coursepages.moodle`, as one saved
# from Moodle would be; Focus's minutes and points are the app's constants.
LANDING_AVATAR_STEPS: tuple[tuple[str, dict[str, str]], ...] = (
    ("", {}),
    ("Hair", {"hair": "curly", "colour": "auburn"}),
    ("Glasses", {"glasses": "round"}),
    ("On your head", {"head": "headphones"}),
    ("Clothes", {"outfit": "hoodie", "top": "c3", "bg": "b3"}),
)
LANDING_MOODLE_SITE = "https://moodle.example.edu"  # a reserved name (RFC 2606): no real university


def _landing_avatar() -> dict:
    spec = dict(AVATAR_START)
    steps: list[dict] = []
    for k, (label, change) in enumerate(LANDING_AVATAR_STEPS):
        spec = {**spec, **change}
        steps.append({"label": _(label) if label else "", "spec": dict(spec), "at": 200 + 700 * k})
    return {
        "steps": steps,
        "post_at": 200 + 700 * len(steps) + 100,
        "skins": len(AVATAR_PARTS["skin"]),
        "hairs": len(AVATAR_PARTS["hair"]),
        "clothes": len(AVATAR_PARTS["outfit"]),
    }


def _landing_moodle() -> dict:
    """A course page in Moodle 4's markup, made up, read by the app's own rules: each file
    lands on the week its section names, in the page's language."""
    from html import escape

    slides = _("Slides, lecture {n}")
    weeks = [
        (
            1,
            _("Vectors"),
            [(slides.format(n=1), "resource", "l1.pdf"), (_("Exercise sheet 1"), "resource", "e1.pdf")],
        ),
        (
            2,
            _("Matrices"),
            [(slides.format(n=2), "resource", "l2.pdf"), (_("Recorded lecture"), "url", "rec")],
        ),
        (
            3,
            _("Eigenvalues"),
            [(slides.format(n=3), "resource", "l3.pdf"), (_("Exercise sheet 3"), "resource", "e3.pdf")],
        ),
    ]
    sections = []
    for n, topic, files in weeks:
        acts = "".join(
            f'<li class="activity modtype_{mod}">'
            f'<a href="{LANDING_MOODLE_SITE}/mod/{mod}/view.php?id={n}{k}&amp;f={f}">'
            f'<span class="instancename">{escape(title)}</span></a></li>'
            for k, (title, mod, f) in enumerate(files)
        )
        name = escape(_("Week {n}: {topic}", n=n, topic=topic))
        sections.append(
            f'<li class="section" id="section-{n}" data-sectionname="{name}"><ul>{acts}</ul></li>'
        )
    course = _("Linear algebra")
    html = (
        f"<html><head><title>{escape(_('Course'))}: {escape(course)}</title></head>"
        f'<body class="course-7"><ul>{"".join(sections)}</ul></body></html>'
    )
    reading = _coursepages.moodle(html)
    assert reading is not None
    at = 900
    placed = []
    for item in reading.items:
        placed.append({"title": item.title, "kind": item.kind, "week": item.week, "at": at})
        at += 380
    return {
        "course": course,
        "sections": [
            {"n": n, "name": _("Week {n}: {topic}", n=n, topic=topic), "files": [t for t, _m, _f in files]}
            for n, topic, files in weeks
        ],
        "weeks": [n for n, _t, _f in weeks],
        "placed": placed,  # not "items": a template reads that as the dict's own method
        "done_at": at + 200,
    }


def _landing_focus() -> dict:
    return {
        "work": RHYTHM_WORK // 60,
        "pause": RHYTHM_PAUSE // 60,
        "points": POINTS_DONE,
        "steps": [_("Read the worked example"), _("Exercises 1 to 3"), _("Exercises 4 and 5")],
    }


def _landing_scenes() -> dict:
    """The scenes' numbers: the streak's week (the badge the app gives for it), the marks of
    those who test themselves (Phi of the study's g), a self-test's refill (FSRS-6), and the
    cards the notes give by D84's rules."""
    family = next(f for f in _crests.FAMILIES if f.key == "streak")
    tier = sum(1 for t in family.thresholds if t <= LANDING_SCENE_DAYS)
    metal = _crests.TIERS[tier - 1]
    monday = date(2026, 1, 5)
    times = [300, 700, 1100, 3000, 3400, 3800, 4200]
    days = [
        {"name": _i18n.weekday_name(monday + timedelta(days=k), short=True), "at": times[k],
         "fixed": k == LANDING_SCENE_MISSED}
        for k in range(LANDING_SCENE_DAYS)
    ]  # fmt: skip
    # The marks (D205): where the average student who tests themselves stands in a class that does
    # not, if marks are bell-shaped: Cohen's U3 of Yang et al.'s g, Phi(g).
    g, studies, students = LANDING_TESTING
    beats = round(100 * 0.5 * (1 + math.erf(g / math.sqrt(2))))
    # A topic seen once, tested when its recall has fallen to about 72 %, recalled ("good").
    first = FSRS_6.initial_state(Grade.GOOD)
    after_days = round(FSRS_6.interval(first.stability, LANDING_TUBE_FROM))
    before = round(100 * FSRS_6.retrievability(after_days, first.stability))
    again = FSRS_6.review(first, after_days, Grade.GOOD)
    target = flashcards.RETENTION
    holds = math.floor(FSRS_6.interval(again.stability, target))
    # The notes and the cards D84's rules read in them.
    lines, cards = [], []
    at = 600
    for text in _landing_notes():
        found = _cardmaker.read(text)
        if found and text.startswith(found[0].front):
            rest = text[len(found[0].front) :].lstrip(" :\u00a0\u202f")
            lines.append({"term": found[0].front, "rest": rest, "at": at})
            cards.append({"front": found[0].front, "back": found[0].back, "at": at + 450})
            at += 900
        else:
            lines.append({"text": text})
    return {
        "streak": {
            "days": LANDING_SCENE_DAYS,
            "days_list": days,
            "per_day": POINTS_DONE,
            "points": POINTS_DONE * LANDING_SCENE_DAYS,
            "repair": REPAIR_MINUTES,
            "topics": LANDING_SCENE_TOPICS,
            "course": "Algebra",
            "badge_at": times[-1] + 500,
            "badge": {
                "name": _(family.name),
                "icon": family.icon,
                "metal": metal,
                "roman": _crests.TIER_ROMAN[tier - 1],
                "tier": {"bronze": _("Bronze"), "silver": _("Silver"), "gold": _("Gold"), "ice": _("Ice")}[
                    metal
                ],
            },
        },
        "grades": {
            "before": 50,  # the middle of the class
            "gain": beats - 50,
            "g": format_number(g, 3),
            "studies": studies,
            "students": _thousands(students),
            "beats": beats,
        },
        "tube": {
            "before": before,
            "after": 100,
            "after_days": after_days,
            "days": holds,
            "target": round(100 * target),
        },
        "cards": {"lines": lines, "cards": cards, "done_at": at + 300},
        # D204: a study group's list, each line ticked off by someone other than who wrote it.
        "group": {
            "name": _("Thursday library"),
            "members": social.GROUP_SIZE,
            "done_at": 3700,
            "lines": [
                {
                    "text": _("Book a room for Thursday"),
                    "by": "@maya",
                    "at": 300,
                    "tick": 2500,
                    "ticked": _("ticked off by @{handle}", handle="tom"),
                },
                {
                    "text": _("Ask the teacher about exercise 4"),
                    "by": "@tom",
                    "at": 650,
                    "tick": 3100,
                    "ticked": _("ticked off by you"),
                },
                {
                    "text": _("Bring the past paper"),
                    "by": _("you, from your list"),
                    "at": 1800,
                    "tick": 3700,
                    "ticked": _("ticked off by @{handle}", handle="maya"),
                },
            ],
        },
        "exam_days": [_i18n.format_date(d, "day_short") for d in LANDING_EXAM_DAYS],
        "avatar": _landing_avatar(),
        "moodle": _landing_moodle(),
        "focus": _landing_focus(),
    }


def garden_view() -> dict:
    """How it works (D116): the garden story's numbers. D121: the game of D116 gave way to
    the month the student plans (garden_month_view); each scene also carries its numbers
    at finer stops, for the laptop's labels, and the cramming chapter is new."""
    model = FSRS_6
    target = flashcards.RETENTION
    shaky = SCIENCE_STABILITIES[0]
    one = [
        _garden_z(model.retrievability(GARDEN_ONE_DAYS * k / (GARDEN_STOPS - 1), shaky))
        for k in range(GARDEN_STOPS)
    ]
    # Chapter 3: the same plant, watered at once and as it droops, on one shared clock.
    state = MemoryState(stability=GARDEN_WEEK, difficulty=5.0)
    soon = model.review(state, 0.0, Grade.GOOD).stability
    wait = model.interval(GARDEN_WEEK, target)
    just = model.review(state, wait, Grade.GOOD).stability
    forgot = model.review(state, wait, Grade.AGAIN).stability
    soon_water = [_garden_z(model.retrievability(d, soon)) for d in GARDEN_SPACING_DAYS]
    just_water = [
        _garden_z(
            model.retrievability(d, GARDEN_WEEK)
            if k < GARDEN_JUST_AT
            else model.retrievability(d - wait, just)
        )
        for k, d in enumerate(GARDEN_SPACING_DAYS)
    ]
    end = GARDEN_SPACING_DAYS[-1]
    spacing = {
        "soon": soon_water,
        "just": just_water,
        "holds": format_number(GARDEN_WEEK, 0),
        "wait": format_number(wait, 0),
        "grown": format_number(just, 0),
        "soon_end": round(100 * model.retrievability(end, soon)),
        "just_end": round(100 * model.retrievability(end - wait, just)),
        # The roots before watering and after (D117's scale): they grow from one to the other.
        "roots_from": _garden_roots(GARDEN_WEEK),
        "roots_to": _garden_roots(just),
        "soon_at": GARDEN_SOON_AT,
        "just_at": GARDEN_JUST_AT,
    }
    app = _garden_week(model, _garden_app_plan(model))
    # Chapter 4: the week of the code page's worked example (D109), its layers.
    code = code_view()
    week = {
        "days": [{"day": d["day"], "runs": d["runs"]} for d in code["days"]],
        "blocks": code["blocks"],
        "block_slots": code["block_slots"],
        "slots_per_day": 24 * 60 // code["slot_minutes"],
        "day_count": code["day_count"],
        "free": len(code["blocks"]),
    }
    # D146: each day as a dial, and where each evening goes, from the app's rule.
    evenings = _garden_evenings(model)
    names = {key: name for key, name, _s, _ago in GARDEN_TOPICS}
    colours = {key: k for k, (key, *_rest) in enumerate(GARDEN_TOPICS)}
    week["dials"] = _garden_dials(week, evenings, names, colours)
    week["water"] = _garden_water(evenings, names, colours)
    return {
        "week": week,
        "target": round(100 * target),
        "one": one,
        "one_days": format_number(GARDEN_ONE_DAYS, 0),
        # D121, chapter 1's steps: the day the shaky topic is down to the target, when the
        # app would revise it, and where it is after the week.
        "one_due": round(model.interval(shaky, target)),
        "one_due_recall": round(100 * model.retrievability(round(model.interval(shaky, target)), shaky)),
        "one_end": round(100 * model.retrievability(GARDEN_ONE_DAYS, shaky)),
        "fade": _science_fade(model),
        # Chapter 2's steps, on the readout's clock: each topic's water after two weeks
        # and after a month (the scene's quarters of its two months).
        "pace": {
            day: [round(100 * model.retrievability(float(day), s)) for s in SCIENCE_STABILITIES]
            for day in (14, 30)
        },
        # Chapter 2's roots, each as long as its topic holds (D117): the solid one's, a
        # month, reach the bottom of its glass.
        "pace_roots": [_garden_roots(s) for s in SCIENCE_STABILITIES],
        "one_roots": _garden_roots(shaky),
        # The opening shelf: the game's three topics as they stand on Monday; the last
        # scene: the same three on exam day after the app's week, in flower.
        "start": [{"key": key, "roots": _garden_roots(s)} for key, _, s, _ in GARDEN_TOPICS],
        "exam": app["plants"],
        "days": SCIENCE_DAYS,
        "spacing": spacing,
        "topics": [{"key": key, "name": name} for key, name, _, _ in GARDEN_TOPICS],
        "evenings": GARDEN_EVENINGS,
        "fine": _garden_fine(model, shaky, soon, just, wait, forgot, app),
        "cram": _garden_cram(model),
        "report": {
            "grown": format_number(just, 0),
            "grown_days": round(just),
            "shrunk": format_number(forgot, 0),
            "shrunk_days": max(1, round(forgot)),
            "roots_from": _garden_roots(GARDEN_WEEK),
            "roots_to": _garden_roots(just),
            "roots_shrunk": _garden_roots(forgot),
        },
    }


def _garden_fine(
    model: Model, shaky: float, soon: float, just: float, wait: float, forgot: float, app: dict
) -> dict:
    """D121: the story's numbers at GARDEN_FINE stops of each scene, for the laptop's live
    labels and its plants: each plant's water (twentieths) and roots at each stop, and the
    label it wears from each stop on ([progress, big, small]); each scene's day badge."""
    target = flashcards.RETENTION
    steps = [_fine_progress(k) for k in range(GARDEN_FINE)]
    pct = _garden_pct

    def note_fresh(recall: float) -> str:
        return (
            _("still fresh")
            if round(100 * recall) >= round(100 * target)
            else _("below {t}: time to revise", t=pct(target))
        )

    # Chapter 1: one topic over its first week.
    one_days = [GARDEN_ONE_DAYS * p for p in steps]
    one = {
        "zf": [round(20 * model.retrievability(d, shaky), 2) for d in one_days],
        "tags": [
            _garden_tag(p, model.retrievability(d, shaky), note_fresh(model.retrievability(d, shaky)))
            for p, d in zip(steps, one_days, strict=True)
        ],
        "badge": [
            [p, _("Day {n}", n=int(d)), _("since the lecture")] for p, d in zip(steps, one_days, strict=True)
        ],
    }
    # Chapter 2: three topics over two months.
    pace_days = [SCIENCE_DAYS * p for p in steps]
    pace = {
        "pots": [
            {
                "zf": [round(20 * model.retrievability(d, s), 2) for d in pace_days],
                "tags": [
                    _garden_tag(p, model.retrievability(d, s), _("lasts {n} days", n=format_number(s, 0)))
                    for p, d in zip(steps, pace_days, strict=True)
                ],
            }
            for s in SCIENCE_STABILITIES
        ],
        "badge": [
            [p, _("Day {n}", n=int(d)), _("without revising")] for p, d in zip(steps, pace_days, strict=True)
        ],
    }
    # Chapter 3: the same topic, revised at once or as it droops, on the scene's clock
    # (its eight stops' days, GARDEN_SPACING_DAYS, read between them).
    last = GARDEN_STOPS - 1
    soon_pot: dict[str, list] = {"zf": [], "rtf": [], "tags": []}
    just_pot: dict[str, list] = {"zf": [], "rtf": [], "tags": []}
    time_badge = []
    for p in steps:
        q = p * last
        i = min(int(q), last - 1)
        day = GARDEN_SPACING_DAYS[i] + (GARDEN_SPACING_DAYS[i + 1] - GARDEN_SPACING_DAYS[i]) * (q - i)
        for pot, at, after, stops in (
            (soon_pot, 0.0, soon, GARDEN_SOON_AT),
            (just_pot, wait, just, GARDEN_JUST_AT),
        ):
            done = q >= stops
            recall = model.retrievability(day - at, after) if done else model.retrievability(day, GARDEN_WEEK)
            holds = after if done else GARDEN_WEEK
            pot["zf"].append(round(20 * recall, 2))
            pot["rtf"].append(_garden_roots(holds))
            small = (
                _("now lasts {n} days", n=format_number(holds, 0))
                if done
                else _("lasts {n} days", n=format_number(GARDEN_WEEK, 0))
            )
            pot["tags"].append(_garden_tag(p, recall, small))
        time_badge.append([p, _("Day {n}", n=int(day)), _("the same topic, twice")])
    # Chapter 6: the answer after a session; the labels change as the roots do.
    grown_from, shrunk_from = 0.36, 0.7

    # AUDIT 102: the big line says what the number is ("lasts 7 days"), since a phone shows
    # no small line under it, and both plants read "7 days" alone before the answer.
    def lasts(days: float) -> str:
        return _n("lasts {n} day", "lasts {n} days", max(1, round(days)))

    morning = {
        "yes": [
            [0.0, lasts(GARDEN_WEEK), _("before the session"), False],
            [grown_from, lasts(just), _("so the next revision waits"), False],
        ],
        "no": [
            [0.0, lasts(GARDEN_WEEK), _("before the session"), False],
            [shrunk_from, lasts(forgot), _("so it comes back soon"), True],
        ],
    }
    return {
        "one": one,
        "pace": pace,
        "time": {"pots": [soon_pot, just_pot], "badge": time_badge},
        "morning": morning,
        # The opening shelf and exam day: one label each.
        "start": [[_garden_tag(0.0, 1.0, _("just revised"))] for _t in GARDEN_TOPICS],
        "exam": [[_garden_tag(0.0, plant["recall"] / 100, _("ready"))] for plant in app["plants"]],
    }


# -- How it works on a laptop, and a month to plan (D121) --------------------------------

# D121: on a laptop each scene of How it works labels its plants as the page is scrolled,
# from the scene's numbers at this many stops (its eight stops stay the flat drawing's).
GARDEN_FINE = 25
# The cramming chapter: the same new topic, crammed in the last evenings before an exam on
# day 28, or revised each time it dips to the target and once more the night before, as
# many revisions each (D142; D121 set one cram against the rule alone); neither is
# revised after the exam; a month on.
GARDEN_CRAM_EXAM = 28.0
GARDEN_CRAM_AFTER = 30.0
GARDEN_CRAM_SPLIT = 0.75  # the share of the scene before the exam (its first three steps)
# The month to plan: three made-up courses (a new one found hard, a new one, one known
# fairly well), a revision ticked on any of days 1 to 27 (each in its evening, half a day
# in), the exam on day 28; the garden shown at two moments a day under the slider.
GARDEN_MONTH_DAYS = 28
GARDEN_MONTH_COURSES: tuple[tuple[str, str], ...] = (("m0", "hard"), ("m1", "new"), ("m2", "known"))
GARDEN_MONTH_KNOWN = 12.0  # how long the course "known fairly well" holds at the start (days)
GARDEN_MONTH_SAMPLES = 2
GARDEN_MONTH_FLOOR = 0.7  # the lanes' bottom: recall from 70 % (bottom) to 100 % (top)


def _garden_pct(recall: float) -> str:
    return f"{round(100 * recall)}\u00a0%"


def _garden_tag(progress: float, recall: float, note: str) -> list:
    """A plant's label from a point of its scene on: its recall, a line under it, and
    whether the recall is below the target (the label is then drawn as a warning)."""
    low = round(100 * recall) < round(100 * flashcards.RETENTION)
    return [progress, _garden_pct(recall), note, low]


def _expected_memory(
    model: Model, state: MemoryState, start: float, times: Sequence[float]
) -> tuple[list[tuple[float, list[tuple[float, MemoryState]]]], list[float]]:
    """D121: a topic's memory over time, counting that a revision may not work. The
    model's recall is the chance of remembering the topic; a revision then works with
    that chance (grade Good) and otherwise is a lapse (Again: relearned in the session,
    held only briefly). Each revision splits the topic's future in two; the mixture after
    each one is kept, the branches whose memory ends alike merged. Returns the mixture
    from each revision on, and each revision's chance of working."""
    segments: list[tuple[float, list[tuple[float, MemoryState]]]] = [(start, [(1.0, state)])]
    chances: list[float] = []
    for t in sorted(times):
        at, mix = segments[-1]
        elapsed = max(t - at, 0.0)
        merged: dict[tuple[int, int], list[float]] = {}
        chance = 0.0
        for weight, memory in mix:
            recall = model.retrievability(elapsed, memory.stability)
            chance += weight * recall
            for grade, share in ((Grade.GOOD, weight * recall), (Grade.AGAIN, weight * (1.0 - recall))):
                if share < 1e-9:
                    continue
                after = model.review(memory, elapsed, grade)
                key = (round(math.log(after.stability) * 40), round(after.difficulty * 4))
                slot = merged.setdefault(key, [0.0, 0.0, 0.0])
                slot[0] += share
                slot[1] += share * after.stability
                slot[2] += share * after.difficulty
        chances.append(chance)
        segments.append(
            (t, [(w, MemoryState(stability=s / w, difficulty=d / w)) for w, s, d in merged.values()])
        )
    return segments, chances


def _expected_at(
    model: Model, segments: Sequence[tuple[float, list[tuple[float, MemoryState]]]], t: float
) -> tuple[float, float, float, float]:
    """The mixture at time t: the expected recall, the expected stability, the time of the
    last revision, and the chance the topic is below the target then."""
    at, mix = segments[0]
    for segment in segments:
        if segment[0] <= t:
            at, mix = segment
    recall = stability = below = 0.0
    for weight, memory in mix:
        r = model.retrievability(max(t - at, 0.0), memory.stability)
        recall += weight * r
        stability += weight * memory.stability
        if r < flashcards.RETENTION:
            below += weight
    return recall, stability, at, below


def _fine_progress(k: int) -> float:
    return round(k / (GARDEN_FINE - 1), 4)


def _garden_cram(model: Model) -> dict:
    """The cramming chapter (D121, D142): the same new topic crammed in the last evenings
    before the exam, or revised whenever it dips to the target and once more the night
    before (the app's exam practice, D46), with as many revisions each; each through the
    exam and a month on. D121 set a single cram against the rule alone, which left out the
    app's last pass and gave cramming fewer revisions."""
    target = flashcards.RETENTION
    start = model.initial_state(Grade.GOOD)
    last = GARDEN_CRAM_EXAM - 1.0
    spaced_days: list[float] = []
    for day in range(1, int(GARDEN_CRAM_EXAM)):
        trial, _chances = _expected_memory(model, start, 0.0, spaced_days)
        if _expected_at(model, trial, float(day))[0] <= target:
            spaced_days.append(float(day))
    spaced_days = sorted({*spaced_days, last})
    cram_days = [last - i for i in reversed(range(len(spaced_days)))]
    crammed, cram_chance = _expected_memory(model, start, 0.0, cram_days)
    spaced, spaced_chances = _expected_memory(model, start, 0.0, spaced_days)
    exam, later = GARDEN_CRAM_EXAM, GARDEN_CRAM_EXAM + GARDEN_CRAM_AFTER

    # Three quarters of the scene before the exam, the month after it in the last
    # quarter: each of the chapter's four steps then holds what it says.
    def day_at(p: float) -> float:
        if p <= GARDEN_CRAM_SPLIT:
            return exam * p / GARDEN_CRAM_SPLIT
        return exam + GARDEN_CRAM_AFTER * (p - GARDEN_CRAM_SPLIT) / (1 - GARDEN_CRAM_SPLIT)

    def at_day(d: float) -> float:
        return round(GARDEN_CRAM_SPLIT * d / exam, 4)

    stops = [day_at(k / (GARDEN_STOPS - 1)) for k in range(GARDEN_STOPS)]
    fine = [day_at(_fine_progress(k)) for k in range(GARDEN_FINE)]

    def pot(segments: list, days: Sequence[float], name: str) -> dict:
        at_exam = _expected_at(model, segments, exam)[0]
        tags = []
        for k, day in enumerate(fine):
            recall = _expected_at(model, segments, day)[0]
            note = _("on exam day: {n}", n=_garden_pct(at_exam)) if day >= exam else _("before the exam")
            tags.append(_garden_tag(_fine_progress(k), recall, note))
        return {
            "w": [_garden_z(_expected_at(model, segments, d)[0]) for d in stops],
            "zf": [round(20 * _expected_at(model, segments, d)[0], 2) for d in fine],
            "rtf": [_garden_roots(_expected_at(model, segments, d)[1]) for d in fine],
            "roots": _garden_roots(_expected_at(model, segments, later)[1]),
            "drops": [[at_day(d - 0.35), at_day(d + 0.45)] for d in days],
            "name": name,
            "tags": tags,
            "exam": round(100 * at_exam),
            "later": round(100 * _expected_at(model, segments, later)[0]),
        }

    badge = []
    for k, when in enumerate(fine):
        if when < exam:
            badge.append([_fine_progress(k), _("Day {n}", n=int(when)), _("exam on day {n}", n=int(exam))])
        elif when < exam + 1:
            badge.append([_fine_progress(k), _("Exam day"), _("day {n}", n=int(exam))])
        else:
            badge.append(
                [
                    _fine_progress(k),
                    _("Day {n}", n=int(when)),
                    _n("{n} day after the exam", "{n} days after the exam", int(when - exam)),
                ]
            )
    shaky = [
        round(sum(_expected_at(model, segments, d - 0.02)[3] for d in range(1, int(exam))))
        for segments in (crammed, spaced)
    ]
    cram_name = _n("Crammed the last evening", "Crammed the last {n} evenings", len(cram_days))
    return {
        "pots": [
            pot(crammed, cram_days, cram_name),
            pot(spaced, spaced_days, _("Spaced, and once more the night before")),
        ],
        "badge": badge,
        "exam_day": int(exam),
        "after": int(GARDEN_CRAM_AFTER),
        "chance": round(100 * cram_chance[0]),
        "split": GARDEN_CRAM_SPLIT,
        "revisions": len(spaced_days),
        "shaky": shaky,
        "spaced_chances": [round(100 * c) for c in spaced_chances],
    }


def _garden_month_starts(model: Model) -> list[MemoryState]:
    starts = {
        "hard": model.initial_state(Grade.HARD),
        "new": model.initial_state(Grade.GOOD),
        "known": MemoryState(stability=GARDEN_MONTH_KNOWN, difficulty=5.0),
    }
    return [starts[kind] for _, kind in GARDEN_MONTH_COURSES]


def _month_evening(day: int) -> float:
    return day - 0.5


def _month_lane_y(recall: float) -> float:
    """Where a recall is on a course's lane, from 0 (top, 100 %) to 100 (the floor)."""
    return 100 * min(1.0, max(0.0, (1 - recall) / (1 - GARDEN_MONTH_FLOOR)))


@functools.lru_cache(maxsize=1)
def _garden_month_app() -> tuple[tuple[int, ...], ...]:
    """The app's plan over the month: its rule (assistant.py's self-tests), each evening
    the course that has faded most, once it is down to the target, otherwise the evening
    free; then a last pass before the exam, one course an evening in the last ones, the
    hardest last, as the exam practice it plans in the final days (D46, D142)."""
    model, target = FSRS_6, flashcards.RETENTION
    starts = _garden_month_starts(model)
    plan: list[list[int]] = [[] for _ in starts]
    for day in range(1, GARDEN_MONTH_DAYS):
        recalls = [
            _expected_at(
                model,
                _expected_memory(model, s, 0.0, [_month_evening(d) for d in plan[i]])[0],
                _month_evening(day),
            )[0]
            for i, s in enumerate(starts)
        ]
        k = min(range(len(starts)), key=recalls.__getitem__)
        if recalls[k] <= target:
            plan[k].append(day)
    last_day = GARDEN_MONTH_DAYS - 1
    for i, days in enumerate(plan):
        if last_day - i not in days:
            days.append(last_day - i)
    return tuple(tuple(sorted(days)) for days in plan)


def _garden_month_score(model: Model, plan: Sequence[Collection[int]]) -> dict:
    """A plan scored three ways: each course on exam day, its days below the target
    (before that evening's revision), and a month after the exam."""
    exam = GARDEN_MONTH_DAYS - 0.5
    later = exam + GARDEN_CRAM_AFTER
    memories = [
        _expected_memory(model, s, 0.0, [_month_evening(d) for d in sorted(plan[i])])
        for i, s in enumerate(_garden_month_starts(model))
    ]
    return {
        "memories": memories,
        "exam": [_expected_at(model, segments, exam)[0] for segments, _chances in memories],
        "later": [_expected_at(model, segments, later)[0] for segments, _chances in memories],
        "shaky": [
            sum(_expected_at(model, segments, d - 0.55)[3] for d in range(1, GARDEN_MONTH_DAYS))
            for segments, _chances in memories
        ],
        "count": sum(len(days) for days in plan),
    }


GARDEN_MONTH_PRESETS = ("app", "cram", "clear")


def garden_month_view(ticks: Collection[str] | None = None, preset: str | None = None) -> dict:
    """D121: the month a student plans on How it works. Three made-up courses; a revision
    ticked ("<course>-<day>") on any of days 1 to 27; the exam on day 28. Each course's
    memory through the month as the app's model sees it, counting that a revision may not
    work; the plan scored three ways (exam day, days below the target, a month later)
    against the app's own plan; and what the comparison says. No ticks and no preset: the
    app's plan."""
    model, target = FSRS_6, flashcards.RETENTION
    last_day = GARDEN_MONTH_DAYS - 1
    count = len(GARDEN_MONTH_COURSES)
    app = [set(days) for days in _garden_month_app()]
    if preset is not None and preset not in GARDEN_MONTH_PRESETS:
        raise InvalidInput(_("choose a plan from the buttons"))
    if preset == "cram":
        # As many revisions as the app's plan (D142), all in the last evenings, one course
        # an evening, the hardest last: the best a cram can do on exam day with that effort.
        plan: list[set[int]] = [set() for _ in range(count)]
        for k in range(sum(len(days) for days in app)):
            plan[k % count].add(last_day - k)
    elif preset == "clear":
        plan = [set() for _ in range(count)]
    elif preset == "app" or ticks is None:
        plan = [set(days) for days in app]
    else:
        plan = [set() for _ in range(count)]
        for tick in ticks:
            course, _sep, day = tick.partition("-")
            if not (course.isdigit() and day.isdigit() and int(course) < count and 1 <= int(day) <= last_day):
                raise InvalidInput(_("tick a course on a day from 1 to {n}", n=last_day))
            plan[int(course)].add(int(day))
    mine, theirs = _garden_month_score(model, plan), _garden_month_score(model, app)
    exam = GARDEN_MONTH_DAYS - 0.5
    names = {"m0": _("Analysis"), "m1": _("Algebra"), "m2": _("Physics")}
    notes = {
        "hard": _("new this week, and you found it hard"),
        "new": _("new this week"),
        "known": _("you know it fairly well"),
    }
    times = [s / GARDEN_MONTH_SAMPLES for s in range(GARDEN_MONTH_SAMPLES * (GARDEN_MONTH_DAYS - 1) + 2)]
    courses = []
    for i, (key, kind) in enumerate(GARDEN_MONTH_COURSES):
        segments, chances = mine["memories"][i]
        days = sorted(plan[i])
        # The lane: recall from the floor (bottom) to 100 % (top), a quarter day at a time.
        points = []
        for q in range(4 * GARDEN_MONTH_DAYS - 1):
            t = q / 4
            recall = _expected_at(model, segments, t)[0]
            points.append(f"{100 * t / GARDEN_MONTH_DAYS:.2f},{_month_lane_y(recall):.2f}")
        tags = []
        for t in times:
            recall, stability, last, _below = _expected_at(model, segments, t)
            left = round(model.interval(stability, target) - (t - last))
            note = (
                _("below {t}: revise it", t=_garden_pct(target))
                if round(100 * recall) < round(100 * target)
                else _n(
                    "below {t} in about {n} day",
                    "below {t} in about {n} days",
                    max(1, left),
                    t=_garden_pct(target),
                )
            )
            tags.append(_garden_tag(round(t / exam, 4), recall, note))
        state_exam = _expected_at(model, segments, exam)
        courses.append(
            {
                "index": i,
                "key": key,
                "name": names[key],
                "note": notes[kind],
                "path": "M" + "L".join(points),
                "cells": [
                    {
                        "day": d,
                        "tick": f"{i}-{d}",
                        "on": d in plan[i],
                        "chance": round(100 * chances[days.index(d)]) if d in plan[i] else None,
                    }
                    for d in range(1, GARDEN_MONTH_DAYS)
                ],
                "exam": round(100 * mine["exam"][i]),
                "later": round(100 * mine["later"][i]),
                "shaky": round(mine["shaky"][i]),
                # The garden under the slider: water and roots at each moment, and its label.
                "zf": [round(20 * _expected_at(model, segments, t)[0], 2) for t in times],
                "rtf": [_garden_roots(_expected_at(model, segments, t)[1]) for t in times],
                "z": _garden_z(state_exam[0]),
                "roots": _garden_roots(state_exam[1]),
                "drops": [
                    [round((_month_evening(d) - 0.35) / exam, 4), round((_month_evening(d) + 0.45) / exam, 4)]
                    for d in days
                ],
                "tags": tags,
            }
        )
    a, b = min(mine["exam"]), min(theirs["exam"])
    shaky_mine, shaky_app = round(sum(mine["shaky"])), round(sum(theirs["shaky"]))
    later_mine, later_app = min(mine["later"]), min(theirs["later"])
    pa, pb, la, lb = round(100 * a), round(100 * b), round(100 * later_mine), round(100 * later_app)
    is_app = [set(p) for p in plan] == app
    verdict: list[str] = []
    if is_app:
        verdict.append(
            _(
                "This is the app's plan: no course stays below {t} for long, "
                "and all three are ready for the exam.",
                t=_garden_pct(target),
            )
        )
    else:
        if pa > pb:
            verdict.append(
                _(
                    "You win exam day: {a} against {b} for the weakest course.",
                    a=_garden_pct(a),
                    b=_garden_pct(b),
                )
            )
            if all(days and max(days) >= last_day - 2 for days in plan):
                verdict.append(_("Revising late does work for the next day."))
        elif pa < pb:
            verdict.append(
                _(
                    "The app's plan does better on exam day: {b} against your {a}.",
                    a=_garden_pct(a),
                    b=_garden_pct(b),
                )
            )
        else:
            verdict.append(_("Both plans reach {a} on exam day.", a=_garden_pct(a)))
        if shaky_mine > shaky_app and pa > pb:
            verdict.append(
                _n(
                    "But between them, your courses spend {n} day below {t} (the app's plan: {m}), "
                    "so you'd be lost in any lecture that builds on them.",
                    "But between them, your courses spend {n} days below {t} (the app's plan: {m}), "
                    "so you'd be lost in any lecture that builds on them.",
                    shaky_mine,
                    t=_garden_pct(target),
                    m=shaky_app,
                )
            )
        elif shaky_mine > shaky_app:
            # D142: the app's plan already won exam day, so this is one more reason, not a "but".
            verdict.append(
                _n(
                    "And between them, your courses spend {n} day below {t} (the app's plan: {m}), "
                    "so you'd be lost in any lecture that builds on them.",
                    "And between them, your courses spend {n} days below {t} (the app's plan: {m}), "
                    "so you'd be lost in any lecture that builds on them.",
                    shaky_mine,
                    t=_garden_pct(target),
                    m=shaky_app,
                )
            )
        if la != lb:
            verdict.append(
                _(
                    "A month after the exam: {a} against {b}.",
                    a=_garden_pct(later_mine),
                    b=_garden_pct(later_app),
                )
            )
        # More revisions are more evenings, which chapter 5 says are few; fewer are evenings
        # saved, which the verdict says too (D142).
        if 0 < mine["count"] < theirs["count"]:
            verdict.append(
                _n(
                    "It does save evenings: {n} revision, against the app's {m}.",
                    "It does save evenings: {n} revisions, against the app's {m}.",
                    mine["count"],
                    m=theirs["count"],
                )
            )
        if mine["count"] > theirs["count"]:
            verdict.append(
                _n(
                    "And it takes {n} revision, against the app's {m}.",
                    "And it takes {n} revisions, against the app's {m}.",
                    mine["count"],
                    m=theirs["count"],
                )
            )
    # The day under the slider: day d runs from d - 1 to d, its evening half-way through.
    badge = [
        [round(t / exam, 4), _("Day {n}", n=int(t) + 1), _("exam on day {n}", n=GARDEN_MONTH_DAYS)]
        if t < exam
        else [round(t / exam, 4), _("Exam day"), _("day {n}", n=GARDEN_MONTH_DAYS)]
        for t in times
    ]
    return {
        "days": GARDEN_MONTH_DAYS,
        "badge": badge,
        "last_day": last_day,
        "target": round(100 * target),
        "courses": courses,
        "count": mine["count"],
        "app_count": theirs["count"],
        "app": {"exam": pb, "shaky": shaky_app, "later": lb},
        "lowest": {"exam": pa, "shaky": shaky_mine, "later": la},
        "verdict": verdict,
        "is_app": is_app,
        "exam_x": round(100 * exam / GARDEN_MONTH_DAYS, 2),
        "target_y": round(_month_lane_y(target), 2),
        "samples": len(times),
    }


# -- How the code works (D109) ---------------------------------------------------------

# The worked example the page runs, demo.py's part 3: two topics, an exam 21 days away,
# a student's working week of five days, one 1.5-hour block an evening. Small enough to
# solve exhaustively as the page is drawn, so every number on it is the code's own.
CODE_DAYS = 5
CODE_EXAM_DAYS = 21.0
CODE_TOPICS = (("Analysis", 2.0, 7.0), ("Algebra", 4.0, 5.0))  # name, stability, difficulty
CODE_HEURISTICS = ("zero", "closed", "ssp", "capacity")
CODE_TABLE_STABILITIES = (1.0, 2.0, 4.0, 8.0, 16.0)  # the value table's columns (days)
CODE_BLOCK_SLOTS = 3  # a study block: three half hours, plan.Instance.build's default
CODE_SLEEP = (23, 7)  # hours taken by sleep, every day
CODE_CLASSES = (8, 18)  # hours taken by classes, on weekdays
# D112: the value table as it fills, at these sweeps and the last (8 stops, one style
# class each: the landscape's towers grow through them as the page is scrolled).
CODE_SWEEP_STOPS = (0, 1, 2, 3, 4, 6, 10)
# D112: the search drawn as it runs, its expansions in this many steps of the scroll.
CODE_SEARCH_STEPS = 25


@functools.cache
def _code_solved() -> dict:
    """The worked example solved, once per process (a few seconds): raw numbers, with
    the traces of value iteration and of each AO* run (D112), formatted per request in
    the reader's language by `code_view`."""
    from . import plan as _plan
    from . import ssp as _ssp
    from .memory import review as _review
    from .memory import stability_for_interval
    from .timegrid import TimeGrid

    weekdays = [d for d in range(CODE_DAYS) if d % 7 < 5]
    sleep = TimeGrid(days=CODE_DAYS).block_daily(*CODE_SLEEP)  # what is taken by sleep, the rest by classes
    grid = sleep.block_daily(*CODE_CLASSES, days=weekdays)
    target = stability_for_interval(CODE_EXAM_DAYS, 0.9)
    topics = [(name, MemoryState(stability, difficulty)) for name, stability, difficulty in CODE_TOPICS]
    # The value table after every sweep (D112), for the landscape that fills as it scrolls.
    sweeps: list[np.ndarray] = []
    policy = _ssp.solve(_ssp.SSPConfig.for_heuristic(target), trace=sweeps)
    instance = _plan.Instance.build(
        grid, topics, target, policy, block_slots=CODE_BLOCK_SLOTS, max_blocks_per_day=1
    )
    exact = _plan.solve_exact(instance)
    heuristics = {
        "zero": _plan.zero_heuristic,
        "closed": _plan.closed_form_heuristic,
        "ssp": _plan.ssp_heuristic,
        "capacity": _plan.capacity_heuristic,
    }
    runs = []
    traces: dict[str, list[_plan.Expansion]] = {}
    for key in CODE_HEURISTICS:
        # Each run's expansions in order (D112): the page draws the search as it ran.
        traces[key] = []
        solution = _plan.solve_ao_star(instance, heuristics[key], trace=traces[key])
        runs.append((key, heuristics[key](instance, instance.initial), solution.nodes, solution.value))
    # Admissible: the heuristic the planner uses never above the true cost, at every
    # reachable state, not only at the root (CLAUDE.md, invariant 2).
    used = heuristics["capacity"]
    over = max(used(instance, state) - value for state, value in exact.values.items())
    overshoots = sum(1 for state, value in exact.values.items() if used(instance, state) > value + 1e-9)

    def value_of(state: _plan.PlanState) -> float:
        return exact.values.get(state, instance.terminal_cost(state))

    root = instance.initial
    choices = []
    for action in instance.actions(root):
        cost, outcomes = instance.successors(root, action)
        rows = [
            (
                p,
                value_of(child),
                used(instance, child),
                child.topics[action].stability if action != _plan.SKIP else None,
            )
            for p, child in outcomes
        ]
        q = cost + sum(p * value_of(child) for p, child in outcomes)
        choices.append((instance.topics[action] if action != _plan.SKIP else "", cost, q, rows))
    # How far each topic could get in the window at best: every block on it, every
    # recall a success (demo.py part 4). Short of the target, its penalty is paid
    # whatever the plan does.
    reach = []
    for name, memory in topics:
        state, last = memory, 0.0
        for block in instance.blocks:
            state = _review(state, block.start_day - last, Grade.GOOD)
            last = block.start_day
        reach.append((name, state.stability))
    # Every evening left free, the plan the search chose here: its score, term by term.
    end = root
    while not instance.is_terminal(end):
        _, ((_, end),) = instance.successors(end, _plan.SKIP)
    unready = instance.unready_count(end)
    owed = instance.terminal_cost(end) - instance.lateness_penalty * unready
    return {
        "grid": grid,
        "sleep": sleep,
        "all_free": (0.0, owed, unready, instance.terminal_cost(end)),
        "overshoots": overshoots,
        "states_checked": len(exact.values),
        "target": target,
        "topics": topics,
        "policy": policy,
        "instance": instance,
        "exact": (exact.value, exact.nodes),
        "runs": runs,
        "admissible": over <= 1e-9,
        "choices": choices,
        "reach": reach,
        "sweeps": sweeps,
        "traces": traces,
        "heuristics": heuristics,
    }


def _code_sweeps(solved: dict, rows_at: list[int], cols_at: list[int]) -> dict:
    """D112: the value table filling, sweep by sweep: the sampled cells' tower heights
    at each stop (twentieths of the final table's largest sampled value, one style class
    per stop and height), and the largest change anywhere in the table at that sweep."""
    sweeps = solved["sweeps"]
    final = sweeps[-1]
    top = max(float(final[i, j]) for i in rows_at for j in cols_at) or 1.0
    stops = [s for s in CODE_SWEEP_STOPS if s < len(sweeps)] + [len(sweeps)]
    zero = np.zeros_like(final)

    def table_at(sweep: int) -> np.ndarray:
        return zero if sweep == 0 else sweeps[sweep - 1]

    def change_at(sweep: int) -> str:
        if sweep == 0:
            return ""
        change = float(np.max(np.abs(table_at(sweep) - table_at(sweep - 1))))
        return format_number(change, 2) if change >= 0.01 else f"{change:.0e}"

    heights = {
        (i, j): [max(0, round(20 * float(table_at(s)[i, j]) / top)) for s in stops]
        for i in rows_at
        for j in cols_at
    }
    return {
        "stops": [{"sweep": s, "change": change_at(s)} for s in stops],
        "heights": heights,
    }


def _is_lapse(instance: Any, state: Any, action: int, child: Any) -> bool:
    """Whether `child` is the forgotten outcome of reviewing in `state` (a review has two
    outcomes, recalled then forgotten; leaving the block free has one)."""
    _, outcomes = instance.successors(state, action)
    return len(outcomes) == 2 and outcomes[1][1] == child


def _code_search(solved: dict) -> dict:
    """D112: AO* drawn as it ran, from its trace. Each state the search opened, and each
    one its marked solution graph reached, sits on a ring per evening (tonight in the
    centre), placed under the state that first produced it; the angle each gets is in
    proportion to what grows beneath it. The expansions are cut into
    `CODE_SEARCH_STEPS` steps of the scroll over the longest run (the one with no
    guess); at the end of each step, the best plan so far (the marked graph). And the
    root's value after each expansion, for the guess the planner uses and for none:
    a lower bound that rises until it meets the optimum."""
    from . import plan as _plan

    instance = solved["instance"]
    trace = solved["traces"]["capacity"]
    longest = max(len(t) for t in solved["traces"].values())
    steps = CODE_SEARCH_STEPS

    def step_of(expansion: int) -> int:  # expansion counts from 1
        return min(steps - 1, (expansion - 1) * steps // longest)

    shown = {e.state for e in trace} | {edge[2] for e in trace for edge in e.marked}
    root = instance.initial
    opened_at = {e.state: k for k, e in enumerate(trace, start=1)}
    parent: dict[Any, tuple[Any, int, int]] = {}
    kids: dict[Any, list[Any]] = {root: []}
    for e in trace:
        for action in instance.actions(e.state):
            _, outcomes = instance.successors(e.state, action)
            for k, (_, child) in enumerate(outcomes):
                if child in shown and child not in parent and child != root:
                    parent[child] = (e.state, action, k)
                    kids.setdefault(e.state, []).append(child)
                    kids.setdefault(child, [])

    leaves: dict[Any, int] = {}

    def count(node: Any) -> int:
        leaves[node] = max(1, sum(count(c) for c in kids.get(node, [])))
        return leaves[node]

    count(root)
    rings = (0.0, 38.0, 72.0, 104.0, 134.0, 162.0)
    place: dict[Any, tuple[float, float]] = {}

    def lay(node: Any, start: float, span: float) -> None:
        angle = start + span / 2
        radius = rings[min(node.block_index, len(rings) - 1)]
        place[node] = (round(radius * math.cos(angle), 1), round(radius * math.sin(angle), 1))
        at = start
        for c in kids.get(node, []):
            share = span * leaves[c] / leaves[node]
            lay(c, at, share)
            at += share

    lay(root, -math.pi / 2 - math.pi, 2 * math.pi)

    def tone(action: int) -> str:
        return "skip" if action == _plan.SKIP else f"c{action}"

    dots, edges = [], []
    for node, (x, y) in place.items():
        made = step_of(opened_at[parent[node][0]]) if node in parent else 0
        opened = opened_at.get(node)
        dots.append(
            {
                "x": x,
                "y": y,
                "made": made,
                "opened": step_of(opened) if opened else None,
                "tone": tone(parent[node][1]) if node in parent else "root",
            }
        )
        if node in parent:
            above, action, k = parent[node]
            px, py = place[above]
            edges.append(
                {"x1": px, "y1": py, "x2": x, "y2": y, "made": made, "tone": tone(action), "lapse": k == 1}
            )
    # The marked graph at the end of each step, until the search is done.
    last = step_of(len(trace))
    plans = []
    for s in range(last + 1):
        within = [k for k in range(1, len(trace) + 1) if step_of(k) == s]
        if not within:
            continue
        marked = trace[within[-1] - 1].marked
        plans.append(
            {
                "step": s,
                "final": s == last,
                "edges": [
                    {
                        "x1": place[a][0],
                        "y1": place[a][1],
                        "x2": place[b][0],
                        "y2": place[b][1],
                        "tone": tone(action),
                        "lapse": _is_lapse(instance, a, action, b),
                    }
                    for a, action, b in marked
                ],
            }
        )
    # The root's value as the search runs, for the planner's guess and for none: x by
    # expansion over the longest run, y by value up to the next whole number past the
    # optimum, drawn as steps.
    optimum = solved["exact"][0]
    ceiling = math.floor(optimum) + 1

    def line(key: str) -> str:
        values = [solved["heuristics"][key](instance, root)] + [e.root_value for e in solved["traces"][key]]
        points, before = [], None
        for k, v in enumerate(values):
            x = round(20 + 300 * k / longest, 1)
            y = round(100 - 88 * v / ceiling, 1)
            if before is not None:
                points.append(f"{x},{before}")
            points.append(f"{x},{y}")
            before = y
        return " ".join(points)

    counts = [
        {"step": s, "opened": sum(1 for k in range(1, len(trace) + 1) if step_of(k) <= s)}
        for s in range(steps)
    ]
    return {
        "dots": dots,
        "edges": edges,
        "plans": plans,
        "rings": [{"r": r, "n": k + 1} for k, r in enumerate(rings) if k],
        "guided": line("capacity"),
        "blind": line("zero"),
        "optimum_y": round(100 - 88 * optimum / ceiling, 1),
        "done_x": round(20 + 300 * len(trace) / longest, 1),
        "counts": counts,
        "last": last,
        "start": format_number(solved["heuristics"]["capacity"](instance, root), 2),
    }


def code_view() -> dict:
    """The page that shows how the research planner works (D109), from its own code:
    the calendar as a bitmask (`timegrid`), a topic's memory state and what a review
    does to it (`memory`, FSRS-4.5, the planner's model), the cost to reach the target
    computed by value iteration (`ssp`, SSP-MMC), four heuristics and AO* on the
    AND/OR graph (`plan`), checked against an exhaustive solve at every reachable
    state."""
    from fractions import Fraction

    from .memory import DECAY, FACTOR
    from .memory import review as _review

    solved = _code_solved()
    grid, policy, instance, topics = solved["grid"], solved["policy"], solved["instance"], solved["topics"]
    most = max(nodes for _, _, nodes, _ in solved["runs"])
    exact_value, exact_states = solved["exact"]
    runs = [
        {
            "key": key,
            "h": format_number(h, 3),
            "nodes": format_number(nodes, 0),
            "value": format_number(value, 5),
            "optimal": abs(value - exact_value) < 1e-9,
            "width": round(100 * nodes / most),
        }
        for key, h, nodes, value in solved["runs"]
    ]

    # The calendar: one bit per half hour, set when busy; the blocks cut from what is free.
    per_day = grid.slots_per_day
    days: list[dict[str, Any]] = []
    for day in range(CODE_DAYS):
        bits = "".join("1" if not grid.is_free(day * per_day + k, 1) else "0" for k in range(per_day))
        # The same day as the calendar shows it: each stretch of taken time, sleep or
        # classes, as one block.
        stretches, k = [], 0
        while k < per_day:
            if bits[k] == "0":
                k += 1
                continue
            start = k
            while k < per_day and bits[k] == "1":
                k += 1
            asleep = not solved["sleep"].is_free(day * per_day + start, 1)
            stretches.append({"kind": "sleep" if asleep else "classes", "start": start, "end": k})
        days.append(
            {
                "day": day,
                "bits": bits,
                "groups": [bits[i : i + 8] for i in range(0, per_day, 8)],
                "runs": stretches,
            }
        )
    blocks = []
    for b in instance.blocks:
        at = b.slot - b.day * per_day
        blocks.append({"day": b.day, "slot": at, "hour": f"{at // 2:02d}:{30 * (at % 2):02d}"})
    # The bits each mask sets, slot by slot (D112: sleep's, then classes' by an OR).
    for d in days:
        d["set"] = [{"kind": r["kind"], "slots": list(range(r["start"], r["end"]))} for r in d["runs"]]

    # The root of the AND/OR graph: tonight's block, its choices, each with its outcomes,
    # valued by the exhaustive solve.
    choices = [
        {
            "topic": name,
            "cost": format_number(cost, 0),
            "q": format_number(q, 2),
            "q_raw": q,
            "outcomes": [
                {
                    "p": round(100 * p),
                    "value": format_number(value, 2),
                    "h": format_number(h, 2),
                    "stability": format_number(stability, 1) if stability is not None else "",
                }
                for p, value, h, stability in rows
            ],
        }
        for name, cost, q, rows in solved["choices"]
    ]
    best = min(choices, key=lambda c: c["q_raw"])
    for c in choices:
        c["best"] = c is best

    # Memory: each topic's recall over the days to the exam if nothing is done, and what
    # tonight's review does to the first topic, recalled or forgotten.
    colour = {name: f"c{i}" for i, (name, _) in enumerate(topics)}
    for c in choices:
        c["color"] = colour.get(c["topic"], "")
    curves = []
    for (name, memory), level in zip(topics, ("review", "good"), strict=True):
        points = [
            (t / 4, retrievability(t / 4, memory.stability)) for t in range(int(4 * CODE_EXAM_DAYS) + 1)
        ]
        curves.append(
            {
                "topic": name,
                "stability": format_number(memory.stability, 0),
                "difficulty": format_number(memory.difficulty, 0),
                "level": level,
                "points": _curve(points, CODE_EXAM_DAYS, 1.0),
                "at_exam": round(100 * retrievability(CODE_EXAM_DAYS, memory.stability)),
                "end_y": round(100 - 100 * retrievability(CODE_EXAM_DAYS, memory.stability), 1),
                "color": colour[name],
            }
        )
    _apart(curves)
    first = topics[0][1]
    delay = instance.blocks[0].start_day
    recalled = _review(first, delay, Grade.GOOD)
    forgot = _review(first, delay, Grade.AGAIN)

    # The value table V*(D, S): expected reviews to reach the target, a few rows and
    # columns of the solved grid, each cell shaded by its value.
    d_grid, s_grid = policy.difficulty_grid, policy.stability_grid
    rows_at = [i for i in range(len(d_grid)) if i % max(1, len(d_grid) // 5) == 0][:5]
    # Columns at stabilities from a day to about two weeks, each the grid's nearest.
    cols_at = sorted({int(np.argmin(np.abs(s_grid - wanted))) for wanted in CODE_TABLE_STABILITIES})
    top = max(float(policy.expected_cost[i, j]) for i in rows_at for j in cols_at) or 1.0
    filling = _code_sweeps(solved, rows_at, cols_at)
    table: list[dict[str, Any]] = [
        {
            "difficulty": format_number(float(d_grid[i]), 1),
            "cells": [
                {
                    "value": format_number(float(policy.expected_cost[i, j]), 1),
                    "shade": min(4, int(5 * float(policy.expected_cost[i, j]) / (top + 1e-9))),
                    # The tower's height in the 3D landscape (D111): twentieths of the
                    # table's largest value, one style class each (no inline style).
                    "z": max(1, round(20 * float(policy.expected_cost[i, j]) / top)),
                    # Its height at each of the sweeps the landscape grows through (D112).
                    "zq": filling["heights"][(i, j)],
                }
                for j in cols_at
            ],
        }
        for i in rows_at
    ]
    for row, i in zip(table, rows_at, strict=True):
        for cell, j in zip(row["cells"], cols_at, strict=True):
            cell["topics"] = [
                {"topic": name, "color": colour[name]}
                for name, m in topics
                if min(rows_at, key=lambda r: abs(float(d_grid[r]) - m.difficulty)) == i
                and min(cols_at, key=lambda c: abs(float(s_grid[c]) - m.stability)) == j
            ]
    h_topics = [(name, policy.expected_reviews(m.difficulty, m.stability)) for name, m in topics]
    factor = Fraction(FACTOR).limit_denominator(100)
    return {
        "days": days,
        "slot_minutes": 24 * 60 // per_day,
        "slots": CODE_DAYS * per_day,
        "busy_slots": sum(d["bits"].count("1") for d in days),
        "free_slots": sum(d["bits"].count("0") for d in days),
        "blocks": blocks,
        "block_hours": format_number(CODE_BLOCK_SLOTS * 24 / per_day, 1),
        "block_slots": CODE_BLOCK_SLOTS,
        "day_count": CODE_DAYS,
        "exam_days": format_number(CODE_EXAM_DAYS, 0),
        "target": format_number(solved["target"], 0),
        "decay": format_number(DECAY, 1),
        "factor": f"{factor.numerator}/{factor.denominator}",
        "curves": curves,
        "review": {
            "topic": topics[0][0],
            "delay": format_number(delay, 2),
            "recall": round(100 * retrievability(delay, first.stability)),
            "lapse": 100 - round(100 * retrievability(delay, first.stability)),
            "recalled": format_number(recalled.stability, 1),
            "forgot": format_number(forgot.stability, 1),
        },
        "penalty": format_number(instance.lateness_penalty, 0),
        "sweeps": policy.sweeps,
        "residual": f"{policy.residual:.0e}",
        "table": table,
        "columns": [format_number(float(s_grid[j]), 0) for j in cols_at],
        "h_topics": [{"topic": name, "value": format_number(v, 3)} for name, v in h_topics],
        "h_sum": format_number(sum(v for _, v in h_topics), 3),
        "runs": runs,
        "exact": {"value": format_number(exact_value, 5), "states": format_number(exact_states, 0)},
        "admissible": solved["admissible"],
        "reach": [
            {"topic": name, "stability": format_number(v, 1), "color": colour[name]}
            for name, v in solved["reach"]
        ],
        # The plan the search chose, every evening left free, term by term (its total
        # is the exhaustive optimum: tests/test_how.py).
        "score": {
            "blocks": format_number(solved["all_free"][0], 0),
            "owed": format_number(solved["all_free"][1], 2),
            "unready": solved["all_free"][2],
            "penalties": format_number(instance.lateness_penalty * solved["all_free"][2], 0),
            "total": format_number(solved["all_free"][3], 2),
        },
        "futures": sum(len(c["outcomes"]) for c in choices),
        "overshoots": format_number(solved["overshoots"], 0),
        "opened_share": max(1, round(100 * min(n for _, _, n, _ in solved["runs"]) / exact_states)),
        "tonight_x": round(300 * delay / CODE_EXAM_DAYS, 1),
        "reachable": any(v >= solved["target"] for _, v in solved["reach"]),
        "choices": choices,
        "best": best["topic"],
        "best_q": best["q"],
        # D112: the scenes that run as the page is scrolled.
        "sleep": {"from": f"{CODE_SLEEP[0]:02d}:00", "to": f"{CODE_SLEEP[1]:02d}:00"},
        "classes": {"from": f"{CODE_CLASSES[0]:02d}:00", "to": f"{CODE_CLASSES[1]:02d}:00"},
        "sweep_stops": filling["stops"],
        "search": _code_search(solved),
        "replans": int(CODE_EXAM_DAYS) - CODE_DAYS + 1,
    }
