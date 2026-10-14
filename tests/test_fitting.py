"""
A student's own FSRS-6 parameters (DECISIONS.md D53): the fitter (`cps.fitting`)
and the flashcards that use it. How many answers a fit needs is measured by
`benchmarks/personal_fsrs.py`, too slow for a test; here, the fitter's arithmetic,
its prior, and the product's path from answers to a fit.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from random import Random

import numpy as np
import pytest

from cps import cards, fitting, service
from cps.memory import DEFAULT_WEIGHTS_6, Grade, initial_state6, retrievability6, review6
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def student():
    """A made-up student who differs from the population, and 60 days of their cards."""
    rng = Random(7)
    truth = fitting.student(rng, 0.3)
    return truth, fitting.answers(truth, rng, days=60)


def test_the_vectorised_replay_is_memory_review6(student):
    truth, logs = student
    histories = [[(e, g) for _, e, g in log] for log in logs]
    assert any(e is not None and e < 1 for h in histories for e, _ in h)  # same-day answers are in it
    for weights in (DEFAULT_WEIGHTS_6, truth):
        loss, stability, difficulty = fitting._replay(
            np.array([weights.as_vector()]), fitting._Batch.of(histories)
        )
        expected = 0.0
        for i, history in enumerate(histories):
            state = initial_state6(history[0][1], weights)
            for elapsed, grade in history[1:]:
                if elapsed >= 1:
                    r = min(max(retrievability6(elapsed, state.stability, weights), 1e-4), 1 - 1e-4)
                    expected -= math.log(r) if grade > Grade.AGAIN else math.log(1 - r)
                state = review6(state, elapsed, grade, weights)
            assert stability[0, i] == pytest.approx(state.stability, rel=1e-12)
            assert difficulty[0, i] == pytest.approx(state.difficulty, rel=1e-12)
        assert loss[0] == pytest.approx(expected, rel=1e-12)
    assert fitting.scored(histories) == sum(
        1 for h in histories for e, _ in h[1:] if e is not None and e >= 1
    )


def test_the_scale_round_trips_and_keeps_the_first_stabilities_in_order():
    defaults = np.array(DEFAULT_WEIGHTS_6.as_vector())
    assert fitting._from_unit(fitting._to_unit(defaults)) == pytest.approx(defaults, rel=1e-12)
    z = fitting._to_unit(defaults)
    z[1] = 0.0  # s_hard pushed below s_again
    w = fitting._from_unit(z)
    assert w[0] <= w[1] <= w[2] <= w[3]


def test_a_fit_learns_from_the_answers_and_the_prior_holds_it(student):
    truth, logs = student
    train, test, mask = fitting.split(logs, 30.0, 51.0)
    fit = fitting.fit(train)
    assert fit.reviews == fitting.scored(train) > 400
    # In-sample, the fit is never worse than where it starts (the prior is zero there).
    assert fit.loss <= fit.default_loss
    # On the three weeks after, for this student, closer to their own parameters'.
    default, fitted, own = (fitting.log_loss(w, test, mask) for w in (DEFAULT_WEIGHTS_6, fit.weights, truth))
    assert own < fitted < default
    # A prior with no room keeps the population's parameters.
    held = fitting.fit(train, prior_scale=1e-6)
    assert held.weights.as_vector() == pytest.approx(DEFAULT_WEIGHTS_6.as_vector(), rel=1e-3, abs=1e-4)
    # Parameters left out of `free` do not move.
    part = fitting.fit(train, free=(0, 1, 2, 3), steps=50)
    assert part.weights.as_vector()[4:] == pytest.approx(DEFAULT_WEIGHTS_6.as_vector()[4:], rel=1e-9)
    with pytest.raises(ValueError, match="no answer to fit"):
        fitting.fit([[(None, Grade.GOOD)]])


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


def _answers(store: Store, token: str, logs, until: float) -> int:
    """The made-up student's answers before day `until`, as card_reviews rows."""
    rows = [
        (f"c{i}", token, (NOW + timedelta(days=at)).isoformat(timespec="seconds"), int(g), e)
        for i, log in enumerate(logs)
        for at, e, g in log
        if at < until
    ]
    with store.connection() as db:
        db.executemany(
            "INSERT INTO card_reviews (card, token, at, grade, elapsed) VALUES (?, ?, ?, ?, ?)", rows
        )
    return cards.scored(store, token)


def test_cards_use_the_students_own_parameters_once_there_are_enough(tmp_path, student):
    _, logs = student
    sub = _plan(tmp_path)
    store = Store(tmp_path)
    few = _answers(store, sub.token, logs, 9.0)
    assert 0 < few < fitting.MIN_REVIEWS
    assert not service.refit_cards(tmp_path, sub, NOW) and cards.personal(store, sub.token) is None
    view = service.cards_view(tmp_path, sub, NOW)
    assert view["fitted"] == 0 and view["to_fit"] == fitting.MIN_REVIEWS - few
    assert cards.model_for(store, sub.token) is cards.MODEL

    with store.connection() as db:
        db.execute("DELETE FROM card_reviews")
    enough = _answers(store, sub.token, logs, 30.0)
    assert enough >= fitting.MIN_REVIEWS
    assert service.refit_cards(tmp_path, sub, NOW)
    own = cards.personal(store, sub.token)
    assert own is not None and own.reviews == enough
    assert cards.model_for(store, sub.token).weights != cards.MODEL.weights
    view = service.cards_view(tmp_path, sub, NOW)
    assert view["fitted"] == enough and view["to_fit"] == 0
    # Not again until the answers have grown by a quarter.
    assert not service.refit_cards(tmp_path, sub, NOW)

    # A card is scheduled with them: its intervals are the fitted model's. (After
    # the made-up answers, so the day's twenty new cards are not used up.)
    later = NOW + timedelta(days=40)
    service.add_cards(tmp_path, sub, {"course": "Algebra 3", "front": "Q", "back": "A"}, now=later)
    review = service.card_review_view(tmp_path, sub, later)
    model = cards.model_for(store, sub.token)
    good = cards.schedule(cards.of(store, sub.token)[0], Grade.GOOD, later, model)[1]
    assert {o["grade"]: o["when"] for o in review["options"]}["good"] == service.wait_words(good - later)

    # The fit goes with the plan: in its copy, and deleted with it.
    assert service.export_data(tmp_path, sub.token, NOW)["card_fit"][0]["reviews"] == enough
    assert service.delete_subscription(tmp_path, sub.token)
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM card_fits").fetchone()[0] == 0


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


def test_an_answer_fits_when_due_and_the_page_says_so(tmp_path, student):
    _, logs = student
    later = NOW + timedelta(days=40)
    config = Config(store=tmp_path, clock=lambda: later, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        token = (
            web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
            .headers["location"]
            .split("/")[2]
        )
        sub = service.load_subscription(tmp_path, token)
        service.add_cards(tmp_path, sub, {"course": "Algebra 3", "front": "Q", "back": "A"}, now=later)
        store = Store(tmp_path)
        count = _answers(store, token, logs, 30.0)
        page = web.get(f"/p/{token}/cards").text
        assert "The intervals follow how most people remember" not in page  # enough answers already
        card = cards.of(store, token)[0]
        web.post(f"/p/{token}/cards/{card.id}/answer", data={"grade": "good"}, follow_redirects=False)
        own = cards.personal(store, token)
        assert own is not None and own.reviews == count  # fitted after the answer
        assert f"fitted to your own memory, from {count} of your answers" in web.get(f"/p/{token}/cards").text
        # Before any fit, the page says how many answers are still to come, in the
        # student's language.
        with store.connection() as db:
            db.execute("DELETE FROM card_fits")
            db.execute("DELETE FROM card_reviews WHERE elapsed >= 1 AND rowid % 2 = 0")
        left = fitting.MIN_REVIEWS - cards.scored(store, token)
        assert left > 0
        assert f"After {left} more answers on later days" in web.get(f"/p/{token}/cards").text
