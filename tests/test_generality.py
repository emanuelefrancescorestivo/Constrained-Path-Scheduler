"""
The planner is for any student, not for the timetable it was built on (the owner,
2026-10-08: "don't overfit on my calendar"). Each case below is a synthetic
semester in the shape another system or another country writes: the exams must
be found, each course's lectures tied to its exam, and a plan made. A case that
fails is a shape the reader does not understand yet; AUDIT.md item 41 lists what
was found this way.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar, Event

from cps import service
from cps.calendar_io import course_of

START = date(2026, 9, 28)  # a Monday


def _ics(
    lectures: list[str], exams: list[str], tz: str = "Europe/Paris", all_day_exams: bool = False
) -> bytes:
    """Twelve weeks of the given weekly lectures (one a day from Monday), then the
    exams, one a day, in the week after."""
    zone = ZoneInfo(tz)
    cal = Calendar()
    cal.add("prodid", "-//test//generality//EN")
    cal.add("version", "2.0")
    for i, title in enumerate(lectures):
        event = Event()
        first = datetime(2026, 9, 28 + i % 5, 9 + 2 * (i // 5), 0, tzinfo=zone)
        event.add("summary", title)
        event.add("dtstart", first)
        event.add("dtend", first + timedelta(hours=1, minutes=30))
        event.add("rrule", {"freq": "weekly", "count": 12})
        event.add("uid", f"lecture-{i}@test")
        cal.add_component(event)
    for i, title in enumerate(exams):
        event = Event()
        day = date(2027, 1, 11) + timedelta(days=i)
        event.add("summary", title)
        if all_day_exams:
            event.add("dtstart", day)
            event.add("dtend", day + timedelta(days=1))
        else:
            begin = datetime(day.year, day.month, day.day, 9, 0, tzinfo=zone)
            event.add("dtstart", begin)
            event.add("dtend", begin + timedelta(hours=3))
        event.add("uid", f"exam-{i}@test")
        cal.add_component(event)
    return cal.to_ical()


# name: (lecture titles, exam titles, the courses expected, time zone)
SHAPES = {
    "ADE (France)": (
        ["Algebra 3, Grp: CM ., Salle: Salle 4", "Analysis 3, Grp: TD ., Salle: B12"],
        ["Examen, Algebra 3, Salle: Amphi A", "Examen, Analysis 3"],
        ["Algebra 3", "Analysis 3"],
        "Europe/Paris",
    ),
    "Hyperplanning (France)": (
        ["Mathématiques - TD - Salle B204", "Économie - CM - Amphi 2"],
        ["Examen terminal - Mathématiques - Salle B204", "Partiel - Économie"],
        ["Mathématiques", "Économie"],
        "Europe/Paris",
    ),
    "Celcat (UK)": (
        ["Algebra 3 (Lecture) - Room 101", "Probability (Tutorial) - Room 12"],
        ["Algebra 3 (Exam) - Sports Hall", "Probability (Exam) - Sports Hall"],
        ["Algebra 3", "Probability"],
        "Europe/London",
    ),
    "Outlook, course codes (UK)": (
        ["MA1001 Linear Algebra Lecture", "PH1002 Mechanics Lecture"],
        ["MA1001 Linear Algebra Examination", "PH1002 Mechanics Examination"],
        ["MA1001 Linear Algebra", "PH1002 Mechanics"],
        "Europe/London",
    ),
    "Italian university": (
        ["Analisi Matematica 1 - Lezione - Aula 3", "Fisica 1 - Esercitazione - Aula 5"],
        ["Appello - Analisi Matematica 1", "Esame di Fisica 1"],
        ["Analisi Matematica 1", "Fisica 1"],
        "Europe/Rome",
    ),
    "German university": (
        ["Vorlesung Lineare Algebra", "Übung Analysis I"],
        ["Klausur Lineare Algebra", "Prüfung Analysis I"],
        ["Lineare Algebra", "Analysis I"],
        "Europe/Berlin",
    ),
    "Spanish university": (
        ["Cálculo - Clase - Aula 2", "Álgebra - Clase - Aula 4"],
        ["Examen final de Cálculo", "Examen parcial de Álgebra"],
        ["Cálculo", "Álgebra"],
        "Europe/Madrid",
    ),
    "Dutch university": (
        ["Statistiek - Hoorcollege - Zaal 1", "Calculus - Werkcollege - Zaal 3"],
        ["Tentamen Statistiek", "Tentamen Calculus"],
        ["Statistiek", "Calculus"],
        "Europe/Amsterdam",
    ),
    "Portuguese university": (
        ["Cálculo I - Aula Teórica", "Física - Aula Prática"],
        ["Exame de Cálculo I", "Exame de Física"],
        ["Cálculo I", "Física"],
        "Europe/Lisbon",
    ),
    "US university": (
        ["CS 101 Intro to Programming - Lecture", "MATH 221 Calculus - Discussion"],
        ["CS 101 Intro to Programming - Final Exam", "MATH 221 Calculus - Midterm"],
        ["CS 101 Intro to Programming", "MATH 221 Calculus"],
        "America/New_York",
    ),
    "Japanese university": (
        ["Linear Algebra - Lecture", "Statistics - Lecture"],
        ["Linear Algebra - Final Exam", "Statistics - Final Exam"],
        ["Linear Algebra", "Statistics"],
        "Asia/Tokyo",
    ),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_exams_and_lectures_are_found_in_every_shape(shape):
    lectures, exams, courses, tz = SHAPES[shape]
    report = service.analyse_calendar(_ics(lectures, exams, tz), start=START, tz=tz)
    found = sorted(a.subject for a in report.assessments)
    assert found == sorted(courses), shape
    # Each course's lectures are tied to its exam (topics come from this).
    for title, course in zip(lectures, courses, strict=True):
        assert course_of(title).casefold() == course.casefold(), (shape, title, course_of(title))


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_plan_is_made_in_every_shape_inside_the_student_s_hours(shape):
    lectures, exams, courses, tz = SHAPES[shape]
    now = datetime(2026, 9, 28, 6, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    sub = service.start_subscription(tz=tz, ics=_ics(lectures, exams, tz), now=now)
    assert sorted(s["name"] for s in sub.subjects) == sorted(courses)
    plan = service.PlanReport.from_dict(sub.plan)
    assert plan.sessions, shape
    # Lectures became topics: sessions review weeks of lectures, not a whole course.
    assert any(" · " in s.title for s in plan.sessions if s.kind in ("review", "first review")), shape
    zone = ZoneInfo(tz)
    for s in plan.sessions:
        local = datetime.fromisoformat(s.start).astimezone(zone)
        assert 8 <= local.hour < 22, (shape, s.start)


def test_an_exam_written_as_an_all_day_event_is_found():
    report = service.analyse_calendar(
        _ics(["Algebra - Lecture"], ["Algebra - Exam"], all_day_exams=True), start=START, tz="Europe/Paris"
    )
    assert [a.subject for a in report.assessments] == ["Algebra"]


def test_a_personal_calendar_without_exams_is_planned_from_deadlines():
    """A student whose calendar is their own Google Calendar: no exams in it, only
    life. The plan comes from the deadlines they add."""
    ics = _ics(["Gym", "Dinner with Sam", "Work shift"], [])
    report = service.analyse_calendar(ics, start=START, tz="Europe/Paris")
    assert report.assessments == ()
    sub = service.new_subscription(
        subjects=[],
        start=START,
        tz="Europe/Paris",
        ics=ics,
        tasks=[service.TaskSpec("Essay", "2026-10-16 18:00", 6.0, "History")],
        preferences=service.DEFAULT_PREFERENCES,
        now=datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
    )
    plan = service.PlanReport.from_dict(sub.plan)
    assert [s.title for s in plan.sessions] and all(s.title == "Essay" for s in plan.sessions)


def test_words_that_only_look_like_exams_are_not_exams():
    """A course whose name contains an exam word is not an exam."""
    report = service.analyse_calendar(
        _ics(["Testing and Verification - Lecture", "Final Cut Pro workshop"], []),
        start=START,
        tz="Europe/Paris",
    )
    assert report.assessments == ()
