"""
The student's day (DECISIONS.md D69): when they get up and go to bed sets when
study may be planned, with half an hour clear at each end, and lunch is kept free
every day unless they would rather study then.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from html import unescape
from pathlib import Path

import pytest

from cps import service

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday


@pytest.fixture(scope="module")
def semester():
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def test_a_plan_made_before_the_questions_shows_its_window_widened():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    day = service.day_view(sub)
    assert (day["wake"], day["bed"], day["lunch"]) == ("07:30", "22:30", "free")
    assert service.lunch_hours(sub.options) is None  # nothing kept free until asked


def test_the_day_sets_the_window_and_lunch_is_kept_free(semester):
    sub = service.set_day(
        semester, wake="07:00", bed="23:00", lunch="free", lunch_start="12:30", lunch_end="13:30", now=MORNING
    )
    assert sub.options["study_window"] == [7.5, 22.5]
    assert (sub.options["wake"], sub.options["bed"]) == ("07:00", "23:00")
    plan = service.PlanReport.from_dict(sub.plan)
    lunches = [e for e in plan.events if e.source == "lunch"]
    days = sorted(_local(e.start).date() for e in lunches)
    assert len(set(days)) == len(days) == (days[-1] - days[0]).days + 1 > 60  # one every day
    assert all(_local(e.start).time() == time(12, 30) for e in lunches)
    for s in plan.sessions:
        start, end = _local(s.start), _local(s.end)
        assert time(7, 30) <= start.time() and (end.time() <= time(22, 30) or end.time() == time(0, 0))
        assert not (start.time() < time(13, 30) and end.time() > time(12, 30)), s  # never over lunch
    shown = service.calendar_view(sub, MORNING.date(), 3, MORNING)
    assert any(i["kind"] == "lunch" and i["label"] == "Lunch" for i in shown["items"])
    assert service.day_view(sub)["lunch"] == "free"


def test_lunch_can_be_studied_through(semester):
    sub = service.set_day(semester, wake="07:00", bed="23:00", lunch="study", now=MORNING)
    plan = service.PlanReport.from_dict(sub.plan)
    assert not [e for e in plan.events if e.source == "lunch"]
    assert service.day_view(sub)["lunch"] == "study" and sub.options["lunch"]["start"] == "12:30"


def test_a_bedtime_after_midnight_ends_the_window_at_midnight(semester):
    sub = service.set_day(semester, wake="09:15", bed="01:30", now=MORNING)
    assert sub.options["study_window"] == [9.75, 24.0] and sub.options["bed"] == "01:30"


@pytest.mark.parametrize(
    ("wake", "bed", "lunch", "start", "end", "words"),
    [
        ("07:00", "08:30", "free", "12:30", "13:30", "three hours"),
        ("seven", "23:00", "free", "12:30", "13:30", "a time like 07:30"),
        ("07:00", "23:00", "free", "13:30", "12:30", "lunch must end"),
        ("07:00", "23:00", "maybe", "12:30", "13:30", "lunch is kept free"),
    ],
)
def test_a_day_that_cannot_be_planned_is_refused(semester, wake, bed, lunch, start, end, words):
    with pytest.raises(service.InvalidInput, match=words):
        service.set_day(
            semester, wake=wake, bed=bed, lunch=lunch, lunch_start=start, lunch_end=end, now=MORNING
        )


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


def test_the_day_on_the_questions_and_in_settings_without_scripts(tmp_path):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        page = unescape(web.get(f"/p/{token}/routine?new=1").text)
        assert "Your day" in page and 'name="wake" type="time" step="900" value="07:30"' in page
        assert 'name="lunch" value="free" checked' in page
        form = {
            "wake": "06:45",
            "bed": "22:15",
            "lunch": "free",
            "lunch_start": "12:00",
            "lunch_end": "13:00",
            "commute": "0",
            "new": "1",
        }
        saved = web.post(f"/p/{token}/routine", data=form, follow_redirects=False)
        assert saved.status_code == 303
        settings = unescape(web.get(f"/p/{token}/settings/week").text)
        assert 'value="06:45"' in settings and 'value="12:00"' in settings and "I get up at" in settings
        refused = web.post(f"/p/{token}/routine", data={**form, "bed": "07:30"})
        assert refused.status_code == 400 and "three hours" in unescape(refused.text)
        assert 'value="07:30"' in unescape(refused.text)  # what was typed, sent back


def test_spark_asks_the_questions_and_the_bars_draw_into_the_fields(tmp_path):
    # D70: the script walks the cards one at a time and draws on the bars; the page
    # it is given is the whole form, so nothing depends on it.
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        page = unescape(web.get(f"/p/{token}/routine?new=1").text)
        assert '<script type="module" src="/static/routine.js">' in page
        assert 'class="card mascot-tip routine-guide" aria-live="polite" hidden' in page
        assert page.count('<section class="card question"') == 6 and "<section hidden" not in page
        assert 'data-night="wake,bed" data-range="lunch_start,lunch_end"' in page
        assert 'data-range="x0_start,x0_end" data-label="Football" data-label-from="x0_label" hidden' in page
        assert all(say in page for say in service.ROUTINE_SAYS.values())


def test_spark_waits_in_the_top_bar_but_not_while_setting_up_or_focusing(tmp_path):
    # D71: a link to the coach without scripts, Spark's sheet with them; in the top bar
    # since D97, never over a page's buttons.
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        today = unescape(web.get(f"/p/{token}").text)
        assert f'<a class="spark-top" href="/p/{token}/coach" data-spark' in today
        assert today.index("spark-top") < today.index("</header>")
        assert 'id="spark-help" class="sheet spark-sheet" popover' in today
        assert (
            f'action="/p/{token}/coach" class="spark-ask"' in today and f'href="/p/{token}/routine"' in today
        )
        for quiet in (f"/p/{token}/routine?new=1", f"/p/{token}/coach"):
            assert "spark-top" not in web.get(quiet).text, quiet
        assert "spark-top" in web.get(f"/p/{token}/focus").text
        web.post(f"/p/{token}/focus/start", data={"course": "Algebra 3"})
        assert "spark-top" not in web.get(f"/p/{token}/focus").text  # nothing beside the clock
