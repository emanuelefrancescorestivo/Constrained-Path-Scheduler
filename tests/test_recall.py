"""
The recall question (DECISIONS.md D25): a self-test asks how much the student could
recall without their notes, and the answer is an FSRS grade (nothing: again, some:
hard, most: good, all: easy). The plan and the exam forecast then use what was
recalled instead of assuming every session went well.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import progress, service, trends

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def plan_and_test():
    """A plan, and its first self-test, as of the end of that self-test."""
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-16 18:00", 4.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(s for s in plan.sessions if s.kind in ("review", "first review"))
    return sub, test


def _answer(sub, test, report):
    return service.report_session(
        sub, service.session_id(test), report, now=datetime.fromisoformat(test.end) + timedelta(minutes=1)
    )


def _next_review(sub, title):
    plan = service.PlanReport.from_dict(sub.plan)
    return min(s.start_day for s in plan.sessions if s.title == title)


def test_each_answer_is_a_grade_that_the_plan_follows(plan_and_test):
    sub, test = plan_and_test
    answered = {report: _answer(sub, test, report) for report in ("forgot", "some", "most", "all")}
    grades = {}
    for report, after in answered.items():
        plan = service.PlanReport.from_dict(after.plan)
        reported = next(s for s in plan.history if service.session_id(s) == service.session_id(test))
        grades[report] = (reported.grade, reported.outcome)
    assert grades == {
        "forgot": ("again", "lapsed"),
        "some": ("hard", "recalled"),
        "most": ("good", "recalled"),
        "all": ("easy", "recalled"),
    }
    # Recalling nothing brings the topic back sooner than recalling all of it.
    assert _next_review(answered["forgot"], test.title) < _next_review(answered["all"], test.title)
    # And the forecast for its exam follows the answer.
    now = datetime.fromisoformat(test.end) + timedelta(minutes=2)
    stopped = {
        report: service.forecast(service.PlanReport.from_dict(after.plan), now)[0]["if_stopped"]
        for report, after in answered.items()
    }
    assert stopped["forgot"] <= stopped["some"] <= stopped["most"] <= stopped["all"]
    assert stopped["forgot"] < stopped["all"]


def test_only_a_self_test_asks_how_much_was_recalled(plan_and_test):
    sub, _ = plan_and_test
    plan = service.PlanReport.from_dict(sub.plan)
    task = next(s for s in plan.sessions if s.kind == "task")
    with pytest.raises(service.InvalidInput, match="only a self-test"):
        service.report_session(
            sub, service.session_id(task), "most", now=datetime.fromisoformat(task.end) + timedelta(minutes=1)
        )
    # "Done" and "hard" still work on a self-test (links in older calendar events).
    done = _answer(sub, plan_and_test[1], "done")
    assert done.outcomes[service.session_id(plan_and_test[1])] == "done"


def test_an_answer_counts_as_studied_and_is_measured_in_trends(plan_and_test):
    test = plan_and_test[1]
    start = datetime.fromisoformat(test.start)
    for report in progress.RECALLED:
        assert progress.Session(start, 90, "review", "Algebra 3", report).confirmed
    sessions = [
        progress.Session(start, 90, "review", "Algebra 3", "most"),
        progress.Session(start + timedelta(hours=3), 90, "review", "Algebra 3", "some"),
        progress.Session(start + timedelta(hours=6), 90, "task", "Stats", "done"),
    ]
    (week,) = trends.weekly(sessions, [], [progress.monday(start.date())], start + timedelta(days=1))
    assert week.answers == 2 and week.recall == 50  # (2/3 + 1/3) / 2
