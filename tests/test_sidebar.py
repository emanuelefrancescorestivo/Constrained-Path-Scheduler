"""
D136: the sidebar on a laptop, as Notion Calendar's. What must hold: this month's days
from a Monday, today marked, a day with a session or an exam marked from the plan, each
day a link that opens the calendar on it (today still today); every section one click
away, the one open marked; the courses with their colours and the days to their exams;
on every page of a plan but not while setting up.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # 10:00 in Paris, a Wednesday


def _plan(where: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(where, service.finish_first_run(sub))
    return service.load_subscription(where, sub.token)


def test_the_month_marks_today_study_and_exams(tmp_path):
    sub = _plan(tmp_path)
    view = service.sidebar_view(tmp_path, sub, NOW)
    assert view["month"] == "October 2026"
    days = [d for week in view["weeks"] for d in week]
    assert all(len(week) == 7 for week in view["weeks"])
    assert date.fromisoformat(days[0]["date"]).weekday() == 0  # from a Monday
    assert days[0]["date"] == "2026-09-28" and not days[0]["in_month"]
    assert [d["date"] for d in days if d["today"]] == ["2026-10-14"]
    plan = service.PlanReport.from_dict(sub.plan)
    zone = service._zone("Europe/Paris")
    planned = {
        datetime.fromisoformat(s.start).astimezone(zone).date().isoformat()
        for s in plan.sessions + plan.history
        if s.outcome != "skipped"
    }
    shown = {d["date"] for d in days}
    assert {d["date"] for d in days if d["study"]} == planned & shown
    tests = [d for d in days if d["exam"]]
    for d in tests:
        assert "exam:" in d["label"]
    assert [w["letter"] for w in view["weekdays"]] == ["M", "T", "W", "T", "F", "S", "S"]


def test_the_courses_with_their_colours_and_exams(tmp_path):
    sub = _plan(tmp_path)
    view = service.sidebar_view(tmp_path, sub, NOW)
    courses = service.courses_view(tmp_path, sub, NOW)
    assert [(c["index"], c["name"], c["color"]) for c in view["courses"]] == [
        (c["index"], c["course"], c["color"]) for c in courses
    ]
    algebra = next(c for c in view["courses"] if c["name"] == "Algebra 3")
    exam = next(c for c in courses if c["course"] == "Algebra 3")["exam"]
    assert algebra["exam"] == exam["says"] and algebra["soon"] == (0 <= exam["days"] <= 14)


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


# D195: the to-do page took Tasks' place in the sidebar; Tasks' page is reached from it.
SECTIONS = ("", "/week", "/focus", "/cards", "/todo", "/progress")
SECTIONS += ("/community", "/map", "/tips", "/you", "/settings")


def test_every_page_of_a_plan_has_the_sidebar_with_its_section_marked(web, tmp_path):
    token = _plan(tmp_path).token
    for path in SECTIONS:
        page = unescape(web.get(f"/p/{token}{path}").text)
        assert "has-sidebar" in page and '<aside class="sidebar"' in page, path
        opens = page.index('<aside class="sidebar"')
        side = page[opens : page.index("</aside>", opens)]
        for other in SECTIONS:
            assert f'href="/p/{token}{other}"' in side, (path, other)
        current = side.split('aria-current="page"')[0].rsplit("<a ", 1)[1]
        assert f'href="/p/{token}{path}"' in current, path
    course = unescape(web.get(f"/p/{token}/courses/0").text)
    side = course[course.index('<aside class="sidebar"') :]
    assert f'href="/p/{token}/courses/0" class="c' in side
    assert 'aria-current="page"><span class="dot">' in side


def test_a_day_of_the_month_opens_the_calendar_on_it(web, tmp_path):
    token = _plan(tmp_path).token
    page = web.get(f"/p/{token}").text
    assert f'href="/p/{token}/week?day=2026-10-21"' in page
    day = web.get(f"/p/{token}/week?day=2026-10-21").text
    assert 'data-first="2026-10-21" data-today="2026-10-14"' in day
    for wrong in ("tomorrow", "2026-13-40"):
        assert 'data-first="2026-10-14"' in web.get(f"/p/{token}/week?day={wrong}").text


def test_no_sidebar_while_setting_up_or_without_a_plan(web, tmp_path):
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    assert '<aside class="sidebar"' not in web.get(f"/p/{token}/week?new=1").text
    assert '<aside class="sidebar"' not in web.get("/").text
