"""D77, D97: Spark's tour of Today, once after setting up, in three steps; every other
section says what it is for in one line the first time it is opened. The server says
which tour a page shows and remembers the ones finished or skipped and the lines
shown; tour.js does the rest, and the Python tests check what it is given."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, tzinfo=UTC)


@pytest.fixture
def plan():
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)


@pytest.mark.parametrize(
    ("tab", "setup", "new", "tour"),
    [
        ("today", False, False, "today"),
        ("calendar", False, False, None),  # a line instead (D97)
        ("today", False, True, None),  # nothing while setting up
        ("calendar", False, True, None),
        ("today", True, False, None),  # drawing a week by hand: its own instructions
        ("settings", False, True, None),
        ("settings", False, False, None),
        ("tasks", False, False, None),
        ("focus", False, False, None),
        ("community", False, False, None),
        ("progress", False, False, None),
        (None, False, False, None),
    ],
)
def test_only_today_has_a_tour(plan, tab, setup, new, tour):
    assert service.tour_for(plan, tab, setup=setup, new=new) == tour


@pytest.mark.parametrize(
    "tab", ["calendar", "courses", "community", "you", "focus", "progress", "tasks", "cards"]
)
def test_every_other_section_has_a_line_once(plan, tab):
    line = service.section_hint(plan, tab)
    assert line and len(line) < 140  # one line, not a tour
    assert service.section_hint(service.see_tour(plan, tab), tab) is None
    assert service.section_hint(plan, "settings") is None and service.section_hint(None, tab) is None


def test_a_tour_seen_is_not_shown_again_and_settings_bring_them_all_back(plan):
    seen = service.see_tour(plan, "today")
    assert service.tour_for(seen, "today") is None
    assert service.see_tour(seen, "today") == seen  # once is enough
    assert service.tours_seen(service.see_tour(seen, "tasks")) == ["today", "tasks"]
    assert service.tour_for(service.reset_tours(seen), "today") == "today"
    assert service.section_hint(service.reset_tours(service.see_tour(seen, "tasks")), "tasks")
    assert service.tour_for(None, "today") is None


def test_a_tour_that_does_not_exist_is_refused(plan):
    with pytest.raises(service.InvalidInput):
        service.see_tour(plan, "everything")


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _start(web) -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def _steps(page: str) -> list[str]:
    tour = re.search(r'<div class="tour" id="tour" data-name="(\w+)".*?</ol>', page, re.S)
    assert tour, "no tour on the page"
    return re.findall(r'<li data-target="([^"]+)"', tour[0])


def test_every_step_of_todays_tour_lights_something(web):
    token = _start(web)
    page = web.get(f"/p/{token}").text
    assert 'data-name="today"' in page
    assert '<script type="module" src="/static/tour.js"></script>' in page
    steps = _steps(page)
    assert len(steps) == 3
    marks = set(re.findall(r'data-tour="([\w-]+)"', page))
    for selector in steps:
        wanted = re.fullmatch(r"\[data-tour=([\w-]+)\]", selector)
        assert wanted and wanted[1] in marks, selector


def test_setting_up_has_no_tour_and_a_section_shows_its_line_once(web):
    token = _start(web)
    for setup in (f"/p/{token}/week?new=1", f"/p/{token}/settings?new=1"):
        assert 'id="tour"' not in web.get(setup).text, setup
    line = "Everything that is due, soonest first."
    assert line in web.get(f"/p/{token}/tasks").text
    assert line not in web.get(f"/p/{token}/tasks").text  # once
    calendar = web.get(f"/p/{token}/week").text
    assert 'class="section-hint board-hint"' in calendar and 'id="tour"' not in calendar


def test_a_tour_finished_stays_finished_and_settings_show_it_again(web):
    token = _start(web)
    assert 'id="tour"' in web.get(f"/p/{token}").text
    done = web.post(f"/p/{token}/tour", data={"name": "today"})
    assert done.status_code == 204
    assert 'id="tour"' not in web.get(f"/p/{token}").text
    assert 'id="tour"' not in web.get(f"/p/{token}/tasks").text  # a line there, not a tour (D97)
    assert web.post(f"/p/{token}/tour", data={"name": "nothing"}).status_code == 400
    settings = web.get(f"/p/{token}/settings/look").text
    assert f'action="/p/{token}/tour/again"' in settings
    again = web.post(f"/p/{token}/tour/again", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == f"/p/{token}"
    assert 'data-name="today"' in web.get(f"/p/{token}").text


def test_a_tour_is_hidden_without_its_script():
    template = (
        Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "templates" / "_tour.html"
    ).read_text()
    assert re.search(r'<div class="tour" id="tour"[^>]* hidden>', template)
    assert '<ol class="tour-steps" hidden>' in template
    assert "style=" not in template  # the page's Content-Security-Policy allows no inline style


def test_the_one_tour_has_three_steps():
    template = (Path(__file__).resolve().parents[1] / "src/cps/web/templates/_tour.html").read_text()
    tours = re.split(r"\{% (?:el)?if tour == ", template)[1:]
    assert len(tours) == 1 and tours[0].startswith('"today"')
    assert tours[0].count("<li data-target=") == 3


def test_the_calendar_offers_a_month_once_set_up(web):
    # D79: Day, 3 Days, Week and Month on the plan; drawing a week by hand keeps the
    # views with hours only (a month is no place to draw classes).
    token = _start(web)
    assert '<button type="button" data-view="30">Month</button>' in web.get(f"/p/{token}").text
    assert 'data-view="30"' in web.get(f"/p/{token}/week").text
