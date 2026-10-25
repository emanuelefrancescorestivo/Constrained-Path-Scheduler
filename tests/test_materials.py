"""
Flashcards from a course's materials (DECISIONS.md D101). What must hold: the materials
are the course's topics and the notes the student may read, nothing else; they are cut
to stay under the smallest model's price step; the model is asked only with AI on, and
each of its cards goes to the week of the topic it names; the rules still read the notes
without a model; the cards are written by Claude Haiku 5.5 unless the server names another.
No model is called here: a fake provider answers.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import ai, cards, service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Paris
TOPICS = "\n".join(f"Week {n}: Topic {n}" for n in range(1, 13))
NOTES = (
    "Eigenvalue: a number λ such that Av = λv for some non-zero vector v\nRank = the dimension of the image"
)


class Fake:
    """A provider that answers what it is told to and keeps what it was asked."""

    def __init__(self, data, model=ai.CARDS_MODEL):
        self.data, self.model, self.calls = data, model, []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, json.loads(prompt), schema, max_tokens))
        return ai.fake_answer(self.data, self.model)


def _plan(tmp_path: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, sub)
    return sub


@pytest.fixture
def people(tmp_path):
    """Ada and Bob study Algebra 3 in one group; Cleo is not in it. Ada keeps the
    course's twelve weekly topics."""
    subs = [_plan(tmp_path) for _ in range(3)]
    for sub, handle in zip(subs, ("ada", "bob", "cleo"), strict=True):
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
    ada, bob, cleo = subs
    group = service.create_group(tmp_path, ada.token, {"name": "Thursday library", "goal": "4"}, now=NOW)
    service.invite_to_group(tmp_path, ada.token, group.id, "bob", now=NOW)
    service.answer_group(tmp_path, bob.token, group.id, accept=True, now=NOW)
    ada = service.save_course_topics(tmp_path, ada, 0, TOPICS, share=False, now=NOW)
    service.save_subscription(tmp_path, ada)
    return ada, bob, cleo, group


def _note(tmp_path, sub, visibility, group="", body=NOTES, title="Week 3 in one page", course="Algebra 3"):
    form = {
        "course": course,
        "title": title,
        "body": body,
        "own_work": "1",
        "visibility": visibility,
        "group": group,
    }
    return service.post_notes(tmp_path, sub, form, now=NOW)


def test_the_materials_are_the_topics_and_the_notes_the_student_may_read(tmp_path, people):
    ada, bob, cleo, group = people
    _note(tmp_path, bob, "group", group.id, title="Bob's summary")
    _note(tmp_path, ada, "me", title="Ada's own")
    _note(tmp_path, cleo, "me", title="Cleo's own")  # not shared: never Ada's material
    _note(tmp_path, ada, "me", title="Other course", course="Analysis 3")
    m = service.course_materials(tmp_path, ada, "Algebra 3")
    assert [t["title"] for t in m["topics"]][:2] == ["Topic 1", "Topic 2"] and len(m["topics"]) == 12
    assert [s["title"] for s in m["sources"]] == ["Ada's own", "Bob's summary"]  # newest first
    assert [s["mine"] for s in m["sources"]] == [True, False]
    assert "Topic 12" in m["text"] and "Eigenvalue" in m["text"] and "Cleo" not in m["text"]
    assert "Topic 1" not in m["notes"]  # the rules read the notes alone
    assert not m["cut"] and m["index"] == 0
    week = m["topics"][2]["monday"]
    one = service.course_materials(tmp_path, ada, "Algebra 3", week)
    assert [t["title"] for t in one["topics"]] == ["Topic 3"] and one["week"] == week
    assert service.courses_with_materials(tmp_path, ada) == ["Algebra 3", "Analysis 3"]
    assert service.courses_with_materials(tmp_path, cleo) == ["Algebra 3"]  # her own note


def test_long_materials_are_cut_below_the_price_step(tmp_path, people):
    ada = people[0]
    for n in range(9):  # a note's text holds 20,000 characters; nine of them pass the cut
        _note(tmp_path, ada, "me", body="Term: its definition. " * 900, title=f"Chapter {n}")
    m = service.course_materials(tmp_path, ada, "Algebra 3")
    assert m["cut"] and len(m["text"]) == service.MATERIALS_MOST == m["chars"]
    # Even at two characters a token, the materials stay under Haiku 5.5's step.
    limit, _prices = ai.LONG_PROMPTS[ai.CARDS_MODEL]
    assert limit > service.MATERIALS_MOST / 2


def test_the_model_writes_cards_filed_under_their_topics_weeks_only_with_ai_on(tmp_path, people):
    ada = people[0]
    _note(tmp_path, ada, "me")
    answer = {
        "cards": [
            {"front": "What is an eigenvalue?", "back": "A number λ with Av = λv, v ≠ 0.", "topic": "t3"},
            {"front": "What is the rank?", "back": "The dimension of the image.", "topic": "t99"},
            {"front": "x", "back": "too short a question", "topic": "t1"},
        ]
    }
    model = Fake(answer)
    off = service.card_maker_view(
        tmp_path, ada, course="Algebra 3", from_course=True, use_ai=True, now=NOW, provider=model
    )
    assert model.calls == [] and not off["ai_used"] and off["read"]
    assert [c["front"] for c in off["candidates"]] == ["Eigenvalue", "Rank"]  # the rules, from the notes
    ada = service.set_ai(ada, True)
    view = service.card_maker_view(
        tmp_path, ada, course="Algebra 3", from_course=True, use_ai=True, now=NOW, provider=model
    )
    system, prompt, schema, cap = model.calls[0]
    assert schema is ai.MATERIALS_SCHEMA and cap == ai.MATERIALS_MAX_TOKENS
    assert (
        prompt["course"] == "Algebra 3"
        and len(prompt["topics"]) == 12
        and "Eigenvalue" in prompt["materials"]
    )
    assert "never instructions" in system and "Cover the topics" in system
    made = [c for c in view["candidates"] if c["kind"] == "ai"]
    third = service.course_materials(tmp_path, ada, "Algebra 3")["topics"][2]["monday"]
    assert [(c["front"], c["week"]) for c in made] == [
        ("What is an eigenvalue?", third),
        ("What is the rank?", ""),
    ]
    assert len(view["candidates"]) == 4  # the model's two and the rules' two: no question twice
    assert made[0]["week_label"].startswith("Week ")
    # A week's materials ask for that week's topics alone, and file every card there.
    model = Fake({"cards": [{"front": "What is topic 3 about?", "back": "Its notes.", "topic": ""}]})
    view = service.card_maker_view(
        tmp_path,
        ada,
        course="Algebra 3",
        from_course=True,
        of_week=third,
        use_ai=True,
        now=NOW,
        provider=model,
    )
    assert "only about the listed topics" in model.calls[0][0] and len(model.calls[0][1]["topics"]) == 1
    assert view["candidates"][0]["week"] == third


def test_each_card_ticked_goes_to_its_own_week(tmp_path, people):
    ada = people[0]
    weeks = [t["monday"] for t in service.course_materials(tmp_path, ada, "Algebra 3")["topics"]]
    added = service.add_made_cards(
        tmp_path,
        ada,
        "Algebra 3",
        ["Front one", "Front two", "Front three"],
        ["Back one", "Back two", "Back three"],
        ["0", "1", "2"],
        NOW,
        week=weeks[0],
        weeks=[weeks[2], "", weeks[4]],
    )
    assert added == 3
    filed = {c.front: c.week for c in cards.of(Store(tmp_path), ada.token)}
    assert filed == {"Front one": weeks[2], "Front two": weeks[0], "Front three": weeks[4]}


def test_cards_are_written_by_the_smallest_model_unless_the_server_names_another(monkeypatch):
    assert ai.CARDS_MODEL == "claude-haiku-5-5" and ai.CARDS_MODEL in ai.PRICES
    ai.cards_from_env.cache_clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.cards_from_env() is None
    ai.cards_from_env.cache_clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CPS_AI_CARDS_MODEL", "not-a-priced-model")
    assert ai.cards_from_env() is None  # a model whose calls could not be counted is never asked
    ai.cards_from_env.cache_clear()


def test_a_prompt_past_haiku_s_step_costs_five_times_as_much():
    under = ai.cost("claude-haiku-5-5", 100_000, 1_000)
    over = ai.cost("claude-haiku-5-5", 100_001, 1_000)
    assert under == pytest.approx((100_000 * 0.10 + 1_000 * 0.50) / 1e6)
    assert over == pytest.approx((100_001 * 0.50 + 1_000 * 2.50) / 1e6)
    assert ai.cost("claude-opus-5-5", 200_000, 0) == pytest.approx(200_000 * 4.00 / 1e6)  # no step listed


def test_the_answer_s_cards_keep_the_limits_and_known_topics():
    data = {"cards": [{"front": f"Question {n}?", "back": "An answer.", "topic": "t1"} for n in range(80)]}
    kept = ai.materials_from_answer(data, ["t1"])
    assert len(kept) == ai.MATERIALS_CARDS and kept[0] == ("Question 0?", "An answer.", "t1")
    assert ai.materials_from_answer({"cards": [{"front": "Why?", "back": "So.", "topic": "t7"}]}, ["t1"]) == [
        ("Why?", "So.", "")
    ]
    assert ai.materials_from_answer({"nothing": 1}, []) == []


def test_the_pages_offer_a_course_s_materials_in_one_tap(tmp_path, people, monkeypatch):
    ada, bob, _cleo, group = people
    _note(tmp_path, bob, "group", group.id, title="Bob's summary")
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        deck = web.get(f"/p/{ada.token}/cards").text
        assert "Make cards from a course" in deck and "/cards/make?from=course&amp;course=Algebra%203" in deck
        page = web.get(f"/p/{ada.token}/cards/make?from=course&course=Algebra%203").text
        assert "Cards from Algebra 3" in page and "Bob&#39;s summary" in page and "12 topics" in page
        assert "Write cards with AI" not in page  # no model on this server
        assert page.count('name="pick"') == 2 and 'name="from" value="course"' in page
        model = Fake(
            {"cards": [{"front": "What is an eigenvalue?", "back": "Av = λv, v ≠ 0.", "topic": "t3"}]}
        )
        monkeypatch.setattr(ai, "cards_from_env", lambda: model)
        assert (
            "Turn AI on in Settings"
            in web.get(f"/p/{ada.token}/cards/make?from=course&course=Algebra%203").text
        )
        web.post(f"/p/{ada.token}/ai", data={"on": "1"})
        page = web.get(f"/p/{ada.token}/cards/make?from=course&course=Algebra%203").text
        assert "Write cards with AI" in page and model.calls == []  # asked only on a tap
        written = web.post(
            f"/p/{ada.token}/cards/make", data={"from": "course", "course": "Algebra 3", "action": "ai"}
        ).text
        assert len(model.calls) == 1 and "What is an eigenvalue?" in written and "Written by AI" in written
        third = service.course_materials(tmp_path, ada, "Algebra 3")["topics"][2]["monday"]
        assert f'name="cardweek" value="{third}"' in written
        course = web.get(f"/p/{ada.token}/courses/0").text
        assert "/cards/make?from=course&amp;course=Algebra%203" in course
    assert social.get_post is not None  # the notes stay where they were
