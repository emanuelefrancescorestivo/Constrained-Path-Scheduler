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

import contextlib
import functools
import hashlib
import math
import os
import re
import secrets
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from . import i18n as _i18n
from . import progress as _progress
from . import social as social
from .assistant import Exam, Pin, Preferences, Task, Topic, blocks_for_hours
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
    Grade,
    MemoryState,
    initial_state,
    interval_for_retention,
    retrievability,
    review,
)
from .plan import Block, tile_free_time
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


def familiarity_prior(level: int) -> MemoryState:
    """Map a 1-to-5 self-rating to a starting memory state. A prior; see module docs."""
    if level not in (1, 2, 3, 4, 5):
        raise InvalidInput(f"familiarity must be 1 to 5, got {level!r}")
    if level <= 4:
        return initial_state(Grade(level))
    easy = initial_state(Grade.EASY)
    return review(easy, interval_for_retention(easy.stability, 0.9), Grade.GOOD)


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

    def memory(self) -> tuple[MemoryState, str]:
        """The starting memory state and a label saying where it came from."""
        if self.stability is not None and self.difficulty is not None:
            try:
                return MemoryState(float(self.stability), float(self.difficulty)), "explicit"
            except ValueError as exc:
                raise InvalidInput(f"{self.name}: {exc}") from exc
        if self.familiarity is not None:
            return familiarity_prior(int(self.familiarity)), f"familiarity {int(self.familiarity)}"
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
    source: str = "calendar"  # "calendar" (the .ics) or "typed" (busy rows)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "start": self.start, "end": self.end, "source": self.source}


@dataclass(frozen=True)
class BlockView:
    slot: int
    day: int
    start_day: float
    start: str
    end: str

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "day": self.day,
            "start_day": self.start_day,
            "start": self.start,
            "end": self.end,
        }

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
) -> CalendarReport:
    """Read a calendar (an `.ics`, typed rows, or both) and find the free blocks.

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
    text = decode_ics(ics) if ics is not None else None

    if days is not None and days < 1:
        raise InvalidInput("the horizon must be at least one day")
    found = find_deadlines(_events(text, rows, start, max(days or 0, ASSESSMENT_SEARCH_DAYS), tz))
    lectures = _lectures(text, start, tz)
    if days is None:
        latest = max((_days_after(start, d.when) for d in found), default=21.0)
        days = max(1, math.ceil(latest - 1e-9))

    tagged = _tagged_events(text, rows, start, days, tz)
    events = [e for e, _ in tagged]
    grid = availability_from_events(events, start, days, SLOTS_PER_DAY, study_window)
    minutes_per_slot = 24 * 60 // grid.slots_per_day
    block_slots = max(1, block_minutes // minutes_per_slot)
    tiles = tile_free_time(grid, block_slots=block_slots, max_blocks_per_day=blocks_per_day)
    zone = _zone(tz)
    midnight = datetime.combine(start, time(0, 0), tzinfo=zone)

    def clock(slot: int) -> datetime:
        return midnight + timedelta(minutes=slot * minutes_per_slot)

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
    )


LECTURE_LOOKBACK_DAYS = 183


def _lectures(ics_text: str | None, start: date, tz: str) -> tuple[LectureView, ...]:
    """Teaching events of the .ics around the plan: see `CalendarReport.lectures`."""
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
    return tuple(LectureView(course, _iso(e.start), _iso(e.end)) for course, e in find_lectures(events))


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
    outcome: str  # "recalled", "lapsed" or "skipped"
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


def _resolve(report: CalendarReport, subjects: Sequence[SubjectSpec]) -> tuple[list[dict[str, Any]], float]:
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
        memory, prior = spec.memory()
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


def _topics(report: CalendarReport, specs: Sequence[dict]) -> list[dict[str, Any]]:
    """Each subject whose lectures are in the calendar becomes a topic per week of
    lectures, plus one for what was taught before the plan starts; the others stay
    one topic. See "Lectures become topics" in docs/METHOD.md."""
    zone = _zone(report.tz)
    midnight = datetime.combine(report.start, time(0, 0), tzinfo=zone)
    lecture_memory = familiarity_prior(LECTURE_FAMILIARITY)
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
        weeks: dict[date, datetime] = {}
        for end in ends:
            if end > midnight:
                local = end.astimezone(zone)
                monday = local.date() - timedelta(days=local.weekday())
                weeks[monday] = max(weeks.get(monday, end), end)
        for monday, last in sorted(weeks.items()):
            available = _days_after(report.start, last)
            if available >= spec["exam_day"]:
                continue
            label = f"week of {monday:%d %b}"
            out.append(
                {
                    **spec,
                    "name": f"{spec['subject']} · {label}",
                    "topic": label,
                    "note": _(
                        "Review the lectures of the week of {day}.", day=format_date(monday, "day_short")
                    ),
                    "about": _("the lectures of the week of {day}", day=format_date(monday, "day")),
                    "stability": lecture_memory.stability,
                    "difficulty": lecture_memory.difficulty,
                    "last_review_day": available,
                    "available_day": available,
                    "target_days": _week_target(spec["exam_day"] - available),
                    "prior": f"lectures, familiarity {LECTURE_FAMILIARITY}",
                }
            )
    return out


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
) -> tuple[PlanSettings, list[dict[str, Any]], CalendarReport]:
    """Validate, resolve exam dates, make the topics, and settle the horizon."""
    if not 0.5 <= retention < 1.0:
        raise InvalidInput("target recall must be at least 0.5 and below 1")
    if window < 1:
        raise InvalidInput("the planning window must be at least one block")
    specs, latest = _resolve(report, subjects)
    needed = max(1, math.ceil(latest - 1e-9))
    if needed > report.days:
        report = _extend(report, needed)
    if lectures_as_topics:
        topics = _topics(report, specs)
        if any(t["topic"] for t in topics):
            specs = topics
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
    usable = [b for b in report.blocks if b.start_day < latest]
    if not usable:
        raise NoFreeBlocks(
            "there is no free study block before the exams; widen the study window, allow more "
            "blocks a day, shorten the blocks, or free some time in the calendar"
        )
    return settings, specs, report


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
    states = _replay(specs, history)
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

    finals = _replay(specs, history + sessions)
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
    topic's stability against its own target."""
    exam_day = members[0][0]["exam_day"]
    ready = [finals[s["name"]][0].stability >= t for s, t, _ in members]
    lost = [bad and not ok for (_, _, bad), ok in zip(members, ready, strict=True)]
    recall = [
        retrievability(max(exam_day - finals[s["name"]][1], 0.0), finals[s["name"]][0].stability)
        for s, _, _ in members
    ]
    weakest, weakest_target, _ = min(members, key=lambda m: finals[m[0]["name"]][0].stability / m[1])
    head = members[0][0]
    view = SubjectView(
        name=name,
        exam=head["exam"],
        exam_day=exam_day,
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


def _replay(specs: Sequence[dict], done: Sequence[SessionView]) -> dict[str, tuple[MemoryState, float]]:
    """Memory state and last review day of every subject after `done`, recomputed
    with exact FSRS transitions (a single source of truth, not stored numbers)."""
    state = {s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in specs}
    for session in sorted(done, key=lambda x: x.start_day):
        if session.outcome == "skipped" or session.title not in state:
            continue  # a task or exam practice: nothing to remember
        memory, last = state[session.title]
        grade = Grade.GOOD if session.outcome == "recalled" else Grade.AGAIN
        state[session.title] = (review(memory, session.start_day - last, grade), session.start_day)
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
    after = _replay(specs, history)
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
    done = [s for s in plan.history + plan.sessions if s.outcome != "skipped"]
    if len(specs) == 1 and not specs[0].get("topic"):
        return _topic_curve(specs[0], [s for s in done if s.title == specs[0]["name"]], step)
    curves = [(s, dict(_topic_curve(s, [x for x in done if x.title == s["name"]], step))) for s in specs]
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


def _topic_curve(spec: Mapping, reviews: Sequence[SessionView], step: float) -> list[tuple[float, float]]:
    memory = MemoryState(spec["stability"], spec["difficulty"])
    last = spec["last_review_day"]
    points: list[tuple[float, float]] = []
    t = spec.get("available_day", 0.0)
    for session in [*sorted(reviews, key=lambda s: s.start_day), None]:
        until = session.start_day if session else spec["exam_day"]
        while t < until:
            points.append((t, retrievability(t - last, memory.stability)))
            t += step
        points.append((until, retrievability(until - last, memory.stability)))
        if session is None:
            break
        grade = Grade.GOOD if session.outcome == "recalled" else Grade.AGAIN
        memory = review(memory, session.start_day - last, grade)
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
    if not 1 <= count <= 14:
        raise InvalidInput("show 1 to 14 days")
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
        kind = "typed" if event.source == "typed" else "calendar"
        if event.start in exams:
            kind = "exam"
            shown_exams.add(event.start)
        label = event.summary if kind == "exam" else short_title(event.summary)
        course = exams.get(event.start) or course_of(event.summary)
        lo, hi = datetime.fromisoformat(event.start), datetime.fromisoformat(event.end)
        place(lo, hi, label, kind, course=course)
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


def topic_words(title: str) -> str:
    """A topic's name in the current language. Topic names are identifiers (a
    session's id is made from its title, so a report survives a change of
    language): "Algebra 3 · week of 05 Oct" is stored in English and shown as
    "Algebra 3 · semaine du 5 oct." in French."""

    def say(match: re.Match[str]) -> str:
        day = date(2000, _MONTH_ABBR.index(match.group(3)) + 1, int(match.group(2)))
        phrase = _("week of {day}") if match.group(1) == "week of" else _("taught before {day}")
        return phrase.format(day=format_date(day, "day_short"))

    return _TOPIC_WEEK.sub(say, title)


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
# Kept 30 days after the last exam or deadline, then deleted (docs/ROADMAP.md, D6).
RETENTION = timedelta(days=30)
# What a student can report about a session.
REPORTS = ("done", "skipped", "struggled")


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
) -> Subscription:
    """A new feed. With a `source_url`, the timetable is read again at every
    refresh; with `ics`, the file given now is the timetable for good. `plan`, if
    the caller has just made it from the same inputs, saves planning again.

    The calendar last read is kept (`ics_text`), also for a link, so that a
    feedback tap replans at once from it instead of waiting for the network."""
    if source_url is None and ics is None and not busy_rows:
        raise InvalidInput(_("give a calendar link, a calendar file or your week"))
    rows = tuple(_row_to_dict(BusyRow.parse(r)) for r in busy_rows)
    subscription = Subscription(
        token=secrets.token_urlsafe(24),
        created=datetime.now(UTC).isoformat(timespec="seconds"),
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
            "lang": pick(lang) if lang else current_language(),
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
        )
        done: tuple[SessionView, ...] = ()
        if subscription.plan is not None:
            previous = PlanReport.from_dict(subscription.plan)
            cut = _days_after(previous.settings.start, now.astimezone(_zone(previous.settings.tz)))
            reported = subscription.outcomes
            done = tuple(
                _as_reported(s, reported.get(session_id(s)))
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


def _as_reported(session: SessionView, report: str | None) -> SessionView:
    """A session with the outcome the student reported. "struggled" is a lapse for
    something to remember (it comes back sooner) and done for a task or practice
    (a task gets one more block instead, in `report_session`)."""
    if report is None:
        return session
    if report == "skipped":
        return replace(session, outcome="skipped")
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
    return revise_subscription(subscription, tasks=tasks, now=now, strict=False)


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
    **options: Any,
) -> Subscription:
    """Change what the plan is made from (subjects, deadlines, activities, weekly
    hours and days off, or any of `new_subscription`'s settings) and plan again at
    once from the calendar last read. A new `deadlines_url` ("" removes it) is read
    at once, which is the only network access here. A change that leaves nothing to
    plan is refused, unless `strict` is False: busy times drawn before any exam is
    known are kept, with `error` saying what is missing."""
    merged = dict(subscription.options)
    unknown = set(options) - set(merged)
    if unknown:
        raise InvalidInput(f"unknown settings: {', '.join(sorted(unknown))}")
    merged.update(options)
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
    if deadlines_url is not None and deadlines_url.strip() != (merged.get("deadlines_url") or ""):
        url = deadlines_url.strip() or None
        changed = replace(changed, options={**changed.options, "deadlines_url": url, "deadlines_error": None})
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
    url = options.get("deadlines_url")
    if not url:
        return subscription
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
        subscription, options={**options, "tasks": tasks, "imported": imported, "deadlines_error": None}
    )


# What a student can block out on the calendar. Every kind is busy time to the
# planner; the kind only colours the block and names it until the student does.
ACTIVITY_KINDS = (
    ("training", "Training"),
    ("commute", "Commute"),
    ("work", "Work"),
    ("timeoff", "Time off"),
    ("other", "Other"),
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


def subscription_expiry(subscription: Subscription) -> datetime | None:
    """When a subscription is deleted: `RETENTION` after its last exam or deadline."""
    if subscription.plan is None:
        return None
    plan = PlanReport.from_dict(subscription.plan)
    moments = [datetime.fromisoformat(s.exam) for s in plan.subjects]
    moments += [datetime.fromisoformat(t.due) for t in plan.tasks]
    return max(moments) + RETENTION if moments else None


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
    expiry = subscription_expiry(subscription)
    _store(store).put(
        subscription.token,
        subscription.to_dict(),
        expiry.astimezone(UTC).isoformat(timespec="seconds") if expiry else None,
    )


def load_subscription(store: str | os.PathLike, token: str) -> Subscription | None:
    data = _store(store).get(token)
    return Subscription.from_dict(data) if data is not None else None


def update_subscription(
    store: str | os.PathLike, token: str, change: Callable[[Subscription], Subscription]
) -> Subscription | None:
    """Apply `change` to a stored subscription without losing a concurrent write
    (`Store.update`). None if it does not exist, or was deleted meanwhile."""

    def apply(data: dict) -> tuple[dict, str | None]:
        new = change(Subscription.from_dict(data))
        expiry = subscription_expiry(new)
        return new.to_dict(), expiry.astimezone(UTC).isoformat(timespec="seconds") if expiry else None

    data = _store(store).update(token, apply)
    return Subscription.from_dict(data) if data is not None else None


def delete_subscription(store: str | os.PathLike, token: str) -> bool:
    return _store(store).delete(token)


@functools.lru_cache(maxsize=16)
def _open(path: str) -> Store:
    return Store(path)


def _store(store: str | os.PathLike) -> Store:
    """One `Store` per file, opened once (its schema is checked on opening)."""
    return _open(os.fspath(store))


def log_event(store: str | os.PathLike, token: str, kind: str, detail: str = "") -> None:
    """Record what happened to a plan (created, changed, a session reported), for the
    pilot's measures. No timetable content, no names."""
    _store(store).log(token, kind, detail)


def log_visit(store: str | os.PathLike, token: str) -> bool:
    """A student opened their plan today; logged once a day (D15)."""
    return _store(store).log_daily(token, "visit")


def events(store: str | os.PathLike, token: str) -> list[dict]:
    return _store(store).events(token)


def sweep_subscriptions(store: str | os.PathLike, now: datetime | None = None) -> int:
    """Delete every subscription past its expiry (`subscription_expiry`)."""
    return _store(store).sweep(now)


def backup_subscriptions(
    store: str | os.PathLike, directory: str | os.PathLike, now: datetime | None = None
) -> Path:
    """A consistent dated copy of the store in `directory`; copies older than a week go."""
    return _store(store).backup_rotating(Path(directory), now=now)


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
DEFAULT_PREFERENCES: dict = {"weekly_hours": 15.0, "rest_days": ["Sun"], "practice_hours": 4.5}
DEFAULT_FAMILIARITY = 3
# How far back the Today page asks about sessions.
REPORT_WINDOW = timedelta(days=7)

KIND_LABELS = {
    "first review": "First self-test",
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
) -> Subscription:
    """The hosted product's first step: a timetable and nothing else. The link is
    read once; every exam found in it becomes a subject; the week starts from the
    stated defaults; and the plan is made if there is anything to plan for yet."""
    now = now or datetime.now(UTC)
    if source_url:
        ics = fetch(source_url.strip())
    if ics is None:
        raise InvalidInput(_("give your timetable's link or its file"))
    today = now.astimezone(_zone(tz)).date()
    report = analyse_calendar(ics, start=today, tz=tz)
    return new_subscription(
        subjects=[SubjectSpec(a.subject, None, familiarity=DEFAULT_FAMILIARITY) for a in report.assessments],
        start=today,
        tz=tz,
        source_url=source_url,
        ics=ics,
        engine="assistant",
        preferences=DEFAULT_PREFERENCES,
        now=now,
        require_plan=False,
        lang=lang,
    )


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
) -> dict:
    start = datetime.fromisoformat(s.start).astimezone(zone)
    end = datetime.fromisoformat(s.end).astimezone(zone)
    sid = session_id(s)
    return {
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
    }


def session_card(subscription: Subscription, sid: str, now: datetime | None = None) -> dict | None:
    """One session of the plan, done or to come, as the pages show it."""
    session = find_session(subscription, sid)
    if session is None or subscription.plan is None:
        return None
    zone = _zone(PlanReport.from_dict(subscription.plan).settings.tz)
    local = (now or datetime.now(UTC)).astimezone(zone)
    return _session_card(session, zone, local, subscription.outcomes)


# How many course colours the pages have (CSS classes c0 to c6): enough to tell a
# semester's courses apart; beyond that, colours repeat. Red is kept for exams.
COURSE_PALETTE = 7


def course_colours(source: CalendarReport | PlanReport) -> list[tuple[str, int]]:
    """Each course and its colour index: the subjects with an exam first, in the
    order of their exams, then the other courses of the timetable. A course's
    lectures and its study sessions share its colour."""
    names: list[str] = []
    if isinstance(source, PlanReport):
        names += [x.name for x in sorted(source.subjects, key=lambda x: x.exam)]
        names += sorted({t.course for t in source.tasks if t.course}, key=str.casefold)
    else:
        names += [a.subject for a in source.assessments]
    taught = {course_of(e.summary) for e in source.events if e.source != "typed"} - {""}
    names += sorted(taught, key=str.casefold)
    seen: dict[str, int] = {}
    ordered = []
    for name in names:
        if name.casefold() not in seen:
            seen[name.casefold()] = len(seen) % COURSE_PALETTE
            ordered.append((name, seen[name.casefold()]))
    return ordered


def course_names(subscription: Subscription) -> list[str]:
    """The courses a student can name a task after: those with an exam, then the
    other courses in the timetable, in the order of their colours."""
    if subscription.plan is None:
        return [s["name"] for s in subscription.subjects]
    return [name for name, _ in course_colours(PlanReport.from_dict(subscription.plan))]


def calendar_view(
    subscription: Subscription, first: date | None = None, count: int = 7, now: datetime | None = None
) -> dict:
    """What the calendar page draws: `count` days from `first` (today by default)
    with the timetable, exams and study sessions (done and to come) as items, and
    the student's own busy times as editable activities, in the shape the week
    calendar widget takes. Works before there is a plan, from the timetable alone."""
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
        )
    week = calendar_week(source, 0, typed=False, first=first, count=count, history=True)
    moment = (now or datetime.now(UTC)).astimezone(zone)
    legend = course_colours(source)
    colours = {name.casefold(): index for name, index in legend}
    upcoming = (
        {session_id(x) for x in source.sessions}
        if isinstance(source, PlanReport) and source.settings.engine == "assistant"
        else set()
    )
    for item in week["items"]:
        item["color"] = colours.get(str(item.get("course") or "").casefold())
        if item["kind"] == "study":
            started = _local(item["at"], zone) <= moment
            item["started"] = started
            item["done"] = _local(item["until"], zone) <= moment
            item["reported"] = subscription.outcomes.get(item["id"])
            item["movable"] = not started and item["id"] in upcoming
    return {
        **week,
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
            }
            for r in subscription.busy_rows
        ],
        "kinds": [{"id": k, "label": _(label)} for k, label in ACTIVITY_KINDS],
        "hours": [7, 23],
        "window": list(options["study_window"]),
        "first": first.isoformat(),
        "planned": subscription.plan is not None,
        "error": subscription.error,
    }


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
    found_names = {f["subject"].casefold(): f["when"] for f in found}
    subjects = [
        {
            "name": x["name"],
            "exam": (x.get("exam") or "")[:16].replace("T", " "),
            "found": x.get("exam") is None,
            "found_when": found_names.get(x["name"].casefold(), ""),
            "familiarity": x.get("familiarity") or DEFAULT_FAMILIARITY,
        }
        for x in subscription.subjects
    ]
    planned = {x["name"].casefold() for x in subscription.subjects}
    prefs = {**DEFAULT_PREFERENCES, **options.get("preferences", {})}
    return {
        "subjects": subjects,
        "unplanned_exams": [f for f in found if f["subject"].casefold() not in planned],
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
        "practice_hours": prefs["practice_hours"],
        "study_window": list(options["study_window"]),
        "blocks_per_day": options["blocks_per_day"],
        "block_minutes": options["block_minutes"],
        "tz": options["tz"],
        "source_url": subscription.source_url,
        "deadlines_url": options.get("deadlines_url") or "",
        "deadlines_error": options.get("deadlines_error"),
        "imported": sorted(set(options.get("imported", {}).values())),
        "weekdays": list(WEEKDAY_NAMES),
    }


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
    }
    if subscription.plan is None:
        return view
    plan = PlanReport.from_dict(subscription.plan)
    zone = _zone(plan.settings.tz)
    local = now.astimezone(zone)
    view["today"] = format_date(local.date())
    colours = {name.casefold(): index for name, index in course_colours(plan)}
    cards = [
        _session_card(s, zone, local, subscription.outcomes, colours) for s in plan.history + plan.sessions
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
    week_end = local + timedelta(days=7)
    coming = [
        s for s in plan.sessions if local <= datetime.fromisoformat(s.start).astimezone(zone) < week_end
    ]
    view["week"] = {
        "sessions": len(coming),
        "hours": round(len(coming) * plan.settings.block_minutes / 60, 1),
    }
    view["tasks"] = [t for t in _task_rows(subscription, plan, now, colours) if not t["finished"]]
    view["exams"] = _exam_rows(plan, now, colours)
    # A subject short of its target already says so in its status line; other
    # warnings (practice that does not fit, work that does not fit) stay.
    shown = tuple(f"{x.name}: " for x in plan.subjects)
    said = ("predicted below", "does not reach the target", _("are predicted below"))
    view["warnings"] = [w for w in plan.warnings if not (w.startswith(shown) and any(x in w for x in said))]
    return view


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
            }
            for t in subscription.options.get("tasks", ())
        ]
    rows = view["rows"]
    view["open"] = sum(not r["finished"] for r in rows)
    view["at_risk"] = sum(r["status"] in ("at risk", "late") for r in rows)
    view["this_week"] = sum(not r["finished"] and 0 <= r["days"] < 7 for r in rows)
    view["finished"] = sum(r["finished"] for r in rows)
    return view


# --------------------------------------------------------------------------- #
# Progress, the streak and the weekly review (DECISIONS.md, D8 and D9)


def progress_sessions(subscription: Subscription) -> list[_progress.Session]:
    """Every session of the plan, past and to come, as `progress` counts them:
    local start, length, kind, course, and what the student reported."""
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


def _hours(minutes: int) -> float:
    return round(minutes / 60, 1)


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
    sessions = progress_sessions(subscription) + list(logged)
    first = min([plan.settings.start, *(s.start.date() for s in sessions)])
    run = _progress.streak(sessions, first, today)
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
            },
            "week": {
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
                for row in _progress.grid(sessions, first, today, weeks=weeks)
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
    sessions = progress_sessions(subscription) + list(logged)
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
activate_language = _i18n.activate
deactivate_language = _i18n.deactivate
pick_language = _i18n.pick
format_day = _i18n.format_date


DAY_WORDS = {
    "studied": "studied",
    "forgiven": "forgiven",
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


def start_focus(
    subscription: Subscription, course: str, *, sid: str | None = None, now: datetime | None = None
) -> Subscription:
    """Start the timer for `course`, or for the plan's session `sid`."""
    now = now or datetime.now(UTC)
    if sid:
        session = find_session(subscription, sid)
        if session is None:
            raise InvalidInput(_("this session is no longer in your plan; the plan has changed since"))
        course = course or (session.title if session.kind == "task" else session.subject)
    course = " ".join(str(course or "").split())[: social.LIMITS["course"]]
    if not course:
        raise InvalidInput(_("say what you studied"))
    focus = {
        "course": course,
        "sid": sid or "",
        "started": _iso(now),
        "last": _iso(now),
        "away": 0,
        "interruptions": 0,
    }
    focus["beats"] = 0
    options = {k: v for k, v in subscription.options.items() if k != "focus_draft"}
    return replace(subscription, options={**options, "focus": focus})


def _beat(focus: dict, now: datetime) -> dict:
    gap = now - datetime.fromisoformat(focus["last"])
    away, interruptions = focus["away"], focus["interruptions"]
    if focus["beats"] and gap > FOCUS_GRACE:
        away += round((gap - FOCUS_BEAT).total_seconds())
        interruptions += 1
    return {
        **focus,
        "last": _iso(now),
        "away": away,
        "interruptions": interruptions,
        "beats": focus["beats"] + 1,
    }


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
    if focus["beats"]:
        focus = _beat(focus, now)
    started = datetime.fromisoformat(focus["started"])
    elapsed = min(now - started, FOCUS_LONGEST)
    focused = max(elapsed - timedelta(seconds=focus["away"]), timedelta(minutes=1))
    draft = {
        "course": focus["course"],
        "sid": focus["sid"],
        "started": focus["started"],
        "ended": _iso(now),
        "minutes": max(1, round(focused / timedelta(minutes=1))),
        "away_minutes": round(focus["away"] / 60),
        "interruptions": focus["interruptions"],
        "checked": bool(focus["beats"]),
        "timed": True,
    }
    options = {k: v for k, v in subscription.options.items() if k != "focus"}
    return replace(subscription, options={**options, "focus_draft": draft})


def cancel_focus(subscription: Subscription) -> Subscription:
    options = {k: v for k, v in subscription.options.items() if k not in ("focus", "focus_draft")}
    return replace(subscription, options=options)


def focus_view(subscription: Subscription, now: datetime | None = None) -> dict:
    """What the focus page shows: the running session, or what can be started
    (the courses, and the plan's session now or next)."""
    now = now or datetime.now(UTC)
    zone = _zone(subscription.options["tz"])
    focus = subscription.options.get("focus")
    view: dict = {"running": bool(focus), "courses": course_names(subscription), "suggested": None}
    if focus:
        started = datetime.fromisoformat(focus["started"])
        view.update(
            course=focus["course"],
            started=_iso(started),
            started_local=f"{started.astimezone(zone):%H:%M}",
            elapsed=round((now - started).total_seconds()),
            away_minutes=round(focus["away"] / 60),
            interruptions=focus["interruptions"],
            beat=round(FOCUS_BEAT.total_seconds()),
        )
    elif subscription.plan is not None:
        today = today_view(subscription, now)
        card = today.get("now") or (today.get("next") or [None])[0]
        if card is not None:
            view["suggested"] = {
                "id": card["id"],
                "title": card["title"],
                "label": card["label"],
                "when": card["when"],
            }
    return view


def log_draft(subscription: Subscription, now: datetime | None = None) -> dict:
    """The log form's starting values: the session just timed, or an empty one."""
    draft = subscription.options.get("focus_draft")
    if draft:
        return {**draft}
    return {
        "course": "",
        "sid": "",
        "minutes": 60,
        "timed": False,
        "checked": False,
        "away_minutes": 0,
        "interruptions": 0,
    }


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
    draft = log_draft(subscription, now)
    minutes = str(form.get("minutes") or draft["minutes"])
    timed = draft["timed"] and minutes == str(draft["minutes"])
    data = {
        "course": form.get("course") or draft["course"],
        "title": form.get("title", ""),
        "note": form.get("note", ""),
        "effort": form.get("effort", ""),
        "progress": form.get("progress", ""),
        "minutes": minutes,
        "timed": timed,
        "checked": timed and draft["checked"],
        "away_minutes": draft["away_minutes"] if timed else 0,
        "interruptions": draft["interruptions"] if timed else 0,
        "sid": draft.get("sid") or "",
    }
    visibility = _sharing(store, subscription.token, form.get("visibility"), "followers")
    day = (
        datetime.fromisoformat(draft["started"]).astimezone(zone).date()
        if draft.get("started")
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
            )
        )
    return out


EFFORT_WORDS = {1: "very easy", 3: "easy", 5: "steady", 7: "hard", 9: "very hard", 10: "all out"}
PROGRESS_WORDS = {1: "stuck", 2: "a little", 3: "steady", 4: "good", 5: "a breakthrough"}


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
    if post.kind == "session":
        effort = int(d.get("effort", 0))
        card["effort_word"] = _(EFFORT_WORDS[max(k for k in EFFORT_WORDS if k <= effort)]) if effort else ""
        card["progress_word"] = _(PROGRESS_WORDS.get(int(d.get("progress", 0)), ""))
        card["hours"] = format_number(int(d.get("minutes", 0)) / 60)
        card["duration"] = duration_words(int(d.get("minutes", 0)))
        if d.get("timed") and d.get("checked"):
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
    return {
        "posts": [post_card(p, viewer=subscription.token, now=now, zone=zone) for p in posts],
        "week": {
            "sessions": len(week),
            "hours": format_number(sum(int(p.data["minutes"]) for p in week) / 60),
            "effort": format_number(sum(efforts) / len(efforts)) if efforts else "",
        },
    }


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
    return path.read_bytes(), "image/png" if name.endswith(".png") else "image/jpeg"


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


def save_profile(store: str | os.PathLike, token: str, form: Mapping[str, str]) -> dict:
    profile = _social(
        lambda: social.set_profile(
            _store(store),
            token,
            handle=str(form.get("handle", "")),
            university=str(form.get("university", "")),
            programme=str(form.get("programme", "")),
            bio=str(form.get("bio", "")),
            old_enough=form.get("old_enough") == "1",
        )
    )
    return profile.public()


def _cards(
    store: Store, viewer: str, posts: Sequence[social.Post], now: datetime, zone: ZoneInfo
) -> list[dict]:
    """Posts as cards, with their authors, kudos and comment counts."""
    kudos = social.kudos_of(store, [p.id for p in posts], viewer)
    comments = social.comment_counts(store, [p.id for p in posts])
    authors: dict[str, dict | None] = {}
    out = []
    for post in posts:
        if post.token not in authors:
            found = social.get_profile(store, post.token)
            authors[post.token] = found.public() if found else None
        card = post_card(post, viewer=viewer, now=now, zone=zone)
        count, given = kudos.get(post.id, (0, False))
        card.update(
            author=authors[post.token], kudos=count, kudos_given=given, comments=comments.get(post.id, 0)
        )
        out.append(card)
    return out


def community_view(
    store: str | os.PathLike,
    subscription: Subscription,
    *,
    tab: str = "following",
    filters: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    """The network's two feeds: people one follows, and Explore (everyone's posts
    across universities, by university, programme, course and kind)."""
    now = now or datetime.now(UTC)
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
        )
    else:
        tab = "following"
        posts = social.feed_following(where, token)
    me = social.get_profile(where, token)
    return {
        "tab": tab,
        "filters": filters,
        "posts": _cards(where, token, posts, now, zone),
        "profile": me.public() if me else None,
        "requests": len(social.relations(where, token)["requests"]) if me else 0,
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
            }
        )
    can_comment = social.get_profile(where, subscription.token) is not None
    return {"post": card, "comments": comments, "can_comment": can_comment}


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
        "posts": _cards(where, subscription.token, posts, now, zone),
    }


def people_view(store: str | os.PathLike, subscription: Subscription, query: str = "") -> dict:
    """The student's own circle (requests, followers, following) and a search."""
    where = _store(store)
    token = subscription.token
    found = social.search_profiles(where, token, query) if query else []
    circle = social.relations(where, token)
    return {
        "query": query,
        "found": [p.public() for p in found],
        "blocked": [p.public() for p in social.blocked_by(where, token)],
        **{name: [p.public() for p in people] for name, people in circle.items()},
    }


def leave_network(store: str | os.PathLike, token: str) -> None:
    """Leave the network; the diary stays, visible only to the student."""
    social.leave(_store(store), token)


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
    return path.read_bytes(), "image/png" if name.endswith(".png") else "image/jpeg"


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


def toggle_kudos(store: str | os.PathLike, token: str, post_id: str) -> bool:
    where = _store(store)
    return bool(_social(lambda: social.toggle_kudos(where, token, _visible_post(where, token, post_id))))


def add_comment(store: str | os.PathLike, token: str, post_id: str, body: str) -> None:
    where = _store(store)
    _social(lambda: social.add_comment(where, token, _visible_post(where, token, post_id), body))


def delete_comment(store: str | os.PathLike, token: str, comment_id: str) -> bool:
    return social.delete_comment(_store(store), token, comment_id)


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
        "course": form.get("course", ""),
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

    def add(start: datetime, end: datetime, label: str, kind: str, sid: str | None = None) -> None:
        start, end = start.astimezone(zone), end.astimezone(zone)
        if start.date() in entries:
            entries[start.date()].append(
                {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}", "label": label, "kind": kind, "id": sid}
            )

    exams = {x.exam for x in plan.subjects}
    for e in plan.events:
        kind = "exam" if e.start in exams else ("typed" if e.source == "typed" else "calendar")
        label = e.summary if kind == "exam" else short_title(e.summary)
        add(datetime.fromisoformat(e.start), datetime.fromisoformat(e.end), label, kind)
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


def make_schedule(
    report: CalendarReport,
    subjects: Sequence[SubjectSpec] = (),
    *,
    tasks: Sequence[TaskSpec] = (),
    weekly_hours: float | None = None,
    rest_days: Sequence[str] = (),
    practice_days: float = 14.0,
    practice_hours: float = 4.5,
    retention: float = 0.9,
    lectures_as_topics: bool = True,
    done: Sequence[SessionView] = (),
    now: datetime | None = None,
    pins: Sequence[Mapping] = (),
) -> PlanReport:
    """The student's week, scheduled by the assistant's rules (`cps.assistant`):
    deadlines first when they get close, exam practice before each exam, self-testing
    on the taught material that is fading, work ahead otherwise, and free time
    when nothing needs it. Within `weekly_hours` a week, never on `rest_days`.

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
    if practice_days < 0 or practice_hours < 0:
        raise InvalidInput(_("exam practice cannot be negative"))
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
            report, subjects, retention, 1, None, DEFAULT_FAILURE_PENALTY, lectures_as_topics
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
    settings = replace(
        settings,
        horizon_days=report.days,
        engine="assistant",
        preferences={
            "weekly_hours": weekly_hours,
            "rest_days": list(rest_days),
            "practice_days": practice_days,
            "practice_hours": practice_hours,
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
    history = tuple(
        replace(x, index=i)
        for i, x in enumerate(sorted((x for x in done if x.title in known), key=lambda x: x.start_day))
    )
    after = _days_after(report.start, now.astimezone(zone)) if now is not None else -1.0
    return _assist(settings, specs, views, report.blocks, report.events, history, after, pins)


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
    states = _replay(specs, history)
    counted = [h for h in history if h.outcome != "skipped"]
    done_task: dict[str, int] = {}
    done_practice: dict[str, int] = {}
    for h in counted:
        if h.kind == "task":
            done_task[h.title] = done_task.get(h.title, 0) + 1
        elif h.kind == "practice":
            done_practice[h.subject] = done_practice.get(h.subject, 0) + 1
    topics = [
        Topic(
            s["name"],
            s.get("subject", s["name"]),
            states[s["name"]][0],
            states[s["name"]][1],
            s.get("available_day", 0.0),
            s["exam_day"],
            s.get("about", s.get("subject", s["name"])),
        )
        for s in specs
    ]
    per_exam = blocks_for_hours(prefs["practice_hours"], minutes) if prefs["practice_hours"] > 0 else 0
    exams = {
        s.get("subject", s["name"]): Exam(
            s.get("subject", s["name"]),
            s["exam_day"],
            max(per_exam - done_practice.get(s.get("subject", s["name"]), 0), 0),
        )
        for s in specs
    }
    work = [
        Task(t.name, t.due_day, 0 if t.done else max(t.blocks - done_task.get(t.name, 0), 0), t.course)
        for t in tasks
    ]
    first_weekday = settings.start.weekday()
    week_used: dict[int, int] = {}
    for h in counted:
        w = (first_weekday + h.day) // 7
        week_used[w] = week_used.get(w, 0) + 1
    weekly = prefs["weekly_hours"]
    result = assist(
        [b.as_block() for b in blocks if b.start_day > after_day],
        first_weekday=first_weekday,
        topics=topics,
        tasks=work,
        exams=list(exams.values()),
        preferences=Preferences(
            weekly_blocks=None if weekly is None else max(1, math.floor(weekly * 60 / minutes + 1e-9)),
            rest_weekdays=tuple(WEEKDAY_NAMES.index(d) for d in prefs["rest_days"]),
            practice_days=prefs["practice_days"],
            review_below=settings.retention,
        ),
        week_used=week_used,
        pins=forced,
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
            retrievability(max(s["exam_day"] - last, 0.0), memory.stability)
            for s, (memory, last) in zip(members, finals, strict=True)
        ]
        ok = [r >= settings.retention for r in recalls]
        ready = all(ok)
        head = members[0]
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
                _(
                    "{name}: {below} of {n} topics are predicted below {target} at the exam. "
                    "More hours a week, or fewer days off, would change that.",
                    name=name,
                    below=len(members) - sum(ok),
                    n=len(members),
                    target=f"{settings.retention:.0%}",
                )
            )
        if result.practice_left.get(name):
            warnings.append(
                _(
                    "{name}: {n} block(s) of exam practice did not fit before the exam.",
                    name=name,
                    n=result.practice_left[name],
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
                _(
                    "{name}: {n} block(s) of work do not fit before it is due ({when}). "
                    "More hours a week, fewer days off or an earlier start would change that.",
                    name=t.name,
                    n=left,
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
