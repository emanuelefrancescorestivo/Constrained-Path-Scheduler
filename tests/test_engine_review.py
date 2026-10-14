"""
The engine review's changes (docs/ENGINE-REVIEW.md), each pinned by what it must do.

A1 (W4, DECISIONS.md D44): a self-test nobody reported within a day is "unknown":
still on the calendar and in the feed, but its topic gains no memory from it, the
plan brings the topic back as if it had not happened, and the exam forecast says how
many it is unsure of. Deadline work and exam practice are not affected.

A2 (W1, D45): "When do you study best?" Each day's free time is cut into blocks
nearest the part of the day the student prefers (earliest, as before, without a
preference), and half an hour after training or work stays free.
"""

from __future__ import annotations

import random
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service
from cps.calendar_io import BusyEvent, availability_from_events
from cps.plan import Block, tile_free_time
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
    config = Config(store=tmp_path, clock=Clock(MORNING), background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.path = tmp_path
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
        form[name] = (chosen.group(1) or chosen.group(2)) if chosen else ""
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
