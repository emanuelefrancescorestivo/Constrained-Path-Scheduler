"""
After an exam, the forecast beside how it went (DECISIONS.md D105). What must hold:
each exam answered about is listed with the recall the plan predicted for it when the
answer came, rounded to 5 as every forecast, highest first; two exams are compared
only when their results and their forecasts differ, "Did not sit it" never; Today
shows a new answer for a day, Progress and the course's page keep it. And a course
taken again is asked about again, its first answer kept (AUDIT item 84).
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
# The sample semester's first two exams: Computer Programming 3 on 25 January and
# Algebra 3 on 26 January 2027.
AFTER = datetime(2027, 1, 27, 7, 0, tzinfo=UTC)  # 08:00 in Paris, before the third exam at 09:00


@pytest.fixture(scope="module")
def plan_then():
    first = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    return service.refresh_subscription(first, now=AFTER, reread=False)


def _with(subscription: service.Subscription, answers: dict) -> service.Subscription:
    return replace(subscription, options={**subscription.options, "exam_results": answers})


def _answer(course: str, result: str, predicted: float | None, at: datetime = AFTER) -> dict:
    stamp = at.isoformat(timespec="seconds")
    return {"course": course, "exam": None, "result": result, "mark": "", "predicted": predicted, "at": stamp}


def test_each_exam_beside_its_forecast_and_the_pairs_that_agree(plan_then):
    answered = service.answer_exam(plan_then, "Algebra 3", {"result": "well", "mark": "28/30"}, AFTER)
    (row,) = service.exam_outcomes(answered, AFTER)["exams"]
    (record,) = answered.options["exam_results"].values()
    predicted = record["predicted"]
    assert row["course"] == "Algebra 3" and row["says"] == "Passed, and well" and row["mark"] == "28/30"
    assert row["predicted"] == 5 * round(100 * predicted / 5) and row["when"]
    # Six answers: the forecasts shown are 95, 90, 70, 70, 60 and 50 %.
    answers = {
        "a": _answer("A", "well", 0.9),
        "b": _answer("B", "passed", 0.71),
        "c": _answer("C", "failed", 0.5),
        "d": _answer("D", "just", 0.69, AFTER + timedelta(minutes=1)),
        "e": _answer("E", "failed", 0.95),
        "f": _answer("F", "absent", 0.6),
        "g": {"skipped": True, "at": AFTER.isoformat(timespec="seconds")},
        "h": _answer("H", "passed", None),
    }
    view = service.exam_outcomes(_with(plan_then, answers), AFTER)
    # "Rather not say" is not listed.
    assert [r["course"] for r in view["exams"]] == ["E", "A", "B", "D", "F", "C", "H"]
    assert [r["predicted"] for r in view["exams"]] == [95, 90, 70, 70, 60, 50, None]
    # Compared: A>B, A>C, A>D, B>C, D>C agree; E against A, B and D does not; B and D
    # show the same forecast, C and E the same result, F did not sit it, H has no forecast.
    assert (view["pairs"], view["agree"]) == (8, 5)


def test_today_shows_a_new_answer_for_a_day(plan_then):
    answers = {"a": _answer("A", "well", 0.9), "b": _answer("B", "just", 0.8, AFTER + timedelta(hours=2))}
    later = service.exam_outcomes(_with(plan_then, answers), AFTER + timedelta(hours=3))["latest"]
    assert later is not None and later["course"] == "B"
    assert service.exam_outcomes(_with(plan_then, answers), AFTER + timedelta(hours=27))["latest"] is None
    # Asked first, then the answer, then the rest of Today's messages (D97).
    assert service.today_message(exam=[], outcome=later, semester=True, tip=True) == "outcome"
    assert service.today_message(exam=[{"course": "C"}], outcome=later) == "exam"


def test_an_answer_kept_before_answers_only_the_exams_before_it(plan_then):
    """AUDIT item 84: answers were kept under the course's name alone, so a course
    taken again (a resit, a later session) was never asked about. An answer of that
    shape still answers the exam it came after, and is shown with its date."""
    old = {"Algebra 3": {"result": "failed", "mark": "", "predicted": 0.6, "at": AFTER.isoformat()}}
    asked = [q["course"] for q in service.exam_questions(_with(plan_then, old), AFTER)]
    assert asked == ["Computer Programming 3"]
    (row,) = service.exam_outcomes(_with(plan_then, old), AFTER)["exams"]
    assert row["course"] == "Algebra 3" and row["when"] and row["predicted"] == 60
    # Given before this exam, it was about an earlier one: this one is asked about.
    early = {"Algebra 3": {**old["Algebra 3"], "at": MORNING.isoformat()}}
    assert "Algebra 3" in [q["course"] for q in service.exam_questions(_with(plan_then, early), AFTER)]


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


def _form(html: str, marker: str) -> dict:
    """The form holding `marker`, as a browser would submit it untouched."""
    (found,) = [f for f in re.findall(r"<form\b.*?</form>", html, re.S) if marker in f]
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", found):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name and ('type="checkbox"' not in tag or " checked" in tag):
            form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', found, re.S):
        chosen = re.search(r'<option value="([^"]*)" selected>', body)
        form[name] = chosen.group(1) if chosen else ""
    return form


def test_the_pages_show_the_answer_and_a_course_taken_again_is_asked_again(tmp_path):
    now = {"at": MORNING}
    config = Config(store=tmp_path, clock=lambda: now["at"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        now["at"] = AFTER
        web.post(f"/p/{token}/exam", data={"course": "Computer Programming 3", "skip": "1"})
        sent = web.post(f"/p/{token}/exam", data={"course": "Algebra 3", "result": "failed", "mark": "12/30"})
        assert sent.status_code == 200
        stored = service.load_subscription(tmp_path, token)
        assert stored is not None
        (first,) = service.exam_outcomes(stored, AFTER)["exams"]
        forecast = f"{first['predicted']} %"
        # Today: the answer beside its forecast, for a day; it leads to Progress.
        today = unescape(sent.text)
        assert "Your exam against the forecast" in today and "Algebra 3: Not passed (12/30)" in today
        assert f"The plan forecast {forecast} of its topics recalled on exam day." in today
        assert f'href="/p/{token}/progress#taken"' in today
        now["at"] = AFTER + timedelta(days=1, hours=1)
        assert "Your exam against the forecast" not in unescape(web.get(f"/p/{token}").text)
        # Progress keeps it, with what the forecast is.
        progress = unescape(web.get(f"/p/{token}/progress").text)
        assert 'id="taken"' in progress and "How your exams went" in progress
        assert f"12/30 · Forecast: {forecast}" in progress and "a hint, not a verdict" in progress
        # The course's page says it under the exam's date.
        index = service.topic_courses(stored).index("Algebra 3")
        course = unescape(web.get(f"/p/{token}/courses/{index}").text)
        assert f"How it went: Not passed (12/30) · Forecast: {forecast}" in course
        # Taken again on 15 February: asked about after it, the first answer kept.
        exam = {**_form(web.get(f"/p/{token}/courses/{index}").text, '/exam"'), "s0_exam": "2027-02-15T09:00"}
        moved = web.post(f"/p/{token}/courses/{index}/exam", data=exam, follow_redirects=False)
        assert moved.status_code == 303
        now["at"] = datetime(2027, 2, 16, 7, 0, tzinfo=UTC)
        stored = service.load_subscription(tmp_path, token)
        assert stored is not None
        assert "Algebra 3" in [q["course"] for q in service.exam_questions(stored, now["at"])]
        web.post(f"/p/{token}/exam", data={"course": "Algebra 3", "result": "passed", "mark": "21/30"})
        stored = service.load_subscription(tmp_path, token)
        assert stored is not None
        taken = [r for r in service.exam_outcomes(stored, now["at"])["exams"] if r["course"] == "Algebra 3"]
        assert sorted(r["result"] for r in taken) == ["failed", "passed"]
        course = unescape(web.get(f"/p/{token}/courses/{index}").text)
        assert "How it went: Passed (21/30)" in course  # the last one
