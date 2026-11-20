"""
Today as one thing (DECISIONS.md D171), the owner's choice A of three layouts: the next step
as one band across the page with one button, the day as one strip of time under it, then the
widgets; and what does not fit in the plan as one card, said once and in the right number
(AUDIT 117). What must hold: the strip places every item inside it, in time order, overlapping
items in lanes, now where it is; Today has one way to start a session where it had three; and a
French plan no longer lists, a second time, the courses its exams already show short.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import service
from cps.i18n import use

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)  # 11:00 in Paris, a Tuesday with two classes
LATER = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)  # the next day: no first day's offer


def _plan(lang: str | None = None, *, now: datetime = MORNING) -> service.Subscription:
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=now, lang=lang)


def test_the_day_strip_places_each_item_where_it_falls():
    sub = _plan()
    strip = service.day_strip(sub, MORNING)
    agenda = service.day_agenda(sub, MORNING)
    assert [b["start"] for b in strip["blocks"]] == [a["start"] for a in agenda] and agenda
    for block in strip["blocks"]:
        assert block["x"] >= 0 and block["w"] >= 1 and block["x"] + block["w"] <= 100
        assert 0 <= block["lane"] < service.DAY_STRIP_LANES
    xs = [b["x"] for b in strip["blocks"]]
    assert xs == sorted(xs)
    # The hours run from before getting up to after going to bed, labelled at their place.
    hours = strip["hours"]
    assert hours[0]["x"] == 0 and hours[-1]["x"] == 100
    assert [h["x"] for h in hours] == sorted(h["x"] for h in hours)
    # 11:00 in Paris lies between the strip's first and last hours.
    assert strip["now"] is not None and 0 < strip["now"] < 100
    first = int(hours[0]["label"])
    last = int(hours[-1]["label"])
    assert strip["now"] == round(100 * (11 * 60 - first * 60) / ((last - first) * 60))


def test_items_that_overlap_take_the_next_lane():
    sub = _plan()
    agenda = service.day_agenda(sub, MORNING)
    clash = {**agenda[0], "label": "Overlap", "kind": "typed"}
    real = service.day_agenda

    def doubled(subscription, now=None):
        return sorted([*real(subscription, now), clash], key=lambda i: (i["start"], i["end"]))

    service.day_agenda = doubled
    try:
        strip = service.day_strip(sub, MORNING)
    finally:
        service.day_agenda = real
    assert strip["lanes"] == 2
    same = [b for b in strip["blocks"] if b["start"] == clash["start"]]
    assert sorted(b["lane"] for b in same) == [0, 1]


def test_today_is_one_band_one_button_and_the_day_across(tmp_path):
    sub = _plan(now=MORNING)
    service.save_subscription(tmp_path, sub)
    web = TestClient(create_app(Config(store=tmp_path, clock=lambda: LATER)))
    page = unescape(web.get(f"/p/{sub.token}").text)
    # One way to start a session on Today, where it had three (the owner's screenshot).
    assert len(re.findall(r'action="/p/[^"]+/focus/start"', page)) == 1
    band = re.search(r'<section class="w w-next w-wide nx[^"]*"[^>]*>.*?</section>', page, re.S)
    assert band is not None and 'class="btn btn-primary nx-go"' in band.group(0)
    assert "Study something else" in band.group(0) and "How, and why now" in band.group(0)
    # The day across the page, each item placed by class (no style attribute: the CSP).
    day = re.search(r'<section class="w w-day w-wide".*?</section>', page, re.S).group(0)
    assert re.search(r'class="dl-item [^"]* dl-x\d+ dl-w\d+ dl-l\d"', day)
    assert 'class="dl-hours"' in day and "style=" not in day
    # Then exams, tasks and the mind; Focus is no longer among the five to start.
    assert re.findall(r'<section class="w w-(\w+)', page)[-3:] == ["exams", "tasks", "mind"]


def test_the_css_places_every_percent():
    css = (ROOT / "src/cps/web/static/style.css").read_text()
    assert all(f".dl-x{i}{{left:{i}%}}" in css for i in range(101))
    assert all(f".dl-w{i}{{width:{i}%}}" in css for i in range(1, 101))


def test_a_french_plan_says_what_does_not_fit_once_and_in_the_right_number():
    """AUDIT 117: French puts a space before the colon, so the filter that keeps a course
    short of its target off Today's list missed every one; "1 sujets", "créneau(x)"."""
    with use("fr"):
        sub = service.revise_subscription(_plan("fr"), preferences={"weekly_hours": 2}, now=MORNING)
        plan = service.PlanReport.from_dict(sub.plan)
        short = [w for w in plan.warnings if "prévu" in w]
        assert short, plan.warnings  # two hours a week leave topics short
        assert all(" : " in w for w in short)
        assert not any(re.search(r"\b1 sujets\b|\(x\)|\(s\)", w) for w in plan.warnings)
        shown = service.today_view(sub, MORNING)["warnings"]
    assert not any("prévu" in w for w in shown)


def test_one_topic_short_is_said_in_the_singular():
    with use("fr"):
        one = service._n(
            "{name}: {below} of {total} topics is predicted below {target} at the exam. "
            "More hours a week, or fewer days off, would change that.",
            "{name}: {below} of {total} topics are predicted below {target} at the exam. "
            "More hours a week, or fewer days off, would change that.",
            1,
            name="Algèbre",
            below=1,
            total=5,
            target="90%",
        )
    assert one.startswith("Algèbre : 1 sujet sur 5 est prévu en dessous de 90%")


def test_what_does_not_fit_is_one_card_with_its_lines_folded(tmp_path):
    sub = service.revise_subscription(_plan(), preferences={"weekly_hours": 2}, now=MORNING)
    shown = service.today_view(sub, MORNING)["warnings"]
    service.save_subscription(tmp_path, sub)
    web = TestClient(create_app(Config(store=tmp_path, clock=lambda: LATER)))
    page = unescape(web.get(f"/p/{sub.token}").text)
    assert 'class="notice section w-wide"' not in page
    # Two hours a week leave exam practice out of every course: six lines, one card.
    assert len(shown) == 6 and "1 block of exam practice" in shown[0]
    card = re.search(r'<details class="card plan-notes section w-wide">.*?</details>', page, re.S).group(0)
    assert card.count("<li>") == len(shown) and "/settings/week" in card
    assert "6 things do not fit in the plan" in card
