"""D80: the insights a student sees about how they study, each from their own data,
shown once there is enough of it, and a pace check that never asks for more."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from cps import insights, service
from cps.trends import Week
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday
PARIS = ZoneInfo("Europe/Paris")
MONDAYS = [date(2026, 9, 28) - timedelta(weeks=k) for k in range(7, -1, -1)]


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=PARIS)


def test_starting_on_time_shows_after_three_and_counts_early_as_on_time():
    starts = [insights.Start(_at(28, 9), _at(28, 9, 5)), insights.Start(_at(28, 14), _at(28, 13, 50))]
    locked = insights.on_time(starts, MONDAYS)
    assert locked == {"key": "on_time", "unlocked": False, "have": 2, "need": 3}
    starts.append(insights.Start(_at(29, 9), _at(29, 9, 25)))  # 25 min late
    seen = insights.on_time(starts, MONDAYS)
    assert seen["unlocked"] and seen["share"] == 67 and seen["late_by"] == 25
    assert seen["series"][-1] == pytest.approx(200 / 3)  # this week: two of three on time


def test_focus_is_the_share_on_the_page_and_a_record_is_new_only_this_week():
    today = date(2026, 9, 29)
    sessions = [
        insights.Timed(date(2026, 9, 14), 50, 0, 0),
        insights.Timed(date(2026, 9, 21), 40, 10, 1),
        insights.Timed(date(2026, 9, 28), 70, 0, 0),
    ]
    seen = insights.focus(sessions, MONDAYS, today)
    assert seen["share"] == round(100 * 160 / 170) and seen["longest"] == 70 and seen["record"]
    older = insights.focus([*sessions[:2], insights.Timed(date(2026, 9, 28), 45, 0, 0)], MONDAYS, today)
    assert older["longest"] == 50 and not older["record"]  # the best is two weeks old
    alone = [insights.Timed(today, 90, 0, 0), sessions[1], insights.Timed(today, 10, 0, 0)]
    assert not insights.focus(alone, MONDAYS, today)["record"]  # nothing earlier to beat
    assert not insights.focus(sessions[:2], MONDAYS, today)["unlocked"]


def _tests(part_hours: dict[int, list[str]]) -> list[insights.SelfTest]:
    return [
        insights.SelfTest(_at(20 + i % 7, hour), 0.6, grade)
        for hour, grades in part_hours.items()
        for i, grade in enumerate(grades)
    ]


def test_the_best_time_needs_three_answers_a_part_and_a_clear_difference():
    clear = insights.recall_by_part(_tests({9: ["easy", "good", "easy"], 20: ["hard", "again", "hard"]}))
    assert clear["best"] == "morning"
    assert clear["parts"]["morning"]["n"] == 3 and clear["parts"]["evening"]["percent"] == 22
    close = insights.recall_by_part(_tests({9: ["good", "good", "good"], 20: ["good", "hard", "good"]}))
    assert close["best"] is None  # 67 against 56: under the 15 points that count as clear
    few = insights.recall_by_part(_tests({9: ["easy", "easy", "easy", "easy", "easy"], 20: ["again"]}))
    assert few["best"] is None  # one evening answer is not compared
    assert not insights.recall_by_part(_tests({9: ["easy"] * 5}))["unlocked"]


def test_memory_against_the_forecast_counts_what_fsrs_counts_as_remembered():
    # Every answer but "nothing" is remembered, as FSRS (and the pilot's calibration,
    # D55) count it; the answers' own scale is not a probability.
    grades = ["good", "hard", "easy", "again", "good"]
    tests = [insights.SelfTest(_at(28, 9), 0.9, g) for g in grades]
    seen = insights.against_forecast(tests)
    assert (seen["remembered"], seen["expected"], seen["gap"]) == (80, 90, -10)
    low = [insights.SelfTest(_at(28, 9), 0.72, "hard") for _ in range(5)]  # 100 against 72
    assert insights.against_forecast(low)["gap"] == 30  # 28 points, rounded to 5
    assert not insights.against_forecast(tests[:4])["unlocked"]


def test_days_in_a_row_wait_for_today():
    studied = {date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)}
    assert insights.days_in_a_row(studied, date(2026, 9, 29)) == 3  # today not studied yet
    assert insights.days_in_a_row(studied | {date(2026, 9, 29)}, date(2026, 9, 29)) == 4
    assert insights.days_in_a_row(studied, date(2026, 9, 30)) == 0


def _weeks(*hours: float) -> list[Week]:
    return [
        Week(MONDAYS[i], i == len(hours) - 1, h, 10.0, 5, 5, 0, 0, None, None) for i, h in enumerate(hours)
    ]


@pytest.mark.parametrize(
    ("hours", "limit", "row", "state"),
    [
        ((8, 9, 8, 9, 9), 15.0, 3, "steady"),
        ((8, 9, 8, 9, 16), 15.0, 3, "above"),  # over the limit
        ((5, 5, 5, 5, 9), None, 3, "above"),  # 1.8 times the usual weeks, 4 h more
        ((5, 5, 5, 5, 6), None, 3, "steady"),  # more, but by an hour
        ((8, 9, 8, 9, 9), 15.0, 10, "rest"),  # ten days in a row
        ((4,), 15.0, 2, "start"),
    ],
)
def test_the_pace_never_asks_for_more(hours, limit, row, state):
    assert insights.pace(_weeks(*hours), limit, row)["state"] == state


# -- through the service --------------------------------------------------------------


@pytest.fixture
def sub() -> service.Subscription:
    return service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )


def _timed_from_plan(tmp_path, sub, planned, late_minutes):
    """A planned session started with the timer `late_minutes` after its start, kept
    on the page, and logged."""
    sid, start = service.session_id(planned), datetime.fromisoformat(planned.start)
    begin = start + timedelta(minutes=late_minutes)
    running = service.start_focus(sub, "", sid=sid, now=begin)
    for minute in (1, 2):
        running = service.focus_beat(running, now=begin + timedelta(minutes=minute))
    timed = service.finish_focus(running, now=begin + timedelta(minutes=2, seconds=20))
    done, _ = service.log_session(
        tmp_path, timed, {"effort": "5", "progress": "3"}, now=begin + timedelta(minutes=3)
    )
    return done


def test_the_insights_come_from_the_diary_and_unlock_with_use(sub, tmp_path):
    service.save_subscription(tmp_path, sub)
    first = service.insights_view(tmp_path, sub, now=NOW)
    keys = [c["key"] for c in first["cards"]]
    assert keys == ["pace", "on_time", "focus", "recall_time", "forecast"]  # the pace first
    assert not any(c["unlocked"] for c in first["cards"][1:])
    assert first["cards"][1]["says"] == "Start 3 more planned sessions with the timer to see this."
    assert first["today"] is None
    planned = service.PlanReport.from_dict(sub.plan).sessions[:3]
    for session, late in zip(planned, (0, 5, 30), strict=True):
        sub = _timed_from_plan(tmp_path, sub, session, late)
    # The timer's start is kept with the log, for starting on time (D80).
    later = datetime.fromisoformat(planned[-1].start) + timedelta(hours=1)
    view = service.insights_view(tmp_path, sub, now=later)
    on_time = view["cards"][1]
    assert on_time["unlocked"] and on_time["value"] == "67 %"
    assert (
        on_time["says"]
        == "When you start late, it is by about 30 min. Try opening Focus as the session begins."
    )
    focus = view["cards"][2]
    assert focus["unlocked"] and focus["new"] and "record" not in focus  # two minutes: no record
    assert view["today"]["key"] == "on_time"  # the first insight just unlocked: a line on Today


def test_progress_shows_the_insights_and_today_the_one_that_matters(tmp_path):
    clock = {"now": NOW}
    config = Config(store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        page = web.get(f"/p/{token}/progress").text
        assert 'id="insights"' in page and "Your insights" in page
        assert page.count('class="card insight ') == 5
        assert "Answer 6 more self-tests to see this." in page
        assert 'id="your-week"' in web.get(f"/p/{token}/settings/week").text  # where "Plan a day off" leads
