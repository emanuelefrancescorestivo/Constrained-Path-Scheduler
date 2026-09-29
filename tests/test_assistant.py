"""
The assistant's rules, one test each, and the service around them.

What a student would notice: every deadline met whenever the calendar allows it, and
a warning when it does not; no study on a day off or beyond the week's hours; nothing
studied before it is taught; exam practice in the last days before an exam and not
after; free time left free when nothing needs it; the whole semester in well under a
second, so that it can be run again whenever anything changes.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cps import service
from cps.assistant import Exam, Preferences, Task, Topic, blocks_for_hours, schedule
from cps.memory import MemoryState
from cps.plan import Block

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MONDAY = 0


def evenings(days: int, per_day: int = 2) -> list[Block]:
    """`per_day` blocks each evening from 18:00, `days` days from a Monday."""
    return [
        Block(slot=d * 48 + 36 + 3 * k, day=d, start_day=d + 0.75 + k / 16)
        for d in range(days)
        for k in range(per_day)
    ]


def fresh(name: str, available: float, exam: float, course: str = "Algebra") -> Topic:
    return Topic(name, course, MemoryState(1.4, 6.0), available, available, exam, f"week {name}")


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


def test_every_deadline_is_met_when_the_calendar_allows_it():
    # 14 blocks in a week; 3 tasks need 11 of them, the tightest due on Wednesday.
    tasks = [Task("essay", 6.5, 4), Task("problem set", 2.9, 4), Task("reading", 4.9, 3)]
    result = schedule(evenings(7), first_weekday=MONDAY, tasks=tasks)
    assert result.task_left == {}
    for task in tasks:
        worked = [s.block.start_day for s in result.sessions if s.title == task.name]
        assert len(worked) == task.blocks and max(worked) < task.due_day


def test_work_that_cannot_fit_is_reported_not_hidden():
    result = schedule(evenings(2), first_weekday=MONDAY, tasks=[Task("thesis chapter", 1.9, 6)])
    assert result.task_left == {"thesis chapter": 2}


def test_days_off_and_the_weekly_budget_are_respected():
    topics = [fresh(f"w{k}", 7 * k + 0.5, 60.0) for k in range(6)]
    prefs = Preferences(weekly_blocks=5, rest_weekdays=(5, 6))
    result = schedule(
        evenings(56), first_weekday=MONDAY, topics=topics, tasks=[Task("report", 30.0, 6)], preferences=prefs
    )
    per_week: dict[int, int] = {}
    for s in result.sessions:
        assert s.block.day % 7 not in (5, 6)
        per_week[s.block.day // 7] = per_week.get(s.block.day // 7, 0) + 1
    assert max(per_week.values()) <= 5


def test_nothing_is_studied_before_it_is_taught_and_the_first_review_comes_soon():
    topic = fresh("w3", 14.6, 60.0)
    result = schedule(evenings(60), first_weekday=MONDAY, topics=[topic])
    days = [s.block.start_day for s in result.sessions if s.title == "w3"]
    assert min(days) >= 14.6
    first = next(s for s in result.sessions if s.title == "w3")
    assert first.kind == "first review" and first.block.start_day - 14.6 < 3


def test_exam_practice_is_in_the_last_days_and_never_after_the_exam():
    exams = [Exam("Algebra", 30.2, practice_blocks=3)]
    result = schedule(
        evenings(40), first_weekday=MONDAY, exams=exams, preferences=Preferences(practice_days=10)
    )
    practice = [s.block.start_day for s in result.sessions if s.kind == "practice"]
    assert len(practice) == 3 and all(20.2 <= d < 30.2 for d in practice)


def test_free_time_stays_free_when_nothing_needs_it():
    """The research planner filled every block of a semester; the assistant leaves
    the evenings a single, remembered topic does not need."""
    blocks = evenings(60)
    result = schedule(blocks, first_weekday=MONDAY, topics=[fresh("w1", 0.5, 59.0)])
    assert 0 < len(result.sessions) < len(blocks) // 10


def test_hours_become_whole_blocks():
    assert blocks_for_hours(2, 90) == 2
    assert blocks_for_hours(3, 90) == 2
    assert blocks_for_hours(3.1, 90) == 3
    assert blocks_for_hours(0.1, 60) == 1


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def semester():
    report = service.analyse_calendar(
        (EXAMPLES / "sample-semester.ics").read_bytes(),
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        blocks_per_day=3,
    )
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    tasks = [
        service.TaskSpec("Algebra problem set", "2026-10-13 23:59", 3, "Algebra 3"),
        service.TaskSpec("Stats report", "2026-11-20", 9, "Advanced Statistics"),
    ]
    return report, subjects, tasks


def test_a_semester_is_scheduled_instantly(semester):
    report, subjects, tasks = semester
    begin = time.perf_counter()
    plan = service.make_schedule(report, subjects, tasks=tasks, weekly_hours=15, rest_days=("Sun",))
    assert time.perf_counter() - begin < 1.0
    assert plan.settings.engine == "assistant" and plan.sessions
    assert all(t.scheduled == t.blocks and not t.at_risk for t in plan.tasks)
    assert {s.kind for s in plan.sessions} >= {"task", "practice", "first review", "review"}
    assert all(s.detail for s in plan.sessions)
    assert all(datetime.fromisoformat(s.start).weekday() != 6 for s in plan.sessions)
    # 15 hours of 90-minute blocks is 10 blocks a week.
    weeks: dict[int, int] = {}
    for s in plan.sessions:
        week = (date(2026, 9, 29).weekday() + s.day) // 7
        weeks[week] = weeks.get(week, 0) + 1
    assert max(weeks.values()) <= 10
    for subject in plan.subjects:
        assert service.subject_status(subject).startswith(("ready", "not ready"))


def test_a_tight_budget_is_said_plainly(semester):
    report, subjects, tasks = semester
    plan = service.make_schedule(report, subjects, tasks=tasks, weekly_hours=3)
    assert any("topics are predicted below 90%" in w for w in plan.warnings)


def test_tasks_alone_are_enough(semester):
    report, _, tasks = semester
    plan = service.make_schedule(report, tasks=tasks)
    assert {s.kind for s in plan.sessions} == {"task"} and not plan.subjects


def test_a_schedule_survives_json_and_a_replan(semester):
    report, subjects, tasks = semester
    plan = service.make_schedule(report, subjects, tasks=tasks, weekly_hours=15)
    assert service.PlanReport.from_dict(plan.to_dict()) == plan
    first_task = next(s for s in plan.sessions if s.kind == "task")
    after = service.replan_after(plan, first_task.index, "skipped")
    assert after.settings.engine == "assistant"
    report_task = next(t for t in after.tasks if t.name == first_task.title)
    # The skipped block is planned again, so the task still gets all its blocks.
    assert report_task.scheduled == report_task.blocks and not report_task.at_risk


def test_continuing_from_now_keeps_what_was_done(semester):
    report, subjects, tasks = semester
    plan = service.make_schedule(report, subjects, tasks=tasks, weekly_hours=15)
    now = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
    cut = [s for s in plan.sessions if datetime.fromisoformat(s.start) < now]
    later = service.make_schedule(report, subjects, tasks=tasks, weekly_hours=15, done=cut, now=now)
    assert [s.title for s in later.history] == [s.title for s in cut]
    assert all(datetime.fromisoformat(s.start) > now for s in later.sessions)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"weekly_hours": 0}, "weekly hours"),
        ({"rest_days": ("Mon", "Funday")}, "days off"),
        ({"rest_days": service.WEEKDAY_NAMES}, "at least one day"),
        ({"tasks": [service.TaskSpec("x", "2026-01-01", 2)]}, "before the start"),
        ({"tasks": [service.TaskSpec("x", "2026-12-01", 0)]}, "positive number of hours"),
    ],
)
def test_bad_preferences_are_readable_errors(semester, options, message):
    report, subjects, _ = semester
    with pytest.raises(service.ServiceError, match=message):
        service.make_schedule(report, subjects, **options)
