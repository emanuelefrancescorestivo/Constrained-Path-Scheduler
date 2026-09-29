"""
The service layer: everything a user-facing surface needs, and nothing else.

The CLI, the Streamlit app and any future web API call these functions and only
these, so they can be swapped without touching the planner. Nothing here imports a
UI framework, prints, or reads a file: bytes and plain values in, frozen result
objects out. Every result has `to_dict()` returning plain JSON types, and
`PlanReport.from_dict` rebuilds a plan, so a front end can hold a plan as JSON and
send it back to `replan_after` or `export_ics`.

    analyse_calendar(ics, *, start, tz, ...)          -> CalendarReport
    make_plan(report, subjects, *, retention, ...)     -> PlanReport
    replan_after(plan, session_index, outcome)         -> PlanReport
    export_ics(plan)                                   -> bytes
    recall_curve(plan, subject)                        -> [(day, probability), ...]

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

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
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
from .rolling import DEFAULT_FAILURE_PENALTY, Subject, run_rolling
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

    def to_dict(self) -> dict:
        return {"summary": self.summary, "start": self.start, "end": self.end}


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
    zone = _zone(tz)
    events: list[BusyEvent] = []
    if ics_text is not None:
        window_start = datetime.combine(start, time(0, 0), tzinfo=zone)
        try:
            events += expand_events(ics_text, window_start, window_start + timedelta(days=days), zone)
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidCalendar(
                f"the calendar file could not be read ({exc}); export it again as .ics"
            ) from exc
    events += busy_from_table(rows, start, days, tz)
    events.sort(key=lambda e: (e.start, e.end))
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
    if days is None:
        latest = max((_days_after(start, d.when) for d in found), default=21.0)
        days = max(1, math.ceil(latest - 1e-9))

    events = _events(text, rows, start, days, tz)
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
        events=tuple(EventView(e.summary, _iso(e.start), _iso(e.end)) for e in events),
        blocks=tuple(
            BlockView(b.slot, b.day, b.start_day, _iso(clock(b.slot)), _iso(clock(b.slot + block_slots)))
            for b in tiles
        ),
        assessments=tuple(AssessmentView(d.subject, _iso(d.when), d.summary) for d in found),
        ics_text=text,
        busy_rows=rows,
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
    """Specs to plain starting states with exam days, validated."""
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
                "exam": _iso(exam),
                "exam_day": exam_day,
                "stability": memory.stability,
                "difficulty": memory.difficulty,
                "last_review_day": 0.0,
                "prior": prior,
            }
        )
    return resolved, max(float(r["exam_day"]) for r in resolved)


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
) -> PlanReport:
    """Plan every subject towards its own exam on the free blocks of `report`."""
    if not 0.5 <= retention < 1.0:
        raise InvalidInput("target recall must be at least 0.5 and below 1")
    if window < 1:
        raise InvalidInput("the planning window must be at least one block")
    specs, latest = _resolve(report, subjects)
    needed = max(1, math.ceil(latest - 1e-9))
    if needed > report.days:
        report = _extend(report, needed)
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
    return _plan(settings, specs, tuple(report.blocks), tuple(report.events), history=(), strict=True)


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
    subjects = [
        Subject(s["name"], MemoryState(s["stability"], s["difficulty"]), s["exam_day"], s["last_review_day"])
        for s in states
    ]
    pending = tuple(b for b in blocks if b.start_day > after_day)
    rng = np.random.default_rng(settings.seed) if settings.seed is not None else None
    result = (
        run_rolling(
            [b.as_block() for b in pending],
            subjects,
            window=settings.window,
            retention=settings.retention,
            failure_penalty=settings.failure_penalty,
            rng=rng,
        )
        if pending
        else None
    )

    by_slot = {b.slot: b for b in blocks}
    built: list[SessionView] = []
    for number, s in enumerate(result.sessions if result else (), start=len(history)):
        view = by_slot[s.block.slot]
        built.append(
            SessionView(
                index=number,
                subject=s.subject,
                start=view.start,
                end=view.end,
                day=view.day,
                start_day=view.start_day,
                slot=view.slot,
                recall=s.retrievability_at_review,
                outcome="recalled" if s.outcome != Grade.AGAIN else "lapsed",
                stability_before=s.stability_before,
                stability_after=s.stability_after,
                rationale=s.rationale,
            )
        )
    sessions = tuple(built)

    finals = _replay(specs, history + sessions)
    views, warnings = [], []
    targets = (
        result.targets
        if result
        else tuple(
            Subject(s["name"], MemoryState(s["stability"], s["difficulty"]), s["exam_day"]).target(
                settings.retention
            )
            for s in specs
        )
    )
    unreachable = result.unreachable if result else tuple(False for _ in specs)
    for spec, target, lost in zip(specs, targets, unreachable, strict=True):
        memory, last = finals[spec["name"]]
        first = next((x.start for x in history + sessions if x.subject == spec["name"]), None)
        ready = memory.stability >= target
        views.append(
            SubjectView(
                name=spec["name"],
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
                f"{spec['name']}: even if every review succeeded, the free blocks before this exam "
                f"cannot build a stability of {target:.0f} days, so no time was spent on it. More "
                f"free time spread over more days would change that."
            )
        elif not ready:
            warnings.append(f"{spec['name']}: this plan does not reach the target by the exam.")
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


def _replay(specs: Sequence[dict], done: Sequence[SessionView]) -> dict[str, tuple[MemoryState, float]]:
    """Memory state and last review day of every subject after `done`, recomputed
    with exact FSRS transitions (a single source of truth, not stored numbers)."""
    state = {s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in specs}
    for session in sorted(done, key=lambda x: x.start_day):
        if session.outcome == "skipped":
            continue
        memory, last = state[session.subject]
        grade = Grade.GOOD if session.outcome == "recalled" else Grade.AGAIN
        state[session.subject] = (review(memory, session.start_day - last, grade), session.start_day)
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


def export_ics(plan: PlanReport) -> bytes:
    """The upcoming sessions as an importable calendar, as bytes (see AUDIT item 21)."""
    text = plan_to_ics(
        [(s.slot, s.subject, s.rationale) for s in plan.sessions],
        plan.settings.start,
        plan.settings.tz,
        slots_per_day=plan.settings.slots_per_day,
        block_slots=max(1, plan.settings.block_minutes // (24 * 60 // plan.settings.slots_per_day)),
    )
    return text.encode("utf-8")


def recall_curve(plan: PlanReport, subject: str, step: float = 0.25) -> list[tuple[float, float]]:
    """Predicted probability of recall over time, from the start to the exam.

    Includes the sessions already reported and the upcoming ones; at each session
    the curve has two points, just before and just after the review.
    """
    spec = next((s for s in plan.specs if s["name"] == subject), None)
    if spec is None:
        raise InvalidInput(f"no subject called {subject!r} in this plan")
    memory = MemoryState(spec["stability"], spec["difficulty"])
    last = spec["last_review_day"]
    reviews = sorted(
        (s for s in plan.history + plan.sessions if s.subject == subject and s.outcome != "skipped"),
        key=lambda s: s.start_day,
    )
    points: list[tuple[float, float]] = []
    t = 0.0
    for session in [*reviews, None]:
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


def session_rows(plan: PlanReport) -> list[dict]:
    """The upcoming sessions, one row each, as a person reads them."""
    return [
        {
            "#": s.index,
            "when": datetime.fromisoformat(s.start).strftime("%a %d %b %H:%M"),
            "subject": s.subject,
            "recall now": f"{s.recall:.0%}",
            "why": s.rationale,
        }
        for s in plan.sessions
    ]


def week_count(plan: PlanReport) -> int:
    return max(1, math.ceil(plan.settings.horizon_days / 7))


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
