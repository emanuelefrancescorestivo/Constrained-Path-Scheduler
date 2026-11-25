"""Tests the timetable does not show (D194): quizzes in class keep a course's recent weeks
ready; a midterm the student gives is a partial exam (D61), practised for and kept free
around (D183); asked once on Today, course by course on their own page."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import assistant, service
from cps.memory import FSRS_6, MemoryState
from cps.plan import Block

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def plan() -> service.Subscription:
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)


def _reviews(sub: service.Subscription, course: str) -> list[service.SessionView]:
    report = service.PlanReport.from_dict(sub.plan)
    return [s for s in report.sessions if s.subject == course and s.kind in ("review", "first review")]


def test_a_topic_kept_ready_is_self_tested_sooner_while_it_is_recent():
    blocks = [Block(slot=d * 96 + 72, day=d, start_day=d + 0.75) for d in range(20)]
    topic = assistant.Topic("A · week 1", "A", MemoryState(stability=3.0, difficulty=5.0), 0.0, 0.0, 40.0)
    prefs = assistant.Preferences(model=FSRS_6)
    plain = assistant.schedule(blocks, first_weekday=0, topics=[topic], preferences=prefs)
    ready = assistant.schedule(
        blocks, first_weekday=0, topics=[replace(topic, ready_until=14.0)], preferences=prefs
    )
    first = plain.sessions[0].block.start_day
    assert ready.sessions[0].block.start_day < first  # tested before it falls to 90 %
    assert ready.sessions[0].recall > prefs.review_below
    assert "quizzes in class" in ready.sessions[0].why
    # Once it is no longer recent, the usual 90 % again: never more often than that.
    late = [s for s in ready.sessions if s.block.start_day >= 14.0]
    assert all(s.recall <= prefs.review_below + 1e-9 for s in late)
    # Gone by the exam's day, held as before.
    expired = replace(topic, ready_until=0.0)
    assert assistant.schedule(blocks, first_weekday=0, topics=[expired], preferences=prefs) == plain


def test_quizzes_in_class_keep_a_course_ready_and_cost_a_few_self_tests(plan):
    course = service.tested_courses(plan)[1]
    told = service.set_quizzes(plan, course, True, now=NOW)
    assert told.options["preferences"]["quiz_ready"] == [course]
    assert told.plan["settings"]["preferences"]["quiz_ready"] == [course]
    before, after = _reviews(plan, course), _reviews(told, course)
    assert len(before) < len(after) <= len(before) + 8
    assert any("quizzes in class" in s.rationale for s in after)
    assert service.tests_view(told, NOW)["courses"][1]["quizzes"]
    # Said no again: the plan is as before.
    again = service.set_quizzes(told, course, False, now=NOW)
    assert again.options["preferences"]["quiz_ready"] == []
    assert len(_reviews(again, course)) == len(before)
    with pytest.raises(service.InvalidInput):
        service.set_quizzes(plan, "Underwater basket weaving", True, now=NOW)


def test_a_midterm_given_is_a_partial_exam_practised_for_and_kept_free_around(plan):
    course = service.tested_courses(plan)[0]
    told = service.add_test(plan, course, "2026-11-12", "10:00", exempt=True, now=NOW)
    report = service.PlanReport.from_dict(told.plan)
    added = [e for e in report.events if e.source == "partial"]
    assert [(e.summary, e.start, e.end) for e in added] == [
        (course, "2026-11-12T10:00+01:00", "2026-11-12T12:00+01:00")
    ]
    partials = report.settings.preferences["partials"]
    assert [(p["course"], p["exempt"]) for p in partials] == [(course, True)]
    practice = [s for s in report.sessions if s.kind == "practice" and s.start < added[0].start]
    assert practice and all(s.subject.startswith(course) for s in practice)
    # D183: the hour before it and the half hour after it stay free.
    start, end = datetime.fromisoformat(added[0].start), datetime.fromisoformat(added[0].end)
    for s in report.sessions:
        a, b = datetime.fromisoformat(s.start), datetime.fromisoformat(s.end)
        assert (
            b <= start - service.EXAM_BEFORE
            or a >= end + service.EXAM_AFTER
            or b <= start - timedelta(days=1)
        )
    view = service.tests_view(told, NOW)["courses"][0]
    assert view["midterms"] == [
        {
            "at": "2026-11-12T10:00+01:00",
            "day": "Thursday 12 November",
            "time": "10:00",
            "exempt": True,
            "past": False,
        }
    ]
    # Taken back from its page, or from the calendar as a class marked as a test.
    assert (
        service.remove_test(told, course, "2026-11-12T10:00+01:00", now=NOW).options["lecture_changes"] == []
    )
    restored = service.change_lecture(told, "2026-11-12T10:00+01:00", course, "restore", now=NOW)
    assert restored.options["lecture_changes"] == []


@pytest.mark.parametrize(
    "day, at, until, says",
    [
        ("2026-09-01", "10:00", "", "already behind you"),
        ("2027-03-01", "10:00", "", "before the course's exam"),
        ("not a day", "", "", "the midterm's day"),
        ("2026-11-12", "10:00", "09:00", "ends after it begins"),
    ],
)
def test_a_midterm_that_cannot_be_is_refused_with_a_reason(plan, day, at, until, says):
    course = service.tested_courses(plan)[0]
    with pytest.raises(service.InvalidInput, match=says):
        service.add_test(plan, course, day, at, until=until, now=NOW)


def test_a_midterm_is_given_once_and_the_time_defaults_to_the_morning(plan):
    course = service.tested_courses(plan)[2]
    once = service.add_test(plan, course, "2026-11-10", now=NOW)
    assert once.options["lecture_changes"][0]["at"] == "2026-11-10T09:00+01:00"
    with pytest.raises(service.InvalidInput, match="there already"):
        service.add_test(once, course, "2026-11-10", "09:00", now=NOW)
    with pytest.raises(service.InvalidInput, match="not in your plan"):
        service.remove_test(once, course, "2026-11-11T09:00+01:00", now=NOW)


def test_today_asks_once_there_are_exams_and_not_once_answered(plan):
    tomorrow = NOW + timedelta(days=1)
    week_done = service.answer_fit(plan, "week", True, NOW)
    assert service.fit_question(week_done, tomorrow + timedelta(days=1))["key"] == "tests"
    course = service.tested_courses(plan)[0]
    told = service.set_quizzes(week_done, course, False, now=NOW)
    assert service.fit_question(told, tomorrow + timedelta(days=1))["key"] == "deadlines"


def test_the_page_says_and_saves_each_course_s_tests(tmp_path):
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    class Clock:
        now = NOW

        def __call__(self):
            return self.now

    web = TestClient(
        create_app(
            Config(store=tmp_path, background=False, sweep_every=None, remind_every=None, clock=Clock())
        )
    )
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    page = unescape(web.get(f"/p/{token}/tests").text)
    courses = service.tested_courses(service.load_subscription(tmp_path, token))
    assert all(c in page for c in courses) and page.count("data-autosave") == len(courses)
    answer = web.post(
        f"/p/{token}/tests/quizzes",
        data={"course": courses[0], "on": "1", "at": "t-1"},
        follow_redirects=False,
    )
    assert answer.status_code == 303 and answer.headers["location"] == f"/p/{token}/tests#t-1"
    answer = web.post(
        f"/p/{token}/tests/add",
        data={"course": courses[0], "day": "2026-11-12", "time": "10:00", "exempt": "1", "at": "t-1"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    page = unescape(web.get(f"/p/{token}/tests").text)
    assert "Thursday 12 November" in page and "its material is off the final" in page
    assert 'name="on" value="1" checked' in page
    # The course's page says it in a line, and leads here.
    course_page = unescape(web.get(f"/p/{token}/courses/0").text)
    assert f'href="/p/{token}/tests#t-1"' in course_page and "Quizzes in class · 1 midterm" in course_page
    # A refusal comes back on the page, with its reason.
    refused = web.post(f"/p/{token}/tests/add", data={"course": courses[0], "day": "2026-09-01"})
    assert refused.status_code == 400 and "already behind you" in refused.text
    gone = web.post(
        f"/p/{token}/tests/remove",
        data={"course": courses[0], "when": "2026-11-12T10:00+01:00"},
        follow_redirects=False,
    )
    assert gone.status_code == 303 and "Thursday 12 November" not in web.get(f"/p/{token}/tests").text
    assert web.post(f"/p/{token}/tests/elsewhere", data={}).status_code == 404
    assert " style=" not in page  # CSP: no inline style
