"""
Property tests for the memory model.

These assert *properties*, not golden numbers. That is a deliberate choice: the
17 FSRS parameters are population defaults and will be replaced the moment we
fit them to anything, so a suite pinned to specific stability values would have
to be rewritten every time. The properties below have to hold for any parameter
vector that passes `Weights.validate()`, which is the actual contract.

The first test is the important one. It runs against both the corrected model
and the January 2026 heuristic, and the January heuristic is marked
`xfail(strict=True)` -- meaning the suite fails if it ever passes. That test is
the reason the original results are not trustworthy.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from cps.legacy import FSRSModel, LegacyHeuristic
from cps.memory import (
    DECAY,
    DEFAULT_WEIGHTS,
    FACTOR,
    Grade,
    MemoryState,
    Weights,
    expected_gain,
    expected_gain_rate,
    expected_retention_at,
    expected_stability,
    initial_state,
    interval_for_retention,
    retrievability,
    review,
)

MODELS = [
    pytest.param(FSRSModel(), id="fsrs-4.5"),
    pytest.param(
        LegacyHeuristic(),
        id="january-2026",
        marks=pytest.mark.xfail(
            strict=True,
            reason=(
                "predict_next_stability(S, D) has no elapsed-time argument, so the "
                "spacing effect cannot be represented. This is the defect that "
                "invalidates the retention figures in Table 1 of the January report."
            ),
        ),
    ),
]


# --------------------------------------------------------------------------- #
# The load-bearing test
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", MODELS)
def test_spacing_effect_exists(model):
    """A review that lands when the memory is weaker must be worth more.

    If this does not hold, *when* a review happens is irrelevant, every free
    slot in the calendar is interchangeable, and there is nothing for a
    scheduler to optimise. Any measured "improvement" would then be an artefact
    of something other than scheduling.
    """
    state = MemoryState(stability=5.0, difficulty=5.0)

    premature = model.stability_after_good_review(state, elapsed_days=0.5)  # R ~ 0.99
    well_timed = model.stability_after_good_review(state, elapsed_days=5.0)  # R = 0.90

    assert well_timed > premature * 1.05, (
        f"reviewing at R=0.90 produced S'={well_timed:.3f} but reviewing at "
        f"R=0.99 produced S'={premature:.3f}; the model is blind to timing"
    )


# --------------------------------------------------------------------------- #
# Forgetting curve
# --------------------------------------------------------------------------- #


def test_stability_is_the_90_percent_interval():
    """R(t = S) == 0.9 is the definition of stability, not a coincidence."""
    for s in (0.5, 1.0, 7.0, 100.0):
        assert retrievability(s, s) == pytest.approx(0.9, abs=1e-9)


def test_retrievability_starts_at_one_and_decays():
    assert retrievability(0.0, 10.0) == pytest.approx(1.0)
    values = [retrievability(t, 10.0) for t in (0, 1, 3, 10, 30, 365)]
    assert all(a > b for a, b in itertools.pairwise(values))
    assert values[-1] > 0.0


def test_retrievability_increases_with_stability():
    """Same delay, stronger memory, higher recall probability."""
    assert retrievability(7.0, 3.0) < retrievability(7.0, 30.0)


def test_interval_inversion_round_trips():
    for target in (0.7, 0.8, 0.9, 0.95):
        t = interval_for_retention(20.0, target)
        assert retrievability(t, 20.0) == pytest.approx(target, rel=1e-9)


def test_lower_target_retention_means_longer_intervals():
    """Accepting more forgetting buys longer gaps -- the FSRS efficiency knob."""
    assert interval_for_retention(10.0, 0.95) < interval_for_retention(10.0, 0.80)


# --------------------------------------------------------------------------- #
# Stability update
# --------------------------------------------------------------------------- #


def test_diminishing_returns_in_stability():
    """A review is worth proportionally less once the memory is already strong."""
    weak = MemoryState(stability=2.0, difficulty=5.0)
    strong = MemoryState(stability=200.0, difficulty=5.0)

    # Compare at equal retrievability, so only stability differs.
    weak_gain = review(weak, weak.ideal_interval(), Grade.GOOD).stability / weak.stability
    strong_gain = review(strong, strong.ideal_interval(), Grade.GOOD).stability / strong.stability
    assert strong_gain < weak_gain


def test_harder_items_gain_less():
    easy_item = MemoryState(stability=5.0, difficulty=2.0)
    hard_item = MemoryState(stability=5.0, difficulty=9.0)
    assert review(hard_item, 5.0, Grade.GOOD).stability < review(easy_item, 5.0, Grade.GOOD).stability


def test_grade_ordering_is_respected():
    state = MemoryState(stability=10.0, difficulty=5.0)
    outcomes = [review(state, 10.0, g).stability for g in (Grade.AGAIN, Grade.HARD, Grade.GOOD, Grade.EASY)]
    assert all(a < b for a, b in itertools.pairwise(outcomes))


def test_a_lapse_never_beats_a_recall():
    """What the best-case bounds need (`plan.best_case_reviews`,
    `rolling.best_case_stability`): from the same state after the same delay, a
    lapse never ends above a successful recall. Holds on the whole grid without any
    clamp; the largest ratio is 0.999, at the stability floor (AUDIT.md item 27)."""
    for s in np.geomspace(0.01, 500, 60):
        for d in np.linspace(1, 10, 10):
            state = MemoryState(float(s), float(d))
            for elapsed in np.geomspace(0.01, 1000, 40):
                lapse = review(state, float(elapsed), Grade.AGAIN).stability
                recall = review(state, float(elapsed), Grade.GOOD).stability
                assert lapse <= recall + 1e-12


def test_post_lapse_stability_is_not_clamped_because_fsrs_4_5_is_not():
    """This test used to assert that a lapse never raises stability, which the old
    `min(., S)` clamp guaranteed. FSRS-4.5 has no such clamp: at S = 0.93 after
    thirty days, py-fsrs 2.5.1 gives 1.185347 (benchmarks/fsrs_reference.py, the
    last step of the mixed trajectory). Matching the reference wins over a
    property the reference does not have (AUDIT.md item 27)."""
    before = MemoryState(0.927273, 10.0)
    after = review(before, 30.0, Grade.AGAIN)
    assert after.stability > before.stability
    assert after.stability == pytest.approx(1.185347, abs=2e-6)


def test_difficulty_stays_in_range_under_adversarial_grading():
    """Twenty consecutive failures then twenty successes must not escape [1, 10]."""
    state = initial_state(Grade.GOOD)
    for grade in [Grade.AGAIN] * 20 + [Grade.EASY] * 20:
        state = review(state, elapsed_days=1.0, grade=grade)
        assert 1.0 <= state.difficulty <= 10.0
        assert state.stability > 0.0


def test_repeated_good_reviews_produce_growing_intervals():
    """The behaviour a spaced-repetition schedule is supposed to have."""
    state = initial_state(Grade.GOOD)
    intervals = []
    for _ in range(6):
        interval = state.ideal_interval()
        intervals.append(interval)
        state = review(state, interval, Grade.GOOD)
    assert all(a < b for a, b in itertools.pairwise(intervals)), intervals


# --------------------------------------------------------------------------- #
# Expected value: the objective the scheduler will actually optimise
# --------------------------------------------------------------------------- #


def test_expected_stability_has_an_interior_optimum():
    """This is the property that gives the search problem a reason to exist.

    Review too early and you learn nothing (R -> 1, gain -> 0). Review far too
    late and you have probably forgotten it, so E[S'] collapses to the post-lapse
    value. Somewhere in between there is a best moment -- and because that moment
    usually collides with a class or a deadline, you need a constrained search to
    find the best *reachable* slot near it.
    """
    state = MemoryState(stability=5.0, difficulty=5.0)
    delays = [0.05 * 1.25**k for k in range(60)]  # ~0.05 to ~400 days, log-spaced
    values = [expected_stability(state, t) for t in delays]

    best = max(range(len(values)), key=values.__getitem__)
    assert 0 < best < len(values) - 1, "optimum sits on a boundary; no trade-off to solve"
    assert values[best] > values[0]
    assert values[best] > values[-1]


# Produced by py-fsrs 2.5.1, the Python FSRS-4.5 by the algorithm's author, driven
# through its own API with whole-day gaps: benchmarks/fsrs_reference.py.
# (interval in days, stability, difficulty) after each review.
# fmt: off
REFERENCE_GOOD = [
    (4, 14.808101, 5.161800),
    (15, 49.461605, 5.161800),
    (49, 145.670632, 5.161800),
    (146, 392.697898, 5.161800),
    (393, 973.427791, 5.161800),
    (973, 2243.573044, 5.161800),
]
# (grade, days since the previous review, stability, difficulty), after a first
# rating of Good.
REFERENCE_MIXED = [
    (3, 3, 12.262351, 5.161800), (1, 9, 2.744538, 6.901155), (3, 1, 4.929007, 6.847235),
    (2, 2, 5.853178, 7.664664), (3, 5, 13.465381, 7.587075), (4, 7, 42.435274, 6.642214),
    (1, 40, 5.063535, 8.335676), (3, 2, 7.666694, 8.237286), (3, 6, 15.012426, 8.141946),
    (2, 15, 18.864802, 8.919239), (4, 30, 83.884577, 7.933081), (1, 3, 5.809487, 9.586526),
    (1, 25, 2.428566, 10.000000), (1, 1, 0.927273, 10.000000), (1, 30, 1.185347, 10.000000),
]
# fmt: on


def test_good_ratings_reproduce_the_reference_trajectory_exactly():
    """Golden test against an independent implementation of the same version.

    It replaces a neighbourhood check (0.6x to 1.7x of "4, 14, 44, 125"), which is
    what caught the FSRS-5 initial-difficulty formula mixed with FSRS-4.5
    parameters (AUDIT.md item 11), a mismatch every property test above tolerated.
    Those four numbers turned out not to be FSRS-4.5's: with its defaults and whole
    days, py-fsrs 2.5.1 schedules 4, 15, 49, 146, 393, 973 days. The six printed
    decimals are matched to 1e-6.
    """
    state = initial_state(Grade.GOOD)
    for interval, stability, difficulty in REFERENCE_GOOD:
        assert max(round(state.ideal_interval()), 1) == interval
        state = review(state, float(interval), Grade.GOOD)
        assert state.stability == pytest.approx(stability, abs=1e-6)
        assert state.difficulty == pytest.approx(difficulty, abs=1e-6)


def test_every_grade_and_lapses_reproduce_the_reference_trajectory_exactly():
    """Again, Hard and Easy and the post-lapse formula, which a Good-only
    trajectory never exercises. This one caught the post-lapse clamp that FSRS-4.5
    does not have (AUDIT.md item 27): with it, the last step gave 0.927 instead of
    1.185."""
    state = initial_state(Grade.GOOD)
    for grade, gap, stability, difficulty in REFERENCE_MIXED:
        state = review(state, float(gap), Grade(grade))
        assert state.stability == pytest.approx(stability, abs=1e-6), (grade, gap)
        assert state.difficulty == pytest.approx(difficulty, abs=1e-6), (grade, gap)


def test_both_naive_single_review_objectives_degenerate():
    """Recorded so nobody re-derives them and ships the result.

    argmax E[S'] runs off to ~100 days for a 5-day item (post-lapse stability
    does not fall with the delay, so the downside of waiting is capped), and
    argmax of the gain *rate*
    collapses to zero delay (the gain is linear in t for small t). Any objective
    defined on a single review in isolation has this problem.
    """
    state = MemoryState(stability=5.0, difficulty=5.0)
    delays = [0.25 * k for k in range(1, 600)]

    by_value = max(delays, key=lambda t: expected_stability(state, t))
    by_rate = max(delays, key=lambda t: expected_gain_rate(state, t))

    assert by_value > 10 * state.ideal_interval()
    assert by_rate == pytest.approx(delays[0])


def _plans(last_slot: int, budget: int):
    from itertools import combinations_with_replacement

    return list(combinations_with_replacement([float(d) for d in range(last_slot + 1)], budget))


def test_objective_is_degenerate_if_review_is_allowed_at_the_exam():
    """Documented boundary case, not a bug.

    Allow a review at the exam moment and expected retention is exactly 1: you
    look at the card and then immediately recall it. The model is right, and so
    is the folk wisdom -- cramming does work for the exam. It just does not build
    stability, so this objective must be evaluated with a realistic gap between
    the last available study slot and the exam (you cannot revise in the hall).
    """
    state = initial_state(Grade.GOOD)
    exam_day = 21.0
    best = max(_plans(21, 3), key=lambda p: expected_retention_at(state, list(p), exam_day))
    assert expected_retention_at(state, list(best), exam_day) == pytest.approx(1.0)


def test_spacing_beats_massed_practice_once_there_is_a_gap_before_the_exam():
    """The finding the project is actually entitled to claim.

    With three study blocks and no revision on exam day, the optimal plan spreads
    them out and beats putting all three at the last possible moment. The first
    review lands near the interval FSRS itself would choose from the initial
    state, which is a non-trivial agreement: nothing in this objective mentions
    90% target retention.
    """
    state = initial_state(Grade.GOOD)
    exam_day = 21.0
    plans = _plans(20, 3)

    def score(plan):
        return expected_retention_at(state, list(plan), exam_day)

    best = max(plans, key=score)
    massed = (20.0, 20.0, 20.0)

    assert score(best) > score(massed)
    assert best[-1] == 20.0, "the last review should still be as late as possible"
    assert 0.5 * state.ideal_interval() <= best[0] <= 2.0 * state.ideal_interval(), (
        f"first review at day {best[0]}, FSRS ideal interval {state.ideal_interval():.2f}"
    )


def test_the_middle_review_placement_is_nearly_free():
    """Why constrained scheduling is a good idea rather than a compromise.

    The objective is very flat in the middle review's position: anywhere from
    roughly day 8 to day 16 scores within a whisker of the optimum. That is the
    honest version of this project's value proposition. We cannot claim a large
    retention gain over an unconstrained memory model, because there isn't one to
    claim. We *can* claim that hard calendar constraints cost almost nothing --
    the search has enough slack to satisfy sleep, classes and deadlines while
    staying within a fraction of a percent of optimal retention.
    """
    state = initial_state(Grade.GOOD)
    exam_day = 21.0

    def score(middle):
        return expected_retention_at(state, [4.0, float(middle), 20.0], exam_day)

    best = max(score(m) for m in range(5, 20))
    flat = [m for m in range(5, 20) if score(m) > best - 1e-3]
    assert len(flat) >= 8, f"only {len(flat)} near-optimal placements: {flat}"


def test_premature_review_has_almost_no_expected_gain():
    state = MemoryState(stability=30.0, difficulty=5.0)
    assert expected_gain(state, 0.02) < 0.05 * state.stability


def test_expected_gain_is_positive_at_the_ideal_interval():
    state = MemoryState(stability=5.0, difficulty=5.0)
    assert expected_gain(state, state.ideal_interval()) > 0.0


# --------------------------------------------------------------------------- #
# Parameter-vector guard
# --------------------------------------------------------------------------- #


def test_default_weights_pass_validation():
    DEFAULT_WEIGHTS.validate()


def test_validate_rejects_a_shuffled_parameter_vector():
    """The guard that would have caught a misremembered parameter order."""
    v = [
        0.4872,
        1.4003,
        3.7145,
        13.8206,
        5.1618,
        1.2298,
        0.8975,
        0.031,
        1.6474,
        0.1367,
        1.0461,
        2.1072,
        0.0793,
        0.3246,
        1.587,
        0.2272,
        2.8755,
    ]
    swapped = v.copy()
    swapped[15], swapped[16] = swapped[16], swapped[15]  # hard penalty <-> easy bonus
    with pytest.raises(ValueError, match="hard_penalty"):
        Weights.from_vector(swapped).validate()


def test_wrong_vector_length_is_rejected():
    with pytest.raises(ValueError, match="17 parameters"):
        Weights.from_vector([0.1] * 16)


def test_forgetting_curve_constants_are_consistent():
    """FACTOR is not a free constant: it is fixed by demanding R(S) = 0.9."""
    assert pytest.approx(0.9 ** (1 / DECAY) - 1, rel=1e-12) == FACTOR
