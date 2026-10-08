"""
The student's trajectory (DECISIONS.md D22, D23): week-by-week series computed from
the plan's sessions with their reports and from the focus sessions logged. Pure
functions; `cps.service` gathers the inputs and the web pages draw the result.

Hours count what was done: planned sessions reported done or hard, and focus
sessions logged. A week's "kept" share is the planned sessions done among those
already past, so a week in progress is not counted against the student for the days
still to come. Study load (D23) is minutes times perceived effort, and exists only
for logged sessions, the only ones with an effort.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .progress import Session, monday, part_of_day

AVERAGE_WEEKS = 4  # the "your 4-week average" every comparison uses
PARTS = ("morning", "afternoon", "evening")


@dataclass(frozen=True)
class Logged:
    """A logged focus session's numbers, as the diary keeps them."""

    start: datetime  # local
    minutes: int
    effort: int  # 1 to 10
    progress: int  # 1 to 5
    course: str
    timed: bool
    checked: bool
    interruptions: int

    @property
    def load(self) -> int:
        return self.minutes * self.effort


@dataclass(frozen=True)
class Week:
    first: date  # the Monday
    current: bool  # the week in progress
    hours: float  # done and logged
    hours_planned: float
    planned_past: int
    done: int
    load: int
    logged: int
    effort: float | None  # mean perceived effort of the logged sessions
    progress: float | None

    @property
    def kept(self) -> int | None:
        """Planned sessions done among those already past, in percent."""
        return round(100 * self.done / self.planned_past) if self.planned_past else None


def mondays(first: date, today: date, weeks: int | None) -> list[date]:
    """The Mondays shown: the last `weeks` up to this one, or all since `first`."""
    last = monday(today)
    start = monday(first) if weeks is None else max(monday(first), last - timedelta(weeks=weeks - 1))
    out = []
    day = start
    while day <= last:
        out.append(day)
        day += timedelta(weeks=1)
    return out


def weekly(
    sessions: Sequence[Session], logged: Sequence[Logged], weeks: Sequence[date], now: datetime
) -> list[Week]:
    out = []
    for first in weeks:
        end = first + timedelta(days=7)
        planned = [s for s in sessions if s.planned and first <= s.start.date() < end]
        past = [s for s in planned if s.start <= now]
        mine = [x for x in logged if first <= x.start.date() < end]
        minutes = sum(s.minutes for s in planned if s.confirmed) + sum(x.minutes for x in mine)
        out.append(
            Week(
                first=first,
                current=first <= now.date() < end,
                hours=minutes / 60,
                hours_planned=sum(s.minutes for s in planned) / 60,
                planned_past=len(past),
                done=sum(s.confirmed for s in past),
                load=sum(x.load for x in mine),
                logged=len(mine),
                effort=sum(x.effort for x in mine) / len(mine) if mine else None,
                progress=sum(x.progress for x in mine) / len(mine) if mine else None,
            )
        )
    return out


def rolling(values: Sequence[float], n: int = AVERAGE_WEEKS) -> list[float]:
    """Each value's trailing mean over at most `n` values, itself included."""
    return [
        sum(values[max(0, i - n + 1) : i + 1]) / len(values[max(0, i - n + 1) : i + 1])
        for i in range(len(values))
    ]


def against_average(weeks: Sequence[Week], field: str) -> tuple[float, float | None]:
    """This week's value and the mean of the `AVERAGE_WEEKS` full weeks before it
    (None when there is no earlier week)."""
    if not weeks:
        return 0.0, None
    now = float(getattr(weeks[-1], field))
    before = [float(getattr(w, field)) for w in weeks[:-1]][-AVERAGE_WEEKS:]
    return now, (sum(before) / len(before) if before else None)


def direction(recent: Sequence[float], earlier: Sequence[float], tolerance: float = 0.1) -> str | None:
    """ "up", "down" or "steady": the mean of `recent` against that of `earlier`,
    steady within `tolerance` (10 %) of it. None without both."""
    if not recent or not earlier:
        return None
    a, b = sum(recent) / len(recent), sum(earlier) / len(earlier)
    if b == 0:
        return "up" if a > 0 else "steady"
    change = (a - b) / b
    return "up" if change > tolerance else "down" if change < -tolerance else "steady"


def by_course(
    sessions: Sequence[Session], logged: Sequence[Logged], first: date, now: datetime, known: Iterable[str]
) -> list[tuple[str, float]]:
    """Hours per course from `first` to now, most first. A logged session names its
    course in the student's words; one that matches no known course (ignoring case)
    is kept under its own name."""
    names = {k.casefold(): k for k in known}
    hours: dict[str, float] = {}
    for s in sessions:
        if s.planned and s.confirmed and first <= s.start.date() and s.start <= now:
            hours[s.course] = hours.get(s.course, 0.0) + s.minutes / 60
    for x in logged:
        if first <= x.start.date():
            name = names.get(x.course.strip().casefold(), x.course.strip() or "?")
            hours[name] = hours.get(name, 0.0) + x.minutes / 60
    return sorted(hours.items(), key=lambda kv: (-kv[1], kv[0].casefold()))


def by_part_of_day(sessions: Sequence[Session], first: date, now: datetime) -> list[tuple[str, int, int]]:
    """(part of day, planned sessions done, planned sessions past) from `first`."""
    out = []
    for part in PARTS:
        past = [
            s
            for s in sessions
            if s.planned and first <= s.start.date() and s.start <= now and part_of_day(s.start) == part
        ]
        out.append((part, sum(s.confirmed for s in past), len(past)))
    return out
