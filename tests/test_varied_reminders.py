"""
Reminders that vary (DECISIONS.md D129): a reminder's title stays the course and the
time, and its body is one of a few true lines in turn: the plan's own (the first
sentence of what to do), the stakes of a self-test (D128), that the session opens with
a question from the student's cards, which of the day's sessions it is. What must hold:
every line starts with the kind of session and its length; two sessions in a row with
the same lines do not say the same one; no line speaks of losing anything; nothing is
sent between going to bed and getting up, and a reminder still due then waits until the
student is up.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from cps import cards as flashcards
from cps import service
from cps.store import Store

pytest.importorskip("pywebpush")

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
PHONE = {
    "endpoint": "https://web.push.apple.com/QGuQyavXutnMCQBdj7NBqcv6",
    "keys": {
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        "auth": "tBHItJI5svbpez7KI4CCXg",
    },
}
KINDS = {"plan", "stakes", "question", "today"}


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


def _kind(body: str) -> str:
    if "stays above 90 %" in body:
        return "stakes"
    if "one question from your cards" in body:
        return "question"
    if "planned today" in body:
        return "today"
    return "plan"


def test_each_reminder_says_one_true_line_in_turn(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    gains = service.session_gains(plan)
    sessions = sorted(plan.sessions, key=lambda s: s.start)[:40]
    said = []
    for session in sessions:
        payload = service._reminder_payload(sub, session, store=tmp_path, plan=plan, gains=gains)
        lead = service.translate(service.KIND_LABELS[session.kind])
        assert payload["body"].startswith(f"{lead} · ")
        start = datetime.fromisoformat(session.start).astimezone(service._zone("Europe/Paris"))
        assert payload["title"].endswith(f" at {start:%H:%M}")  # the title does not vary
        lines = service._reminder_lines(sub, session, start, lead, store=tmp_path, plan=plan, gains=gains)
        said.append((_kind(payload["body"]), 1 + len(lines)))
    kinds = {k for k, _ in said}
    assert {"plan", "stakes", "today"} <= kinds <= KINDS, kinds
    # Two sessions in a row with as many lines to choose from never say the same kind.
    for (a, n), (b, m) in pairwise(said):
        if n == m > 1:
            assert a != b


def test_a_week_with_cards_says_the_session_opens_with_a_question(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(
        s for s in sorted(plan.sessions, key=lambda s: s.start) if s.kind in ("review", "first review")
    )
    start = datetime.fromisoformat(test.start).astimezone(service._zone("Europe/Paris"))
    week = service.session_card(sub, service.session_id(test), start)["week"]
    gains = service.session_gains(plan)
    before = service._reminder_lines(sub, test, start, "x", store=tmp_path, plan=plan, gains=gains)
    assert not any("one question from your cards" in line for line in before)
    flashcards.add(
        Store(tmp_path), sub.token, test.subject, [("What is a group?", "A set with…")], START, week=week
    )
    after = service._reminder_lines(sub, test, start, "x", store=tmp_path, plan=plan, gains=gains)
    assert "x. It opens with one question from your cards." in after
    assert any(line.startswith("x. Recalled now, it stays above 90 % until ") for line in after)
    # The card's own words never reach the lock screen.
    assert not any("group" in line for line in after)


def test_no_line_speaks_of_losing_anything(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    gains = service.session_gains(plan)
    for session in plan.sessions:
        body = service._reminder_payload(sub, session, store=tmp_path, plan=plan, gains=gains)["body"].lower()
        for word in ("streak", "lose", "lost", "miss", "don't", "last chance", "hurry"):
            assert word not in body, (word, body)


def test_the_hours_awake_are_those_of_the_students_day(tmp_path):
    sub = _plan(tmp_path)
    paris = service._zone("Europe/Paris")

    def at(day: service.Subscription, clock: str) -> bool:
        hour, minute = (int(x) for x in clock.split(":"))
        return service.awake(day, datetime(2026, 10, 5, hour, minute, tzinfo=paris))

    day = replace(sub, options={**sub.options, "wake": "07:00", "bed": "23:00"})
    assert [at(day, c) for c in ("06:59", "07:00", "22:59", "23:00", "02:00")] == [
        False,
        True,
        True,
        False,
        False,
    ]
    late = replace(sub, options={**sub.options, "wake": "09:00", "bed": "01:00"})  # to bed after midnight
    assert [at(late, c) for c in ("08:59", "09:00", "23:30", "00:30", "01:00")] == [
        False,
        True,
        True,
        True,
        False,
    ]


def test_a_reminder_due_before_getting_up_waits_until_then(tmp_path):
    sub = _plan(tmp_path)
    paris = service._zone("Europe/Paris")
    first = sorted(service.PlanReport.from_dict(sub.plan).sessions, key=lambda s: s.start)[0]
    start = datetime.fromisoformat(first.start).astimezone(paris)
    wake = start - timedelta(minutes=4)
    service.update_subscription(
        tmp_path,
        sub.token,
        lambda s: replace(s, options={**s.options, "wake": f"{wake:%H:%M}", "bed": "23:30"}),
    )
    service.add_push_device(tmp_path, sub.token, PHONE, now=START)
    sent: list[dict] = []

    def send(device, payload, key, *, subject, ttl):
        sent.append(payload)
        return 201

    assert service.send_due_reminders(tmp_path, start - timedelta(minutes=8), send=send) == 0  # asleep
    assert service.send_due_reminders(tmp_path, start - timedelta(minutes=3), send=send) == 1  # up: sent
    assert sent[0]["tag"] == service.session_id(first)
    assert service.send_due_reminders(tmp_path, start - timedelta(minutes=2), send=send) == 0  # once
