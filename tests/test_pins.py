"""
Sessions the student moves: they stay where they were put, the plan is made again
around them at once, and a move that makes no sense is refused with a reason.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris


@pytest.fixture(scope="module")
def planned() -> service.Subscription:
    return service.new_subscription(
        subjects=[
            service.SubjectSpec(n, None, familiarity=3)
            for n in ("Algebra 3", "Analysis 3", "Deep Learning 1")
        ],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        engine="assistant",
        tasks=[service.TaskSpec("Stats report", "2026-10-10 18:00", 6.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )


def _plan(sub):
    return service.PlanReport.from_dict(sub.plan)


def _free_evening(sub, day: date) -> datetime:
    """20:30 on `day`, checked to be free of timetable events."""
    moment = datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=20, minute=30)
    for e in _plan(sub).events:
        lo, hi = datetime.fromisoformat(e.start), datetime.fromisoformat(e.end)
        assert not (lo < moment + timedelta(hours=1.5) and moment < hi), "the sample is free then"
    return moment


def test_a_moved_review_stays_where_it_was_put(planned):
    review = next(s for s in _plan(planned).sessions if s.kind in ("review", "first review"))
    old = datetime.fromisoformat(review.start)
    target = _free_evening(planned, old.date() + timedelta(days=2))
    moved = service.move_session(
        planned, service.session_id(review), target.replace(tzinfo=None).isoformat(), now=NOW
    )
    plan = _plan(moved)
    pinned = [s for s in plan.sessions if s.pinned]
    assert [(s.title, datetime.fromisoformat(s.start)) for s in pinned] == [(review.title, target)]
    assert pinned[0].rationale == "You put it here."
    # Not planned again between where it was and where it went.
    between = [
        s
        for s in plan.sessions
        if s.title == review.title and old <= datetime.fromisoformat(s.start) < target
    ]
    assert between == []
    assert moved.options["pins"][0]["from"] == review.start


def test_moving_it_again_keeps_where_it_first_was(planned):
    review = next(s for s in _plan(planned).sessions if s.kind in ("review", "first review"))
    first = _free_evening(planned, datetime.fromisoformat(review.start).date() + timedelta(days=2))
    moved = service.move_session(planned, service.session_id(review), first, now=NOW)
    again = next(s for s in _plan(moved).sessions if s.pinned)
    second = first + timedelta(days=1)
    twice = service.move_session(moved, service.session_id(again), second, now=NOW)
    assert len(twice.options["pins"]) == 1 and twice.options["pins"][0]["from"] == review.start


def test_unpinning_gives_it_back_to_the_planner(planned):
    review = next(s for s in _plan(planned).sessions if s.kind in ("review", "first review"))
    target = _free_evening(planned, datetime.fromisoformat(review.start).date() + timedelta(days=2))
    moved = service.move_session(planned, service.session_id(review), target, now=NOW)
    pinned = next(s for s in _plan(moved).sessions if s.pinned)
    back = service.unpin_session(moved, service.session_id(pinned), now=NOW)
    assert back.options["pins"] == [] and not any(s.pinned for s in _plan(back).sessions)
    assert [(s.title, s.start) for s in _plan(back).sessions] == [
        (s.title, s.start) for s in _plan(planned).sessions
    ]


def test_a_moved_block_of_work_counts_and_is_not_added_again(planned):
    work = [s for s in _plan(planned).sessions if s.kind == "task"]
    target = _free_evening(planned, date(2026, 10, 3))
    moved = service.move_session(planned, service.session_id(work[0]), target, now=NOW)
    after = [s for s in _plan(moved).sessions if s.kind == "task"]
    assert len(after) == len(work) and sum(s.pinned for s in after) == 1


@pytest.mark.parametrize(
    ("where", "why"),
    [
        (lambda s, e: e.start, "that time is taken"),
        (lambda s, e: "2026-09-28T20:00", "already past"),
        (lambda s, e: "2027-06-01T20:00", "after the end of your plan"),
    ],
)
def test_a_move_that_makes_no_sense_is_refused(planned, where, why):
    session = _plan(planned).sessions[0]
    lecture = next(
        e
        for e in _plan(planned).events
        if datetime.fromisoformat(e.start) > datetime.fromisoformat(session.start)
    )
    with pytest.raises(service.InvalidInput, match=why):
        service.move_session(planned, service.session_id(session), where(session, lecture), now=NOW)


def test_work_cannot_be_moved_past_its_deadline_nor_a_started_session(planned):
    work = next(s for s in _plan(planned).sessions if s.kind == "task")
    with pytest.raises(service.InvalidInput, match="after it is due"):
        service.move_session(
            planned, service.session_id(work), _free_evening(planned, date(2026, 10, 12)), now=NOW
        )
    started = datetime.fromisoformat(work.start).astimezone(UTC) + timedelta(minutes=5)
    with pytest.raises(service.InvalidInput, match="already started"):
        service.move_session(
            planned, service.session_id(work), _free_evening(planned, date(2026, 10, 3)), now=started
        )


def test_a_pin_survives_a_refresh_and_is_dropped_once_behind(planned):
    review = next(s for s in _plan(planned).sessions if s.kind in ("review", "first review"))
    target = _free_evening(planned, datetime.fromisoformat(review.start).date() + timedelta(days=2))
    moved = service.move_session(planned, service.session_id(review), target, now=NOW)
    kept = service.refresh_subscription(moved, now=NOW + timedelta(hours=1), reread=False)
    assert kept.options["pins"] and any(s.pinned for s in _plan(kept).sessions)
    later = service.refresh_subscription(moved, now=target.astimezone(UTC) + timedelta(hours=3), reread=False)
    assert later.options["pins"] == []
    done = [
        s
        for s in _plan(later).history
        if s.title == review.title and datetime.fromisoformat(s.start) == target
    ]
    assert len(done) == 1
