"""
D167: lighter pages (the owner, 2026-11-20, on the course page: "heavy heavy heavy to watch
and scroll through"; on the diary's older weeks: "what is that list crap"). What must hold: a
course's weeks are one row each, this week open; its exam shows its date, the rest folded
and saved as each field changes, stopping its own button; the credits, once known, folded;
adding a link one row; and every form still works without a script.
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
NOW = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _plan(root: Path) -> service.Subscription:
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
    service.save_subscription(root, sub)
    return sub


def test_the_course_page_folds_what_is_not_needed_at_a_glance(web, tmp_path):
    sub = _plan(tmp_path)
    base = f"/p/{sub.token}"
    page = unescape(web.get(f"{base}/courses/0").text)

    weeks = re.findall(r'<details class="week-fold"( open)?>', page)
    assert len(weeks) >= 3 and weeks.count(" open") == 1  # this week, and only it, open
    assert page.index('class="week-now"') < page.index('<details class="week-fold" open>')

    exam = page[page.index('id="exam"') : page.index('id="credits"')]
    assert '<details class="exam-change">' in exam  # folded until wanted
    autosaved = re.search(r'<form method="post" action="[^"]*/exam" data-autosave>.*?</form>', exam, re.S)
    assert autosaved and "data-saved" in autosaved.group(0)
    assert "<noscript>" in autosaved.group(0) and "Save and plan" in autosaved.group(0)
    assert "s0_remove" not in autosaved.group(0)  # never a switch a stray tap saves
    stop_form = r'<form method="post" action="[^"]*/exam\?stop=1" class="exam-stop">.*?</form>'
    stop = re.search(stop_form, exam, re.S)
    assert stop and 'name="s0_remove" value="1"' in stop.group(0)

    credits = r'<form method="post" action="[^"]*/credits" class="inline-form credits-form" data-autosave>'
    assert re.search(credits, page)
    assert '<details class="card section link-add" id="link">' in page

    # Without a script, the same forms save as before.
    stop_fields = dict(re.findall(r'<input type="hidden" name="(s0_\w+)" value="([^"]*)">', stop.group(0)))
    fields = {k: v for k, v in stop_fields.items() if k != "s0_remove"} | {"s0_familiarity": "2"}
    answer = web.post(f"{base}/courses/0/exam", data=fields, follow_redirects=False)
    assert answer.headers["location"] == f"{base}/courses/0?saved=exam#exam"
    answer = web.post(f"{base}/courses/0/credits", data={"credits": "6"}, follow_redirects=False)
    assert answer.status_code == 303
    page = unescape(web.get(f"{base}/courses/0").text)
    assert '<details class="credits-change"><summary>ECTS credits: 6</summary>' in page

    # Stopping is its own button: the course leaves the plan.
    answer = web.post(f"{base}/courses/0/exam?stop=1", data=stop_fields, follow_redirects=False)
    assert answer.headers["location"] == f"{base}/settings/exams?saved=removed"
