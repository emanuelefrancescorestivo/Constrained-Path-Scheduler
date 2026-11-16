"""
Unannounced moments (DECISIONS.md D130): rare, true and never counted down to. An
answered self-test that takes its topic past a month of memory for the first time
blooms on its card; the first week in which every self-test the student answered was
recalled, at least three, is said on Today the week after, once. What must hold: the
bloom only on the answer that crosses the month, never on a forgotten one nor on a topic
already past it; the week only when it is the first such week, and only the week after.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")
SELF_TESTS = ("review", "first review")


def _plan(tmp_path: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 3", None, familiarity=3),
            service.SubjectSpec("Advanced Statistics", None, familiarity=3),
        ],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )
    service.save_subscription(tmp_path, service.finish_first_run(sub))
    return service.load_subscription(tmp_path, sub.token)


def _monday(session: service.SessionView) -> date:
    day = datetime.fromisoformat(session.start).astimezone(PARIS).date()
    return day - timedelta(days=day.weekday())


def _answer_until(sub, before: date, answer_for) -> service.Subscription:
    """Answer each self-test planned before `before`, in order, as `answer_for(session)`
    says, a minute after it ends, as a student would."""
    done: set[str] = set()
    while True:
        plan = service.PlanReport.from_dict(sub.plan)
        todo = sorted(
            (
                s
                for s in (*plan.history, *plan.sessions)
                if s.kind in SELF_TESTS and not s.grade and _monday(s) < before
            ),
            key=lambda s: s.start,
        )
        todo = [s for s in todo if service.session_id(s) not in done]
        if not todo:
            return sub
        session = todo[0]
        sid = service.session_id(session)
        done.add(sid)
        end = datetime.fromisoformat(session.end) + timedelta(minutes=1)
        sub = service.report_session(sub, sid, answer_for(session), now=end)


def test_a_recall_that_takes_a_topic_past_a_month_blooms_once():
    now = datetime(2026, 10, 3, 9, 0, tzinfo=PARIS)
    gain = {
        "chance": 85,
        "same_day": False,
        "added": 20.0,
        "stability": 35.0,
        "holds": date(2026, 11, 7),
        "next": None,
    }
    assert service._memory_words(gain, "most", PARIS, now)["month"] is True  # 15 days before, 35 after
    assert (
        service._memory_words({**gain, "chance": 40}, "all", PARIS, now)["month"] is True
    )  # against the odds too
    assert service._memory_words({**gain, "stability": 29.0}, "most", PARIS, now)["month"] is False  # not yet
    assert (
        service._memory_words({**gain, "stability": 60.0}, "most", PARIS, now)["month"] is False
    )  # past it before
    assert service._memory_words(gain, "forgot", PARIS, now)["month"] is False  # a lapse never blooms
    same_day = service._memory_words({**gain, "same_day": True}, "most", PARIS, now)
    assert same_day["month"] is False


def test_a_plan_followed_blooms_on_the_answers_that_cross_a_month(tmp_path):
    sub = _plan(tmp_path)
    sub = _answer_until(sub, date(2026, 11, 30), lambda s: "most")
    plan = service.PlanReport.from_dict(sub.plan)
    when = datetime(2026, 11, 30, 12, 0, tzinfo=PARIS)
    cards = [
        service.session_card(sub, service.session_id(s), when)
        for s in plan.history
        if s.kind in SELF_TESTS and s.grade
    ]
    bloomed = [c for c in cards if c["memory"] and c["memory"]["month"]]
    assert bloomed, "no answer took a topic past a month in nine weeks"
    for card in bloomed:
        gain = card["gain"]
        assert gain["stability"] - gain["added"] < service.MONTH_DAYS <= gain["stability"]
    # Each topic blooms once at most.
    topics = [c["title"] + c["topic"] for c in bloomed]
    assert len(topics) == len(set(topics))


def test_the_first_week_without_forgetting_is_said_the_week_after_only(tmp_path):
    sub = _plan(tmp_path)
    first = date(2026, 10, 5)
    # The week of 5 October has one forgotten; the week of 12 October none.
    forgot = {"done": False}

    def answer(session):
        if _monday(session) == first and not forgot["done"]:
            forgot["done"] = True
            return "forgot"
        return "most"

    sub = _answer_until(sub, date(2026, 10, 19), answer)
    assert forgot["done"]
    tuesday = datetime(2026, 10, 20, 9, 0, tzinfo=PARIS)
    said = service.recall_week(sub, tuesday)
    plan = service.PlanReport.from_dict(sub.plan)
    week = [s for s in plan.history if s.kind in SELF_TESTS and s.grade and _monday(s) == date(2026, 10, 12)]
    assert len(week) >= service.RECALL_WEEK_LEAST
    assert said == {"answered": len(week), "monday": "12 Oct"}
    assert service.recall_week(sub, tuesday + timedelta(days=7)) is None  # said that week only
    assert service.recall_week(sub, tuesday - timedelta(days=7)) is None  # the week before had a lapse


def test_a_later_week_is_not_a_first(tmp_path):
    sub = _answer_until(_plan(tmp_path), date(2026, 10, 19), lambda s: "most")
    assert service.recall_week(sub, datetime(2026, 10, 13, 9, 0, tzinfo=PARIS))["monday"] == "5 Oct"
    assert (
        service.recall_week(sub, datetime(2026, 10, 20, 9, 0, tzinfo=PARIS)) is None
    )  # 12 Oct is not a first


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    clock = {"now": START}
    config = Config(
        store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.clock = clock
        yield client


def test_today_says_the_week(web, tmp_path):
    sub = _answer_until(_plan(tmp_path), date(2026, 10, 12), lambda s: "most")
    service.save_subscription(tmp_path, sub)
    web.clock["now"] = datetime(2026, 10, 13, 7, 0, tzinfo=PARIS)
    panel = unescape(web.get(f"/p/{sub.token}/panel").text)
    assert "Last week, you recalled every self-test you answered." in panel
    assert "the week of 5 Oct: none forgotten." in panel


def test_the_answers_card_shows_the_bloom(web, tmp_path):
    sub = _answer_until(_plan(tmp_path), date(2026, 11, 30), lambda s: "most")
    service.save_subscription(tmp_path, sub)
    web.clock["now"] = when = datetime(2026, 11, 30, 12, 0, tzinfo=PARIS)
    plan = service.PlanReport.from_dict(sub.plan)
    bloomed = [
        s
        for s in plan.history
        if s.kind in SELF_TESTS
        and s.grade
        and (service.session_card(sub, service.session_id(s), when)["memory"] or {}).get("month")
    ]
    assert bloomed
    line = "A first for this topic: it now holds more than a month."
    assert line in unescape(web.get(f"/s/{sub.token}/{service.session_id(bloomed[0])}").text)
    others = [s for s in plan.history if s.kind in SELF_TESTS and s.grade and s not in bloomed]
    assert line not in unescape(web.get(f"/s/{sub.token}/{service.session_id(others[0])}").text)


def test_a_plan_followed_leaves_some_topic_below_90_each_week(tmp_path):
    """Why D130 rejected "a week with every topic above 90 %": followed exactly as
    planned, the sample semester never has such a week, so the moment would never come."""
    sub = service.refresh_subscription(
        _plan(tmp_path), now=datetime(2026, 11, 23, 6, 0, tzinfo=UTC), reread=False
    )
    plan = service.PlanReport.from_dict(sub.plan)
    model = service.model_of(plan.settings)
    start = plan.settings.start
    state = {
        s["name"]: (service.MemoryState(s["stability"], s["difficulty"]), s["last_review_day"])
        for s in plan.specs
    }
    taught = {s["name"]: s.get("available_day", 0.0) for s in plan.specs}
    reviews = sorted(
        (x for x in plan.history if x.title in state and x.outcome not in service._NO_MEMORY),
        key=lambda x: x.start_day,
    )

    def day_of(day: date) -> float:
        return service._days_after(start, datetime.combine(day, datetime.min.time(), tzinfo=PARIS))

    i, monday, weeks = 0, date(2026, 10, 5), 0
    while monday < date(2026, 11, 23):
        names = [n for n, d in taught.items() if d <= day_of(monday)]
        end = day_of(monday + timedelta(days=7))
        while i < len(reviews) and reviews[i].start_day <= end:
            memory, last = state[reviews[i].title]
            state[reviews[i].title] = (
                model.review(memory, reviews[i].start_day - last, service._grade(reviews[i])),
                reviews[i].start_day,
            )
            i += 1
        lowest = min(model.retrievability(max(end - state[n][1], 0.0), state[n][0].stability) for n in names)
        assert lowest < 0.90, (monday, lowest)
        weeks += 1
        monday += timedelta(days=7)
    assert weeks == 7
