"""
Tests for the service layer: bytes in, plan out, `.ics` back, no UI anywhere.

What the service promises a front end, each checked here: plain-JSON results that
round-trip, determinism given a seed, replanning after a reported outcome, and a
typed, readable error instead of a traceback for everything a user can get wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from cps import service
from cps.memory import Grade, initial_state

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-timetable.ics"
START = date(2026, 3, 2)
TZ = "Europe/Rome"


@pytest.fixture(scope="module")
def report():
    return service.analyse_calendar(SAMPLE.read_bytes(), start=START, tz=TZ)


@pytest.fixture(scope="module")
def subjects():
    return [
        service.SubjectSpec("Analysis", None, stability=2.0, difficulty=7.0),  # date from the calendar
        service.SubjectSpec("Algebra", "2026-03-27", familiarity=3),
    ]


@pytest.fixture(scope="module")
def plan(report, subjects):
    return service.make_plan(report, subjects, window=4)


# --------------------------------------------------------------------------- #
# The whole path
# --------------------------------------------------------------------------- #


def test_bytes_to_plan_to_ics(plan):
    assert plan.sessions
    data = service.export_ics(plan)
    assert data.startswith(b"BEGIN:VCALENDAR\r\n")
    assert b"\r\r\n" not in data
    assert b"BEGIN:VTIMEZONE" in data
    assert data.count(b"BEGIN:VEVENT") == len(plan.sessions)


def test_the_service_imports_no_ui():
    """Run in a fresh interpreter, so nothing another test imported can hide it."""
    code = (
        "import sys, cps.service, cps.cli; "
        "bad = [m for m in ('streamlit', 'fastapi', 'flask', 'tkinter') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_each_subject_is_planned_to_its_own_exam(plan):
    analysis, algebra = plan.subjects
    assert analysis.exam.startswith("2026-03-20T09:00")  # found in the calendar
    assert algebra.exam.startswith("2026-03-27T00:00")  # given as a date
    assert analysis.target == pytest.approx(analysis.exam_day)
    assert all(s.ready for s in plan.subjects)
    last_analysis = max(s.start_day for s in plan.sessions if s.subject == "Analysis")
    assert last_analysis < analysis.exam_day


def test_a_horizon_too_short_for_the_exams_is_extended(subjects):
    short = service.analyse_calendar(SAMPLE.read_bytes(), start=START, tz=TZ, days=7)
    plan = service.make_plan(short, subjects, window=3)
    assert plan.settings.horizon_days == 25  # Algebra at 00:00 on 27 March is day 25.0
    assert max(b.start_day for b in plan.blocks) > 20


# --------------------------------------------------------------------------- #
# Plain data
# --------------------------------------------------------------------------- #


def test_reports_are_plain_json_and_round_trip(report, plan):
    for result in (report, plan):
        data = result.to_dict()
        assert json.loads(json.dumps(data)) == data
    rebuilt = service.PlanReport.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert rebuilt.to_dict() == plan.to_dict()


def test_a_round_tripped_plan_replans_like_the_original(plan):
    rebuilt = service.PlanReport.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert (service.replan_after(rebuilt, 1, "lapsed").to_dict()
            == service.replan_after(plan, 1, "lapsed").to_dict())


def test_a_seed_makes_the_plan_deterministic(report, subjects):
    first = service.make_plan(report, subjects, window=3, seed=5).to_dict()
    second = service.make_plan(report, subjects, window=3, seed=5).to_dict()
    assert first == second


# --------------------------------------------------------------------------- #
# Replanning
# --------------------------------------------------------------------------- #


def test_a_lapse_is_recorded_and_the_rest_is_replanned(plan):
    after = service.replan_after(plan, 1, "lapsed")
    assert [s.outcome for s in after.history] == ["recalled", "lapsed"]
    assert all(s.start_day > after.history[-1].start_day for s in after.sessions)
    lapsed = after.history[-1].subject
    # the forgotten subject is reviewed again sooner than it was going to be
    before = [s.start_day for s in plan.sessions[2:] if s.subject == lapsed]
    again = [s.start_day for s in after.sessions if s.subject == lapsed]
    assert again and (not before or again[0] <= before[0])


def test_a_skipped_session_leaves_the_memory_untouched(plan):
    after = service.replan_after(plan, 0, "skipped")
    assert after.history[0].outcome == "skipped"
    curve = service.recall_curve(after, plan.sessions[0].subject)
    at = [p for d, p in curve if d == pytest.approx(plan.sessions[0].start_day)]
    assert at == [] or max(at) < 1.0  # no review, so no jump back to 1.0 there
    assert curve[1][1] < 1.0 and all(p < 1.0 for d, p in curve[1:] if d <= plan.sessions[0].start_day + 0.3)


def test_a_bad_outcome_or_index_is_a_readable_error(plan):
    with pytest.raises(service.InvalidInput, match="outcome must be one of"):
        service.replan_after(plan, 0, "forgot")
    with pytest.raises(service.InvalidInput, match="not an upcoming session"):
        service.replan_after(plan, 99, "lapsed")


# --------------------------------------------------------------------------- #
# Recall curve
# --------------------------------------------------------------------------- #


def test_the_recall_curve_decays_jumps_at_reviews_and_ends_at_the_exam(plan):
    subject = plan.subjects[0]
    curve = service.recall_curve(plan, subject.name)
    assert curve[0] == (0.0, 1.0)
    assert curve[-1][0] == pytest.approx(subject.exam_day)
    assert curve[-1][1] == pytest.approx(subject.recall_at_exam)
    reviews = [s.start_day for s in plan.sessions if s.subject == subject.name]
    for day in reviews:
        at = [p for d, p in curve if d == pytest.approx(day)]
        assert min(at) < 1.0 and max(at) == 1.0


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def test_familiarity_is_anchored_to_the_first_review_grades():
    """1 to 4 are FSRS-4.5's states after a first review graded Again, Hard, Good,
    Easy. 5 adds one successful review, so its stability is higher still; its
    difficulty drifts back towards D0(Good), as FSRS-4.5's mean reversion does."""
    levels = [service.familiarity_prior(k) for k in range(1, 6)]
    assert levels[2] == initial_state(Grade.GOOD)
    assert all(a.stability < b.stability for a, b in zip(levels, levels[1:]))
    assert all(a.difficulty > b.difficulty for a, b in zip(levels[:4], levels[1:4]))


def test_a_typed_timetable_works_without_any_ics():
    rows = [
        {"label": "Lectures", "weekday": "Mon", "start": "09:00", "end": "13:00"},
        {"label": "Lectures", "weekday": "Wed", "start": "09:00", "end": "13:00"},
        {"label": "Physics exam", "date": "2026-03-19", "start": "09:00", "end": "12:00"},
    ]
    report = service.analyse_calendar(None, start=START, tz=TZ, busy_rows=rows)
    assert [a.subject for a in report.assessments] == ["Physics"]
    plan = service.make_plan(report, [service.SubjectSpec("Physics", None, familiarity=2)], window=3)
    assert plan.sessions and plan.subjects[0].ready


# --------------------------------------------------------------------------- #
# Errors a user can cause
# --------------------------------------------------------------------------- #


def test_no_free_blocks_is_a_typed_error(subjects):
    report = service.analyse_calendar(SAMPLE.read_bytes(), start=START, tz=TZ, study_window=(9, 10))
    with pytest.raises(service.NoFreeBlocks, match="widen the study window") as error:
        service.make_plan(report, subjects)
    assert error.value.to_dict()["error"] == "no_free_blocks"


def test_an_exam_in_the_past_is_a_typed_error(report):
    with pytest.raises(service.ExamInPast, match="not after the start"):
        service.make_plan(report, [service.SubjectSpec("Algebra", "2026-03-01", familiarity=3)])


def test_an_unreachable_target_is_a_typed_error():
    """Free only on the first two days, exam on day 12: even if every review
    succeeded, reviews hours apart cannot build twelve days of stability."""
    away = [{"label": "Away", "date": (START + timedelta(days=d)).isoformat(), "start": "00:00",
             "end": "24:00"} for d in range(2, 14)]
    report = service.analyse_calendar(None, start=START, tz=TZ, days=13, busy_rows=away)
    with pytest.raises(service.UnreachableTarget, match="even if every review succeeded"):
        service.make_plan(report, [service.SubjectSpec("Analysis", "2026-03-14", stability=2, difficulty=7)])


def test_a_subject_without_an_exam_is_a_typed_error(report):
    with pytest.raises(service.MissingExam, match="No exam date for 'Physics'"):
        service.make_plan(report, [service.SubjectSpec("Physics", None, familiarity=3)])


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda r: service.make_plan(r, [service.SubjectSpec("A", "2026-03-20", familiarity=7)]), "1 to 5"),
        (lambda r: service.make_plan(r, [service.SubjectSpec("A", "2026-03-20")]), "familiarity"),
        (lambda r: service.make_plan(r, [service.SubjectSpec("A", "next week", familiarity=3)]), "not a date"),
        (lambda r: service.make_plan(r, [service.SubjectSpec("A", "2026-03-20", familiarity=3),
                                         service.SubjectSpec("a", "2026-03-21", familiarity=3)]), "same name"),
        (lambda r: service.make_plan(r, []), "at least one subject"),
        (lambda r: service.make_plan(r, [service.SubjectSpec("A", "2026-03-20", familiarity=3)],
                                     retention=1.2), "target recall"),
    ],
)
def test_nonsense_inputs_are_readable_errors(report, call, message):
    with pytest.raises(service.InvalidInput, match=message):
        call(report)


def test_an_unreadable_calendar_or_zone_is_a_typed_error():
    with pytest.raises(service.InvalidCalendar):
        service.analyse_calendar(b"this is not a calendar", start=START, tz=TZ)
    with pytest.raises(service.InvalidInput, match="time zone"):
        service.analyse_calendar(SAMPLE.read_bytes(), start=START, tz="Mars/Olympus")
    with pytest.raises(service.InvalidInput, match="not a weekday"):
        service.analyse_calendar(None, start=START, tz=TZ,
                                 busy_rows=[{"label": "x", "weekday": "Funday", "start": "9", "end": "10"}])
