"""
D84 and D85: flashcards made from a student's own notes, read by rules (and by a
model only when asked, with AI on), picked before any is kept; notes written as
text, shared with one study group, seen by its members only while the author is
one of them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import ai, cardmaker, cards, service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Paris

NOTES = """# Linear algebra, week 3

Eigenvalue: a number λ such that Av = λv for some non-zero vector v
Kernel – the set of vectors that A sends to zero
Rank = the dimension of the image

Properties of the determinant:
- det(AB) = det(A) det(B)
- swapping two rows changes the sign

A basis is a set of linearly independent vectors that spans the space. It is a minimal spanning set.
The **trace** is the sum of the diagonal entries.

Q: What does the rank-nullity theorem say?
A: rank(A) + nullity(A) = number of columns.

What is a diagonalisable matrix?
One similar to a diagonal matrix.

Note: exam on 12 January
Week 4: Inner products
See https://example.org/slides
"""


# -- the reader --------------------------------------------------------------------------


def _read(text: str) -> list[tuple[str, str, str]]:
    return [(c.kind, c.front, c.back) for c in cardmaker.read(text)]


def test_each_shape_of_notes_gives_its_card_in_the_notes_order():
    assert _read(NOTES) == [
        ("term", "Eigenvalue", "a number λ such that Av = λv for some non-zero vector v"),
        ("term", "Kernel", "the set of vectors that A sends to zero"),
        ("term", "Rank", "the dimension of the image"),
        (
            "list",
            "Properties of the determinant (2)",
            "det(AB) = det(A) det(B); swapping two rows changes the sign",
        ),
        ("term", "det(AB)", "det(A) det(B)"),
        ("definition", "Basis", "A basis is a set of linearly independent vectors that spans the space"),
        ("cloze", "The […] is the sum of the diagonal entries", "trace"),
        ("qa", "What does the rank-nullity theorem say?", "rank(A) + nullity(A) = number of columns"),
        ("qa", "What is a diagonalisable matrix?", "One similar to a diagonal matrix"),
    ]  # "It is a…", the note, the week and the link are not cards


def test_an_answer_keeps_its_own_list_and_an_anki_cloze_is_a_gap():
    text = "Q: Name the phases of mitosis\nA: four:\n- prophase\n- metaphase\n\n"
    text += "The {{c1::mitochondria}} is the powerhouse."
    assert _read(text) == [
        ("qa", "Name the phases of mitosis", "four: prophase; metaphase"),
        ("cloze", "The […] is the powerhouse", "mitochondria"),
    ]


def test_cards_come_once_both_ways_on_request_and_within_the_limit():
    assert len(cardmaker.read("Rank: the dimension\nRank: the dimension")) == 1
    both = cardmaker.both_ways(cardmaker.read("Rank: the dimension of the image\nQ: Why?\nA: Because."))
    assert [(c.front, c.back) for c in both] == [
        ("Rank", "the dimension of the image"),
        ("the dimension of the image", "Rank"),
        ("Why?", "Because"),
    ]
    many = "\n".join(f"Term {n}: meaning {n}" for n in range(300))
    assert len(cardmaker.read(many)) == cardmaker.MAX_CARDS


# -- the model's cards, kept to the flashcards' limits ----------------------------------------


def test_the_model_s_cards_are_kept_only_within_the_limits():
    data = {"cards": [{"front": "What is a basis?", "back": "A spanning independent set."}] * 2}
    data["cards"].append({"front": "?", "back": "too short a question"})
    data["cards"].append({"front": "Long", "back": "x" * 700})
    assert ai.cards_from_answer(data) == [("What is a basis?", "A spanning independent set.")]
    assert ai.cards_from_answer({"cards": "nonsense"}) == [] and ai.cards_from_answer({}) == []


# -- through the service -------------------------------------------------------------------


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
    """Ada, Bob and Cleo have handles; Ada and Bob study in one group."""
    subs = [_plan(tmp_path) for _ in range(3)]
    for sub, handle in zip(subs, ("ada", "bob", "cleo"), strict=True):
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
    ada, bob, _cleo = subs
    group = service.create_group(tmp_path, ada.token, {"name": "Thursday library", "goal": "4"}, now=NOW)
    service.invite_to_group(tmp_path, ada.token, group.id, "bob", now=NOW)
    service.answer_group(tmp_path, bob.token, group.id, accept=True, now=NOW)
    return (*subs, group)


def _note(tmp_path, sub, visibility, group="", body=NOTES):
    form = {
        "course": "Algebra 3",
        "title": "Week 3 in one page",
        "body": body,
        "own_work": "1",
        "visibility": visibility,
        "group": group,
    }
    return service.post_notes(tmp_path, sub, form, now=NOW)


def test_a_text_note_shared_with_a_group_is_seen_by_its_members_only(tmp_path, people):
    ada, bob, cleo, group = people
    note = _note(tmp_path, ada, "group", group.id)
    where = Store(tmp_path)
    assert note.data["group"] == group.id and note.data["body"].startswith("# Linear")
    assert social.may_see(where, bob.token, note) and not social.may_see(where, cleo.token, note)
    assert [n["id"] for n in service.group_view(tmp_path, bob, group.id, NOW)["notes"]] == [note.id]
    assert service.post_view(tmp_path, cleo, note.id, NOW) is None
    # Never in the feeds or the library, which are for followers and everyone.
    assert service.library_view(tmp_path, bob, now=NOW)["notes"] == []
    assert social.feed_explore(where, cleo.token) == [] and social.feed_following(where, bob.token) == []
    # Not a member, not a group: refused.
    with pytest.raises(service.InvalidInput):
        _note(tmp_path, cleo, "group", group.id)
    with pytest.raises(service.InvalidInput):
        _note(tmp_path, ada, "everyone", body="")  # neither text nor photos
    # The note leaves the group with its author.
    service.leave_group(tmp_path, ada.token, group.id)
    assert not social.may_see(where, bob.token, social.get_post(where, note.id))
    assert service.group_view(tmp_path, bob, group.id, NOW)["notes"] == []


def test_cards_are_made_from_a_note_the_student_may_read_and_added_when_ticked(tmp_path, people):
    ada, bob, cleo, group = people
    note = _note(tmp_path, ada, "group", group.id)
    view = service.card_maker_view(tmp_path, bob, note_id=note.id, now=NOW)
    assert view["note"]["title"] == "Week 3 in one page" and view["course"] == "Algebra 3"
    assert len(view["candidates"]) == 9 and view["candidates"][0]["word"] == "Term and definition"
    assert not view["ai_ready"]  # AI is off by default
    # Cleo is not in the group: no note, nothing read.
    assert service.card_maker_view(tmp_path, cleo, note_id=note.id, now=NOW)["candidates"] == []
    fronts = [c["front"] for c in view["candidates"]]
    backs = [c["back"] for c in view["candidates"]]
    assert service.add_made_cards(tmp_path, bob, "Algebra 3", fronts, backs, ["0", "6", "x"], NOW) == 2
    assert [c.front for c in cards.of(Store(tmp_path), bob.token)] == [
        "Eigenvalue",
        "The […] is the sum of the diagonal entries",
    ]
    with pytest.raises(service.InvalidInput):
        service.add_made_cards(tmp_path, bob, "Algebra 3", fronts, backs, [], NOW)


class _Model:
    model = ai.DEFAULT_MODEL

    def __init__(self, data):
        self.data, self.asked = data, 0

    def ask(self, system, prompt, schema, max_tokens):
        self.asked += 1
        return ai.fake_answer(self.data)


def test_the_model_reads_the_notes_only_when_asked_with_ai_on(tmp_path, people):
    ada = people[0]
    model = _Model({"cards": [{"front": "What is the trace?", "back": "The sum of the diagonal entries."}]})
    view = service.card_maker_view(tmp_path, ada, text=NOTES, use_ai=True, now=NOW, provider=model)
    assert model.asked == 0 and all(c["kind"] != "ai" for c in view["candidates"])  # AI off
    ada = service.set_ai(ada, True)
    view = service.card_maker_view(tmp_path, ada, text=NOTES, use_ai=True, now=NOW, provider=model)
    assert model.asked == 1 and view["ai_used"]
    # D101: a card also says its week, none for a note's cards (the form's week files them).
    assert view["candidates"][0] == {
        "front": "What is the trace?",
        "back": "The sum of the diagonal entries.",
        "kind": "ai",
        "word": "Written by AI",
        "week": "",
        "week_label": "",
    }
    assert len(view["candidates"]) == 10  # and the rules' nine
    broken = _Model({"cards": []})
    view = service.card_maker_view(tmp_path, ada, text=NOTES, use_ai=True, now=NOW, provider=broken)
    assert not view["ai_used"] and len(view["candidates"]) == 9


# -- the pages -------------------------------------------------------------------------------


def test_the_pages_make_cards_and_share_notes_with_a_group(tmp_path, people):
    ada, bob, _cleo, group = people
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        # D101: Flashcards offers a course's materials first, and pasting notes still.
        deck = web.get(f"/p/{bob.token}/cards").text
        assert "Make cards from a course" in deck and "Or paste your notes" in deck
        page = web.post(f"/p/{bob.token}/cards/make", data={"text": NOTES, "action": "read"}).text
        assert "Cards found" in page and page.count('name="pick"') == 9
        form = {"text": NOTES, "action": "add", "course": "Algebra 3", "front": ["Rank"], "back": ["dim"]}
        added = web.post(f"/p/{bob.token}/cards/make", data={**form, "pick": ["0"]}, follow_redirects=False)
        assert added.status_code == 303 and "added=1" in added.headers["location"]
        assert web.post(f"/p/{bob.token}/cards/make", data=form).status_code == 400  # nothing ticked
        form = web.get(f"/p/{ada.token}/notes/new?group={group.id}").text
        assert "A study group" in form and 'value="group" checked' in form
        sent = web.post(
            f"/p/{ada.token}/notes/new",
            data={
                "course": "Algebra 3",
                "title": "Week 3",
                "body": NOTES,
                "own_work": "1",
                "visibility": "group",
                "group": group.id,
            },
            follow_redirects=False,
        )
        assert sent.status_code == 303
        post = web.get(sent.headers["location"]).text
        assert "Make flashcards from these notes" in post and "Thursday library" in post
        assert "Week 3" in web.get(f"/p/{bob.token}/groups/{group.id}").text
