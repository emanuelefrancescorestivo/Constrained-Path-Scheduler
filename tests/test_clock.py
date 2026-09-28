"""
Tests for the value function with a clock.

The load-bearing ones:

`test_the_bound_is_below_the_estimate_in_every_cell`: the optimistic solve is used
as an AO* heuristic and the accurate one as the window's terminal value, so the
heuristic is admissible for the window objective only if this holds cell by cell.

`test_waiting_is_never_free_once_the_best_moment_has_passed`: the property whose
absence was AUDIT.md item 20. In `budget.py` postponing cost 3.4e-09.

`test_the_exam_day_goal_is_met_by_cramming`: why the target is a stability, not
"recall at the exam". Recorded so the next person does not switch to the natural
goal and ship a planner that crams.
"""

from __future__ import annotations

import numpy as np
import pytest

from cps.clock import ClockConfig, simulate, solve
from cps.memory import (
    Grade,
    MemoryState,
    _stability_on_recall,
    retrievability,
    review,
    stability_for_interval,
)

EXAM = 21.0


@pytest.fixture(scope="module")
def accurate():
    return solve(ClockConfig.for_exam(EXAM, 0.9))


@pytest.fixture(scope="module")
def bound():
    return solve(ClockConfig.for_exam(EXAM, 0.9, interpolation="optimistic"))


# --------------------------------------------------------------------------- #
# Shape of the value function
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("which", ["accurate", "bound"])
def test_value_is_monotone_in_every_dimension(which, request):
    """Optimistic interpolation is a bound only because the true value is monotone:
    more stability and more time cannot hurt, more difficulty cannot help. The
    solved tables must show the same shape, or the argument has no support."""
    table = request.getfixturevalue(which).expected_cost
    assert np.all(np.diff(table, axis=0) <= 1e-9), "more time left must not cost more"
    assert np.all(np.diff(table, axis=2) <= 1e-9), "more stability must not cost more"
    assert np.all(np.diff(table, axis=1) >= -1e-9), "more difficulty must not cost less"


def test_the_bound_is_below_the_estimate_in_every_cell(accurate, bound):
    assert np.all(bound.expected_cost <= accurate.expected_cost + 1e-12)


def test_with_no_time_left_only_the_goal_is_free(accurate):
    level0 = accurate.expected_cost[0]
    at_goal = accurate.stability_grid >= accurate.target_stability
    assert np.all(level0[:, at_goal] == 0.0)
    assert np.all(level0[:, ~at_goal] == accurate.config.failure_penalty)


def test_just_below_the_target_is_at_least_one_more_review(accurate, bound):
    """The value is discontinuous at the target. Interpolation must not smear the
    zero above it into the states just below, which was once suspected of causing
    a wasted review; it measured 1.06 blocks here, and the cause was elsewhere
    (AUDIT.md item 26)."""
    target = accurate.target_stability
    for policy in (accurate, bound):
        for fraction in (0.95, 0.99, 0.999):
            assert policy.waiting(6.9, target * fraction, 0.1, 5.0) >= 1.0 - 1e-9


def test_queries_beyond_the_table_raise_instead_of_clamping(accurate):
    """V is non-increasing in time left, so clamping a longer query to the horizon
    would overestimate, the direction that breaks a heuristic."""
    with pytest.raises(ValueError, match="horizon"):
        accurate.waiting(7.0, 2.0, 0.0, EXAM + 1.0)


def test_a_topic_at_its_target_costs_nothing(accurate, bound):
    for policy in (accurate, bound):
        assert policy.waiting(5.0, policy.target_stability, 3.0, 10.0) == 0.0


def test_bound_queries_stay_below_accurate_queries_off_the_grid(accurate, bound):
    """The window heuristic evaluates W at arbitrary times and states, not only at
    grid points, so the cell-wise property has to survive interpolation."""
    rng = np.random.default_rng(0)
    for _ in range(300):
        d = rng.uniform(1, 10)
        s = float(np.exp(rng.uniform(np.log(0.1), np.log(25))))
        left = rng.uniform(0.1, EXAM)
        elapsed = rng.uniform(0, 10)
        assert bound.waiting(d, s, elapsed, left) <= accurate.waiting(d, s, elapsed, left) + 1e-9


# --------------------------------------------------------------------------- #
# The clock
# --------------------------------------------------------------------------- #


def test_waiting_is_never_free_once_the_best_moment_has_passed(accurate):
    """The defect this module fixes. For a topic at S=2, D=7 with the exam in 21
    days, postponing by a day or two changes nothing (the best first review is
    about day 2), and postponing by a week costs several blocks."""
    best = accurate.best_review_time(7.0, 2.0, 0.0, EXAM)
    assert 1.0 <= best <= 4.0
    assert accurate.cost_of_postponing(7.0, 2.0, 0.0, EXAM, 1.0) == pytest.approx(0.0, abs=0.05)
    assert accurate.cost_of_postponing(7.0, 2.0, 0.0, EXAM, 8.0) > 2.0
    assert accurate.cost_of_postponing(7.0, 2.0, 0.0, EXAM, 16.0) > 8.0


def test_waiting_value_rises_with_elapsed_time_at_a_fixed_exam(accurate, bound):
    """While a topic waits, elapsed plus time left is constant and the options only
    shrink, so W can only go up. This is what makes skipping a block cost
    something in the planner."""
    for policy in (accurate, bound):
        values = [policy.waiting(7.0, 2.0, e, EXAM - e) for e in np.arange(0, 20, 0.5)]
        assert np.all(np.diff(values) >= -1e-9)


def test_the_first_review_is_early_for_a_weak_topic(accurate):
    """Acceptance criterion from milestone M1, unconstrained version: review a
    topic at S=2 by day 6, at recall of at least 0.75."""
    best = accurate.best_review_time(7.0, 2.0, 0.0, EXAM)
    assert best <= 6.0
    assert retrievability(best, 2.0) >= 0.75


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", [MemoryState(2.0, 7.0), MemoryState(0.5, 8.0)])
def test_estimate_and_bound_against_monte_carlo(accurate, bound, state):
    """Simulate the policy the accurate table induces and compare its realised
    cost (reviews plus penalty when not ready) with the table.

    The bound must sit below the realised cost. The estimate is not a bound and is
    somewhat optimistic: measured over 2,000 runs it sits 0.1 to 0.7 blocks below
    the simulated cost (6.14 against 6.85 +- 0.28 at S=2, D=7; 11.88 against
    11.99 +- 0.28 at S=0.5, D=8). The tolerance pins that, with the standard error.
    """
    trials = 1000
    costs = []
    for seed in range(trials):
        reviews, ready = simulate(accurate, state, EXAM, np.random.default_rng(seed))
        costs.append(reviews + (0.0 if ready else accurate.config.failure_penalty))
    mean = float(np.mean(costs))
    se = float(np.std(costs, ddof=1) / np.sqrt(trials))
    estimate = accurate.post_review(state.difficulty, state.stability, EXAM)
    lower = bound.post_review(state.difficulty, state.stability, EXAM)
    assert lower <= mean - 2 * se
    assert abs(estimate - mean) <= 1.0 + 3 * se, (estimate, mean, se)


# --------------------------------------------------------------------------- #
# The goal that was rejected
# --------------------------------------------------------------------------- #


def test_the_exam_day_goal_is_met_by_cramming():
    """"Recall at the exam >= 0.9" is the natural goal and it degenerates.

    One review twelve hours before a day-21 exam, from S=2 and three weeks without
    review, leaves recall at the exam at 0.9 or more whether that review succeeds
    or lapses. So the cheapest way to meet the goal is a single review the night
    before: one block, no spacing. FSRS is right that cramming works for the exam
    day itself. The planner therefore targets a stability instead, which a single
    late review cannot reach (`benchmarks/spike_clock.py`, row "B-exam").
    """
    start = MemoryState(2.0, 7.0)
    review_at, exam = 20.5, 21.0
    for grade in (Grade.GOOD, Grade.AGAIN):
        after = review(start, review_at, grade)
        assert retrievability(exam - review_at, after.stability) >= 0.9
    lapsed = review(start, review_at, Grade.AGAIN)
    assert lapsed.stability < stability_for_interval(exam, 0.9)


def test_recall_stability_increases_with_stability():
    """`rolling.best_case_stability` keeps only the best stability per review day,
    which is valid only if a stronger memory never ends weaker after the same gap
    and a successful review."""
    for gap in (0.5, 1, 3, 7, 14, 30):
        for d in (1.0, 5.0, 10.0):
            grid = np.geomspace(0.05, 200, 400)
            after = [_stability_on_recall(s, d, retrievability(gap, s), Grade.GOOD, _w()) for s in grid]
            assert np.all(np.diff(after) > 0), (gap, d)


def _w():
    from cps.memory import DEFAULT_WEIGHTS

    return DEFAULT_WEIGHTS
