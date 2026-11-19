"""
D159: the new-task sheet's course is a list of the plan's courses with "Other…" (the owner,
2026-11-19: '"other:" option please'), and its day and time are the app's own picker over
the form's field (the system's grey picker was "ugly"). What must hold: a course picked,
typed under "Other…", or none, reaches the task; "Other…" with nothing typed is no course;
the page carries the list and the picker's hooks, and works without its script.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app, forms

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)


def test_the_course_a_sheet_names():
    other = forms.OTHER_COURSE
    assert forms.chosen_course({"course": "Algebra 3", "course_other": ""}) == "Algebra 3"
    assert forms.chosen_course({"course": other, "course_other": " Econ 101 "}) == "Econ 101"
    assert forms.chosen_course({"course": other, "course_other": ""}) == ""  # "Other…", nothing typed
    assert forms.chosen_course({"course": "", "course_other": ""}) == ""
    # Typed last wins, as on the share forms (D141): without the script both show.
    assert forms.chosen_course({"course": "Algebra 3", "course_other": "Econ 101"}) == "Econ 101"
    assert forms.chosen_course({"course": "Econ 101"}) == "Econ 101"  # a plan without courses


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return sub


def test_the_sheet_lists_the_courses_with_other_and_a_task_takes_either(web, tmp_path):
    sub = _plan(tmp_path)
    page = unescape(web.get(f"/p/{sub.token}").text)
    sheet = page[page.index('id="quick-add"') :]
    sheet = sheet[: sheet.index("</form>")]
    options = re.findall(r'<option value="([^"]*)">([^<]*)</option>', sheet)
    assert options[0] == ("", "No course") and options[-1] == (forms.OTHER_COURSE, "Other…")
    assert ("Algebra 3", "Algebra 3") in options
    assert 'name="course_other"' in sheet and "datalist" not in sheet
    # The picker's hooks: its button and panel start hidden, for the script to show.
    assert re.search(r'class="btn btn-sm btn-icon due-open"[^>]*hidden', sheet)
    assert re.search(r'class="due-pick" id="qa-due-pick" hidden', sheet)
    assert 'type="datetime-local" required' in sheet  # the field the form sends, as before

    def add(name, **course):
        data = {"name": name, "due": "2026-10-20T23:59", "hours": "2", **course}
        assert web.post(f"/p/{sub.token}/tasks", data=data, follow_redirects=False).status_code == 303

    add("Problem sheet", course="Algebra 3")
    add("Econ presentation", course=forms.OTHER_COURSE, course_other="Econ 101")
    add("Read a paper", course=forms.OTHER_COURSE)
    plan = service.PlanReport.from_dict(service.load_subscription(tmp_path, sub.token).plan)
    courses = {t.name: t.course for t in plan.tasks}
    assert courses == {"Problem sheet": "Algebra 3", "Econ presentation": "Econ 101", "Read a paper": ""}
