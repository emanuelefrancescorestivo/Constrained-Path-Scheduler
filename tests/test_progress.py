"""
Progress and the streak (DECISIONS.md, D8 and D9): studied days count, rest days
are neutral, one missed day a week is forgiven, today cannot be missed before it
ends, and a late report repairs a day. Then the views the pages draw, and the
weekly review's suggestion, on a real semester.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import i18n, service
from cps.progress import Session, full_weeks, grid, streak, week

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MON = date(2026, 10, 5)  # a Monday


def _s(day: date, report: str | None = None, hour: int = 10, minutes: int = 90) -> Session:
    return Session(datetime(day.year, day.month, day.day, hour), minutes, "review", "Algebra", report)


def _states(result):
    return [d.state for d in result.days]


def test_studied_days_count_and_rest_days_pass_the_streak_on():
    days = [MON, MON + timedelta(days=2), MON + timedelta(days=3)]  # Tuesday has nothing planned
    result = streak([_s(d, "done") for d in days], MON, MON + timedelta(days=3))
    assert _states(result) == ["studied", "rest", "studied", "studied"]
    assert result.current == 3 and result.best == 3 and result.goal == 7
    # "Hard" counts as studied: the student showed up.
    assert streak([_s(MON, "struggled")], MON, MON).current == 1


def test_one_missed_day_a_week_is_forgiven_the_second_breaks_the_streak():
    sessions = [
        _s(MON, "done"),
        _s(MON + timedelta(days=1), None),  # nothing reported: forgiven, once
        _s(MON + timedelta(days=2), "done"),
        _s(MON + timedelta(days=3), "skipped"),  # a second miss the same week
        _s(MON + timedelta(days=4), "done"),
    ]
    result = streak(sessions, MON, MON + timedelta(days=4))
    assert _states(result) == ["studied", "forgiven", "studied", "missed", "studied"]
    assert result.current == 1 and result.best == 2
    # The next week brings a new allowance.
    nxt = [*sessions, _s(MON + timedelta(days=7), None), _s(MON + timedelta(days=8), "done")]
    later = streak(nxt, MON, MON + timedelta(days=8))
    assert [d.state for d in later.days][-2:] == ["forgiven", "studied"] and later.current == 2


def test_today_cannot_be_missed_before_it_is_over():
    sessions = [_s(MON, "done"), _s(MON + timedelta(days=1), None)]
    result = streak(sessions, MON, MON + timedelta(days=1))
    assert _states(result) == ["studied", "today"]
    assert result.current == 1 and result.today_planned == 1 and not result.today_done


def test_the_week_counts_done_of_planned_and_what_waits_for_a_report():
    sessions = [
        _s(MON, "done"),
        _s(MON, "skipped", hour=14),
        _s(MON + timedelta(days=1)),
        _s(MON + timedelta(days=3)),
    ]
    w = week(sessions, MON, datetime(2026, 10, 7, 9))
    assert (w.planned, w.done, w.skipped, w.waiting) == (4, 1, 1, 1)  # Thursday is still to come
    assert w.percent == 25 and w.minutes_done == 90 and w.days_studied == 1
    assert week([], MON, datetime(2026, 10, 7, 9)).percent is None


def test_the_grid_is_twelve_weeks_of_days_with_the_plan_s_start_and_what_is_to_come():
    today = MON + timedelta(days=2)
    rows = grid([_s(MON, "done"), _s(MON + timedelta(days=4))], MON - timedelta(days=100), today)
    assert len(rows) == 12 and all(len(r) == 7 for r in rows)
    assert rows[0][0].state == "rest" and rows[-1][0].state == "studied"
    assert rows[-1][4].state == "future" and rows[-1][4].planned == 1
    assert rows[-1][0].level == 1
    # A plan younger than the grid starts at its own first week and runs ahead.
    young = grid(
        [_s(MON + timedelta(days=1), "done"), _s(MON + timedelta(days=9))],
        MON + timedelta(days=1),
        today,
        weeks=4,
    )
    assert young[0][0].day == MON and young[0][0].state == "before" and young[0][1].state == "studied"
    assert young[1][2].state == "future" and young[1][2].planned == 1 and young[3][6].state == "future"


def test_a_full_week_is_three_sessions_or_more_all_done():
    week_one = [_s(MON + timedelta(days=i), "done") for i in range(3)]
    assert full_weeks(week_one, MON + timedelta(days=7)) == 1
    assert full_weeks(week_one[:2], MON + timedelta(days=7)) == 0  # too few to call it a full week
    assert full_weeks([*week_one, _s(MON + timedelta(days=4), "skipped")], MON + timedelta(days=7)) == 0


# --- on a real semester ---------------------------------------------------------

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)  # a Wednesday, noon in Paris


@pytest.fixture(scope="module")
def semester() -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        engine="assistant",
        tasks=[service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    return service.refresh_subscription(sub, now=LATER, reread=False)


def _past(sub):
    return service.PlanReport.from_dict(sub.plan).history


def _report_all(sub, outcome, when=LATER):
    for s in _past(sub):
        sub = service.report_session(sub, service.session_id(s), outcome, now=when)
    return sub


def test_nothing_reported_is_no_streak_and_the_review_asks_for_taps(semester):
    view = service.progress_view(semester, LATER)
    assert view["streak"]["current"] == 0 and view["totals"]["sessions"] == 0
    # The grid starts at the plan's first week, and shows at least four.
    assert len(view["grid"]) == 4 and view["since"] == "28 September"
    assert not any(m["reached"] for m in view["milestones"])
    review = service.weekly_review(semester, LATER)
    assert review["week"] == "2026-09-28" and review["sessions_planned"] == len(_past(semester))
    assert review["suggestion_key"] == "report"
    assert review["text"].startswith(f"You did 0 of {review['sessions_planned']} planned sessions (0 h).")


def test_reports_made_late_build_the_streak_and_the_review_says_so(semester):
    done = _report_all(semester, "done")
    days = sorted({datetime.fromisoformat(s.start).date() for s in _past(semester)})
    view = service.progress_view(done, LATER)
    # Every day with a session is studied, the days between are rest: one run.
    assert view["streak"]["current"] == len(days) == view["totals"]["days"]
    assert view["totals"]["sessions"] == len(_past(semester))
    assert {m["key"] for m in view["milestones"] if m["reached"]} >= {"first", "full_week"}
    review = service.weekly_review(done, LATER)
    assert review["suggestion_key"] == "all_done" and review["percent"] == 100
    assert f"Your streak is {view['streak']['current']} days." in review["text"]


def test_sessions_skipped_at_the_same_time_of_day_are_named(semester):
    morning = [s for s in _past(semester) if datetime.fromisoformat(s.start).hour < 12]
    assert len(morning) >= 2
    sub = semester
    for s in _past(semester):
        outcome = "skipped" if s in morning else "done"
        sub = service.report_session(sub, service.session_id(s), outcome, now=LATER)
    review = service.weekly_review(sub, LATER)
    assert (
        review["suggestion_key"] == "time_of_day" and "morning sessions were skipped" in review["suggestion"]
    )


def test_the_views_speak_the_language_asked_for(semester):
    with i18n.use("fr"):
        assert i18n.format_date(MON) == "lundi 5 octobre"
        assert i18n.format_date(date(2026, 10, 1)) == "jeudi 1er octobre"
        assert i18n.number(1.5) == "1,5" and i18n.number(3.0) == "3"
    assert service.weekly_review(semester, LATER)["label"] == "Week of 28 September"
    assert i18n.format_date(MON, "short") == "Mon 5 Oct" and i18n.number(1.25, 2) == "1.25"
    assert i18n.pick("fr-FR,fr;q=0.9,en;q=0.8") == "fr" and i18n.pick("de", None) == "en"


def test_a_plan_not_made_yet_has_no_progress():
    sub = service.start_subscription(
        tz="Europe/Paris", ics=(EXAMPLES / "sample-timetable.ics").read_bytes(), now=NOW
    )
    sub = service.Subscription.from_dict({**sub.to_dict(), "plan": None})
    assert service.progress_view(sub, NOW) == {"planned": False, "error": sub.error}
    assert service.weekly_review(sub, NOW) == {"planned": False}
