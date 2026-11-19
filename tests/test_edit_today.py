"""
D155: Edit Today (the owner's choice 2-B, 2026-11-19, after "this page is very overwhelming
i do not feel in control"). What must hold: a plan's Today starts with five widgets (Up
next, Today, Focus, Tasks, Exams); each of the twelve is turned on or off on its own page,
in the page's order; a name Today does not draw is refused; the five come back in a tap;
and the tour shows where Today is edited.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(n, None, familiarity=3) for n in ("Algebra 3", "Advanced Statistics")],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-20 18:00", 6, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _widgets(page: str) -> list[str]:
    return re.findall(r'<section class="w w-(\w+)', page)


def test_today_starts_with_five_widgets_and_says_where_to_choose(web, tmp_path):
    sub = _plan(tmp_path)
    assert service.today_widgets(sub) == ("next", "day", "focus", "tasks", "exams")
    today = web.get(f"/p/{sub.token}").text
    shown = _widgets(today)
    assert shown[:2] == ["next", "focus"] and {"day", "tasks", "exams"} <= set(shown)
    assert not {"mind", "time", "tips", "work", "week", "cards", "friends"} & set(shown)
    assert f'href="/p/{sub.token}/today/edit" data-tour="edit-today"' in today
    # Today's tour points at it (tour.js passes over a step whose part is hidden).
    assert 'data-target="[data-tour=edit-today]"' in today


def test_each_widget_is_turned_on_or_off_and_the_five_come_back(web, tmp_path):
    sub = _plan(tmp_path)
    page = unescape(web.get(f"/p/{sub.token}/today/edit").text)
    boxes = re.findall(r'<input type="checkbox" id="w-(\w+)" name="widget" value="\w+"( checked)?>', page)
    assert [k for k, _on in boxes] == [k for k, _n, _l in service.TODAY_WIDGETS]
    assert [k for k, on in boxes if on] == list(service.TODAY_START)
    assert "What needs an answer from you, like how a session went, always shows." in page
    # The week and the tips on, Focus off: Today follows, in its own order.
    saved = web.post(
        f"/p/{sub.token}/today/edit",
        data={"widget": ["tips", "next", "week", "day", "tasks", "exams"]},
        follow_redirects=False,
    )
    assert saved.status_code == 303 and saved.headers["location"] == f"/p/{sub.token}?saved=today"
    stored = service.load_subscription(tmp_path, sub.token)
    assert service.today_widgets(stored) == ("next", "day", "tasks", "exams", "tips", "week")
    today = web.get(f"/p/{sub.token}?saved=today").text
    assert "Today shows what you chose." in unescape(today)
    shown = _widgets(today)
    assert "week" in shown and "tips" in shown and "focus" not in shown
    # Off, Focus leaves the way to the focus page under the next step.
    assert "Study something else" in today
    # A name Today does not draw is refused, and nothing changes.
    wrong = web.post(f"/p/{sub.token}/today/edit", data={"widget": ["next", "weather"]})
    assert wrong.status_code == 400 and "choose among Today" in unescape(wrong.text)
    assert service.today_widgets(service.load_subscription(tmp_path, sub.token))[-1] == "week"
    # Nothing on is a choice too; the five come back in a tap.
    web.post(f"/p/{sub.token}/today/edit", data={})
    assert service.today_widgets(service.load_subscription(tmp_path, sub.token)) == ()
    back = re.search(
        r'<form method="post" action="[^"]+/today/edit" class="row-end">(.*?)</form>', page, re.S
    )
    keys = re.findall(r'name="widget" value="(\w+)"', back.group(1))
    web.post(f"/p/{sub.token}/today/edit", data={"widget": keys})
    assert service.today_widgets(service.load_subscription(tmp_path, sub.token)) == service.TODAY_START
