"""
D169: Focus as one sentence (the owner's B, 2026-11-20: "i like B"). What must hold: the
page starts a session with one form, a sentence whose course and length are words to tap
(the plan's session first, the other courses, "Other…" with a field), one button; what
staying earns for each length; today's minutes and the streak; the tip as one row with its
research in place. Choosing another course starts a session of its own, not the plan's.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


@pytest.fixture
def plan(tmp_path):
    sub = service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 3", date(2027, 1, 26), familiarity=3),
            service.SubjectSpec("Analysis 3", date(2027, 1, 28), familiarity=2),
        ],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(tmp_path, sub)
    return sub


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_rewards_ahead_are_the_first_two_and_the_length():
    assert [a["label"] for a in service.focus_ahead(90)] == ["30 min", "1 h", "1 h 30"]
    assert [a["goal"] for a in service.focus_ahead(90)] == [False, False, True]
    assert [a["minutes"] for a in service.focus_ahead(180)] == [30, 60, 180]
    assert [a["minutes"] for a in service.focus_ahead(25)] == [25]
    assert [a["minutes"] for a in service.focus_ahead(0)] == [30, 60, 105]  # no length: the ladder


def test_the_page_is_one_sentence_one_button_and_what_staying_earns(web, plan):
    view = service.focus_view(plan, NOW)
    suggested = view["suggested"]
    assert suggested and view["verb"].endswith("{course} for {length}.")
    page = unescape(web.get(f"/p/{plan.token}/focus").text)
    assert page.count('action="/p/' + plan.token + '/focus/start"') == 1  # one form starts a session
    assert page.count('<details class="pick" name="focus">') == 2  # the course and the length
    assert f'name="course_pick" value="plan" checked><span>{suggested["course"]}</span>' in page
    assert 'name="course_pick" value="__other__"' in page and 'name="course_other"' in page
    assert f'name="goal" value="{suggested["goal"]}" checked' in page and "Let's go" in page
    assert page.count('<ol class="focus-ahead') == len(view["goals"])
    assert f'<ol class="focus-ahead g{suggested["goal"]} is-default">' in page
    assert "What you earn by staying" in page and "first session of the day" in page
    assert page.count("data-why=") == 1 and '<details class="card why-row section"' in page


def test_another_course_is_a_session_of_its_own(web, plan, tmp_path):
    base = f"/p/{plan.token}"
    sid = service.focus_view(plan, NOW)["suggested"]["id"]
    web.post(f"{base}/focus/start", data={"sid": sid, "course_pick": "plan", "goal": "45"})
    running = service.load_subscription(tmp_path, plan.token).options["focus"]
    assert running["sid"] == sid
    web.post(f"{base}/focus/cancel")
    web.post(f"{base}/focus/start", data={"sid": sid, "course_pick": "Analysis 3", "goal": "45"})
    running = service.load_subscription(tmp_path, plan.token).options["focus"]
    assert running["sid"] == "" and running["course"] == "Analysis 3"
    web.post(f"{base}/focus/cancel")
    web.post(
        f"{base}/focus/start",
        data={"sid": sid, "course_pick": "__other__", "course_other": "Reading", "goal": "0"},
    )
    running = service.load_subscription(tmp_path, plan.token).options["focus"]
    assert running["sid"] == "" and running["course"] == "Reading" and running["goal"] == 0
