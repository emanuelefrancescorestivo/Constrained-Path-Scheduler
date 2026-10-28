"""
A lighter app before the pilot (DECISIONS.md D97, D98). What must hold: five sections
in the tab bar and nothing floating over a page; setting up shows one way forward and
nothing else, in three screens; Today shows one message at a time, in a fixed order;
what setting up no longer asks comes back on Today, one question a day from the day
after the plan was made, each with "Not now".
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(MORNING)


@pytest.fixture
def web(tmp_path, clock):
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _start(client) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def _tabs(page: str) -> list[str]:
    bar = re.search(r'<nav class="tabbar".*?</nav>', page, re.S)
    assert bar, "no tab bar"
    return [unescape(t).strip() for t in re.findall(r"</svg>([^<]+)</a>", bar[0])]


def test_five_sections_in_the_tab_bar_and_four_in_the_top_bar(web):
    token = _start(web)
    today = web.get(f"/p/{token}").text
    # D102: the feed first, where the app opens for a student who has joined, then the rest.
    assert _tabs(today) == ["Community", "Today", "Calendar", "Courses", "You"]
    nav = re.search(r'<nav class="nav".*?</nav>', today, re.S)[0]
    assert re.findall(r">([^<]+)</a>", nav) == ["Community", "Plan", "Courses", "You"]
    # No gear, no "Connect calendar", no logo menu: all of them are under You.
    header = today[today.index('<header class="topbar">') : today.index("</header>")]
    assert f'href="/p/{token}/settings"' not in header and "brand-menu" not in header
    assert f'href="/p/{token}/feed"' not in header


@pytest.mark.parametrize(
    ("path", "current"),
    [("", "Today"), ("/week", "Calendar"), ("/progress", "You"), ("/cards", "You"), ("/focus", "Today")],
)
def test_each_page_is_in_one_section(web, path, current):
    token = _start(web)
    page = web.get(f"/p/{token}{path}").text
    bar = re.search(r'<nav class="tabbar".*?</nav>', page, re.S)[0]
    assert re.findall(r'aria-current="page"[^>]*>\s*<svg.*?</svg>([^<]+)</a>', bar, re.S) == [current]


def test_you_holds_everything_else(web):
    token = _start(web)
    you = web.get(f"/p/{token}/you").text
    for place in ("progress", "cards", "tasks", "focus", "tips", "science", "settings", "feed", "feedback"):
        assert f'href="/p/{token}/{place}' in you, place


@pytest.mark.parametrize("path", ["/settings?new=1", "/week?new=1", "/timetable"])
def test_setting_up_shows_one_way_forward_and_nothing_else(web, path):
    token = _start(web)
    page = web.get(f"/p/{token}{path}").text
    assert 'class="tabbar"' not in page and '<nav class="nav"' not in page
    assert 'popovertarget="quick-add"' not in page.split("</header>")[0]
    assert "spark-top" not in page and 'id="spark-help"' not in page
    assert '<span class="brand">' in page  # the logo leads nowhere while setting up
    assert "usage-ask" not in page and "fit-ask" not in page  # no message beside the week


def test_today_shows_one_message_in_a_fixed_order():
    assert service.today_message(exam=[1], usage=True, tip=True) == "exam"
    assert service.today_message(semester=True, usage=True) == "semester"
    assert service.today_message(usage=True, fit={"key": "week"}, insight={}) == "usage"
    assert service.today_message(fit={"key": "week"}, insight={"says": "x"}, tip=True) == "fit"
    assert service.today_message(insight={"says": "x"}, tip=True) == "insight"
    assert service.today_message(tip=True) == "tip"
    assert service.today_message(exam=[], tip=False) is None
    with pytest.raises(ValueError):
        service.today_message(weather=True)


def test_what_setting_up_no_longer_asks_comes_one_question_a_day(web, clock):
    token = _start(web)
    web.post(f"/p/{token}/usage", data={"yes": "0"})  # the first message out of the way
    assert "fit-ask" not in web.get(f"/p/{token}").text  # not on the day the plan was made
    clock.now = MORNING + timedelta(days=1)
    today = unescape(web.get(f"/p/{token}").text)
    assert today.count("fit-ask") == 1 and "Make the plan fit your week" in today
    later = web.post(f"/p/{token}/fit", data={"key": "week", "go": "0"}, follow_redirects=False)
    assert later.headers["location"] == f"/p/{token}"
    assert "fit-ask" not in web.get(f"/p/{token}").text  # one a day
    clock.now = MORNING + timedelta(days=2)
    assert "Bring in your deadlines" in unescape(web.get(f"/p/{token}").text)
    taken = web.post(f"/p/{token}/fit", data={"key": "deadlines", "go": "1"}, follow_redirects=False)
    assert taken.headers["location"] == f"/p/{token}/settings#deadlines"
    clock.now = MORNING + timedelta(days=3)
    assert "Your sessions in your phone's calendar" in unescape(web.get(f"/p/{token}").text)
    web.post(f"/p/{token}/fit", data={"key": "calendar", "go": "1"})
    clock.now = MORNING + timedelta(days=4)
    assert "fit-ask" not in web.get(f"/p/{token}").text  # all settled
    assert web.post(f"/p/{token}/fit", data={"key": "weather", "go": "1"}).status_code == 400
    kinds = [e["detail"] for e in service.events(web.root, token) if e["kind"] == "fit"]
    assert kinds == ["week later", "deadlines go", "calendar go"]


def test_a_question_already_settled_is_not_asked(web):
    token = _start(web)

    def settle(plan: service.Subscription) -> service.Subscription:
        linked = replace(plan, options={**plan.options, "deadlines_url": "https://moodle.example/x"})
        return service.answer_fit(linked, "week", True, MORNING)

    service.update_subscription(web.root, token, settle)
    plan = service.load_subscription(web.root, token)
    assert service.fit_question(plan, MORNING) is None  # the day it was made
    assert service.fit_question(plan, MORNING + timedelta(days=1))["key"] == "calendar"


def test_the_tab_bar_shares_its_width_evenly_among_the_sections():
    # Found by the owner on 2026-10-23 (AUDIT 78): six fixed columns for five sections
    # left an empty sixth at the right. As many columns as there are links.
    css = (Path(__file__).resolve().parent.parent / "src/cps/web/static/style.css").read_text()
    rule = re.search(r"\n  \.tabbar \{(.*?)\n  \}", css, re.S)[1]
    assert "grid-auto-flow: column" in rule and "grid-auto-columns: minmax(0, 1fr)" in rule
    assert "grid-template-columns" not in rule
