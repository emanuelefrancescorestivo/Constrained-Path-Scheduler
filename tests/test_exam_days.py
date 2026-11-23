"""
The time around an exam (DECISIONS.md D183): the hour before an exam and the half hour after it
are kept free of study, whatever the course, so that no session of another course runs into an
exam and none starts the minute one ends. Found on the owner's first-year timetable: Economics
at 08:00 before Probability's exam at 09:00, another session at 10:30 as that exam ended.

What must hold: an exam of the timetable is busy, with its hour before and half hour after, when
the day is cut into blocks, so that a block starts after the half hour rather than being lost; an
exam typed by hand, with no event in the timetable, is taken to last two hours and kept free alike;
no session of any course falls in either.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cps import service

PARIS = ZoneInfo("Europe/Paris")


def _event(uid: str, start: str, end: str, summary: str) -> str:
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}@test\r\nDTSTART;TZID=Europe/Paris:{start}\r\n"
        f"DTEND;TZID=Europe/Paris:{end}\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\n"
    )


def _calendar() -> bytes:
    """Two weeks of Chemistry and Biology lectures, then Chemistry's exam on Wednesday
    13 January, 09:00 to 12:00. Biology's exam is not in it: the student types it."""
    events = []
    for week in range(2):
        monday = date(2027, 1, 4) + timedelta(weeks=week)
        day = monday.strftime("%Y%m%d")
        events.append(_event(f"chem-{week}", f"{day}T100000", f"{day}T120000", "Chemistry 1 - CM"))
        events.append(_event(f"bio-{week}", f"{day}T140000", f"{day}T160000", "Biology 1 - CM"))
    events.append(_event("exam", "20270113T090000", "20270113T120000", "Chemistry 1 - Examen"))
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"
    ).encode()


def _overlaps(start: str, end: str, w0: datetime, w1: datetime) -> bool:
    return datetime.fromisoformat(start) < w1 and datetime.fromisoformat(end) > w0


def test_the_hour_before_an_exam_and_the_half_hour_after_are_kept_free():
    report = service.analyse_calendar(_calendar(), start=date(2027, 1, 4), tz="Europe/Paris")
    exam = datetime(2027, 1, 13, 9, 0, tzinfo=PARIS)
    before, after = (
        exam - service.EXAM_BEFORE,
        datetime(2027, 1, 13, 12, 0, tzinfo=PARIS) + service.EXAM_AFTER,
    )
    assert not [b for b in report.blocks if _overlaps(b.start, b.end, before, after)]
    # The day is cut around them: the afternoon's first block starts once the half hour is over.
    that_day = sorted(
        datetime.fromisoformat(b.start) for b in report.blocks if b.start.startswith("2027-01-13")
    )
    assert that_day and that_day[0] == after

    # A plan of both courses, Biology's exam typed for that afternoon at 15:00: no session in
    # Chemistry's margin, nor from 14:00 to two hours and a half after Biology's starts.
    typed = datetime(2027, 1, 13, 15, 0, tzinfo=PARIS)
    plan = service.make_schedule(
        report,
        [
            service.SubjectSpec("Chemistry 1", None, familiarity=2),
            service.SubjectSpec("Biology 1", typed, familiarity=2),
        ],
        weekly_hours=30,
        now=datetime(2027, 1, 4, 7, 0, tzinfo=PARIS),
    )
    windows = [
        (before, after),
        (typed - service.EXAM_BEFORE, typed + service.TYPED_EXAM_LENGTH + service.EXAM_AFTER),
    ]
    near = [s.start for s in plan.sessions for w0, w1 in windows if _overlaps(s.start, s.end, w0, w1)]
    assert plan.sessions and near == []
