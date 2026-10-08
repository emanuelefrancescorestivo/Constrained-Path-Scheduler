"""
Progress: what a student's reports add up to (DECISIONS.md, D8 and D9).

Everything here is computed from the sessions of a plan and what was reported
about them; nothing is stored, so it always agrees with the calendar. The
rules, in one place:

* A day is **studied** when at least one of its sessions was reported done or
  hard ("struggled"). Reports count on the day the session was, whenever the
  tap came: a late report repairs a day honestly.
* A day with nothing planned is **rest**: it neither extends nor breaks a streak.
* A day with sessions planned and none reported done is **missed**, except the
  first such day of each week (Monday to Sunday), which is **forgiven**.
* Today is **today** until it is studied: it cannot be missed before it is over.
* The streak counts studied days since the last missed day; rest and forgiven
  days pass it on unchanged.

The planner's own rule that a session not reported counts as done (so a
forgotten tap does not derail a plan) does not apply here: a streak that grows
without anything being done would measure nothing.

Pure functions of plain values; `service.progress_view` feeds them a plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

CONFIRMED = ("done", "struggled")
STREAK_GOALS = (3, 7, 14, 30, 60, 100)
# A full week: at least this many sessions planned, all reported done or hard.
FULL_WEEK = 3


@dataclass(frozen=True)
class Session:
    """A session as progress sees it: when (local time), how long, what kind, for
    which course, and what the student reported (None when nothing yet)."""

    start: datetime
    minutes: int
    kind: str
    course: str
    report: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.report in CONFIRMED


@dataclass(frozen=True)
class Day:
    day: date
    state: str  # studied, missed, forgiven, rest, today, future, before
    planned: int
    done: int

    @property
    def level(self) -> int:
        """0 to 3, for the shade of a studied day."""
        return min(self.done, 3) if self.state == "studied" else 0


@dataclass(frozen=True)
class Streak:
    current: int
    best: int
    today_planned: int
    today_done: bool
    goal: int  # the next streak worth naming
    days: tuple[Day, ...]


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _by_day(sessions: Iterable[Session]) -> dict[date, list[Session]]:
    out: dict[date, list[Session]] = {}
    for s in sessions:
        out.setdefault(s.start.date(), []).append(s)
    return out


def streak(sessions: Sequence[Session], first: date, today: date) -> Streak:
    """Every day from `first` (the plan's start) to `today`, its state, and the
    streak they make."""
    days = _by_day(sessions)
    run = best = 0
    forgiven_weeks: set[date] = set()
    out = []
    day = first
    while day <= today:
        items = days.get(day, [])
        done = sum(s.confirmed for s in items)
        if done:
            state = "studied"
            run += 1
            best = max(best, run)
        elif not items:
            state = "rest"
        elif day == today:
            state = "today"
        elif monday(day) not in forgiven_weeks:
            forgiven_weeks.add(monday(day))
            state = "forgiven"
        else:
            state = "missed"
            run = 0
        out.append(Day(day, state, len(items), done))
        day += timedelta(days=1)
    todays = days.get(today, [])
    goal = next((g for g in STREAK_GOALS if g > run), run + 1)
    return Streak(
        current=run,
        best=best,
        today_planned=len(todays),
        today_done=any(s.confirmed for s in todays),
        goal=goal,
        days=tuple(out),
    )


def grid(sessions: Sequence[Session], first: date, today: date, weeks: int = 12) -> list[list[Day]]:
    """`weeks` weeks, Monday first, one row per week, oldest first: the last ones up
    to this week, or, for a plan younger than that, its first weeks and the weeks to
    come. Days up to today have their state from `streak`; later ones are "future"
    (with what is planned); days before the plan are "before"."""
    known = {d.day: d for d in streak(sessions, first, today).days}
    days = _by_day(sessions)
    start = max(monday(first), monday(today) - timedelta(weeks=weeks - 1))
    rows = []
    for w in range(weeks):
        row = []
        for i in range(7):
            day = start + timedelta(weeks=w, days=i)
            if day in known:
                row.append(known[day])
            elif day < first:
                row.append(Day(day, "before", 0, 0))
            else:
                items = days.get(day, [])
                row.append(Day(day, "future", len(items), 0))
        rows.append(row)
    return rows


@dataclass(frozen=True)
class Week:
    """One week's sessions, Monday to Sunday."""

    first: date
    planned: int
    done: int
    skipped: int
    waiting: int  # past and not reported
    minutes_done: int
    minutes_planned: int
    days_studied: int
    sessions: tuple[Session, ...]

    @property
    def percent(self) -> int | None:
        """Done of planned, at most 100: studying beyond the plan earns nothing."""
        if not self.planned:
            return None
        return min(100, round(100 * self.done / self.planned))


def week(sessions: Sequence[Session], first: date, now: datetime) -> Week:
    """The week starting on the Monday `first`, as of `now` (local)."""
    end = first + timedelta(days=7)
    items = tuple(s for s in sessions if first <= s.start.date() < end)
    past = [s for s in items if s.start <= now]
    return Week(
        first=first,
        planned=len(items),
        done=sum(s.confirmed for s in items),
        skipped=sum(s.report == "skipped" for s in items),
        waiting=sum(s.report is None for s in past),
        minutes_done=sum(s.minutes for s in items if s.confirmed),
        minutes_planned=sum(s.minutes for s in items),
        days_studied=len({s.start.date() for s in items if s.confirmed}),
        sessions=items,
    )


def full_weeks(sessions: Sequence[Session], today: date) -> int:
    """Weeks before this one with at least FULL_WEEK sessions, all confirmed."""
    weeks: dict[date, list[Session]] = {}
    for s in sessions:
        if s.start.date() < monday(today):
            weeks.setdefault(monday(s.start.date()), []).append(s)
    return sum(len(v) >= FULL_WEEK and all(s.confirmed for s in v) for v in weeks.values())


def part_of_day(moment: datetime) -> str:
    return "morning" if moment.hour < 12 else "afternoon" if moment.hour < 18 else "evening"
