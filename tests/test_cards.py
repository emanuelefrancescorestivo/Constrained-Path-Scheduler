"""
Flashcards (DECISIONS.md D29): a student's own cards, scheduled by the planner's
FSRS-4.5 model one card at a time; "Again" back in the same sitting; at most
NEW_PER_DAY first showings a day; private to the student.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import cards, memory, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris


def _plan(tmp_path) -> service.Subscription:
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
def sub(tmp_path):
    return _plan(tmp_path)


def _one(tmp_path, sub, front="What is an eigenvalue?", course="Algebra 3", when=NOW) -> cards.Card:
    service.add_cards(
        tmp_path, sub, {"course": course, "front": front, "back": "A scalar λ with Av = λv."}, now=when
    )
    return next(c for c in cards.of(Store(tmp_path), sub.token) if c.front == front)


def test_a_card_follows_fsrs_from_its_first_answer(tmp_path, sub):
    card = _one(tmp_path, sub)
    assert card.new and card.due is None
    service.answer_card(tmp_path, sub, card.id, "good", now=NOW)
    first = cards.get(Store(tmp_path), sub.token, card.id)
    assert first.state() == memory.initial_state(memory.Grade.GOOD)
    # Stability is the interval at which recall falls to 90 %: a new card answered
    # "good" comes back after its initial stability, 3.7145 days (FSRS-4.5 default).
    due = datetime.fromisoformat(first.due)
    assert (due - NOW) / timedelta(days=1) == pytest.approx(3.7145, abs=1e-4)
    later = NOW + timedelta(days=4)
    service.answer_card(tmp_path, sub, card.id, "hard", now=later)
    second = cards.get(Store(tmp_path), sub.token, card.id)
    expected = memory.review(first.state(), 4.0, memory.Grade.HARD)
    assert second.state().stability == pytest.approx(expected.stability)
    assert second.state().difficulty == pytest.approx(expected.difficulty)
    assert second.reps == 2 and second.lapses == 0
    with Store(tmp_path).connection() as db:
        log = db.execute(
            "SELECT grade, elapsed FROM card_reviews WHERE card = ? ORDER BY at", (card.id,)
        ).fetchall()
    assert log == [(3, None), (2, pytest.approx(4.0))]


def test_again_brings_a_card_back_in_the_same_sitting(tmp_path, sub):
    card = _one(tmp_path, sub)
    service.answer_card(tmp_path, sub, card.id, "good", now=NOW)
    later = NOW + timedelta(days=5)
    service.answer_card(tmp_path, sub, card.id, "again", now=later)
    lapsed = cards.get(Store(tmp_path), sub.token, card.id)
    assert lapsed.lapses == 1 and datetime.fromisoformat(lapsed.due) == later + cards.RELEARN
    zone = service._zone("Europe/Paris")
    assert cards.queue(Store(tmp_path), sub.token, later + timedelta(minutes=5), zone) == []
    assert [c.id for c in cards.queue(Store(tmp_path), sub.token, later + timedelta(minutes=11), zone)] == [
        card.id
    ]
    # Minutes later, recall is near certain: FSRS-4.5 barely moves stability.
    service.answer_card(tmp_path, sub, card.id, "good", now=later + timedelta(minutes=11))
    again = cards.get(Store(tmp_path), sub.token, card.id)
    assert lapsed.stability < again.stability < 1.02 * lapsed.stability
    assert again.difficulty < lapsed.difficulty  # the answer still moves difficulty


def test_the_queue_puts_the_overdue_first_and_shows_twenty_new_cards_a_day(tmp_path, sub):
    zone = service._zone("Europe/Paris")
    store = Store(tmp_path)
    old = _one(tmp_path, sub, "Old", when=NOW - timedelta(days=10))
    older = _one(tmp_path, sub, "Older", when=NOW - timedelta(days=10))
    service.answer_card(tmp_path, sub, older.id, "good", now=NOW - timedelta(days=10))
    service.answer_card(tmp_path, sub, old.id, "good", now=NOW - timedelta(days=8))
    paste = "\n".join(f"Question {i}\tAnswer {i}" for i in range(25))
    assert service.add_cards(tmp_path, sub, {"course": "Analysis", "paste": paste}, now=NOW) == 25
    waiting = cards.queue(store, sub.token, NOW, zone)
    assert [c.front for c in waiting[:2]] == ["Older", "Old"]  # due first, the most overdue first
    assert len(waiting) == 2 + cards.NEW_PER_DAY and waiting[2].front == "Question 0"
    for c in waiting[2:7]:
        service.answer_card(tmp_path, sub, c.id, "good", now=NOW)
    assert sum(c.new for c in cards.queue(store, sub.token, NOW, zone)) == cards.NEW_PER_DAY - 5
    tomorrow = NOW + timedelta(days=1)
    assert sum(c.new for c in cards.queue(store, sub.token, tomorrow, zone)) == cards.NEW_PER_DAY
    view = service.cards_view(tmp_path, sub, NOW, course="analysis")
    assert [(d["course"], d["total"], d["due"], d["new"]) for d in view["decks"]] == [
        ("Algebra 3", 2, 2, 0),
        ("Analysis", 25, 0, 15),
    ]
    assert view["course"] == "Analysis" and len(view["cards"]) == 25 and view["today"] == 5
    assert view["cards"][0]["due"] == "in 4 days" and view["cards"][-1]["due"] is None
    assert service.cards_due(tmp_path, sub, NOW) == 2 + 15


def test_pasted_cards_and_what_is_refused(tmp_path, sub):
    assert cards.parse_paste("Q1\tA1\n\n  Q2 | A2 with | a bar  \n") == [
        ("Q1", "A1"),
        ("Q2", "A2 with | a bar"),
    ]
    with pytest.raises(cards.CardError, match="line 2: put a tab"):
        cards.parse_paste("Q1\tA1\nno separator here")
    for form, words in (
        ({"course": "", "front": "Q", "back": "A"}, "which course"),
        ({"course": "Algebra 3", "front": "", "back": "A"}, "write the question"),
        ({"course": "Algebra 3", "front": "Q", "back": " "}, "write the answer"),
        ({"course": "Algebra 3", "front": "Q" * 301, "back": "A"}, "at most 300 characters"),
        ({"course": "Algebra 3", "paste": "Q\tA\n" * 201}, "at most 200 cards at a time"),
        ({"course": "Algebra 3", "paste": "Q\tA\nQ\t"}, "write the answer"),
    ):
        with pytest.raises(service.InvalidInput, match=words):
            service.add_cards(tmp_path, sub, form, now=NOW)
    assert cards.of(Store(tmp_path), sub.token) == []  # nothing half-kept


def test_cards_are_private_and_go_with_the_plan(tmp_path, sub):
    card = _one(tmp_path, sub)
    other = _plan(tmp_path)
    assert service.card_view(tmp_path, other, card.id) is None
    with pytest.raises(service.InvalidInput, match="no longer, there"):
        service.answer_card(tmp_path, other, card.id, "good", now=NOW)
    assert not service.delete_card(tmp_path, other, card.id)
    with pytest.raises(service.InvalidInput, match="how well you recalled"):
        service.answer_card(tmp_path, sub, card.id, "perfect", now=NOW)
    service.edit_card(tmp_path, sub, card.id, {"course": "Algebra 3", "front": "Eigenvalue?", "back": "λ"})
    assert service.card_view(tmp_path, sub, card.id)["front"] == "Eigenvalue?"
    service.answer_card(tmp_path, sub, card.id, "easy", now=NOW)
    assert service.delete_subscription(tmp_path, sub.token)
    with Store(tmp_path).connection() as db:
        assert db.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM card_reviews").fetchone()[0] == 0


def test_the_review_page_says_when_each_answer_brings_the_card_back(tmp_path, sub):
    card = _one(tmp_path, sub)
    view = service.card_review_view(tmp_path, sub, NOW)
    assert view["card"]["id"] == card.id and view["left"] == 1
    assert [(o["grade"], o["when"]) for o in view["options"]] == [
        ("again", "in 10 minutes"),
        ("hard", "in 1 day"),
        ("good", "in 4 days"),
        ("easy", "in 14 days"),
    ]
    service.answer_card(tmp_path, sub, card.id, "good", now=NOW)
    done = service.card_review_view(tmp_path, sub, NOW)
    assert done["card"] is None and done["next"] == "in 4 days" and done["today"] == 1
    assert service.wait_words(timedelta(days=61)) == "in 2 months"
    assert service.wait_words(timedelta(hours=5)) == "in 5 hours"


def test_the_pilots_measures_count_card_answers(tmp_path, sub):
    card = _one(tmp_path, sub)
    service.answer_card(tmp_path, sub, card.id, "good", now=NOW)
    network = service.engagement(tmp_path, now=NOW + timedelta(hours=1))["network"]
    assert network["card_reviews"] == 1 and network["card_students"] == 1
