"""
A course with no exam in the timetable (DECISIONS.md D185), such as one assessed as it
goes, is offered when setting up and in Settings: its name, and an exam date to give.
Found on the first-year timetable (D182): Math in Practice has sixteen classes and no
exam in it, so it was never offered and never planned.

What must hold: the offer lists the timetable's courses with no exam found and not
planned, only those; a date given plans the course; left empty, nothing changes.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


def test_the_courses_without_an_exam_are_offered_and_only_they():
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 1", None, familiarity=2)],
        start=date(2026, 9, 7),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-year1.ics").read_bytes(),
        now=NOW,
    )
    values = service.setup_view(sub)
    assert values["no_exam"] == ["Math in Practice"]
    # The six other exams of the timetable are offered as exams, not here.
    assert {f["subject"] for f in values["unplanned_exams"]} == {
        "Analysis 1",
        "Computer Programming 1",
        "Economics 1",
        "Introduction to AI",
        "Probability Theory",
        "Scientific English 1",
    }


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _form(html: str) -> dict:
    """The setting-up form as a browser sends it untouched: inputs with their values,
    checkboxes as ticked, selects as selected."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag or 'type="radio"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), unescape(value.group(1)) if value else "on")
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def test_setting_up_offers_math_in_practice_and_a_date_plans_it(web):
    files = {"file": ("t.ics", (EXAMPLES / "sample-year1.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    page = unescape(web.get(f"/p/{token}/settings?new=1").text)
    assert "No exam in your timetable for: Math in Practice" in page
    form = _form(page)
    row = next(k[: -len("_name")] for k, v in form.items() if k.endswith("_name") and v == "Math in Practice")
    assert form[f"{row}_suggested"] == "1" and form[f"{row}_exam"] == ""
    # Left empty: the plan is made without it.
    assert web.post(f"/p/{token}/settings", data=form, follow_redirects=False).status_code == 303
    names = [s["name"] for s in service.load_subscription(web.root, token).subjects]
    assert "Math in Practice" not in names and names
    # In Settings, its last test's date given: it is planned, and offered no more.
    page = unescape(web.get(f"/p/{token}/settings/exams").text)
    assert "No exam in your timetable for: Math in Practice" in page
    added = {"part": "add", "s0_found": "", "s0_name": "Math in Practice", "s0_familiarity": "3"}
    added["s0_exam"] = "2027-01-12T10:00"
    assert web.post(f"/p/{token}/settings", data=added, follow_redirects=False).status_code == 303
    names = [s["name"] for s in service.load_subscription(web.root, token).subjects]
    assert "Math in Practice" in names
    assert "No exam in your timetable for" not in unescape(web.get(f"/p/{token}/settings/exams").text)
