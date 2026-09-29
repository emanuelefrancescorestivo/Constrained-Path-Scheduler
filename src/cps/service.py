"""
The service layer: everything a user-facing surface needs, and nothing else.

The CLI, the Streamlit app and any future web API call these functions and only
these, so they can be swapped without touching the planner. Nothing here imports a
UI framework or prints: bytes and plain values in, frozen result objects out. Two
kinds of input and output are allowed, both explicit: `fetch_calendar` reads a
calendar link the person gave, and the `*_subscription` functions keep feeds in a
directory the caller names. Every result has `to_dict()` returning plain JSON
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

import json
import math
import os
import re
import secrets
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from .calendar_io import (
    BusyEvent,
    BusyRow,
    availability_from_events,
    busy_from_table,
    decode_ics,
    expand_events,
    find_deadlines,
    find_lectures,
    plan_to_ics,
)
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
        raise InvalidInput(f"{self.name}: give a familiarity from 1 to 5, or a stability and a difficulty")

    def exam_datetime(self, zone: ZoneInfo) -> datetime:
        value = self.exam
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.strip())
            except ValueError as exc:
                raise InvalidInput(f"{self.name}: {self.exam!r} is not a date like 2026-06-15") from exc
        if isinstance(value, datetime):
            return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        if isinstance(value, date):
            return datetime.combine(value, time(0, 0), tzinfo=zone)
        raise InvalidInput(f"{self.name}: unsupported exam value {value!r}")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInput(f"unknown time zone {name!r}; use an IANA name such as Europe/Rome") from exc


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
        raise InvalidInput("the study window must run from an earlier to a later hour, within 0 to 24")
    if blocks_per_day < 1 or block_minutes < 30:
        raise InvalidInput("use at least one block a day of at least 30 minutes")
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
    # are topics; "" when the subject is one topic.
    topic: str = ""

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

    def to_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["start"] = self.start.isoformat()
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
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput(f"not a plan produced by this version: {exc}") from exc


def _resolve(report: CalendarReport, subjects: Sequence[SubjectSpec]) -> tuple[list[dict[str, Any]], float]:
    """Specs to plain starting states with exam days, validated: one per subject."""
    if not subjects:
        raise InvalidInput("add at least one subject with an exam date")
    names = [s.name.strip() for s in subjects]
    if any(not n for n in names):
        raise InvalidInput("every subject needs a name")
    if len(set(n.casefold() for n in names)) != len(names):
        raise InvalidInput("two subjects have the same name")
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
                    "note": f"Review what was taught before {report.start:%d %b}.",
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
                    "note": f"Review the lectures of the {label}.",
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
        if session.outcome == "skipped":
            continue
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
    shown = (plan.history if include_history else ()) + plan.sessions
    text = plan_to_ics(
        [(s.slot, s.title, s.rationale) for s in shown if s.outcome != "skipped"],
        plan.settings.start,
        plan.settings.tz,
        slots_per_day=plan.settings.slots_per_day,
        block_slots=max(1, plan.settings.block_minutes // (24 * 60 // plan.settings.slots_per_day)),
        uid_prefix=uid_prefix,
        refresh=refresh,
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
    status = "ready" if subject.ready else ("out of reach" if subject.unreachable else "not ready")
    if subject.topics > 1:
        return f"{status}: {subject.topics_ready} of {subject.topics} topics at target"
    return f"{status}: stability {subject.stability_at_exam:.0f} of {subject.target:.0f} days"


def session_rows(plan: PlanReport) -> list[dict]:
    """The upcoming sessions, one row each, as a person reads them."""
    return [
        {
            "#": s.index,
            "when": datetime.fromisoformat(s.start).strftime("%a %d %b %H:%M"),
            "subject": s.title,
            "recall now": f"{s.recall:.0%}",
            "why": s.rationale,
        }
        for s in plan.sessions
    ]


def week_count(plan: PlanReport) -> int:
    return max(1, math.ceil(plan.settings.horizon_days / 7))


def calendar_week(source: CalendarReport | PlanReport, week: int, *, typed: bool = True) -> dict:
    """One week, Monday or not, as positioned items for a calendar widget.

    `{"days": [{"date", "label", "weekday", "in_horizon"} x 7], "items": [{"day",
    "start", "end", "label", "kind"}]}`, with `start` and `end` in minutes after
    local midnight and an item cut at midnight when it runs into the next day.
    `kind` is "calendar" (from the .ics), "typed" (a busy row), "exam" or "study".
    The week starts on the plan's first day, like `week_view`. `typed=False`
    leaves out the busy rows, for an editor that draws them itself.
    """
    if isinstance(source, PlanReport):
        start, tz, days = source.settings.start, source.settings.tz, source.settings.horizon_days
        exams = {s.exam: s.name for s in source.subjects}
        sessions = [(x.start, x.end, x.title) for x in source.sessions]
    else:
        start, tz, days = source.start, source.tz, source.days
        exams = {a.when: a.subject for a in source.assessments}
        sessions = []
    weeks = max(1, math.ceil(days / 7))
    if not 0 <= week < weeks:
        raise InvalidInput(f"week must be 0 to {weeks - 1}")
    zone = _zone(tz)
    first = start + timedelta(days=7 * week)
    dates = [first + timedelta(days=d) for d in range(7)]
    items: list[dict] = []

    def place(begin: datetime, end: datetime, label: str, kind: str) -> None:
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
        place(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), event.summary, kind)
    for when, name in exams.items():
        if when not in shown_exams:
            moment = datetime.fromisoformat(when)
            place(moment, moment + timedelta(hours=1), f"Exam: {name}", "exam")
    for begin, end, label in sessions:
        place(datetime.fromisoformat(begin), datetime.fromisoformat(end), label, "study")
    return {
        "days": [
            {
                "date": d.isoformat(),
                "label": d.strftime("%a %d %b"),
                "weekday": d.strftime("%a"),
                "in_horizon": 0 <= (d - start).days < days,
            }
            for d in dates
        ],
        "items": items,
    }


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
FEED_STORE = Path(os.environ.get("CPS_FEED_DIR") or Path.home() / ".cps" / "feeds")
FEED_URL = os.environ.get("CPS_FEED_URL") or "http://localhost:8765"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{22,64}")


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

    def to_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["busy_rows"] = [dict(r) for r in self.busy_rows]
        out["subjects"] = [dict(s) for s in self.subjects]
        return out

    @classmethod
    def from_dict(cls, data: Mapping) -> Subscription:
        try:
            return cls(
                **{
                    **data,
                    "busy_rows": tuple(dict(r) for r in data["busy_rows"]),
                    "subjects": tuple(dict(s) for s in data["subjects"]),
                }
            )
        except (KeyError, TypeError) as exc:
            raise InvalidInput(f"not a subscription made by this version: {exc}") from exc


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
    plan: PlanReport | None = None,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
) -> Subscription:
    """A new feed. With a `source_url`, the timetable is read again at every
    refresh; with `ics`, the file given now is the timetable for good. `plan`, if
    the caller has just made it from the same inputs, saves planning again."""
    if source_url is None and ics is None and not busy_rows:
        raise InvalidInput("give a calendar link, a calendar file or your week")
    rows = tuple(_row_to_dict(BusyRow.parse(r)) for r in busy_rows)
    subscription = Subscription(
        token=secrets.token_urlsafe(24),
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        source_url=source_url.strip() if source_url else None,
        ics_text=decode_ics(ics) if ics is not None and not source_url else None,
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
        },
    )
    if plan is not None:
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        return replace(subscription, plan=plan.to_dict(), refreshed=stamp)
    refreshed = refresh_subscription(subscription, now=now, fetch=fetch)
    if refreshed.plan is None:
        raise ServiceError(refreshed.error or "the plan could not be made")
    return refreshed


def refresh_subscription(
    subscription: Subscription,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = fetch_calendar,
) -> Subscription:
    """Read the timetable again and plan from `now` on.

    Sessions of the current plan that are behind `now` count as done as planned,
    those reported through `replan_after` keep their outcome, and the plan goes on
    from the memory they leave. If the link cannot be read or the plan cannot be
    made, the previous plan stays and `error` says why, so that a feed never goes
    blank because a university server was down for an hour.
    """
    now = now or datetime.now(UTC)
    options = subscription.options
    try:
        ics = (
            fetch(subscription.source_url)
            if subscription.source_url
            else (subscription.ics_text.encode("utf-8") if subscription.ics_text is not None else None)
        )
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
            done = previous.history + tuple(s for s in previous.sessions if s.start_day < cut)
        plan = continue_plan(
            report,
            [SubjectSpec(**s) for s in subscription.subjects],
            done=done,
            now=now,
            retention=options["retention"],
            window=options["window"],
            lectures_as_topics=options["lectures_as_topics"],
        )
    except ServiceError as error:
        return replace(subscription, error=str(error))
    return replace(
        subscription,
        plan=plan.to_dict(),
        refreshed=now.astimezone(UTC).isoformat(timespec="seconds"),
        error=None,
    )


def is_stale(subscription: Subscription, now: datetime | None = None, age: timedelta = FEED_REFRESH) -> bool:
    if subscription.refreshed is None:
        return True
    return (now or datetime.now(UTC)) - datetime.fromisoformat(subscription.refreshed) >= age


def feed_ics(subscription: Subscription) -> bytes:
    """The feed a calendar app reads: every session, done and to come, with stable
    identifiers and a request to be read again every `FEED_REFRESH`."""
    if subscription.plan is None:
        raise InvalidInput("this feed has no plan yet")
    return export_ics(
        PlanReport.from_dict(subscription.plan),
        include_history=True,
        uid_prefix=subscription.token[:8] + "-",
        refresh=FEED_REFRESH,
    )


def feed_url(base_url: str, token: str) -> str:
    """The address to subscribe to, under the feed server at `base_url`."""
    return f"{base_url.rstrip('/')}/feed/{token}.ics"


def _path(store: str | os.PathLike, token: str) -> Path:
    if not _TOKEN.fullmatch(token):
        raise InvalidInput("not a feed token")
    return Path(store) / f"{token}.json"


def save_subscription(store: str | os.PathLike, subscription: Subscription) -> None:
    """Write a subscription into the store directory, atomically: a feed server
    reading it at the same moment sees the old file or the new one, never half."""
    target = _path(store, subscription.token)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(subscription.to_dict(), out)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_subscription(store: str | os.PathLike, token: str) -> Subscription | None:
    try:
        path = _path(store, token)
    except InvalidInput:
        return None
    if not path.is_file():
        return None
    return Subscription.from_dict(json.loads(path.read_text(encoding="utf-8")))


def delete_subscription(store: str | os.PathLike, token: str) -> bool:
    try:
        path = _path(store, token)
    except InvalidInput:
        return False
    if not path.is_file():
        return False
    path.unlink()
    return True
