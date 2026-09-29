"""
Tests for calendar ingestion.

`test_ignoring_rrule_would_free_the_whole_term` is the one that matters. A parser
that reads DTSTART and stops sees one lecture where there are twelve weeks of
them, and every schedule it produces after week one collides with a class. The
January stub would have had to get this right and never got the chance.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from cps.calendar_io import (
    BusyEvent,
    BusyRow,
    busy_from_table,
    busy_grid,
    decode_ics,
    expand_events,
    find_deadlines,
    load_availability,
    plan_to_ics,
)
from cps.plan import tile_free_time

LONDON = ZoneInfo("Europe/London")

TIMETABLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:lecture@test
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/London:20260302T090000
DTEND;TZID=Europe/London:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20260525T235959Z
EXDATE;TZID=Europe/London:20260406T090000
END:VEVENT
BEGIN:VEVENT
UID:gym@test
SUMMARY:Gym
DTSTART;TZID=Europe/London:20260303T181000
DTEND;TZID=Europe/London:20260303T185000
END:VEVENT
BEGIN:VEVENT
UID:trip@test
SUMMARY:Family visit
DTSTART;VALUE=DATE:20260307
DTEND;VALUE=DATE:20260309
END:VEVENT
BEGIN:VEVENT
UID:nightshift@test
SUMMARY:Night shift
DTSTART;TZID=Europe/London:20260304T220000
DTEND;TZID=Europe/London:20260305T060000
END:VEVENT
BEGIN:VEVENT
UID:exam@test
SUMMARY:Analysis exam
DTSTART;TZID=Europe/London:20260323T090000
DTEND;TZID=Europe/London:20260323T120000
END:VEVENT
END:VCALENDAR
"""


def window(days: int = 28, start: date = date(2026, 3, 2)):
    begin = datetime.combine(start, time(0, 0), tzinfo=LONDON)
    return begin, begin + timedelta(days=days), start


# --------------------------------------------------------------------------- #
# Recurrence
# --------------------------------------------------------------------------- #


def test_recurring_lectures_are_expanded():
    begin, end, _ = window()
    lectures = [e for e in expand_events(TIMETABLE, begin, end, LONDON) if "lecture" in e.summary]
    # Four Mondays fall inside a 28-day window opening on Monday 2 March. My first
    # version of this assertion said three; the parser was right and I was not.
    assert len(lectures) == 4, [e.start.isoformat() for e in lectures]
    assert {e.start.date() for e in lectures} == {
        date(2026, 3, 2),
        date(2026, 3, 9),
        date(2026, 3, 16),
        date(2026, 3, 23),
    }


def test_ignoring_rrule_would_free_the_whole_term():
    """The failure mode this module exists to prevent.

    With recurrence expanded, Monday 09:00 is blocked on every Monday in the
    window. A DTSTART-only parser blocks it on the first Monday and reports the
    rest as free, so the scheduler books study time inside a lecture from week
    two onward.
    """
    begin, end, start = window()
    events = expand_events(TIMETABLE, begin, end, LONDON)
    grid = busy_grid(events, start, 28)

    for monday in (0, 7, 14):
        assert not grid.is_free(grid.slot(monday, 9)), f"Monday {monday} 09:00 should be blocked"

    naive = busy_grid([e for e in events if e.start.date() == start], start, 28)
    assert naive.is_free(naive.slot(7, 9)), "sanity: the naive grid really does leave it free"


def test_exdate_is_honoured():
    begin, end, _ = window(days=60)
    lectures = [e for e in expand_events(TIMETABLE, begin, end, LONDON) if "lecture" in e.summary]
    assert date(2026, 4, 6) not in {e.start.date() for e in lectures}


def test_until_is_respected():
    begin, end, _ = window(days=180)
    lectures = [e for e in expand_events(TIMETABLE, begin, end, LONDON) if "lecture" in e.summary]
    assert max(e.start.date() for e in lectures) <= date(2026, 5, 25)


def test_a_naive_until_does_not_crash():
    """Real exports contain this, and dateutil raises on it by default."""
    ics = TIMETABLE.replace("UNTIL=20260525T235959Z", "UNTIL=20260525T235959")
    begin, end, _ = window()
    events = expand_events(ics, begin, end, LONDON)
    assert any("lecture" in e.summary for e in events)


# --------------------------------------------------------------------------- #
# Rounding
# --------------------------------------------------------------------------- #


def test_busy_intervals_round_outward():
    """A 18:10–18:50 gym session blocks both the 18:00 and 18:30 slots.

    Rounding to nearest would mark 18:00–18:30 free and let the scheduler place a
    study block inside it. Over-blocking costs a slot; under-blocking produces a
    plan the student cannot follow.
    """
    begin, end, start = window()
    grid = busy_grid(expand_events(TIMETABLE, begin, end, LONDON), start, 28)
    assert not grid.is_free(grid.slot(1, 18, 0))
    assert not grid.is_free(grid.slot(1, 18, 30))
    assert grid.is_free(grid.slot(1, 19, 0))


def test_all_day_events_block_the_whole_day():
    begin, end, start = window()
    grid = busy_grid(expand_events(TIMETABLE, begin, end, LONDON), start, 28)
    for hour in (0, 6, 12, 21):
        assert not grid.is_free(grid.slot(5, hour)), f"7 March at {hour:02d}:00 should be blocked"
    assert grid.is_free(grid.slot(7, 12)), "9 March is the exclusive DTEND, so it stays free"


def test_events_crossing_midnight_block_both_sides():
    begin, end, start = window()
    grid = busy_grid(expand_events(TIMETABLE, begin, end, LONDON), start, 28)
    assert not grid.is_free(grid.slot(2, 23))
    assert not grid.is_free(grid.slot(3, 2))
    assert grid.is_free(grid.slot(3, 7))


def test_an_event_before_the_window_still_blocks_its_tail():
    ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:overnight@test
SUMMARY:Overnight travel
DTSTART;TZID=Europe/London:20260301T220000
DTEND;TZID=Europe/London:20260302T080000
END:VEVENT
END:VCALENDAR
"""
    grid, events = load_availability(ics, date(2026, 3, 2), 3, "Europe/London")
    assert events, "the occurrence should be clipped into the window, not dropped"
    assert not grid.is_free(grid.slot(0, 3))
    assert grid.is_free(grid.slot(0, 9))


# --------------------------------------------------------------------------- #
# Time zones
# --------------------------------------------------------------------------- #


def test_slots_follow_local_wall_clock_across_a_dst_transition():
    """Documented limitation, verified rather than assumed.

    British Summer Time starts on 29 March 2026. A lecture at 09:00 local stays at
    slot 18 of its day on both sides of the transition, which is what a student
    means by "my Monday nine o'clock". The cost is that one calendar day contains
    23 real hours while the grid gives it 48 half-hour slots, so elapsed-time
    arithmetic drifts by one hour across that boundary — about 1/24 of a day
    against a memory model measured in days.
    """
    ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:dst@test
SUMMARY:Nine o'clock lecture
DTSTART;TZID=Europe/London:20260323T090000
DTEND;TZID=Europe/London:20260323T100000
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
END:VCALENDAR
"""
    start = date(2026, 3, 23)
    grid, events = load_availability(ics, start, 21, "Europe/London")
    offsets = {e.start.utcoffset() for e in events}
    assert len(offsets) == 2, "the window must actually straddle the transition"
    for day in (0, 7, 14):
        assert not grid.is_free(grid.slot(day, 9)), f"day {day} 09:00 local should be blocked"


def test_utc_input_is_converted_to_the_requested_zone():
    ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:z@test
SUMMARY:Standup
DTSTART:20260701T083000Z
DTEND:20260701T090000Z
END:VEVENT
END:VCALENDAR
"""
    grid, _ = load_availability(ics, date(2026, 7, 1), 2, "Europe/London")
    assert not grid.is_free(grid.slot(0, 9, 30)), "08:30 UTC is 09:30 in London in July"
    assert grid.is_free(grid.slot(0, 8, 30))


# --------------------------------------------------------------------------- #
# Deadlines
# --------------------------------------------------------------------------- #


def test_exams_are_found_and_named():
    begin, end, _ = window()
    deadlines = find_deadlines(expand_events(TIMETABLE, begin, end, LONDON))
    assert len(deadlines) == 1
    assert deadlines[0].subject == "Analysis"
    assert deadlines[0].when.date() == date(2026, 3, 23)


def test_deadline_days_from_start_feeds_the_stability_target():
    begin, end, start = window()
    deadline = find_deadlines(expand_events(TIMETABLE, begin, end, LONDON))[0]
    assert deadline.days_from(start) == pytest.approx(21.375, abs=0.01)


def test_lectures_are_not_mistaken_for_exams():
    """Keyword matching is transparent and therefore fixable. This is the case it
    has to get right: a lecture whose title merely mentions the subject."""
    begin, end, _ = window()
    titles = {d.summary for d in find_deadlines(expand_events(TIMETABLE, begin, end, LONDON))}
    assert "Analysis lecture" not in titles


def test_earliest_occurrence_wins_per_subject():
    ics = TIMETABLE.replace(
        "END:VCALENDAR",
        """BEGIN:VEVENT
UID:resit@test
SUMMARY:Analysis exam
DTSTART;TZID=Europe/London:20260324T090000
DTEND;TZID=Europe/London:20260324T120000
END:VEVENT
END:VCALENDAR""",
    )
    begin, end, _ = window()
    deadlines = find_deadlines(expand_events(ics, begin, end, LONDON))
    assert len(deadlines) == 1
    assert deadlines[0].when.date() == date(2026, 3, 23)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


def test_quiet_hours_are_required_because_calendars_only_record_busy_time():
    """Without a stated study window, midnight is free and gets scheduled.

    This is not hypothetical: the first end-to-end run exported a study block at
    00:00–01:30, because the test timetable has lectures and a gym slot and no
    event called "sleep". A calendar cannot tell you when someone is available,
    only when they are not.
    """
    raw, _ = load_availability(TIMETABLE, date(2026, 3, 2), 14, "Europe/London", study_window=None)
    assert raw.is_free(raw.slot(1, 3), 3), "raw occupancy really does leave 03:00 free"

    bounded, _ = load_availability(TIMETABLE, date(2026, 3, 2), 14, "Europe/London")
    assert not bounded.is_free(bounded.slot(1, 3))
    assert bounded.is_free(bounded.slot(1, 19), 3), "evenings must survive"
    blocks = tile_free_time(bounded, block_slots=3, max_blocks_per_day=2)
    assert all(8 <= (b.slot % 48) / 2 <= 22 for b in blocks)


def test_study_window_is_validated():
    for bad in ((22.0, 8.0), (-1.0, 10.0), (8.0, 25.0)):
        with pytest.raises(ValueError, match="study_window"):
            load_availability(TIMETABLE, date(2026, 3, 2), 7, "Europe/London", study_window=bad)


def test_real_timetable_leaves_usable_evening_blocks():
    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), 14, "Europe/London")
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    assert blocks, "a calendar with only lectures and a gym slot should leave study time"
    assert all(grid.is_free(b.slot, 3) for b in blocks)
    assert not any(b.day == 5 for b in blocks), "the all-day family visit should yield no blocks"


def test_exported_plan_reimports_as_busy_time():
    """Round trip: what the scheduler emits must be something a calendar accepts.

    Reading our own output back through the ingestion path is the cheapest
    possible check that the export is well-formed, and it is the operation a user
    performs the moment they import the file.
    """
    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), 14, "Europe/London")
    blocks = tile_free_time(grid, max_blocks_per_day=1)[:3]
    sessions = [(b.slot, "Analysis", "Review 1 of 3 — timed for 85% recall") for b in blocks]
    ics = plan_to_ics(sessions, date(2026, 3, 2), "Europe/London")

    assert "BEGIN:VCALENDAR" in ics and "Study: Analysis" in ics
    reloaded, events = load_availability(ics, date(2026, 3, 2), 14, "Europe/London")
    assert len(events) == 3
    for block in blocks:
        assert not reloaded.is_free(block.slot, 3)


def test_exported_plan_defines_every_time_zone_it_references():
    """RFC 5545 section 3.2.19: a TZID parameter must name a VTIMEZONE in the same
    file. Google and Apple accept a bare IANA name, Outlook does not reliably, and
    an importer that cannot resolve the zone may fall back to floating or UTC time,
    moving every study block by the offset. Found in AUDIT.md item 24.
    """
    ics = plan_to_ics([(36, "Algebra", "why")], date(2026, 3, 2), "Europe/Rome")
    assert "DTSTART;TZID=Europe/Rome:" in ics
    assert "BEGIN:VTIMEZONE" in ics and "TZID:Europe/Rome" in ics
    # the export still reads back at the same wall-clock time
    _, events = load_availability(ics, date(2026, 3, 2), 3, "Europe/Rome")
    assert events[0].start.hour == 18 and events[0].start.minute == 0


def test_exported_events_carry_the_reason(tmp_path):
    ics = plan_to_ics(
        [(36, "Algebra", "Third review; the gap is what makes it worth a block")],
        date(2026, 3, 2),
        "Europe/London",
    )
    assert "the gap is what makes it worth a block" in ics.replace("\r\n ", "")


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def test_a_utf8_bom_does_not_break_ingestion():
    """Windows Notepad's "UTF-8 with BOM" prepends U+FEFF, and icalendar then
    raises on the first line. Google's own exports have no BOM, so this only
    shows up once a user opens the file in an editor and saves it again.
    """
    begin, end, _ = window()
    events = expand_events("\ufeff" + TIMETABLE, begin, end, LONDON)
    assert any("lecture" in e.summary for e in events)


def test_decode_ics_strips_a_bom_and_survives_bad_bytes():
    assert decode_ics(b"\xef\xbb\xbf" + TIMETABLE.encode("utf-8")).startswith("BEGIN:VCALENDAR")
    damaged = b"BEGIN:VCALENDAR\nSUMMARY:Caf\xe9\nEND:VCALENDAR"
    assert "\ufffd" in decode_ics(damaged), "invalid bytes should be replaced, not raised"


# --------------------------------------------------------------------------- #
# Hand-typed timetables
# --------------------------------------------------------------------------- #

WEEKLY_ONLY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:lecture@test
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/London:20260302T090000
DTEND;TZID=Europe/London:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO
END:VEVENT
END:VCALENDAR
"""


def test_a_typed_weekly_row_gives_the_events_an_ics_gives():
    """The point of `busy_from_table`: nothing downstream can tell the difference.
    Checked across the clock change on 29 March, where wall-clock times matter."""
    begin, end, _ = window(start=date(2026, 3, 2), days=42)
    from_ics = expand_events(WEEKLY_ONLY, begin, end, LONDON)
    from_table = busy_from_table(
        [{"label": "Analysis lecture", "weekday": "Monday", "start": "09:00", "end": "11:00"}],
        date(2026, 3, 2),
        42,
        "Europe/London",
    )
    assert from_table == from_ics


def test_a_night_row_blocks_the_first_morning_too():
    """Same trap as AUDIT.md item 15: a window crossing midnight must be inherited
    from the day before the horizon."""
    rows = [BusyRow("Sleep", time(23, 0), time(7, 0), weekday=d) for d in range(7)]
    events = busy_from_table(rows, date(2026, 3, 2), 3, "Europe/London")
    assert events[0].start.hour == 0 and events[0].end.hour == 7
    grid = busy_grid(events, date(2026, 3, 2), 3)
    assert not grid.is_free(grid.slot(0, 3), 1)
    assert grid.is_free(grid.slot(0, 12), 1)


def test_one_off_rows_and_exam_detection():
    rows = [
        {"label": "Dentist", "date": "2026-03-04", "start": "14:00", "end": "15:00"},
        {"label": "Esame di Fisica", "date": date(2026, 3, 9), "start": "09:00", "end": "12:00"},
        {"label": "Holiday", "date": "2026-03-05", "start": "00:00", "end": "24:00"},
    ]
    events = busy_from_table(rows, date(2026, 3, 2), 14, "Europe/Rome")
    assert [e.summary for e in events] == ["Dentist", "Holiday", "Esame di Fisica"]
    assert events[1].duration == timedelta(days=1)
    assert [d.subject for d in find_deadlines(events)] == ["Fisica"]


@pytest.mark.parametrize(
    "row, message",
    [
        ({"label": "x", "start": "9", "end": "10"}, "weekday or a date"),
        ({"label": "x", "weekday": "Mon", "date": "2026-03-04", "start": "9", "end": "10"}, "not both"),
        ({"label": "x", "weekday": "Mon", "start": "nine", "end": "10"}, "time of day"),
        ({"label": "x", "date": "4 March", "start": "9", "end": "10"}, "not a date"),
    ],
)
def test_bad_rows_say_what_is_wrong(row, message):
    with pytest.raises(ValueError, match=message):
        busy_from_table([row], date(2026, 3, 2), 7, "Europe/Rome")


# --------------------------------------------------------------------------- #
# A university timetable export (ADE / Hyperplanning style)
# --------------------------------------------------------------------------- #
# Synthetic, in the shape of a real export: "Course, Grp: TYPE ., Salle: Room",
# exams marked by the group EXAMEN, a cancelled lecture with STATUS:CANCELLED.
# Found when the owner ran the planner on his own timetable (AUDIT.md items 29-32).

ADE_STYLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:l1@test
DTSTART;TZID=Europe/Paris:20260930T090000
DTEND;TZID=Europe/Paris:20260930T103000
SUMMARY: Advanced Statistics, Grp: CM ., Salle: Salle 2   Estrapade
END:VEVENT
BEGIN:VEVENT
UID:l2@test
DTSTART;TZID=Europe/Paris:20261203T153000
DTEND;TZID=Europe/Paris:20261203T170000
SUMMARY: Ethics & Philosophy of AI, Grp: CM ., Salle: Salle 4   Estrapade, COURS ANNULE
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:e1@test
DTSTART;TZID=Europe/Paris:20270125T134500
DTEND;TZID=Europe/Paris:20270125T164500
SUMMARY: Computer Programming 3, Grp: EXAMEN ., Salle: Salle 5   Estrapade
CATEGORIES:EXAMEN
END:VEVENT
BEGIN:VEVENT
UID:e2@test
DTSTART;TZID=Europe/Paris:20270129T134500
DTEND;TZID=Europe/Paris:20270129T154500
SUMMARY: Deep Learning 1, Grp: EXAMEN ., Salle: Salle 2   Estrapade
CATEGORIES:EXAMEN
END:VEVENT
END:VCALENDAR
"""
PARIS = ZoneInfo("Europe/Paris")


def ade_events(days: int = 150):
    begin = datetime.combine(date(2026, 9, 29), time(0, 0), tzinfo=PARIS)
    return expand_events(ADE_STYLE, begin, begin + timedelta(days=days), PARIS)


def test_an_exam_is_named_after_its_course_not_its_room():
    """AUDIT.md item 30. The subject used to be the whole title, room and group
    included: "Computer Programming 3, Grp: EN ., Salle: Salle 5 Estrapade"."""
    assert [d.subject for d in find_deadlines(ade_events())] == [
        "Computer Programming 3",
        "Deep Learning 1",
    ]


def test_a_keyword_inside_a_longer_keyword_is_not_matched():
    """AUDIT.md item 29. "exam" was found inside "EXAMEN", and stripping it left
    "EN" in the subject. Keywords now match whole words, longest first."""
    found = find_deadlines(ade_events())
    assert all("EN ." not in d.subject and "Grp" not in d.subject for d in found)
    assert all(d.summary.strip().startswith(d.subject) for d in found)


@pytest.mark.parametrize(
    "title, subject",
    [
        ("Analysis exam", "Analysis"),
        ("Final - Economics", "Economics"),
        ("Esame di Fisica", "Fisica"),
        ("Exam: Linear Algebra, Room 3", "Linear Algebra"),
        ("EXAMEN - Analyse 3 | Amphi B", "Analyse 3"),
        ("Midterms: Probability", "Probability"),
    ],
)
def test_assessment_titles_in_other_shapes_keep_working(title, subject):
    event = BusyEvent(
        datetime(2026, 3, 20, 9, tzinfo=LONDON), datetime(2026, 3, 20, 12, tzinfo=LONDON), title
    )
    assert [d.subject for d in find_deadlines([event])] == [subject]


def test_a_word_that_only_contains_a_keyword_is_not_an_exam():
    """Whole words: "Examination techniques seminar" is not an exam of anything,
    and "Contest" does not contain the word "test"."""
    titles = ("Contest registration", "Latest news", "Finalist dinner")
    events = [
        BusyEvent(datetime(2026, 3, d, 9, tzinfo=LONDON), datetime(2026, 3, d, 10, tzinfo=LONDON), t)
        for d, t in zip((2, 3, 4), titles, strict=True)
    ]
    assert find_deadlines(events) == []


def test_a_cancelled_event_does_not_block_time():
    """AUDIT.md item 31. RFC 5545 STATUS:CANCELLED; the export also says "COURS
    ANNULE" in the title. A cancelled lecture is free time."""
    summaries = [e.summary for e in ade_events()]
    assert not any("ANNULE" in s for s in summaries)
    assert any("Advanced Statistics" in s for s in summaries)
