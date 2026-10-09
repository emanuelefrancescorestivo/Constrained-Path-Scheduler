"""
The trajectory and the exam forecast (DECISIONS.md D22 to D24): weekly series from
the reported and logged sessions, the student's own 4-week average, study load, and
the FSRS forecast of recall on exam day, with what each self-test adds.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import progress, service, trends
from cps.progress import Session
from cps.web import charts

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PARIS = ZoneInfo("Europe/Paris")


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, 5, hour, 0, tzinfo=PARIS) + timedelta(days=day)  # day 0: Monday 5 October


def _logged(day: int, minutes: int, effort: int, course: str = "Algebra 3") -> trends.Logged:
    return trends.Logged(_at(day, 12), minutes, effort, 3, course, False, False, 0)


def test_weeks_count_what_was_done_and_keep_only_the_past():
    sessions = [
        Session(_at(0, 9), 90, "review", "Algebra 3", "done"),
        Session(_at(1, 19), 90, "review", "Algebra 3", "skipped"),
        Session(_at(2, 14), 90, "review", "Analysis 3", "struggled"),  # hard counts as done
        Session(_at(4, 9), 90, "review", "Analysis 3"),  # still to come
        Session(_at(7, 9), 60, "practice", "Algebra 3", "done"),
    ]
    now = _at(3, 12)  # Thursday of the first week
    weeks = trends.weekly(sessions, [_logged(1, 45, 6), _logged(2, 30, 8)], [date(2026, 10, 5)], now)
    week = weeks[0]
    assert week.current and week.planned_past == 3 and week.done == 2 and week.kept == 67
    assert week.hours == pytest.approx((90 + 90 + 45 + 30) / 60)
    assert week.load == 45 * 6 + 30 * 8 and week.logged == 2 and week.effort == 7
    assert trends.mondays(date(2026, 9, 30), date(2026, 10, 21), None) == [
        date(2026, 9, 28),
        date(2026, 10, 5),
        date(2026, 10, 12),
        date(2026, 10, 19),
    ]
    assert trends.mondays(date(2026, 9, 30), date(2026, 10, 21), 2) == [
        date(2026, 10, 12),
        date(2026, 10, 19),
    ]


def test_a_session_timed_from_the_plan_counts_once():
    # AUDIT item 47: the timer started on a planned session reports it done and
    # logs a session; the hours and the courses count it once, as the planned one.
    planned = Session(_at(0, 9), 90, "review", "Algebra 3", "done", "abc")
    twin = trends.Logged(_at(0, 12), 50, 6, 4, "Algebra 3", True, True, 0, "abc")
    alone = trends.Logged(_at(1, 12), 30, 5, 3, "Algebra 3", False, False, 0)
    week = trends.weekly([planned], [twin, alone], [date(2026, 10, 5)], _at(2, 12))[0]
    assert week.hours == pytest.approx((90 + 30) / 60) and week.logged == 2  # effort keeps both
    assert week.load == 50 * 6 + 30 * 5
    assert trends.by_course([planned], [twin, alone], date(2026, 10, 5), _at(2, 12), ["Algebra 3"]) == [
        ("Algebra 3", 2.0)
    ]
    # Reported skipped afterwards, the logged session stands on its own.
    skipped = Session(_at(0, 9), 90, "review", "Algebra 3", "skipped", "abc")
    assert trends.weekly([skipped], [twin], [date(2026, 10, 5)], _at(2, 12))[0].hours == pytest.approx(
        50 / 60
    )
    logged = [
        Session(_at(0, 12), 50, "logged", "Algebra 3", "done", "abc"),
        Session(_at(1, 12), 30, "logged", "x", "done"),
    ]
    assert [s.minutes for s in progress.merge_logged([planned], logged)] == [90, 30]
    assert [s.minutes for s in progress.merge_logged([skipped], logged)] == [90, 50, 30]


def test_averages_directions_courses_and_parts_of_day():
    assert trends.rolling([4, 8, 6, 2, 10]) == [4, 6, 6, 5, 6.5]
    week = trends.Week(date(2026, 10, 5), True, 9.0, 12.0, 4, 3, 0, 0, None, None)
    earlier = [trends.Week(date(2026, 9, 28), False, h, 12.0, 4, 4, 0, 0, None, None) for h in (5.0, 7.0)]
    assert trends.against_average([*earlier, week], "hours") == (9.0, 6.0)
    assert trends.against_average([week], "hours") == (9.0, None)
    assert trends.direction([10, 12], [8, 8]) == "up"
    assert trends.direction([8.4, 8], [8, 8]) == "steady"
    assert trends.direction([4], [8, 8]) == "down" and trends.direction([], [1]) is None
    sessions = [
        Session(_at(0, 9), 60, "review", "Algebra 3", "done"),
        Session(_at(0, 14), 90, "review", "Analysis 3", "done"),
        Session(_at(1, 15), 90, "review", "Analysis 3", "skipped"),
    ]
    logged = [_logged(1, 30, 5, course="algebra 3"), _logged(2, 60, 5, course="Reading group")]
    courses = trends.by_course(sessions, logged, date(2026, 10, 5), _at(6, 23), ["Algebra 3", "Analysis 3"])
    assert courses == [("Algebra 3", 1.5), ("Analysis 3", 1.5), ("Reading group", 1.0)]
    assert trends.by_part_of_day(sessions, date(2026, 10, 5), _at(6, 23)) == [
        ("morning", 1, 1),
        ("afternoon", 1, 2),
        ("evening", 0, 0),
    ]


def test_chart_geometry_keeps_one_axis_and_marks_the_week():
    c = charts.columns(
        [2.0, 0.0, 7.5], ["a", "b", "c"], current=[False, False, True], average=[2, 1, 3.2], limit=12
    )
    assert c["top"] == 20 and c["limit"] == pytest.approx(160 - 160 * 12 / 20)
    assert c["bars"][1]["d"] == "" and c["bars"][2]["current"] and c["average"].startswith("M")
    assert charts.nice_top(13.2) == 20 and charts.nice_top(0) == 1 and charts.nice_top(660) == 1000
    gaps = charts.line([50, None, 100], ["a", "b", "c"])
    assert gaps["d"].count("M") == 2 and gaps["area"] == ""  # a missing week breaks the line
    assert charts._labels([str(i) for i in range(14)])[-1] == "13"  # the latest week is always labelled


@pytest.fixture
def reported() -> service.Subscription:
    """Three weeks of a plan, every session up to 20 October reported done."""
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
    )
    cutoff = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    done = set()
    while True:
        plan = service.PlanReport.from_dict(sub.plan)
        todo = [
            s
            for s in plan.history + plan.sessions
            if datetime.fromisoformat(s.end) <= cutoff and service.session_id(s) not in done
        ]
        if not todo:
            return sub
        first = min(todo, key=lambda s: s.start)
        done.add(service.session_id(first))
        sub = service.report_session(
            sub,
            service.session_id(first),
            "done",
            now=datetime.fromisoformat(first.end) + timedelta(minutes=1),
        )


def test_the_forecast_rises_with_the_sessions_done(tmp_path, reported):
    now = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    plan = service.PlanReport.from_dict(reported.plan)
    weeks = trends.mondays(date(2026, 9, 28), date(2026, 10, 21), None)
    (exam,) = service.forecast(plan, now, weeks)
    assert exam["name"] == "Algebra 3" and exam["days"] > 60
    for value in (exam["with_plan"], exam["if_stopped"], *exam["path"]):
        assert value % 5 == 0 and 0 <= value <= 100  # rounded to 5: no false precision
    assert exam["with_plan"] >= exam["if_stopped"]  # following the plan never predicts less
    assert exam["path"] == sorted(exam["path"])  # every session reported done adds
    assert exam["path"][-1] == exam["if_stopped"]
    gains = service.session_gains(plan)
    assert gains and all(g["with"] >= g["without"] for g in gains.values())
    service.save_subscription(tmp_path, reported)
    view = service.trends_view(tmp_path, reported, now, "4")
    assert view["span"] == "4" and len(view["weeks"]) == 4 and view["weeks"][-1]["current"]
    assert view["weeks"][1]["kept"] == 100 and view["tiles"]["kept"]["value"] == 100
    assert view["forecast"][0]["if_stopped"] == exam["if_stopped"]
    assert service.trends_view(tmp_path, reported, now, "nonsense")["span"] == "12"
