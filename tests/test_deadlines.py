"""
Deadlines from a learning platform (Moodle's calendar export). The event names
and the course category follow Moodle's source (see `calendar_io.find_assignments`);
`examples/sample-moodle.ics` is synthetic, written in that shape.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cps import service
from cps.calendar_io import find_assignments

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MOODLE = (EXAMPLES / "sample-moodle.ics").read_bytes()
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
URL = "https://moodle.example.org/calendar/export_execute.php"


def test_assignments_quizzes_and_extensions_are_found():
    found = find_assignments(MOODLE.decode(), "Europe/Paris")
    assert [(a.name, a.course, a.due.strftime("%Y-%m-%d %H:%M")) for a in found] == [
        ("Problem sheet 1", "STAT301", "2026-09-18 23:59"),
        ("Week 4 quiz", "ML220", "2026-10-07 23:59"),
        ("Problem sheet 3", "STAT301", "2026-10-09 23:59"),
        # The extension (19 Oct) replaces the course's date (16 Oct); "due to be
        # graded" is the teacher's, "opens" and office hours are not deadlines.
        ("Essay on fairness", "ETH210", "2026-10-19 12:00"),
    ]


def test_a_file_that_is_not_a_calendar_says_so():
    with pytest.raises(ValueError):
        find_assignments("<html>login</html>", "Europe/Paris")


@pytest.fixture
def subscription() -> service.Subscription:
    ics = (EXAMPLES / "sample-semester.ics").read_bytes()
    sub = service.new_subscription(
        subjects=[], start=date(2026, 9, 29), tz="Europe/Paris", ics=ics, now=NOW, require_plan=False
    )
    return replace(sub, options={**sub.options, "deadlines_url": URL})


def _names(sub):
    return [(t["name"], t["due"], t["hours"]) for t in sub.options["tasks"]]


def test_upcoming_deadlines_become_tasks_once(subscription):
    synced = service.sync_deadlines(subscription, now=NOW, fetch=lambda url: MOODLE)
    assert _names(synced) == [
        ("Week 4 quiz", "2026-10-07 23:59", 2.0),
        ("Problem sheet 3", "2026-10-09 23:59", 2.0),
        ("Essay on fairness", "2026-10-19 12:00", 2.0),
    ]
    assert synced.options["deadlines_error"] is None
    again = service.sync_deadlines(synced, now=NOW, fetch=lambda url: MOODLE)
    assert _names(again) == _names(synced)


def test_the_student_owns_the_hours_and_the_list_the_platform_owns_the_date(subscription):
    synced = service.sync_deadlines(subscription, now=NOW, fetch=lambda url: MOODLE)
    tasks = [
        {**t, "hours": 5.0} for t in synced.options["tasks"] if t["name"] != "Week 4 quiz"
    ]  # a guess corrected, and one deadline removed
    edited = replace(synced, options={**synced.options, "tasks": tasks})
    moved = MOODLE.replace(b"DTSTART:20261009T215900Z", b"DTSTART:20261012T215900Z")
    again = service.sync_deadlines(edited, now=NOW, fetch=lambda url: moved)
    assert _names(again) == [
        ("Problem sheet 3", "2026-10-12 23:59", 5.0),
        ("Essay on fairness", "2026-10-19 12:00", 5.0),
    ]


def test_a_name_already_taken_gets_the_course(subscription):
    typed = {"name": "Problem sheet 3", "due": "2026-10-02 18:00", "hours": 1.0, "course": "Analysis"}
    mine = replace(subscription, options={**subscription.options, "tasks": [typed]})
    synced = service.sync_deadlines(mine, now=NOW, fetch=lambda url: MOODLE)
    assert "Problem sheet 3 (STAT301)" in [t["name"] for t in synced.options["tasks"]]
    assert synced.options["tasks"][0] == typed


def test_an_unreadable_platform_changes_nothing_and_says_why(subscription):
    def down(url):
        raise service.UnreadableLink("the link could not be read (timed out)")

    kept = service.sync_deadlines(subscription, now=NOW, fetch=down)
    assert kept.options["tasks"] == [] and "timed out" in kept.options["deadlines_error"]
    page = service.sync_deadlines(subscription, now=NOW, fetch=lambda url: b"<html>login</html>")
    assert "not a calendar" in page.options["deadlines_error"]


def test_a_refresh_reads_the_platform_and_plans_the_deadlines(subscription):
    fresh = service.refresh_subscription(subscription, now=NOW, fetch=lambda url: MOODLE)
    plan = service.PlanReport.from_dict(fresh.plan)
    assert [t.name for t in plan.tasks] == ["Week 4 quiz", "Problem sheet 3", "Essay on fairness"]


def test_setting_the_link_reads_it_at_once_and_a_bad_one_is_refused(subscription):
    bare = replace(subscription, options={**subscription.options, "deadlines_url": None})
    revised = service.revise_subscription(bare, deadlines_url=URL, now=NOW, fetch=lambda url: MOODLE)
    assert len(revised.options["tasks"]) == 3 and revised.plan is not None
    with pytest.raises(service.InvalidInput, match="learning platform"):
        service.revise_subscription(bare, deadlines_url=URL, now=NOW, fetch=lambda url: b"nope")
    cleared = service.revise_subscription(revised, deadlines_url="", now=NOW)
    assert cleared.options["deadlines_url"] is None and len(cleared.options["tasks"]) == 3
