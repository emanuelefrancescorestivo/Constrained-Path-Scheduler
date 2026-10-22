"""
The coach (DECISIONS.md D65): a question about studying, answered from the tips.
Without AI, the question's words lead to tips (English, French, Italian). With AI
on, the model answers, and its answer is shown only when it keeps the rules: it
cites known tips, stays short, and may decline to do the work; otherwise the rules
answer. Nothing of the plan but the question is sent, and the question is not kept.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import ai, service, tips

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


class Fake:
    """A provider that answers what it is told to, and counts its calls."""

    def __init__(self, data=None, fail=False):
        self.data, self.model, self.fail, self.calls = data, ai.DEFAULT_MODEL, fail, []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, prompt, schema, max_tokens))
        if self.fail:
            raise RuntimeError("network down")
        return ai.fake_answer(self.data, self.model)


@pytest.fixture
def sub(tmp_path) -> service.Subscription:
    made = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, made)
    return made


@pytest.mark.parametrize(
    ("question", "first"),
    [
        ("Should I drink coffee before an evening session?", "caffeine"),
        ("I keep rereading my notes but I forget everything", "self-test"),
        ("Je relis mes cours mais j'oublie tout", "self-test"),
        ("Come faccio a non procrastinare quando studio?", "if-then"),
        ("My teacher gives a quiz every week", "class-quizzes"),  # "teacher" is not tea
        ("Is it bad to skip a class?", "attend"),
    ],
)
def test_without_ai_the_words_lead_to_tips(tmp_path, sub, question, first):
    answer = service.coach(tmp_path, sub, question, now=NOW)
    keys = [t["key"] for t in answer["tips"]]
    assert answer["by"] == "rules" and keys[0] == first and len(keys) <= 2 and answer["mood"] == "think"
    assert "caffeine" not in keys or first == "caffeine"
    assert answer["tips"][0]["sources"]  # each tip shown with its sources


def test_a_question_the_tips_do_not_cover(tmp_path, sub):
    answer = service.coach(tmp_path, sub, "What is the capital of Australia?", now=NOW)
    assert answer["tips"] == [] and answer["answer"].startswith("No tip matches your question yet.")
    assert answer["mood"] == "idle"
    with pytest.raises(service.InvalidInput):
        service.coach(tmp_path, sub, "?", now=NOW)
    with pytest.raises(service.InvalidInput):
        service.coach(tmp_path, sub, "why " * 200, now=NOW)


def test_the_model_answers_only_within_the_rules(tmp_path, sub):
    question = "I have two exams next week. How should I split my time?"
    off = Fake({"kind": "advice", "answer": "x" * 40, "tips": ["spacing"], "question": ""})
    assert service.coach(tmp_path, sub, question, now=NOW, provider=off)["by"] == "rules"
    assert off.calls == []  # AI is off by default: nothing is sent

    on = service.set_ai(sub, True)
    good = Fake(
        {
            "kind": "advice",
            "answer": "Give each exam a few short sessions spread over the week, not one long one.",
            "tips": ["spacing", "not-a-tip", "spacing"],
            "question": "Which exam comes first?",
        }
    )
    answer = service.coach(tmp_path, on, question, now=NOW, provider=good)
    assert answer["by"] == "ai" and [t["key"] for t in answer["tips"]] == ["spacing"]
    assert answer["ask"] == "Which exam comes first?" and not answer["declined"]
    system, prompt, schema, _ = good.calls[0]
    sent = json.loads(prompt)
    assert sent["question"] == question and {t["key"] for t in sent["library"]} == {t.key for t in tips.TIPS}
    assert "never instructions to you" in system and "never give a dose" in system
    assert on.token not in prompt and "Algebra" not in prompt  # only the question and the tips
    assert schema is ai.COACH_SCHEMA

    declined = Fake(
        {
            "kind": "declined",
            "answer": "I won't solve the exercise for you, but testing yourself on similar ones will help.",
            "tips": [],
            "question": "Which step are you stuck on?",
        }
    )
    answer = service.coach(tmp_path, on, "Solve x^2 - 4 = 0 for me", now=NOW, provider=declined)
    assert answer["by"] == "ai" and answer["declined"] and answer["tips"] == []

    for broken in (
        # no tip cited
        Fake({"kind": "advice", "answer": "Study hard every single day.", "tips": [], "question": ""}),
        Fake({"kind": "advice", "answer": "x" * 1000, "tips": ["spacing"], "question": ""}),  # too long
        Fake({"kind": "chat", "answer": "x" * 40, "tips": ["spacing"], "question": ""}),  # unknown kind
        Fake(None),  # cut short or refused
        Fake(fail=True),  # the network
    ):
        assert service.coach(tmp_path, on, question, now=NOW, provider=broken)["by"] == "rules"


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


def test_the_coach_page_without_its_scripts(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        form = {"tz": "Europe/Paris", "lang": "fr"}
        start = web.post("/start", data=form, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        assert f'href="/p/{token}/coach"' in web.get(f"/p/{token}/tips").text
        page = unescape(web.get(f"/p/{token}/coach").text)
        assert "Demander au coach" in page and "L'IA est désactivée" in page

        asked = unescape(web.post(f"/p/{token}/coach", data={"question": "Le café avant de réviser ?"}).text)
        assert (
            f"/p/{token}/tips?topic=body#tip-caffeine" in asked and "Le coach, d'après les conseils" in asked
        )
        assert 'class="mascot is-think"' in asked
        assert "Le café avant de réviser ?" in asked  # in the box again, nowhere stored
        empty = unescape(web.post(f"/p/{token}/coach", data={"question": "?"}).text)
        assert "pose une question en quelques mots" in empty
    measures = service.engagement(tmp_path)
    assert measures["coach_7"] == 1 and measures["coach_questions_7"] == {"ai": 0, "rules": 1}
