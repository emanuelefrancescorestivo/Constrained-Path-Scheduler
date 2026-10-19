"""D77: Spark's tour of each section, the first time it is opened. The server says
which tour a page shows and remembers the ones finished or skipped; tour.js does the
rest, and the Python tests check what it is given."""

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
        ("calendar", False, False, "today"),
        ("today", False, True, "week"),  # the calendar while setting up
        ("calendar", False, True, "week"),
        ("today", True, False, None),  # drawing a week by hand: its own instructions
        ("settings", False, True, None),
        ("settings", False, False, None),
        ("tasks", False, False, "tasks"),
        ("focus", False, False, "focus"),
        ("community", False, False, "community"),
        ("progress", False, False, "progress"),
        (None, False, False, None),
    ],
)
def test_each_section_has_its_tour(plan, tab, setup, new, tour):
    assert service.tour_for(plan, tab, setup=setup, new=new) == tour


def test_a_tour_seen_is_not_shown_again_and_settings_bring_them_all_back(plan):
    seen = service.see_tour(plan, "today")
    assert service.tour_for(seen, "today") is None
    assert service.tour_for(seen, "calendar") is None
    assert service.tour_for(seen, "tasks") == "tasks"
    assert service.see_tour(seen, "today") == seen  # once is enough
    assert service.tours_seen(service.see_tour(seen, "tasks")) == ["today", "tasks"]
    assert service.tour_for(service.reset_tours(seen), "today") == "today"
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


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("", "today"),
        ("/tasks", "tasks"),
        ("/focus", "focus"),
        ("/community", "community"),
        ("/progress", "progress"),
    ],
)
def test_every_step_lights_something_on_its_page(web, path, name):
    token = _start(web)
    page = web.get(f"/p/{token}{path}").text
    assert f'data-name="{name}"' in page
    assert '<script type="module" src="/static/tour.js"></script>' in page
    steps = _steps(page)
    assert len(steps) >= 2
    marks = set(re.findall(r'data-tour="([\w-]+)"', page))
    # A step whose part a new plan does not have yet is passed over by tour.js
    # (nothing to report, no profile yet): the others must be there.
    later = {"reports", "community-tabs", "community-share"}
    for selector in steps:
        wanted = re.fullmatch(r"\[data-tour=([\w-]+)\]", selector)
        if wanted and wanted[1] not in later:
            assert wanted[1] in marks, (name, selector)


def test_the_setup_calendar_has_its_own_tour(web):
    token = _start(web)
    page = web.get(f"/p/{token}/week?new=1").text
    assert 'data-name="week"' in page
    steps = _steps(page)
    assert "#calendar .ev-study" in steps  # a study session, drawn by calendar.js
    assert 'data-tour="setup-next"' in page and 'data-tour="block"' in page
    assert 'id="tour"' not in web.get(f"/p/{token}/settings?new=1").text  # the other setup pages: none


def test_a_tour_finished_stays_finished_and_settings_show_it_again(web):
    token = _start(web)
    assert 'id="tour"' in web.get(f"/p/{token}").text
    done = web.post(f"/p/{token}/tour", data={"name": "today"})
    assert done.status_code == 204
    assert 'id="tour"' not in web.get(f"/p/{token}").text
    assert 'data-name="tasks"' in web.get(f"/p/{token}/tasks").text  # each section its own
    assert web.post(f"/p/{token}/tour", data={"name": "nothing"}).status_code == 400
    settings = web.get(f"/p/{token}/settings").text
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


def test_no_tour_is_longer_than_seven_steps():
    template = (Path(__file__).resolve().parents[1] / "src/cps/web/templates/_tour.html").read_text()
    tours = re.split(r"\{% (?:el)?if tour == ", template)[1:]
    assert len(tours) == len(service.TOURS)
    assert all(2 <= t.count("<li data-target=") <= 7 for t in tours)
