"""
The engine review's changes (docs/ENGINE-REVIEW.md), each pinned by what it must do.

A1 (W4, DECISIONS.md D44): a self-test nobody reported within a day is "unknown":
still on the calendar and in the feed, but its topic gains no memory from it, the
plan brings the topic back as if it had not happened, and the exam forecast says how
many it is unsure of. Deadline work and exam practice are not affected.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
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
        forecast_silent[name]["if_stopped"] <= forecast_reported[name]["if_stopped"] for name in forecast_silent
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
