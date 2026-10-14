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

import random
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service
from cps.assistant import Exam, Preferences, schedule
from cps.calendar_io import BusyEvent, availability_from_events
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
    silent_state = service._replay(plan.specs, plan.history)
    reported_state = service._replay(plan_reported.specs, plan_reported.history)
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
    plan = service.make_schedule(report, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=4.5)
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
    assert "Practice: 4.5 h" in page and 'name="s0_practice"' in page
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
    assert "Practice: 4.5 h" in unescape(refused.text)  # the other rows keep their default


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
