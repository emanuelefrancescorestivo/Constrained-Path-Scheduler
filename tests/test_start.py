"""
Starting from zero (DECISIONS.md D32): a timetable imported from a file or a link,
or none at all, and the week drawn by hand. Classes drawn on the calendar are busy
times of the kind "class": each is its course, in the course's colour, offered on
the next page as an exam row with its name filled in.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app, forms

NOW = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)  # a Monday, 09:00 in Paris
CLASSES = [
    {"label": "Algebra", "kind": "class", "weekday": "Mon", "start": "09:00", "end": "11:00"},
    {"label": "Physics", "kind": "class", "weekday": "Tue", "start": "14:00", "end": "16:00"},
    {"label": "algebra", "kind": "class", "weekday": "Thu", "start": "10:00", "end": "12:00"},
    {"label": "Gym", "kind": "training", "weekday": "Wed", "start": "18:00", "end": "19:30"},
]


def _drawn() -> service.Subscription:
    blank = service.start_subscription(tz="Europe/Paris", draw=True, now=NOW)
    return service.revise_subscription(blank, busy_rows=CLASSES, now=NOW, strict=False)


def test_a_week_can_start_empty_but_only_on_purpose():
    blank = service.start_subscription(tz="Europe/Paris", draw=True, now=NOW)
    assert blank.ics_text is None and blank.source_url is None
    assert blank.busy_rows == () and blank.subjects == () and blank.plan is None
    with pytest.raises(service.InvalidInput, match="link or its file"):
        service.start_subscription(tz="Europe/Paris", now=NOW)
    view = service.calendar_view(blank, None, 7, NOW)
    assert view["drawn"] and view["activities"] == [] and view["kinds"][0]["id"] == "class"
    assert all(d["in_horizon"] for d in view["days"])  # any day of the week can be drawn on


def test_drawn_classes_are_courses_with_their_colours():
    drawn = _drawn()
    assert [r["kind"] for r in drawn.busy_rows] == ["class", "class", "class", "training"]
    # Each course once, as first written, whatever the case of a later class.
    assert service.drawn_courses(drawn) == ["Algebra", "Physics"]
    assert service.course_names(drawn) == ["Algebra", "Physics"]
    view = service.calendar_view(drawn, None, 7, NOW)
    colours = [(a["label"], a["color"]) for a in view["activities"]]
    assert colours[0][1] == colours[2][1] != colours[1][1]  # a course keeps one colour
    assert colours[3] == ("Gym", None)  # other busy times stay neutral
    assert [x["course"] for x in view["legend"]] == ["Algebra", "Physics"]
    values = service.setup_view(drawn)
    assert values["drawn"] and values["suggested"] == ["Algebra", "Physics"]
    assert values["courses"] == ["Algebra", "Physics"]


def test_an_offered_exam_row_without_a_date_is_left_out():
    drawn = _drawn()
    values = forms.settings_values(
        {
            "s0_name": "Algebra",
            "s0_exam": "2026-12-14T09:00",
            "s0_suggested": "1",
            "s0_familiarity": "3",
            "s1_name": "Physics",
            "s1_exam": "",
            "s1_suggested": "1",
            "s1_familiarity": "3",
        },
        [],
    )
    change = forms.revision(values)
    assert [s.name for s in change["subjects"]] == ["Algebra"]  # Physics has no exam yet
    planned = service.revise_subscription(drawn, subjects=change["subjects"], now=NOW)
    assert planned.plan is not None and planned.error is None
    sessions = service.PlanReport.from_dict(planned.plan).sessions
    assert sessions and {s.subject for s in sessions} == {"Algebra"}
    assert service.setup_view(planned)["suggested"] == ["Physics"]
    # A typed row that was not offered still needs its date.
    with pytest.raises(service.InvalidInput, match="give the exam's date"):
        forms.revision(forms.settings_values({"s0_name": "Chemistry", "s0_exam": ""}, []))


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.path = tmp_path
        yield client


def _inputs(html: str) -> dict[str, str]:
    found = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name and 'type="checkbox"' not in tag:
            found[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        found[name] = (chosen.group(1) or chosen.group(2)) if chosen else ""
    return found


def test_the_home_page_offers_a_file_a_link_or_a_week_to_draw(web):
    home = unescape(web.get("/").text)
    assert home.count('action="/start"') == 2
    assert 'name="draw" value="1"' in home and "No file? Draw your week" in home
    assert 'class="drop"' in home and 'type="file"' in home and 'name="link"' in home
    assert 'action="/demo"' not in home  # the demo's card is the public demo's only


def test_drawing_a_week_from_the_start_to_a_plan(web):
    started = web.post("/start", data={"draw": "1", "tz": "Europe/Paris"}, follow_redirects=False)
    assert started.status_code == 303 and started.headers["location"].endswith("/timetable")
    token = started.headers["location"].split("/")[2]
    page = unescape(web.get(f"/p/{token}/timetable").text)
    assert 'data-setup="classes"' in page and "Draw your week" in page
    assert 'id="panel"' not in page and "topbar-add" not in page  # the calendar alone
    assert f'href="/p/{token}/settings?new=1"' in page

    drawn = web.post(f"/p/{token}/activities?days=7", json=CLASSES)
    assert drawn.status_code == 200, drawn.text
    assert [a["color"] is not None for a in drawn.json()["activities"]] == [True, True, True, False]

    exams = web.get(f"/p/{token}/settings?new=1").text
    assert "Your exams and deadlines" in unescape(exams)
    form = _inputs(exams)
    offered = {
        form[k]: k.split("_")[0]
        for k in form
        if k.endswith("_name") and form.get(k.replace("name", "suggested")) == "1"
    }
    assert set(offered) == {"Algebra", "Physics"}
    form[f"{offered['Algebra']}_exam"] = "2026-12-14T09:00"
    form["new"] = "1"
    saved = web.post(f"/p/{token}/settings", data=form, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"].endswith("/week?new=1")
    stored = service.load_subscription(web.path, token)
    assert stored is not None and [s["name"] for s in stored.subjects] == ["Algebra"]
    assert stored.plan is not None and service.PlanReport.from_dict(stored.plan).sessions
    settings = unescape(web.get(f"/p/{token}/settings").text)
    assert "Drawn by you" in settings and f'href="/p/{token}/timetable"' in settings
    today = unescape(web.get(f"/p/{token}").text)
    assert "Algebra" in today


def test_a_mistake_on_the_exams_page_does_not_offer_a_course_twice(web):
    token = web.post("/start", data={"draw": "1"}, follow_redirects=False).headers["location"].split("/")[2]
    web.post(f"/p/{token}/activities?days=7", json=CLASSES[:2])
    form = _inputs(web.get(f"/p/{token}/settings?new=1").text)
    form["t0_name"] = "Report"  # a deadline without its date: refused
    answer = web.post(f"/p/{token}/settings", data={**form, "new": "1"})
    assert answer.status_code == 400
    assert unescape(answer.text).count('class="offered-name"') == 0
    assert len(re.findall(r'name="s\d+_suggested" value="1"', answer.text)) == 2


def test_classes_can_be_typed_without_scripts(web):
    token = web.post("/start", data={"draw": "1"}, follow_redirects=False).headers["location"].split("/")[2]
    page = web.get(f"/p/{token}/timetable").text
    assert "Type your classes instead" in unescape(page)
    form = _inputs(web.get(f"/p/{token}/settings?new=1").text)
    assert form["a0_kind"] == "class"  # a drawn week's typed rows are classes at first
    form.update({"a0_label": "Chemistry", "a0_weekday": "Fri", "a0_start": "08:00", "a0_end": "10:00"})
    form.update({"s0_name": "Chemistry", "s0_exam": "2026-12-18T09:00"})
    answer = web.post(f"/p/{token}/settings", data={**form, "new": "1"}, follow_redirects=False)
    assert answer.status_code == 303, re.findall(r'role="alert">([^<]*)', answer.text)
    values = _inputs(web.get(f"/p/{token}/settings").text)
    assert values["a0_label"] == "Chemistry" and values["a0_kind"] == "class"
