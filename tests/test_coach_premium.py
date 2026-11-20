"""
The coach on Haiku, its answers a day, premium codes, and the plan it is told of
(DECISIONS.md D176).

What must hold: the coach asks Claude Haiku unless the server names another model; a
student has five answers with AI a day (their own day, from midnight in the plan's time
zone), then the tips answer and the page says why; the coach's calls do not use up the
other features' day, nor theirs the coach's; a premium code, made by the owner and
shown once, is kept as a digest, gives two hundred a day, and is used no more times than
it allows; the coach is told the next exams, the topics the model gives the least
chance now and today's sessions, and nothing else of the plan: not the timetable's
rooms, not a personal event, not the plan's address.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import ai, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Thursday with a session at 08:00
QUESTION = "I have two exams next week. How should I split my time?"
GOOD = {
    "kind": "advice",
    "answer": "Give each exam a few short sessions spread over the week, not one long one.",
    "tips": ["spacing"],
    "question": "Which exam comes first?",
}


class Fake:
    """A provider that answers what it is told to, and counts its calls."""

    def __init__(self, data=None, model=ai.COACH_MODEL):
        self.data, self.model, self.calls = data, model, []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, prompt, schema, max_tokens))
        return ai.fake_answer(self.data, self.model)


def _plan(store, now=NOW) -> service.Subscription:
    made = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=datetime(2026, 9, 29, 6, 0, tzinfo=UTC),
    )
    on = service.set_ai(made, True)
    service.save_subscription(store, on)
    return on


def test_the_coach_runs_on_haiku_unless_the_server_names_another(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CPS_AI_COACH_MODEL", raising=False)
    monkeypatch.setenv("CPS_AI_MODEL", "not-a-priced-model")
    for cached in (ai.from_env, ai.coach_from_env):
        cached.cache_clear()
    try:
        assert ai.coach_from_env().model == "claude-haiku-5-5" == ai.COACH_MODEL
        assert ai.from_env() is None and service.ai_available()  # the coach's model is enough
        ai.coach_from_env.cache_clear()
        monkeypatch.setenv("CPS_AI_COACH_MODEL", "claude-sonnet-5-5")
        assert ai.coach_from_env().model == "claude-sonnet-5-5"
    finally:
        for cached in (ai.from_env, ai.coach_from_env):
            cached.cache_clear()


def test_five_answers_a_day_then_the_tips_and_other_features_keep_their_own_day(tmp_path):
    sub = _plan(tmp_path)
    model = Fake(GOOD)
    for left in range(service.COACH_FREE_A_DAY, 0, -1):
        assert service.coach_allowance(tmp_path, sub, NOW)["left"] == left
        answer = service.coach(tmp_path, sub, QUESTION, now=NOW, provider=model)
        assert answer["by"] == "ai" and not answer["spent"]
    spent = service.coach(
        tmp_path, sub, "I keep rereading my notes but I forget everything", now=NOW, provider=model
    )
    assert spent["by"] == "rules" and spent["spent"] and spent["tips"]  # the tips still answer
    assert len(model.calls) == service.COACH_FREE_A_DAY == 5
    store = Store(tmp_path)
    assert ai.calls_today(store, sub.token, NOW, "coach") == 5 and ai.calls_today(store, sub.token, NOW) == 0
    # The tasks' reader keeps its own day: the coach's calls did not use it, and its own
    # twenty do not use the coach's.
    task = Fake(
        {"name": "Essay", "due": "2026-10-12T23:59", "hours": 4, "course": ""}, model=ai.DEFAULT_MODEL
    )
    assert service.understand_task(tmp_path, sub, "essay due 12/10", now=NOW, provider=task)["by"] == "ai"
    # The student's day starts at midnight in Paris (22:00 UTC in October): five again.
    before, after = NOW + timedelta(hours=15, minutes=55), NOW + timedelta(hours=16, minutes=5)
    assert service.coach_allowance(tmp_path, sub, before)["left"] == 0
    assert service.coach_allowance(tmp_path, sub, after)["left"] == 5
    assert service.coach(tmp_path, sub, QUESTION, now=after, provider=model)["by"] == "ai"
    # AI off: nothing is sent and nothing is counted.
    off = service.set_ai(sub, False)
    assert service.coach(tmp_path, off, QUESTION, now=after, provider=model)["spent"] is False
    assert len(model.calls) == 6


def test_premium_codes_are_shown_once_kept_as_digests_and_used_no_more_than_they_allow(tmp_path):
    first, second, third = _plan(tmp_path), _plan(tmp_path), _plan(tmp_path)
    code = service.make_premium_code(tmp_path, "Analysis 3 class, pilot", 2, NOW)
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", code)
    kept = b"".join(p.read_bytes() for p in Path(tmp_path).rglob("*") if p.is_file())
    assert code.encode() not in kept and code.replace("-", "").encode() not in kept  # a digest only
    typed = " " + code.lower().replace("-", " ") + " "  # as a student may type it
    given = service.redeem_premium(tmp_path, first.token, typed, NOW)
    assert service.premium(given)["since"] == "2026-10-01T06:00:00+00:00"
    assert service.coach_allowance(tmp_path, given, NOW) == {
        "most": 200,
        "used": 0,
        "left": 200,
        "premium": True,
    }
    assert service.premium_view(given)["on"] and service.premium_view(given)["most"] == 200
    service.redeem_premium(tmp_path, first.token, code, NOW)  # premium already: no use taken
    assert service.premium_codes_view(tmp_path, NOW)["codes"][0]["uses"] == 1
    service.redeem_premium(tmp_path, second.token, code, NOW)
    with pytest.raises(service.InvalidInput, match="used as many times"):
        service.redeem_premium(tmp_path, third.token, code, NOW)
    with pytest.raises(service.InvalidInput, match="not known"):
        service.redeem_premium(tmp_path, third.token, "ABCD-EFGH-JKLM", NOW)
    with pytest.raises(service.InvalidInput, match="not known"):
        service.redeem_premium(tmp_path, third.token, "x", NOW)
    closed = service.make_premium_code(tmp_path, "One student", 5, NOW)
    ident = next(
        c["id"] for c in service.premium_codes_view(tmp_path, NOW)["codes"] if c["label"] == "One student"
    )
    assert service.close_premium_code(tmp_path, ident) and not service.close_premium_code(tmp_path, "nope")
    with pytest.raises(service.InvalidInput, match="used as many times"):
        service.redeem_premium(tmp_path, third.token, closed, NOW)
    assert not service.premium(service.load_subscription(tmp_path, third.token))
    for label, most in (("", 1), ("Someone", 0), ("Someone", 1001)):
        with pytest.raises(service.InvalidInput):
            service.make_premium_code(tmp_path, label, most, NOW)


def test_the_coach_is_told_the_plan_and_nothing_else(tmp_path):
    sub = _plan(tmp_path)
    model = Fake(GOOD)
    service.coach(tmp_path, sub, QUESTION, now=NOW, provider=model)
    system, prompt, _schema, _tokens = model.calls[0]
    plan = json.loads(prompt)["plan"]
    assert set(plan) == {"today", "exams", "weakest_topics", "today_sessions"}
    assert plan["today"] == "2026-10-01 (Thursday)"
    assert plan["exams"] == [{"course": "Algebra 3", "date": "2027-01-26", "days_left": 117}]
    assert plan["today_sessions"] == [
        {"time": "08:00-09:30", "kind": "First review", "of": "Algebra 3", "over": False}
    ]
    topics = plan["weakest_topics"]
    assert 1 <= len(topics) <= service.COACH_PLAN_TOPICS
    assert all(set(t) == {"course", "topic", "covers", "recall_now"} for t in topics)
    recalls = [int(t["recall_now"].removesuffix(" %")) for t in topics]
    assert recalls == sorted(recalls) and all(r % 5 == 0 for r in recalls)  # the least first, to 5 %
    assert "estimate" in system and "add nothing about the plan that is not there" in system
    for absent in (
        sub.token,
        "Salle",
        "Amphi",
        "Gym",
        "Analysis 3",
    ):  # rooms, a personal event, another course
        assert absent not in prompt


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402

ADMIN = "owner-key-" + "x" * 20


def test_the_pages_say_what_is_left_and_premium_comes_from_the_owners_page(tmp_path, monkeypatch):
    model = Fake(GOOD)
    monkeypatch.setattr(ai, "coach_from_env", lambda: model)
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, admin_token=ADMIN)
    with TestClient(create_app(config)) as web:
        token = _plan(tmp_path).token
        page = unescape(web.get(f"/p/{token}/coach").text)
        assert "5 answers with AI left today." in page and "what your plan says today" in page
        asked = unescape(web.post(f"/p/{token}/coach", data={"question": QUESTION}).text)
        assert "The coach, with AI" in asked and "4 answers with AI left today." in asked
        data = unescape(web.get(f"/p/{token}/settings/data").text)
        assert "Free: the coach answers with AI 5 times a day, then from the tips." in data
        assert 'action="/p/' + token + '/premium"' in data and "your next exams and their dates" in data

        assert web.get("/admin/wrong-key-" + "y" * 20 + "/premium").status_code == 404
        made = web.post(f"/admin/{ADMIN}/premium", data={"label": "Analysis 3 class, pilot", "most": "30"})
        assert made.status_code == 200
        code = re.search(r'class="premium-code"><b>([A-Z2-9-]{14})</b>', made.text).group(1)
        assert "0 of 30 used" in unescape(made.text)
        assert code not in web.get(f"/admin/{ADMIN}/premium").text  # shown once
        refused = web.post(f"/admin/{ADMIN}/premium", data={"label": "", "most": "many"})
        assert refused.status_code == 400

        wrong = web.post(f"/p/{token}/premium", data={"code": "ABCD-EFGH-JKLM"}, follow_redirects=False)
        assert wrong.status_code == 400 and "that code is not known" in unescape(wrong.text)
        used = web.post(f"/p/{token}/premium", data={"code": code}, follow_redirects=False)
        assert used.status_code == 303 and used.headers["location"].endswith("saved=premium#premium")
        data = unescape(web.get(f"/p/{token}/settings/data?saved=premium").text)
        assert "Premium is on" in data and "up to 200 times a day." in data and "Premium code" not in data
        assert "199 answers with AI left today." in unescape(web.get(f"/p/{token}/coach").text)
        owner = unescape(web.get(f"/admin/{ADMIN}/premium").text)
        assert "1 of 30 used" in owner and "The coach answered with AI 1 time." in owner
