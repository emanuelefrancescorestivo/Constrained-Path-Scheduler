"""
The stakes and a curious start (DECISIONS.md D128): before a self-test, one line says
its exam and how long a recall in this session keeps the topic above 90 %, by the
model; a planned self-test opens with one question from its week's own cards, and the
answer comes when the session is logged. What must hold: the stakes are true of the
plan (its exam day, its forecast) and gone once the session is answered; the question
is from the session's week, the card the model gives the least chance now among those
answered before, else the first written; the answer is not on the page while the timer
runs; the question can be set aside, and then nothing comes at the end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import cards as flashcards
from cps import service
from cps.i18n import format_date
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")


def _plan(tmp_path: Path) -> tuple[service.Subscription, service.SessionView]:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )
    service.save_subscription(tmp_path, service.finish_first_run(sub))
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(
        s for s in sorted(plan.sessions, key=lambda s: s.start) if s.kind in ("review", "first review")
    )
    return service.load_subscription(tmp_path, sub.token), test


def _before(test: service.SessionView) -> datetime:
    return datetime.fromisoformat(test.start) - timedelta(hours=1)


def test_the_stakes_are_the_exam_and_the_day_a_recall_keeps_it_until():
    gain = {"exam": date(2026, 10, 10), "holds": date(2026, 10, 6), "same_day": False}
    stakes = service._stakes(gain, None, date(2026, 10, 1))
    assert stakes is not None and stakes["days"] == 9 and not stakes["to_exam"]
    day = format_date(date(2026, 10, 6), "short")
    assert stakes["words"] == (
        f"Exam in 9 days. Recalled in this session, it stays above 90 % until {day} (a model's estimate)."
    )
    # The last review before the exam keeps it there: "until then".
    late = service._stakes({**gain, "holds": date(2026, 10, 12)}, None, date(2026, 10, 9))
    assert late is not None and late["to_exam"] and late["words"].startswith("Exam in 1 day. ")
    assert late["words"].endswith("until then (a model's estimate).")
    assert service._stakes({**gain, "holds": date(2026, 10, 12)}, None, date(2026, 10, 10))[
        "words"
    ].startswith("Exam today.")
    # None once answered, for a second pass on the same day, and without a forecast.
    assert service._stakes(gain, "most", date(2026, 10, 1)) is None
    assert service._stakes({**gain, "same_day": True}, None, date(2026, 10, 1)) is None
    assert service._stakes(None, None, date(2026, 10, 1)) is None


def test_a_self_test_states_its_stakes_from_the_plan_until_it_is_answered(tmp_path):
    sub, test = _plan(tmp_path)
    sid = service.session_id(test)
    card = service.session_card(sub, sid, _before(test))
    gain = card["gain"]
    stakes = card["stakes"]
    session_day = datetime.fromisoformat(test.start).astimezone(PARIS).date()
    assert stakes["days"] == (gain["exam"] - session_day).days > 0
    assert stakes["to_exam"] == (gain["holds"] >= gain["exam"])
    if not stakes["to_exam"]:
        assert format_date(gain["holds"], "short") in stakes["words"]
    answered = service.report_session(sub, sid, "most", now=datetime.fromisoformat(test.end))
    assert service.session_card(answered, sid, datetime.fromisoformat(test.end))["stakes"] is None


def test_the_opening_question_is_the_weeks_card_most_likely_forgotten(tmp_path):
    sub, test = _plan(tmp_path)
    sid = service.session_id(test)
    store = Store(tmp_path)
    week = service.session_card(sub, sid, _before(test))["week"]
    assert week
    assert service.opening_card(tmp_path, sub, sid, _before(test)) is None  # no cards yet
    other = (date.fromisoformat(week) + timedelta(days=7)).isoformat()
    flashcards.add(
        store, sub.token, "Algebra 3", [("Next week's question?", "Not this one.")], START, week=other
    )
    assert service.opening_card(tmp_path, sub, sid, _before(test)) is None  # another week's card
    first, second, third = flashcards.add(
        store,
        sub.token,
        "Algebra 3",
        [
            ("What is a group?", "A set with an associative law, an identity and inverses."),
            ("Rank?", "dim of image"),
            ("Kernel?", "what maps to 0"),
        ],
        START,
        week=week,
    )
    # None answered: the first written.
    assert service.opening_card(tmp_path, sub, sid, _before(test)) == first.id
    # One answered before comes ahead of those never answered, however well it went.
    flashcards.answer(store, sub.token, third.id, "easy", START)
    assert service.opening_card(tmp_path, sub, sid, _before(test)) == third.id
    # Among those answered, the one with the least chance of recall now.
    flashcards.answer(store, sub.token, second.id, "again", START)
    assert service.opening_card(tmp_path, sub, sid, _before(test)) == second.id
    # Only a self-test opens with a question.
    assert service.opening_card(tmp_path, sub, None, _before(test)) is None
    plan = service.PlanReport.from_dict(sub.plan)
    other_kind = next((s for s in plan.sessions if s.kind not in ("review", "first review")), None)
    if other_kind is not None:
        assert service.opening_card(tmp_path, sub, service.session_id(other_kind), _before(test)) is None


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


def test_the_stakes_on_today_and_the_question_from_start_to_log(web, tmp_path):
    sub, test = _plan(tmp_path)
    sid = service.session_id(test)
    web.clock["now"] = _before(test)
    card = service.session_card(sub, sid, _before(test))
    flashcards.add(
        Store(tmp_path),
        sub.token,
        "Algebra 3",
        [("What is a group?", "A set with an associative law, an identity and inverses.")],
        START,
        week=card["week"],
    )
    # Today's next session, and the focus page's offer, state the stakes.
    shown = service.today_view(sub, _before(test))
    nxt = shown.get("now") or shown["next"][0]
    assert nxt["id"] == sid and nxt["stakes"]
    assert nxt["stakes"]["words"] in unescape(web.get(f"/p/{sub.token}/panel").text)
    assert nxt["stakes"]["words"] in unescape(web.get(f"/p/{sub.token}/focus").text)
    # Started, the session opens with the week's card; its answer is kept for the end.
    started = web.post(f"/p/{sub.token}/focus/start", data={"sid": sid, "goal": "45"}, follow_redirects=False)
    assert started.status_code == 303
    page = unescape(web.get(f"/p/{sub.token}/focus").text)
    assert "Before your notes" in page and "What is a group?" in page
    assert "an identity and inverses" not in page
    assert nxt["stakes"]["words"] in page
    web.clock["now"] = _before(test) + timedelta(minutes=50)
    assert web.post(f"/p/{sub.token}/focus/finish", follow_redirects=False).status_code == 303
    log = unescape(web.get(f"/p/{sub.token}/log").text)
    assert "The question you started with" in log and "an identity and inverses" in log
    assert f"week={card['week']}" in log and "Practise this week's cards" in log
    # Set aside, the question brings nothing at the end.
    web.post(f"/p/{sub.token}/focus/cancel")
    web.post(f"/p/{sub.token}/focus/start", data={"sid": sid, "goal": "45"})
    skipped = web.post(f"/p/{sub.token}/focus/question/skip", follow_redirects=False)
    assert skipped.status_code == 303 and skipped.headers["location"] == f"/p/{sub.token}/focus"
    assert "What is a group?" not in unescape(web.get(f"/p/{sub.token}/focus").text)
    web.post(f"/p/{sub.token}/focus/finish")
    assert "The question you started with" not in unescape(web.get(f"/p/{sub.token}/log").text)
    # A timer started without a planned session asks nothing.
    web.post(f"/p/{sub.token}/focus/cancel")
    web.post(f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "45"})
    assert "Before your notes" not in unescape(web.get(f"/p/{sub.token}/focus").text)
