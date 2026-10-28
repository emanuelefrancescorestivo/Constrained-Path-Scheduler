"""
Lighter pages (DECISIONS.md D103, package C of the review of 2026-10-23). What must
hold: Progress shows the week, the streak, the exams and the climb, and folds the rest
under "More detail", which opens by itself for a link or a diary change into it, with
one "How we measure these" for every number; Settings is six rows, each its own page,
an exam changed on its course's page; a page saves only what it shows, so posting one
unchanged changes nothing; every link to a part of the old page leads to its page.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app
from cps.web import app as web_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def web(tmp_path):
    config = Config(
        store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _form(html: str) -> dict:
    """A form as a browser would submit it untouched (as in test_web)."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), []).append(unescape(value.group(1)))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def _part(html: str, marker: str) -> dict:
    found = [f for f in re.findall(r"<form\b.*?</form>", html, re.S) if marker in f]
    assert len(found) == 1, (marker, len(found))
    return _form(found[0])


def _set_up(client) -> str:
    """A plan through setting up, its exams' form saved as it came, then one deadline."""
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    token = (
        client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    form = {**_form(client.get(f"/p/{token}/settings?new=1").text), "new": "1"}
    form.update(t0_name="Report", t0_due="2026-10-20T18:00", t0_hours="4")
    assert client.post(f"/p/{token}/settings", data=form, follow_redirects=False).status_code == 303
    return token


def _kept(client, token: str) -> tuple:
    s = service.load_subscription(client.root, token)
    options = {k: v for k, v in s.options.items() if k not in ("visits", "last_visit", "tours_seen")}
    # Unset is the default the week's page shows, and saving it writes that down.
    options = {"commute_minutes": 0, "study_time": "any", "study_on_the_way": False, **options}
    return s.subjects, s.busy_rows, options


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


def test_progress_shows_the_week_the_exams_and_the_climb_and_folds_the_rest(web):
    token = _set_up(web)
    page = unescape(web.get(f"/p/{token}/progress").text)
    fold = page.index('<details class="more section progress-more" id="more">')
    above, below = page[:fold], page[fold:]
    for shown in ('class="card hero-ring"', 'class="card hero-streak"', 'id="exams"', "Your climb"):
        assert shown in above, shown
    for folded in ('id="memory"', 'id="insights"', 'id="diary"', 'id="badges"', 'id="how"'):
        assert folded in below and folded not in above, folded
    assert "Your memory · Your insights · Your diary · Badges" in page  # what the fold holds
    # One "How we measure these" instead of one on each card.
    assert "How it is measured" not in page and page.count('class="section how-all"') == 1
    how = page[page.index('id="how"') :]
    assert "Memory strength" in how and "Starting on time" in how


def test_more_detail_opens_for_a_link_into_it_and_after_a_diary_change(web):
    token = _set_up(web)
    assert 'id="more" open>' not in web.get(f"/p/{token}/progress").text
    assert 'id="more" open>' in web.get(f"/p/{token}/progress?more=1").text
    logged = web.post(
        f"/p/{token}/log",
        data={"course": "Algebra 3", "title": "Sheet", "effort": "5", "progress": "3", "minutes": "30"},
        follow_redirects=False,
    )
    assert logged.headers["location"] == f"/p/{token}/progress?saved=logged#diary"
    assert 'id="more" open>' in web.get(logged.headers["location"]).text
    # The links into it ask for it: Today's insight, the weekly review's.
    templates = ROOT / "src" / "cps" / "web" / "templates"
    assert "/progress?more=1#insights" in (templates / "_panel.html").read_text()
    assert "/progress?more=1#review" in (templates / "_review.html").read_text()
    assert 'id="review"' in (templates / "progress.html").read_text()  # its anchor exists


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_settings_is_six_rows_each_its_own_page_with_its_picture(web):
    token = _set_up(web)
    page = web.get(f"/p/{token}/settings").text
    rows = re.findall(
        r'<a href="/p/[^/]+/settings/([a-z]+)" data-anchors="[^"]*"><span class="art art-([a-z]+)"', page
    )
    assert [part for part, _ in rows] == list(web_app.SETTINGS_PARTS) and len(rows) == 6
    assert len({art for _, art in rows}) == 6
    assert "<form" not in page.split("<main")[1].split("</main>")[0]  # a list, nothing to fill
    for part, art in rows:
        own = web.get(f"/p/{token}/settings/{part}")
        assert own.status_code == 200 and f'class="art art-{art}"' in own.text, part
        assert f'<a href="/p/{token}/settings">' in own.text  # back to the list
    assert web.get(f"/p/{token}/settings/everything").status_code == 404
    # Each anchor of the old page leads to the page that holds it now (ui.js).
    anchors = dict(re.findall(r'settings/([a-z]+)" data-anchors="([^"]*)"', page))
    for part, names in anchors.items():
        body = web.get(f"/p/{token}/settings/{part}").text
        for name in names.split():
            # the topics' section is gone (Courses has them); a plan without an account has no login
            if name not in ("topics", "login"):
                assert f'id="{name}"' in body, (part, name)
    script = (ROOT / "src" / "cps" / "web" / "static" / "ui.js").read_text()
    assert ".settings-list a[data-anchors]" in script and "location.replace" in script


def test_no_link_or_redirect_leads_into_the_old_page(web):
    src = ROOT / "src" / "cps"
    for path in [
        *src.rglob("*.py"),
        *(src / "web" / "templates").glob("*.html"),
        *(src / "web" / "static").glob("*.js"),
    ]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"/settings(\?(?!new=1)[^\"'#]*)?#", text), path.name
        assert not re.search(r"/settings\?saved=", text), path.name


def test_each_page_saves_only_its_part_and_unchanged_changes_nothing(web):
    token = _set_up(web)
    before = _kept(web, token)
    week = _part(web.get(f"/p/{token}/settings/week").text, 'name="part" value="week"')
    assert web.post(f"/p/{token}/settings", data=week, follow_redirects=False).status_code == 303
    exams = web.get(f"/p/{token}/settings/exams").text
    deadlines = _part(exams, 'name="part" value="deadlines"')
    assert web.post(f"/p/{token}/settings", data=deadlines, follow_redirects=False).status_code == 303
    exam = _part(web.get(f"/p/{token}/courses/1").text, '/exam"')
    assert web.post(f"/p/{token}/courses/1/exam", data=exam, follow_redirects=False).status_code == 303
    assert _kept(web, token) == before


def test_a_change_on_one_page_leaves_the_others_as_they_were(web):
    token = _set_up(web)
    # An exam's weight and practice, on its course's page.
    exam = {
        **_part(web.get(f"/p/{token}/courses/0").text, '/exam"'),
        "s0_weight": "2.0",
        "s0_practice": "6.0",
    }
    name = exam["s0_name"]
    assert web.post(f"/p/{token}/courses/0/exam", data=exam, follow_redirects=False).status_code == 303
    subjects, _, options = _kept(web, token)
    assert options["preferences"]["weights"] == {name: 2.0} and options["preferences"]["practice"] == {
        name: 6.0
    }
    # The week: its hours and days off change, the exam's weight and practice do not.
    week = {
        **_part(web.get(f"/p/{token}/settings/week").text, 'value="week"'),
        "weekly_hours": "12",
        "rest": "Sun",
    }
    week.update(a0_label="Job", a0_weekday="Sat", a0_start="10:00", a0_end="14:00")
    assert web.post(f"/p/{token}/settings", data=week, follow_redirects=False).status_code == 303
    after = _kept(web, token)
    assert after[0] == subjects and after[2]["tasks"] == options["tasks"]
    assert after[2]["preferences"]["weekly_hours"] == 12.0 and after[2]["preferences"]["rest_days"] == ["Sun"]
    assert after[2]["preferences"]["weights"] == {name: 2.0} and after[2]["preferences"]["practice"] == {
        name: 6.0
    }
    assert [r["label"] for r in after[1]] == ["Job"]
    # The deadlines: a task added; the week and the exams stay.
    deadlines = {**_part(web.get(f"/p/{token}/settings/exams").text, 'value="deadlines"')}
    deadlines.update(t1_name="Essay", t1_due="2026-10-27T12:00", t1_hours="3")
    saved = web.post(f"/p/{token}/settings", data=deadlines, follow_redirects=False)
    assert saved.headers["location"] == f"/p/{token}/settings/exams?saved=1#deadlines"
    last = _kept(web, token)
    assert [t["name"] for t in last[2]["tasks"]] == ["Report", "Essay"]
    assert last[0] == subjects and last[1] == after[1] and last[2]["preferences"] == after[2]["preferences"]


def test_an_exam_added_on_exams_and_deadlines_joins_the_others(web):
    token = _set_up(web)
    count = len(_kept(web, token)[0])
    form = {**_part(web.get(f"/p/{token}/settings/exams").text, 'name="part" value="add"')}
    form.update(s0_name="Philosophy", s0_exam="2026-12-15T09:00")
    saved = web.post(f"/p/{token}/settings", data=form, follow_redirects=False)
    assert saved.headers["location"] == f"/p/{token}/settings/exams?saved=1"
    subjects = _kept(web, token)[0]
    assert len(subjects) == count + 1 and subjects[-1]["name"] == "Philosophy"
    page = unescape(web.get(f"/p/{token}/settings/exams").text)
    assert f'href="/p/{token}/courses/{count}#exam"' in page and "Philosophy" in page
    course = web.get(f"/p/{token}/courses/{count}").text
    assert 'id="exam"' in course and 'value="2026-12-15T09:00"' in course
