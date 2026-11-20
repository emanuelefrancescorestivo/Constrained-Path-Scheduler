"""D77, D97, D141: Spark's tour of Today, once after setting up, in three steps; every
other section says what it is for in one line the first time it is opened, and since
D141 Spark shows it around on that visit too. The server says which tour a page shows
and remembers the ones finished or skipped and the lines shown; tour.js does the rest,
and the Python tests check what it is given."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app


def _unversioned(html: str) -> str:
    """A page with its files' plain names: pages name them under the release's
    version (D106, checked in test_full_check)."""
    return re.sub(r"/static/v/[0-9a-f]{10}/", "/static/", html)


SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, tzinfo=UTC)


@pytest.fixture
def plan():
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)


@pytest.mark.parametrize(
    ("tab", "setup", "new", "tour"),
    [
        ("today", False, False, "today"),
        ("calendar", False, False, "calendar"),  # D141: each section its tour again
        ("today", False, True, None),  # nothing while setting up
        ("calendar", False, True, None),
        ("today", True, False, None),  # drawing a week by hand: its own instructions
        ("settings", False, True, None),
        ("settings", False, False, None),  # a list of six rows: no tour
        ("tasks", False, False, "tasks"),
        ("focus", False, False, "focus"),
        ("community", False, False, "community"),
        ("progress", False, False, "progress"),
        ("courses", False, False, "courses"),
        ("you", False, False, "you"),
        ("cards", False, False, "cards"),
        ("tips", False, False, "tips"),
        (None, False, False, None),
    ],
)
def test_today_and_each_section_have_a_tour(plan, tab, setup, new, tour):
    assert service.tour_for(plan, tab, setup=setup, new=new) == tour
    if tour and tour != "today":  # seen with its line, the first visit
        assert service.tour_for(service.see_tour(plan, tab), tab) is None


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
        client.root = tmp_path
        yield client


def _start(web) -> str:
    """A plan as one made before D107 is: no first run, so Today's tour comes after
    setting up. A new plan's first run has Spark's line instead (test_first_run)."""
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]

    def before_d107(current: service.Subscription) -> service.Subscription:
        return replace(current, options={k: v for k, v in current.options.items() if k != "first_run"})

    service.update_subscription(web.root, token, before_d107)
    return token


def _steps(page: str) -> list[str]:
    tour = re.search(r'<div class="tour" id="tour" data-name="(\w+)".*?</ol>', page, re.S)
    assert tour, "no tour on the page"
    return re.findall(r'<li data-target="([^"]+)"', tour[0])


def test_every_step_of_todays_tour_lights_something(web):
    token = _start(web)
    page = _unversioned(web.get(f"/p/{token}").text)
    assert 'data-name="today"' in page
    assert '<script type="module" src="/static/tour.js"></script>' in page
    steps = _steps(page)
    assert len(steps) == 3
    marks = set(re.findall(r'data-tour="([\w-]+)"', page))
    for selector in steps:
        wanted = re.fullmatch(r"\[data-tour=([\w-]+)\]", selector)
        assert wanted and wanted[1] in marks, selector


def test_setting_up_has_no_tour_and_a_section_shows_its_tour_once(web):
    token = _start(web)
    for setup in (f"/p/{token}/week?new=1", f"/p/{token}/settings?new=1"):
        assert 'id="tour"' not in web.get(setup).text, setup
    # D152: no line above the page (the owner, 2026-11-19: it pushed every page down);
    # Spark's tour of the section (D141) says what it is for, the first time only.
    first = web.get(f"/p/{token}/tasks").text
    assert 'data-name="tasks"' in first and "section-hint" not in first
    again = web.get(f"/p/{token}/tasks").text
    assert 'id="tour"' not in again  # once, even if left half way
    calendar = web.get(f"/p/{token}/week").text
    assert "section-hint" not in calendar and 'data-name="calendar"' in calendar


def test_a_tour_finished_stays_finished_and_settings_show_it_again(web):
    token = _start(web)
    assert 'id="tour"' in web.get(f"/p/{token}").text
    done = web.post(f"/p/{token}/tour", data={"name": "today"})
    assert done.status_code == 204
    assert 'id="tour"' not in web.get(f"/p/{token}").text
    assert 'data-name="tasks"' in web.get(f"/p/{token}/tasks").text  # its own, once (D141)
    assert 'id="tour"' not in web.get(f"/p/{token}/tasks").text
    assert web.post(f"/p/{token}/tour", data={"name": "nothing"}).status_code == 400
    settings = web.get(f"/p/{token}/settings/look").text
    assert f'action="/p/{token}/tour/again"' in settings
    again = web.post(f"/p/{token}/tour/again", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == f"/p/{token}"
    assert 'data-name="today"' in web.get(f"/p/{token}").text


def test_a_tour_is_hidden_without_its_script():
    template = (
        Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "templates" / "_tour.html"
    ).read_text(encoding="utf-8")
    assert re.search(r'<div class="tour" id="tour"[^>]* hidden>', template)
    assert '<ol class="tour-steps" hidden>' in template
    assert "style=" not in template  # the page's Content-Security-Policy allows no inline style


def test_each_tour_is_short_and_lights_parts_the_pages_mark():
    """Today's three steps (D97); each section's two to four (D141). A step lights a part
    marked `data-tour` on some page, or one found by its class (a session on the
    calendar, a tip's label), passed over when it is not on screen."""
    root = Path(__file__).resolve().parents[1] / "src/cps/web/templates"
    template = (root / "_tour.html").read_text(encoding="utf-8")
    tours = dict(re.findall(r'\{% (?:el)?if tour == "(\w+)" %\}(.*?)(?=\{% (?:el|end)if)', template, re.S))
    assert set(tours) == {"today", *service.SECTION_TOURS}
    assert tours["today"].count("<li data-target=") == 3
    pages = [page.read_text(encoding="utf-8") for page in root.glob("*.html")]
    marks = {m for page in pages for m in re.findall(r'data-tour="([\w-]+)"', page)}
    for name, steps in tours.items():
        targets = re.findall(r'<li data-target="([^"]+)"', steps)
        assert 2 <= len(targets) <= 4, name
        for target in targets:
            wanted = re.fullmatch(r"\[data-tour=([\w-]+)\]", target)
            assert wanted is None or wanted[1] in marks, (name, target)


def test_the_calendar_offers_a_month_once_set_up(web):
    # D79: Day, 3 Days, Week and Month on the plan; drawing a week by hand keeps the
    # views with hours only (a month is no place to draw classes).
    token = _start(web)
    # D140: the calendar is its own page; Today has no calendar, so no views. Since D157
    # the views are one menu.
    assert '<option value="30">Month</option>' in web.get(f"/p/{token}/week").text
    assert 'id="calendar-view"' not in web.get(f"/p/{token}").text


def test_a_first_run_shows_the_tour_once_it_reaches_today_and_settings_bring_it_back(web):
    """D107 kept the tours away while a first run lasted, and its end marked Today's
    seen: a new student never had one (AUDIT 116). Since D170 there is none while its
    steps are ahead, Today's once it reaches Today or ends, and Settings show it again."""
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    assert 'id="tour"' not in web.get(f"/p/{token}").text  # its steps are ahead
    service.update_subscription(web.root, token, service.finish_first_run)
    assert 'id="tour"' in web.get(f"/p/{token}").text
    service.update_subscription(web.root, token, lambda current: service.see_tour(current, "today"))
    assert 'id="tour"' not in web.get(f"/p/{token}").text
    service.update_subscription(web.root, token, service.reset_tours)
    assert 'id="tour"' in web.get(f"/p/{token}").text
