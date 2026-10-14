"""
FSRS-6, the product's memory model since D52, pinned to py-fsrs 6.3.2 (invariant
9: a parameterised model needs a trajectory pinned to an external reference).

The golden values below were printed by benchmarks/fsrs6_reference.py with py-fsrs
6.3.2; with `fsrs` installed (the dev extra), the same trajectories are also run
against it live.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from cps import memory
from cps.memory import (
    DEFAULT_WEIGHTS_6,
    FSRS_4_5,
    FSRS_6,
    Grade,
    MemoryState,
    Weights6,
    initial_state6,
    interval_for_retention6,
    retrievability6,
    review6,
    stability_for_interval6,
)

# gap in days, then py-fsrs 6.3.2's stability and difficulty after the review
GOOD_TRAJECTORY = [
    (2, 10.964332, 2.111214),
    (11, 46.280217, 2.104331),
    (46, 162.862202, 2.097455),
    (163, 497.447194, 2.090586),
    (497, 1345.528778, 2.083724),
    (1346, 3292.279596, 2.076869),
    (3292, 7397.830529, 2.070020),
]
# grade, gap in days, then py-fsrs 6.3.2's stability and difficulty
MIXED_TRAJECTORY = [
    (3, 0, 2.306500, 2.118104),
    (3, 0, 2.306500, 2.111214),  # the same day: the short-term update
    (2, 3, 9.240245, 4.748285),
    (1, 10, 1.353001, 8.259025),  # a lapse
    (3, 1, 2.872423, 8.245995),
    (4, 7, 14.697058, 7.645116),
    (1, 30, 1.798616, 9.211194),
    (3, 2, 3.492810, 9.197211),
    (3, 5, 7.013773, 9.183242),
]


def test_good_at_every_due_date_reproduces_py_fsrs():
    state = initial_state6(Grade.GOOD)
    for gap, stability, difficulty in GOOD_TRAJECTORY:
        state = review6(state, gap, Grade.GOOD)
        assert state.stability == pytest.approx(stability, abs=1e-6), gap
        assert state.difficulty == pytest.approx(difficulty, abs=1e-6), gap


def test_every_grade_lapses_and_a_same_day_review_reproduce_py_fsrs():
    state = None
    for grade, gap, stability, difficulty in MIXED_TRAJECTORY:
        state = initial_state6(Grade(grade)) if state is None else review6(state, gap, Grade(grade))
        assert state.stability == pytest.approx(stability, abs=1e-6), (grade, gap)
        assert state.difficulty == pytest.approx(difficulty, abs=1e-6), (grade, gap)


def test_the_same_trajectories_against_py_fsrs_itself():
    pytest.importorskip("fsrs", reason="py-fsrs 6.3.2 (the dev extra) is not installed")
    path = Path(__file__).resolve().parent.parent / "benchmarks" / "fsrs6_reference.py"
    spec = importlib.util.spec_from_file_location("fsrs6_reference", path)
    assert spec is not None and spec.loader is not None
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    for _, s, d, ours_s, ours_d in reference.good_trajectory():
        assert ours_s == pytest.approx(s, rel=1e-9) and ours_d == pytest.approx(d, rel=1e-9)
    for _, _, s, d, ours_s, ours_d in reference.mixed_trajectory():
        assert ours_s == pytest.approx(s, rel=1e-9) and ours_d == pytest.approx(d, rel=1e-9)


def test_stability_is_still_the_90_percent_interval_with_a_flatter_curve():
    for s in (0.5, 3.0, 40.0):
        assert retrievability6(s, s) == pytest.approx(0.9)
        assert interval_for_retention6(s, 0.9) == pytest.approx(s)
        assert stability_for_interval6(interval_for_retention6(s, 0.8), 0.8) == pytest.approx(s)
    # FSRS-6's curve (decay 0.1542) falls faster at first and slower later than
    # FSRS-4.5's (decay 0.5), both through 0.9 at t = S.
    assert retrievability6(1.0, 10.0) < memory.retrievability(1.0, 10.0)
    assert retrievability6(100.0, 10.0) > memory.retrievability(100.0, 10.0)


def test_a_lapse_never_leaves_more_than_the_short_term_cap():
    w = DEFAULT_WEIGHTS_6
    state = MemoryState(1.0, 3.0)
    after = review6(state, 400.0, Grade.AGAIN)
    assert after.stability <= state.stability / math.exp(w.short_scale * w.short_offset) + 1e-12


def test_the_weights_are_named_bounded_and_ordered():
    assert len(DEFAULT_WEIGHTS_6.as_vector()) == 21
    DEFAULT_WEIGHTS_6.validate()
    bad = list(DEFAULT_WEIGHTS_6.as_vector())
    bad[20] = 0.9  # decay above py-fsrs's bound
    with pytest.raises(ValueError, match="decay"):
        Weights6.from_vector(bad).validate()
    with pytest.raises(ValueError, match="21"):
        Weights6.from_vector(bad[:17])


def test_a_model_is_the_functions_it_names():
    state = MemoryState(5.0, 5.0)
    assert FSRS_6.retrievability(3.0, 5.0) == retrievability6(3.0, 5.0)
    assert FSRS_6.review(state, 4.0, Grade.GOOD) == review6(state, 4.0, Grade.GOOD)
    assert FSRS_4_5.review(state, 4.0, Grade.GOOD) == memory.review(state, 4.0, Grade.GOOD)
    assert FSRS_4_5.retrievability(3.0, 5.0) == memory.retrievability(3.0, 5.0)
    assert FSRS_6.initial_state(Grade.EASY) == initial_state6(Grade.EASY)
    assert memory.MODELS["fsrs-6"] is FSRS_6 and memory.MODELS["fsrs-4.5"] is FSRS_4_5
