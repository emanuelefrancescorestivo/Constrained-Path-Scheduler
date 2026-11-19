"""
D135: the study tips beside what a page is about, not only on their own page. What
must hold: each page that shows one (Focus, a self-test's cards, Flashcards, cards from
notes, the calendar, the week's settings, a course, Tasks, Progress) has one, with why
here, how strong the evidence is, what it does not show and its checked sources; a line
about the plan says only what the plan holds (a self-test about to start, an exam
close, a course's next days); the tips turn day by day; with tips off, none shows.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service, tips

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)  # 10:00 in Paris, a Tuesday


def _plan(where: Path, now: datetime = NOW) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=now,
    )
    service.save_subscription(where, service.finish_first_run(sub))
    return service.load_subscription(where, sub.token)


def test_every_place_has_a_tip_with_its_evidence(tmp_path):
    sub = _plan(tmp_path)
    checked = {key for key, s in tips.SOURCES.items() if s.checked}
    for place in service.TIP_PLACES:
        t = service.tip_here(sub, place, NOW, course="Algebra 3")
        assert t is not None, place
        assert t["here"] and t["advice"] and t["why"] and t["limits"], place
        assert t["strength_label"] in {label for label, _m in tips.STRENGTHS.values()}
        assert t["sources"] and all(s["url"].startswith("https://") for s in t["sources"])
        tip = next(x for x in tips.TIPS if x.key == t["key"])
        assert set(tip.sources) <= checked


def test_with_tips_off_no_page_shows_one(tmp_path):
    sub = service.set_tips(_plan(tmp_path), "off")
    assert all(service.tip_here(sub, place, NOW, course="Algebra 3") is None for place in service.TIP_PLACES)


def test_the_tips_of_a_page_turn_day_by_day(tmp_path):
    sub = _plan(tmp_path)
    seen = {service.tip_here(sub, "cards", NOW + timedelta(days=d))["key"] for d in range(3)}
    assert seen == {"spacing", "self-test", "spacing-cells"}


def test_a_self_test_about_to_start_comes_first_on_focus(tmp_path):
    sub = _plan(tmp_path)
    for day in range(3):
        t = service.tip_here(sub, "focus", NOW + timedelta(days=day), suggested={"kind": "review"})
        assert t["key"] == "recall-strengthens" and "self-test" in t["here"]
    other = {
        service.tip_here(sub, "focus", NOW + timedelta(days=d), suggested={"kind": "first review"})["key"]
        for d in range(4)
    }
    assert other == {"recall-strengthens"}  # a first review is a self-test too (D63)
    lecture = {
        service.tip_here(sub, "focus", NOW + timedelta(days=d), suggested=None)["key"] for d in range(3)
    }
    assert "recall-strengthens" not in lecture


def test_a_course_close_to_its_exam_speaks_of_sleep_with_its_day(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    exam = datetime.fromisoformat(next(s.exam for s in plan.subjects if s.name == "Algebra 3"))
    far = service.tip_here(sub, "course", exam - timedelta(days=40), course="Algebra 3")
    assert far["key"] != "sleep-regular"
    near = service.tip_here(sub, "course", exam - timedelta(days=10), course="Algebra 3")
    assert near["key"] == "sleep-regular" and "Algebra 3" in near["here"]


def test_the_calendar_names_the_days_a_course_comes_back(tmp_path):
    sub = _plan(tmp_path)
    local = NOW.astimezone(service._zone("Europe/Paris"))
    options = service._tip_options(sub, "calendar", local)
    spacing = next(here for key, here in options if key == "spacing")
    plan = service.PlanReport.from_dict(sub.plan)
    course = re.match(r"Your plan comes back to (.+) on ", spacing)[1]
    assert course in {s.subject for s in plan.sessions}
    days = {
        datetime.fromisoformat(s.start).astimezone(local.tzinfo).date()
        for s in plan.sessions
        if s.subject == course and datetime.fromisoformat(s.start) > NOW
    }
    named = re.findall(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ \w+", spacing)
    assert len(named) == 3 and all(service.format_date(d, "short") in named for d in sorted(days)[:3])


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


PAGES = (
    "/focus",
    "/cards",
    "/cards/review",
    "/cards/make",
    "/settings/week",
    "/courses/0",
    "/tasks",
    "/progress",
)


def test_each_page_shows_its_tip_with_the_research_folded(web, tmp_path):
    token = _plan(tmp_path).token
    for path in PAGES:
        page = unescape(web.get(f"/p/{token}{path}").text)
        assert page.count("data-why=") == 1, path
        assert "What it does not show" in page and 'rel="noopener noreferrer"' in page, path
        assert f"/p/{token}/settings/look#tips" in page, path
    # The calendar has no tip line: it pushed the grid down (the owner, 2026-11-19).
    calendar = unescape(web.get(f"/p/{token}/week").text)
    assert '<details class="why-line"' not in calendar and "data-why=" not in calendar
    # D140: the calendar is its own page; Today has no grid, so no planning tip.
    today = unescape(web.get(f"/p/{token}").text)
    assert '<section class="board"' not in today and '<details class="why-line"' not in today


def test_with_tips_off_the_pages_show_none(web, tmp_path):
    token = _plan(tmp_path).token
    web.post(f"/p/{token}/tip-setting", data={"tips": "off"})
    for path in PAGES:
        assert "data-why=" not in web.get(f"/p/{token}{path}").text, path
