"""
The engine review's changes (docs/ENGINE-REVIEW.md), each pinned by what it must do.

A1 (W4, DECISIONS.md D44): a self-test nobody reported within a day is "unknown":
still on the calendar and in the feed, but its topic gains no memory from it, the
plan brings the topic back as if it had not happened, and the exam forecast says how
many it is unsure of. Deadline work and exam practice are not affected.

A2 (W1, D45): "When do you study best?" Each day's free time is cut into blocks
nearest the part of the day the student prefers (earliest, as before, without a
preference), and half an hour after training or work stays free.

A3 (W6, D46): exam practice sized by the weeks each course was taught, changeable
per exam; exams in their last days take turns, the most practice left per day first;
every practice session asks for mixed kinds of question.
"""

from __future__ import annotations

import importlib.util
import random
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import memory, service
from cps.assistant import WEIGHT_TIE, Exam, Preferences, Quick, Topic, schedule
from cps.calendar_io import BusyEvent, availability_from_events
from cps.memory import MemoryState
from cps.plan import Block, short_gaps, tile_free_time
from cps.timegrid import TimeGrid

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PARIS = ZoneInfo("Europe/Paris")
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, the semester's first Tuesday
REVIEWS = ("review", "first review")


def _start(**options) -> service.Subscription:
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING, **options)


def _behind(subscription: service.Subscription, now: datetime) -> list[service.SessionView]:
    plan = service.PlanReport.from_dict(subscription.plan)
    return [s for s in plan.history + plan.sessions if datetime.fromisoformat(s.end) <= now]


def test_a_self_test_not_reported_after_a_day_is_unknown_and_gains_nothing():
    first = _start()
    later = MORNING + timedelta(days=5)
    behind = _behind(first, later)
    assert any(s.kind in REVIEWS for s in behind)

    silent = service.refresh_subscription(first, now=later, reread=False)
    plan = service.PlanReport.from_dict(silent.plan)
    for s in plan.history:
        overdue = datetime.fromisoformat(s.end) <= later - service.UNREPORTED_AFTER
        if s.kind in REVIEWS:
            assert s.outcome == ("unknown" if overdue else "recalled"), (s.title, s.end)
        else:
            assert s.outcome == "recalled", (s.kind, s.title)  # tasks and practice: no memory to doubt

    told = replace(first, outcomes={service.session_id(s): "done" for s in behind})
    reported = service.refresh_subscription(told, now=later, reread=False)
    plan_reported = service.PlanReport.from_dict(reported.plan)
    assert not any(s.outcome == "unknown" for s in plan_reported.history)

    # The same week, reported or silent: silence leaves every topic remembered less,
    # and the forecast says how many self-tests it did not count.
    forecast_silent = {f["name"]: f for f in service.forecast(plan, later)}
    forecast_reported = {f["name"]: f for f in service.forecast(plan_reported, later)}
    unknown = sum(f["unknown"] for f in forecast_silent.values())
    assert unknown == sum(1 for s in plan.history if s.outcome == "unknown") > 0
    assert all(f["unknown"] == 0 for f in forecast_reported.values())
    assert all(
        forecast_silent[name]["if_stopped"] <= forecast_reported[name]["if_stopped"]
        for name in forecast_silent
    )
    # Unrounded: each topic with an unknown self-test is less stable than when reported.
    silent_state = service._replay(plan.specs, plan.history, service.model_of(plan.settings))
    reported_state = service._replay(
        plan_reported.specs, plan_reported.history, service.model_of(plan.settings)
    )
    doubted = {s.title for s in plan.history if s.outcome == "unknown"}
    assert doubted and all(silent_state[t][0].stability < reported_state[t][0].stability for t in doubted)


def test_a_late_report_counts_again():
    first = _start()
    later = MORNING + timedelta(days=5)
    silent = service.refresh_subscription(first, now=later, reread=False)
    unknown = [s for s in service.PlanReport.from_dict(silent.plan).history if s.outcome == "unknown"]
    assert unknown
    late = service.report_session(silent, service.session_id(unknown[0]), "most", now=later)
    after = {service.session_id(s): s for s in service.PlanReport.from_dict(late.plan).history}
    assert after[service.session_id(unknown[0])].outcome == "recalled"
    assert after[service.session_id(unknown[0])].grade == "good"


def test_within_the_day_a_session_still_counts_as_done():
    first = _start()
    plan = service.PlanReport.from_dict(first.plan)
    review = next(s for s in plan.sessions if s.kind in REVIEWS)
    soon = datetime.fromisoformat(review.end) + timedelta(hours=12)
    fresh = service.refresh_subscription(first, now=soon, reread=False)
    same = next(
        s
        for s in service.PlanReport.from_dict(fresh.plan).history
        if service.session_id(s) == service.session_id(review)
    )
    assert same.outcome == "recalled"


def test_an_unknown_self_test_stays_in_the_calendar_feed():
    first = _start()
    later = MORNING + timedelta(days=5)
    silent = service.refresh_subscription(first, now=later, reread=False)
    plan = service.PlanReport.from_dict(silent.plan)
    unknown = [s for s in plan.history if s.outcome == "unknown"]
    feed = service.export_ics(plan, include_history=True).decode("utf-8")
    assert feed.count("BEGIN:VEVENT") == len(plan.history) + len(plan.sessions)
    assert unknown


# --------------------------------------------------------------------------- #
# A2: when the student studies best, and a pause after training or work
# --------------------------------------------------------------------------- #


def _earliest_fit(grid: TimeGrid, block_slots: int, max_blocks_per_day: int) -> tuple[Block, ...]:
    """`tile_free_time` as it was before D45, kept here as the reference."""
    blocks = []
    for day in range(grid.days):
        placed, slot = 0, day * grid.slots_per_day
        end = slot + grid.slots_per_day
        while slot + block_slots <= end and placed < max_blocks_per_day:
            if grid.is_free(slot, block_slots):
                blocks.append(Block(slot, day, grid.days_from_start(slot)))
                placed += 1
                slot += block_slots
            else:
                slot += 1
    return tuple(blocks)


def test_without_a_preference_free_time_is_cut_exactly_as_before():
    rng = random.Random(45)
    for _ in range(400):
        days = rng.randint(1, 4)
        total, busy = days * 48, 0
        for _ in range(rng.randint(0, 12)):
            start = rng.randrange(total - 1)
            busy |= ((1 << rng.randint(1, min(10, total - start))) - 1) << start
        grid = TimeGrid(days, busy)
        size, most = rng.randint(1, 4), rng.randint(1, 3)
        assert tile_free_time(grid, size, most) == _earliest_fit(grid, size, most)


def _hours(blocks) -> list[str]:
    return [f"{b.slot % 48 // 2:02d}:{b.slot % 2 * 30:02d}" for b in blocks]


def test_a_free_day_is_filled_from_the_part_the_student_prefers():
    grid = availability_from_events([], date(2026, 9, 28), 1, study_window=(8.0, 22.0))
    expected = {
        "morning": ["08:00", "09:30"],
        "any": ["08:00", "09:30"],
        "afternoon": ["14:00", "15:30"],
        "evening": ["19:00", "20:30"],
    }
    for when, hours in expected.items():
        assert _hours(tile_free_time(grid, 3, 2, around=service.STUDY_TIMES[when])) == hours, when


def test_when_that_part_is_busy_the_nearest_free_time_is_used():
    evening_class = [
        BusyEvent(datetime(2026, 9, 28, 17, tzinfo=PARIS), datetime(2026, 9, 28, 22, tzinfo=PARIS), "Lab")
    ]
    grid = availability_from_events(evening_class, date(2026, 9, 28), 1, study_window=(8.0, 22.0))
    assert _hours(tile_free_time(grid, 3, 2, around=service.STUDY_TIMES["evening"])) == ["14:00", "15:30"]
    full = availability_from_events(
        [BusyEvent(datetime(2026, 9, 28, 8, tzinfo=PARIS), datetime(2026, 9, 28, 21, tzinfo=PARIS), "Lab")],
        date(2026, 9, 28),
        1,
        study_window=(8.0, 22.0),
    )
    assert tile_free_time(full, 3, 2, around=24.0) == ()  # an hour left: no block


@pytest.mark.parametrize(
    ("kind", "hours"),
    [("training", ["16:00", "17:30"]), ("work", ["16:00", "17:30"]), ("other", ["17:30", "20:30"])],
)
def test_half_an_hour_after_training_or_work_stays_free(kind, hours):
    row = {"label": "Football", "kind": kind, "weekday": "Mon", "start": "19:00", "end": "20:30"}
    report = service.analyse_calendar(
        None, start=date(2026, 9, 28), tz="Europe/Paris", days=1, busy_rows=[row], study_time="evening"
    )
    assert [b.start[11:16] for b in report.blocks] == hours
    # The pause is free time kept, not an event on the calendar.
    assert [(e.start[11:16], e.end[11:16]) for e in report.events] == [("19:00", "20:30")]


def test_an_unknown_time_of_day_is_refused():
    with pytest.raises(service.InvalidInput, match="when you study best"):
        service.analyse_calendar(None, start=date(2026, 9, 28), study_time="noon")
    with pytest.raises(service.InvalidInput, match="when you study best"):
        service.revise_subscription(_start(), study_time="noon", now=MORNING)


def _evening_share(subscription: service.Subscription) -> float:
    sessions = service.PlanReport.from_dict(subscription.plan).sessions
    late = [s for s in sessions if datetime.fromisoformat(s.start).astimezone(PARIS).hour >= 17]
    return len(late) / len(sessions)


def test_an_evening_student_s_sessions_are_in_the_evening():
    first = _start()
    assert first.options.get("study_time", "any") == "any"
    evening = service.revise_subscription(first, study_time="evening", now=MORNING)
    assert evening.options["study_time"] == "evening"
    assert _evening_share(first) < 0.1 < 0.8 < _evening_share(evening)
    # Kept when the calendar is read again for a longer horizon or the next refresh.
    later = service.refresh_subscription(evening, now=MORNING + timedelta(days=3), reread=False)
    assert later.options["study_time"] == "evening" and _evening_share(later) > 0.8


# --------------------------------------------------------------------------- #
# A3: exam practice sized per course, taking turns, mixed
# --------------------------------------------------------------------------- #


def _semester() -> tuple[service.CalendarReport, list[service.SubjectSpec]]:
    report = service.analyse_calendar(SEMESTER.read_bytes(), start=date(2026, 9, 29), tz="Europe/Paris")
    return report, [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]


def _practice(plan: service.PlanReport) -> list[service.SessionView]:
    return [s for s in plan.sessions if s.kind == "practice"]


def test_practice_is_1_5_hours_for_every_four_weeks_taught_and_at_least_3():
    sized = {w: service.practice_hours_for(w) for w in (0, 1, 4, 8, 9, 12, 13, 16, 17)}
    assert sized == {0: 4.5, 1: 3.0, 4: 3.0, 8: 3.0, 9: 4.5, 12: 4.5, 13: 6.0, 16: 6.0, 17: 7.5}


def test_a_course_taught_three_weeks_gets_less_practice_than_one_taught_eleven():
    report, subjects = _semester()
    exams = {a.subject: datetime.fromisoformat(a.when) for a in report.assessments}
    assert {service.weeks_taught(report, name, when) for name, when in exams.items()} == {11}
    # Algebra 3's lectures cut to its first three weeks: 3 hours, two blocks.
    algebra = sorted(
        (x for x in report.lectures if x.course == "Algebra 3"), key=lambda x: datetime.fromisoformat(x.end)
    )
    first = datetime.fromisoformat(algebra[0].end).date()
    short = [
        x
        for x in report.lectures
        if x.course != "Algebra 3" or (datetime.fromisoformat(x.end).date() - first).days < 21
    ]
    shorter = replace(report, lectures=tuple(short))
    assert service.weeks_taught(shorter, "Algebra 3", exams["Algebra 3"]) == 3
    plan = service.make_schedule(shorter, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=None)
    per_course = {name: sum(s.subject == name for s in _practice(plan)) for name in exams}
    assert per_course["Algebra 3"] == 2
    assert all(n == 3 for name, n in per_course.items() if name != "Algebra 3")
    assert plan.settings.preferences["practice_sized"]["Algebra 3"] == 3.0


def test_one_exam_can_have_its_own_practice():
    report, subjects = _semester()
    plan = service.make_schedule(
        report, subjects, weekly_hours=15, practice_hours=None, practice={"Algebra 3": 9, "Analysis 3": 0}
    )
    per_course = {s.name: sum(x.subject == s.name for x in _practice(plan)) for s in plan.subjects}
    assert per_course["Algebra 3"] == 6 and per_course["Analysis 3"] == 0
    assert {n for name, n in per_course.items() if name not in ("Algebra 3", "Analysis 3")} == {3}
    with pytest.raises(service.InvalidInput, match="0 to 40 hours"):
        service.make_schedule(report, subjects, practice={"Algebra 3": 41})


def test_exams_in_the_same_week_take_turns_and_each_is_practised_before_it():
    report, subjects = _semester()
    plan = service.make_schedule(report, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=4.5)
    practice = _practice(plan)
    assert len(practice) == 18
    order = [s.subject for s in practice]
    assert all(a != b for a, b in pairwise(order))  # never twice in a row
    exam = {s.name: datetime.fromisoformat(s.exam) for s in plan.subjects}
    assert all(datetime.fromisoformat(s.end) <= exam[s.subject] for s in practice)
    assert all("mixed" in s.detail for s in practice)


def test_the_nearest_exam_wins_once_it_falls_behind():
    blocks = [Block(i, i, i + 0.5) for i in range(12)]
    # Two exams a day apart with two blocks each: they alternate.
    turns = schedule(blocks, first_weekday=0, exams=[Exam("A", 10.0, 2), Exam("B", 11.0, 2)])
    assert [s.course for s in turns.sessions] == ["A", "B", "A", "B"]
    # Tomorrow's exam with two blocks left goes before one in three days with three.
    urgent = schedule(
        blocks[:3],
        first_weekday=0,
        exams=[Exam("A", 2.0, 2), Exam("B", 3.0, 3)],
        preferences=Preferences(practice_days=14),
    )
    assert [s.course for s in urgent.sessions] == ["A", "A", "B"]


# --------------------------------------------------------------------------- #
# B1: quick self-tests in the gaps, and on the way (D48)
# --------------------------------------------------------------------------- #


def _minutes(s: service.SessionView) -> int:
    return round((datetime.fromisoformat(s.end) - datetime.fromisoformat(s.start)) / timedelta(minutes=1))


def test_a_gap_is_an_hour_between_two_busy_times_one_a_day():
    day = [(8, 11), (12, 14), (15, 16), (16.5, 18)]  # free: 11-12 and 14-15 (an hour), 16-16:30
    events = [
        BusyEvent(
            datetime(2026, 9, 28, int(a), int(a % 1 * 60), tzinfo=PARIS),
            datetime(2026, 9, 28, int(b), int(b % 1 * 60), tzinfo=PARIS),
            "Class",
        )
        for a, b in day
    ]
    grid = availability_from_events(events, date(2026, 9, 28), 1, study_window=(8.0, 22.0))
    gaps = short_gaps(grid, block_slots=3)
    assert [(b.slot % 48 / 2, n) for b, n in gaps] == [(11.0, 2)]  # the first only; half an hour is not one


def test_quick_self_tests_fill_the_gaps_and_nothing_else():
    report = service.analyse_calendar(
        SEMESTER.read_bytes(), start=date(2026, 9, 29), tz="Europe/Paris", quick_tests=True
    )
    assert report.quick and all(b.quick == "gap" and b.minutes == 45 for b in report.quick)
    busy = [(datetime.fromisoformat(e.start), datetime.fromisoformat(e.end)) for e in report.events]
    blocks = [(datetime.fromisoformat(b.start), datetime.fromisoformat(b.end)) for b in report.blocks]
    for b in report.quick:
        lo, hi = datetime.fromisoformat(b.start), datetime.fromisoformat(b.end)
        assert not any(a < hi and lo < z for a, z in busy + blocks), b.start
    assert len({b.day for b in report.quick}) == len(report.quick)  # one a day at most
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    plan = service.make_schedule(report, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=4.5)
    quick = [s for s in plan.sessions if _minutes(s) == 45]
    assert quick and all(s.kind in ("review", "first review") for s in quick)
    assert all("quick self-test of 45 minutes" in s.detail for s in quick)
    # The week's budget is minutes now: 15 hours, whatever mix of blocks and quick tests.
    weeks: dict[int, int] = {}
    for s in plan.sessions:
        weeks[(plan.settings.start.weekday() + s.day) // 7] = weeks.get(
            (plan.settings.start.weekday() + s.day) // 7, 0
        ) + _minutes(s)
    assert max(weeks.values()) <= 15 * 60


def test_without_quick_self_tests_the_plan_is_as_before():
    report = service.analyse_calendar(SEMESTER.read_bytes(), start=date(2026, 9, 29), tz="Europe/Paris")
    assert report.quick == ()
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    # The benchmark's figures, made with FSRS-4.5 (benchmarks/rule_vs_planner.py, D52).
    plan = service.make_schedule(
        report, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=4.5, model="fsrs-4.5"
    )
    assert len(plan.sessions) == 174 and {_minutes(s) for s in plan.sessions} == {90}
    assert sum(s.topics_ready for s in plan.subjects) == 51


def test_on_the_way_only_when_the_student_says_so():
    ics = SEMESTER.read_bytes()
    trip = service.analyse_calendar(
        ics, start=date(2026, 9, 29), tz="Europe/Paris", days=14, commute_minutes=45
    )
    assert trip.quick == ()
    way = service.analyse_calendar(
        ics, start=date(2026, 9, 29), tz="Europe/Paris", days=14, commute_minutes=45, study_on_the_way=True
    )
    trips = [e for e in way.events if e.source == "commute"]
    assert way.quick and all(b.quick == "way" for b in way.quick)
    assert {(b.start, b.end) for b in way.quick} == {(e.start, e.end) for e in trips}
    short = service.analyse_calendar(
        ics, start=date(2026, 9, 29), tz="Europe/Paris", days=14, commute_minutes=20, study_on_the_way=True
    )
    assert short.quick == ()  # under half an hour: nothing to do on it


def test_new_plans_use_the_gaps_and_older_ones_keep_what_they_had():
    first = _start()
    assert first.options["quick_tests"] is True
    plan = service.PlanReport.from_dict(first.plan)
    assert any(_minutes(s) == 45 for s in plan.sessions)
    older = service.Subscription.from_dict(
        {**first.to_dict(), "options": {k: v for k, v in first.options.items() if k != "quick_tests"}}
    )
    again = service.refresh_subscription(older, now=MORNING, reread=False)
    assert all(_minutes(s) == 90 for s in service.PlanReport.from_dict(again.plan).sessions)
    off = service.revise_subscription(first, quick_tests=False, now=MORNING)
    assert all(_minutes(s) == 90 for s in service.PlanReport.from_dict(off.plan).sessions)


def _feed_times(plan: service.PlanReport) -> dict[str, tuple[datetime, datetime]]:
    from icalendar import Calendar

    cal = Calendar.from_ical(service.export_ics(plan).decode("utf-8"))
    return {str(e["uid"]): (e["dtstart"].dt, e["dtend"].dt) for e in cal.walk("VEVENT")}


def test_the_feed_has_each_session_at_its_own_times():
    """AUDIT.md item 60: the feed wrote every session as a block from its slot, so a
    quick self-test was 90 minutes there and a session the student moved was at
    23:30 the night before the plan began."""
    first = _start()
    plan = service.PlanReport.from_dict(first.plan)
    feed = _feed_times(plan)
    wanted = {(datetime.fromisoformat(s.start), datetime.fromisoformat(s.end)) for s in plan.sessions}
    assert set(feed.values()) == wanted
    assert any(end - start == timedelta(minutes=45) for start, end in feed.values())
    block = next(s for s in plan.sessions if _minutes(s) == 90)
    target = datetime.fromisoformat(block.start) + timedelta(days=1, hours=1)
    moved = service.PlanReport.from_dict(
        service.move_session(first, service.session_id(block), target.isoformat(), now=MORNING).plan
    )
    there = next(s for s in moved.sessions if s.slot < 0)
    assert (datetime.fromisoformat(there.start), datetime.fromisoformat(there.end)) in set(
        _feed_times(moved).values()
    )
    # Today's week counts each session's own length.
    view = service.today_view(first, MORNING)
    week = [
        s for s in plan.sessions if MORNING <= datetime.fromisoformat(s.start) < MORNING + timedelta(days=7)
    ]
    assert view["week"]["hours"] == round(sum(_minutes(s) for s in week) / 60, 1)


# --------------------------------------------------------------------------- #
# B2: how much an exam weighs, only between topics fading together (D49)
# --------------------------------------------------------------------------- #


def _faded_to(recall: float, day: float) -> MemoryState:
    """A memory whose predicted recall is `recall` `day` days after its review."""
    # R(t) = (1 + 19/81 t/S)^-0.5, so S = 19/81 t / (R^-2 - 1)
    return MemoryState(19 / 81 * day / (recall**-2 - 1), 5.0)


def test_a_weight_chooses_only_between_topics_fading_together():
    def pick(recalls: dict[str, float], weights: dict[str, float]) -> str:
        topics = [
            Topic(name, name, _faded_to(r, 10.0), 0.0, 0.0, 60.0, weight=weights.get(name, 1.0))
            for name, r in recalls.items()
        ]
        done = schedule([Block(0, 10, 10.0)], first_weekday=0, topics=topics)
        return done.sessions[0].title

    assert pick({"A": 0.85, "B": 0.87}, {}) == "A"  # the most faded, as before
    assert pick({"A": 0.85, "B": 0.87}, {"B": 2.0}) == "B"  # within the tie: the heavier
    assert pick({"A": 0.80, "B": 0.87}, {"B": 2.0}) == "A"  # beyond it: never passed over
    assert pick({"A": 0.85, "B": 0.87}, {"A": 0.5}) == "B"
    assert 0.87 - 0.85 < WEIGHT_TIE < 0.87 - 0.80


def _algebra_recall(plan: service.PlanReport) -> float:
    model = service.model_of(plan.settings)
    state = service._replay(plan.specs, plan.sessions, model)
    recall = [
        model.retrievability(max(s["exam_day"] - state[s["name"]][1], 0.0), state[s["name"]][0].stability)
        for s in plan.specs
        if s["subject"] == "Algebra 3"
    ]
    return sum(recall) / len(recall)


@pytest.mark.parametrize("hours", [12, 15, 18])
def test_an_exam_that_weighs_more_is_remembered_better_and_none_is_left_out(hours):
    report, subjects = _semester()

    def plan(weights=None):
        return service.make_schedule(
            report, subjects, weekly_hours=hours, rest_days=("Sun",), practice_hours=4.5, weights=weights
        )

    usual, more, less = plan(), plan({"Algebra 3": 2.0}), plan({"Algebra 3": 0.5})
    assert _algebra_recall(less) < _algebra_recall(usual) < _algebra_recall(more)
    for weighed in (more, less):
        reviewed = {s.subject for s in weighed.sessions if s.kind in ("review", "first review")}
        assert reviewed == {s.name for s in weighed.subjects}
    assert more.settings.preferences["weights"] == {"Algebra 3": 2.0}
    with pytest.raises(service.InvalidInput, match="weighs less, as usual or more"):
        plan({"Algebra 3": 3.0})


# --------------------------------------------------------------------------- #
# B3: a heavy week is more than one topic; a poor self-test asks for a block (D50)
# --------------------------------------------------------------------------- #


def _lectures(*hours: float) -> list[tuple[datetime, datetime]]:
    out, start = [], datetime(2026, 10, 5, 8, tzinfo=PARIS)
    for h in hours:
        out.append((start, start + timedelta(hours=h)))
        start += timedelta(days=1)
    return out


def test_a_week_over_three_hours_of_lectures_is_split_in_the_order_taught():
    assert [len(p) for p in service._week_parts(_lectures(1.5, 1.5))] == [2]
    assert [len(p) for p in service._week_parts(_lectures(1.5, 1.5, 1.5))] == [2, 1]
    assert [len(p) for p in service._week_parts(_lectures(1.5, 1.5, 1.5, 1.5))] == [2, 2]
    assert [len(p) for p in service._week_parts(_lectures(4.0))] == [1]  # one lecture: one topic
    parts = service._week_parts(_lectures(2, 2, 2, 2, 2))
    assert len(parts) == 4 and [x for p in parts for x in p] == _lectures(2, 2, 2, 2, 2)


def test_a_course_taught_six_hours_a_week_has_two_topics_a_week():
    report, subjects = _semester()
    algebra = [x for x in report.lectures if x.course == "Algebra 3"]
    later = [
        replace(
            x,
            start=(datetime.fromisoformat(x.start) + timedelta(days=1)).isoformat(),
            end=(datetime.fromisoformat(x.end) + timedelta(days=1)).isoformat(),
        )
        for x in algebra
    ]
    heavy = replace(report, lectures=report.lectures + tuple(later))
    plain = service.make_schedule(report, subjects, weekly_hours=15, practice_hours=4.5)
    split = service.make_schedule(heavy, subjects, weekly_hours=15, practice_hours=4.5)
    count = lambda plan, name: sum(s["subject"] == name for s in plan.specs)  # noqa: E731
    weeks = count(plain, "Algebra 3") - 1  # without "taught before"
    assert count(split, "Algebra 3") == 1 + 2 * weeks
    assert all(count(split, n) == count(plain, n) for n in {s.name for s in plain.subjects} - {"Algebra 3"})
    first, second = (
        s for s in split.specs if s["subject"] == "Algebra 3" and s["topic"].startswith("week of 05 Oct")
    )
    assert (first["topic"], second["topic"]) == ("week of 05 Oct (1 of 2)", "week of 05 Oct (2 of 2)")
    assert first["available_day"] < second["available_day"]
    assert "part 1 of 2" in first["note"]


def test_after_a_poor_self_test_the_next_one_takes_a_full_block():
    def topic(name: str, recall: float, full_next: bool = False) -> Topic:
        return Topic(name, name, _faded_to(recall, 10.0), 0.0, 0.0, 60.0, full_next=full_next)

    room = Quick(Block(10 * 48 + 22, 10, 10.0), 45)
    block = Block(10 * 48 + 30, 10, 10.2)
    plain = schedule([block], first_weekday=0, topics=[topic("A", 0.8), topic("B", 0.85)], quick=[room])
    assert [s.title for s in plain.sessions] == ["A", "B"]  # the quick one first, as usual
    poor = schedule(
        [block], first_weekday=0, topics=[topic("A", 0.8, full_next=True), topic("B", 0.85)], quick=[room]
    )
    assert [(s.title, s.block) for s in poor.sessions] == [("B", room.block), ("A", block)]


def test_a_forgotten_self_test_is_followed_by_a_full_block_in_the_plan():
    first = _start()
    plan = service.PlanReport.from_dict(first.plan)
    test = next(s for s in plan.sessions if s.kind in REVIEWS and _minutes(s) == 90)
    after = datetime.fromisoformat(test.end) + timedelta(hours=1)
    told = service.report_session(first, service.session_id(test), "forgot", now=after)
    later = service.PlanReport.from_dict(told.plan)
    following = [s for s in later.sessions if s.title == test.title]
    assert following and _minutes(following[0]) == 90


# --------------------------------------------------------------------------- #
# B4: a warm-up before today's lectures, opt-in (D51)
# --------------------------------------------------------------------------- #


def test_the_warm_up_lists_today_s_lectures_still_ahead_only_when_asked():
    first = _start()
    assert service.today_view(first, MORNING)["warm_up"] == []  # off unless asked
    on = service.revise_subscription(first, warm_up=True, now=MORNING)
    plan = service.PlanReport.from_dict(on.plan)
    courses = {s.name for s in plan.subjects}
    today = [
        e
        for e in plan.events
        if e.source == "calendar"
        and datetime.fromisoformat(e.start).astimezone(PARIS).date() == MORNING.astimezone(PARIS).date()
    ]
    warm = service.warm_ups(on, MORNING)
    assert warm and {w["course"] for w in warm} <= courses
    expected = sorted(
        datetime.fromisoformat(e.start).astimezone(PARIS).strftime("%H:%M")
        for e in today
        if service.course_of(e.summary) in courses and datetime.fromisoformat(e.start) > MORNING
    )
    assert [w["when"] for w in warm] == expected
    assert service.today_view(on, MORNING)["warm_up"] == warm
    # Once the day's last lecture has begun, nothing is left to warm up for.
    last = max(datetime.fromisoformat(e.start) for e in today)
    assert service.warm_ups(on, last) == []
    # Nothing is scheduled for it: the plan is the same with or without.
    assert [s.start for s in plan.sessions] == [
        s.start for s in service.PlanReport.from_dict(first.plan).sessions
    ]


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def web(tmp_path):
    clock = Clock(MORNING)
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.path = tmp_path
        client.clock = clock
        yield client


def _web_start(web, lang: str = "en") -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = web.post(
        "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
    )
    return answer.headers["location"].split("/")[2]


def _form(html: str) -> dict:
    """A form as a browser would submit it untouched (as in test_routine)."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), []).append(unescape(value.group(1)))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def test_setting_up_asks_when_the_student_studies_best(web):
    token = _web_start(web)
    page = unescape(web.get(f"/p/{token}/routine?new=1").text)
    assert "When do you study best?" in page
    assert re.findall(r'name="study_time" value="(\w+)"', page) == ["morning", "afternoon", "evening", "any"]
    assert 'value="any" checked' in page
    answer = web.post(
        f"/p/{token}/routine",
        data={"new": "1", "commute": "0", "study_time": "evening"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["study_time"] == "evening"
    assert 'value="evening" checked' in web.get(f"/p/{token}/routine").text


def test_settings_change_when_the_student_studies_best(web):
    token = _web_start(web)
    assert 'name="study_time"' not in web.get(f"/p/{token}/settings?new=1").text  # asked next
    page = web.get(f"/p/{token}/settings").text
    assert '<option value="any" selected>' in page
    fields = {**_form(page), "study_time": "afternoon"}
    saved = web.post(f"/p/{token}/settings", data=fields, follow_redirects=False)
    assert saved.status_code == 303, saved.text
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["study_time"] == "afternoon"
    refused = web.post(f"/p/{token}/settings", data={**fields, "study_time": "noon"})
    assert refused.status_code == 400 and "when you study best" in unescape(refused.text)


def test_the_question_is_asked_in_the_student_s_language(web):
    for lang, question, choice in (
        ("fr", "Quand travailles-tu le mieux ?", "Le soir"),
        ("it", "Quando studi meglio?", "Di sera"),
    ):
        token = _web_start(web, lang)
        page = unescape(web.get(f"/p/{token}/routine?new=1").text)
        assert question in page and choice in page


def test_settings_show_each_exam_s_practice_and_change_one(web):
    token = _web_start(web)
    page = unescape(web.get(f"/p/{token}/settings").text)
    assert '<option value="" selected>Sized by each course</option>' in page
    assert "Exam practice: 4.5 h" in page and 'name="s0_practice"' in page
    fields = {**_form(web.get(f"/p/{token}/settings").text), "s0_practice": "9.0"}
    name = fields["s0_name"]
    assert web.post(f"/p/{token}/settings", data=fields, follow_redirects=False).status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None
    assert stored.options["preferences"]["practice"] == {name: 9.0}
    assert stored.options["preferences"]["practice_hours"] is None
    plan = service.PlanReport.from_dict(stored.plan)
    assert sum(s.subject == name for s in _practice(plan)) == 6
    assert '<option value="9.0" selected>' in web.get(f"/p/{token}/settings").text
    refused = web.post(f"/p/{token}/settings", data={**fields, "s0_practice": "50"})
    assert refused.status_code == 400 and "0 to 40 hours" in unescape(refused.text)
    assert "Exam practice: 4.5 h" in unescape(refused.text)  # the other rows keep their default


def test_a_plan_made_before_sizing_keeps_its_hours(web):
    token = _web_start(web)
    service.update_subscription(
        web.path,
        token,
        lambda s: service.revise_subscription(s, preferences={"practice_hours": 4.5}, now=MORNING),
    )
    page = web.get(f"/p/{token}/settings").text
    assert '<option value="4.5" selected>' in page


# --------------------------------------------------------------------------- #
# A4: a weekly budget offered from what is kept
# --------------------------------------------------------------------------- #

OCTOBER_26 = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)  # a Monday, three full weeks in


def _weeks_kept(
    subscription: service.Subscription, keep: int, now: datetime = OCTOBER_26
) -> service.Subscription:
    """The plan's sessions of the three weeks before `now` reported: `keep` of every
    three done, the others skipped; then planned again from `now`."""
    plan = service.PlanReport.from_dict(subscription.plan)
    first = (now - timedelta(weeks=3)).astimezone(PARIS).date()
    behind = [
        s
        for s in plan.history + plan.sessions
        if first <= datetime.fromisoformat(s.start).astimezone(PARIS).date()
        and datetime.fromisoformat(s.end) <= now
    ]
    outcomes = {service.session_id(s): ("done" if i % 3 < keep else "skipped") for i, s in enumerate(behind)}
    return service.refresh_subscription(replace(subscription, outcomes=outcomes), now=now, reread=False)


def test_three_weeks_under_seventy_percent_bring_an_offer_of_the_hours_kept():
    sub = _weeks_kept(_start(), keep=1)
    offer = service.budget_offer(sub, OCTOBER_26)
    assert offer is not None
    assert all(k < service.BUDGET_KEPT_BELOW for k in offer["weeks"]) and len(offer["weeks"]) == 3
    assert offer["hours"] < 15 and offer["hours"] % 1.5 == 0 and offer["hours"] >= 3
    assert abs(offer["hours"] - offer["kept"]) <= 0.75 or offer["hours"] == 3
    # What it costs, said before anything changes: the plan predicts fewer topics.
    assert offer["ready"] <= offer["ready_now"] <= offer["topics"]
    assert service.PlanReport.from_dict(sub.plan).settings.preferences["weekly_hours"] == 15


def test_weeks_kept_well_or_not_reported_bring_no_offer():
    assert service.budget_offer(_weeks_kept(_start(), keep=3), OCTOBER_26) is None
    # Two of every three done: 70, 70 and 60% kept, and 70% is not under 70%.
    assert service.budget_offer(_weeks_kept(_start(), keep=2), OCTOBER_26) is None
    silent = service.refresh_subscription(_start(), now=OCTOBER_26, reread=False)
    assert service.budget_offer(silent, OCTOBER_26) is None  # nothing reported: Today asks instead
    early = _weeks_kept(_start(), keep=1, now=OCTOBER_26 - timedelta(weeks=1))
    assert service.budget_offer(early, OCTOBER_26 - timedelta(weeks=1)) is None  # two full weeks only


def test_the_offer_changes_the_budget_only_on_the_student_s_tap():
    sub = _weeks_kept(_start(), keep=1)
    offer = service.budget_offer(sub, OCTOBER_26)
    assert offer is not None
    later = service.budget_later(sub, OCTOBER_26)
    assert service.budget_offer(later, OCTOBER_26) is None
    assert service.budget_offer(later, OCTOBER_26 + timedelta(weeks=3)) is None
    taken = service.revise_subscription(sub, preferences={"weekly_hours": offer["hours"]}, now=OCTOBER_26)
    assert service.PlanReport.from_dict(taken.plan).settings.preferences["weekly_hours"] == offer["hours"]
    assert service.budget_offer(taken, OCTOBER_26) is None  # already that small


def test_today_offers_the_budget_and_one_tap_takes_it(web):
    token = _web_start(web)
    service.update_subscription(web.path, token, lambda s: _weeks_kept(s, keep=1))
    web.clock.now = OCTOBER_26
    today = unescape(web.get(f"/p/{token}").text)
    offer = service.budget_offer(service.load_subscription(web.path, token), OCTOBER_26)
    assert offer is not None and offer["hours"] == 4.5
    assert "Plan the hours you keep" in today and "Plan 4.5 hours a week" in today
    kept, planned = (service.format_number(offer[k]) for k in ("kept", "planned"))
    assert f"about {kept} of the {planned} hours planned each week" in today
    taken = web.post(f"/p/{token}/budget", data={"hours": "4.5"}, follow_redirects=False)
    assert taken.status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["preferences"]["weekly_hours"] == 4.5
    assert "Plan the hours you keep" not in unescape(web.get(f"/p/{token}").text)
    assert web.post(f"/p/{token}/budget", data={"hours": "lots"}).status_code == 400


def test_not_now_keeps_the_budget_and_the_offer_waits(web):
    token = _web_start(web)
    service.update_subscription(web.path, token, lambda s: _weeks_kept(s, keep=1))
    web.clock.now = OCTOBER_26
    assert web.post(f"/p/{token}/budget/later", follow_redirects=False).status_code == 303
    assert "Plan the hours you keep" not in unescape(web.get(f"/p/{token}").text)
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["preferences"]["weekly_hours"] == 15


def test_settings_switch_quick_self_tests_and_the_way(web):
    token = _web_start(web)
    page = web.get(f"/p/{token}/settings").text
    assert 'name="quick_tests" value="1" checked' in page
    fields = {k: v for k, v in _form(page).items() if k not in ("quick_tests", "study_on_the_way")}
    assert web.post(f"/p/{token}/settings", data=fields, follow_redirects=False).status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["quick_tests"] is False  # unticked
    fields.update({"quick_tests": "1", "study_on_the_way": "1", "commute": "45"})
    web.post(f"/p/{token}/settings", data=fields)
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["quick_tests"] and stored.options["study_on_the_way"]
    # A form that does not show the switch leaves it as it is.
    service.update_subscription(web.path, token, lambda s: service.revise_subscription(s, now=MORNING))
    assert service.load_subscription(web.path, token).options["quick_tests"] is True


def test_the_trip_question_asks_about_studying_on_the_way(web):
    token = _web_start(web)
    page = unescape(web.get(f"/p/{token}/routine?new=1").text)
    assert "I can study on the way (train, bus)" in page
    web.post(
        f"/p/{token}/routine",
        data={"new": "1", "commute": "45", "way_shown": "1", "study_on_the_way": "1"},
        follow_redirects=False,
    )
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["study_on_the_way"] is True
    plan = service.PlanReport.from_dict(stored.plan)
    trips = {(e.start, e.end) for e in plan.events if e.source == "commute"}
    on_the_way = [s for s in plan.sessions if (s.start, s.end) in trips]
    assert on_the_way and all("on the way" in s.detail for s in on_the_way)


def test_settings_give_an_exam_its_weight(web):
    token = _web_start(web)
    page = web.get(f"/p/{token}/settings").text
    assert 'name="s0_weight"' in page and '<option value="1.0" selected>Usual weight</option>' in page
    fields = {**_form(page), "s0_weight": "2.0"}
    name = fields["s0_name"]
    assert web.post(f"/p/{token}/settings", data=fields, follow_redirects=False).status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["preferences"]["weights"] == {name: 2.0}
    assert '<option value="2.0" selected>' in web.get(f"/p/{token}/settings").text
    web.post(f"/p/{token}/settings", data={**fields, "s0_weight": "1.0"})
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["preferences"]["weights"] == {}


def test_today_shows_the_warm_up_when_settings_ask_for_it(web):
    token = _web_start(web)
    assert "Warm up before today's lectures" not in unescape(web.get(f"/p/{token}").text)
    fields = {**_form(web.get(f"/p/{token}/settings").text), "warm_up": "1"}
    assert web.post(f"/p/{token}/settings", data=fields, follow_redirects=False).status_code == 303
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["warm_up"] is True
    today = unescape(web.get(f"/p/{token}").text)
    assert "Warm up before today's lectures" in today
    for w in service.warm_ups(stored, MORNING):
        assert f"<b>{w['when']}</b> {w['course']}" in today


# C1 · FSRS-6 for the product (D52) --------------------------------------------


def test_a_new_plan_names_fsrs6_and_an_older_one_keeps_fsrs45():
    sub = _start()
    plan = service.PlanReport.from_dict(sub.plan)
    assert plan.settings.preferences["model"] == "fsrs-6"
    assert service.model_of(plan.settings) is memory.FSRS_6
    # A plan made before D52 names no model: it was made, and is read, with FSRS-4.5.
    older = replace(
        plan.settings, preferences={k: v for k, v in plan.settings.preferences.items() if k != "model"}
    )
    assert service.model_of(older) is memory.FSRS_4_5
    # Its next refresh makes it again, from its history, with FSRS-6.
    again = service.refresh_subscription(
        service.Subscription.from_dict({**sub.to_dict(), "plan": replace(plan, settings=older).to_dict()}),
        now=MORNING,
    )
    assert service.PlanReport.from_dict(again.plan).settings.preferences["model"] == "fsrs-6"
    report = service.analyse_calendar(SEMESTER.read_bytes(), start=date(2026, 9, 29), tz="Europe/Paris")
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    with pytest.raises(service.InvalidInput, match="model"):
        service.make_schedule(report, subjects, model="fsrs-7")


def test_a_lecture_week_starts_in_each_models_own_state():
    for model in (memory.FSRS_4_5, memory.FSRS_6):
        assert service.familiarity_prior(service.LECTURE_FAMILIARITY, model) == model.initial_state(
            memory.Grade(service.LECTURE_FAMILIARITY)
        )


def test_the_fsrs6_numbers_quoted_are_the_benchmarks():
    spec = importlib.util.spec_from_file_location(
        "fsrs6_product", EXAMPLES.parent / "benchmarks" / "fsrs6_product.py"
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    rows = {(r["hours"], r["made"]): r for r in bench.results()["rows"]}
    six, old = rows[(15, "fsrs-6")], rows[(15, "fsrs-4.5")]
    readme = " ".join((EXAMPLES.parent / "README.md").read_text(encoding="utf-8").split())
    assert (
        f"the same week planned with it has {six['sessions']} sessions, {six['fsrs-6']['ready']} of the 69 "
        f"topics at 90% or more and the lowest recall at an exam {six['fsrs-6']['lowest']:.3f}; planned "
        f"with FSRS-4.5, {old['fsrs-6']['ready']} topics and {old['fsrs-6']['lowest']:.3f}"
    ) in readme
    decisions = " ".join((EXAMPLES.parent / "DECISIONS.md").read_text(encoding="utf-8").split())
    for hours in bench.HOURS:
        six, old = rows[(hours, "fsrs-6")], rows[(hours, "fsrs-4.5")]
        a, b = six["fsrs-6"], old["fsrs-6"]
        assert (
            f"| {hours} | {a['ready']} against {b['ready']}{' of 69' if hours == 12 else ''} | "
            f"{a['recall']:.3f} against {b['recall']:.3f} | {a['lowest']:.3f} against {b['lowest']:.3f} |"
        ) in decisions
    assert rows[(15, "fsrs-6")]["once"] == 0 and rows[(15, "fsrs-4.5")]["once"] == 2
