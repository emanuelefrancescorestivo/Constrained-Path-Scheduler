"""
Tests for the SSP-MMC solver.

The load-bearing one is `test_restricting_the_action_set_cannot_lower_the_cost`.
It is the admissibility lemma for the A* heuristic, checked numerically over the
whole state grid rather than asserted in a report.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cps.memory import (
    DEFAULT_WEIGHTS,
    Grade,
    MemoryState,
    _next_difficulty,
    _stability_on_lapse,
    _stability_on_recall,
    initial_state,
    stability_for_interval,
)
from cps.ssp import (
    SSPConfig,
    _vec_next_difficulty,
    _vec_stability_on_lapse,
    _vec_stability_on_recall,
    mean_reviews_to_target,
    reviews_statistics,
    solve,
)


@pytest.fixture(scope="module")
def policy():
    return solve(SSPConfig(target_stability=365.0))


# --------------------------------------------------------------------------- #
# The vectorised model must not drift from the scalar one
# --------------------------------------------------------------------------- #


def test_vectorised_transitions_match_the_scalar_model():
    """Two implementations of the same equations is a liability; this is the guard.

    Without it, a fix applied to `memory.py` would silently leave `ssp.py`
    solving a slightly different MDP, and the resulting value function would
    still look perfectly plausible.
    """
    rng = np.random.default_rng(0)
    s = rng.uniform(0.05, 400.0, 500)
    d = rng.uniform(1.0, 10.0, 500)
    r = rng.uniform(0.55, 0.98, 500)
    w = DEFAULT_WEIGHTS

    np.testing.assert_allclose(
        _vec_stability_on_recall(s, d, r, w),
        [_stability_on_recall(si, di, ri, Grade.GOOD, w) for si, di, ri in zip(s, d, r, strict=True)],
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        _vec_stability_on_lapse(s, d, r, w),
        [_stability_on_lapse(si, di, ri, w) for si, di, ri in zip(s, d, r, strict=True)],
        rtol=1e-12,
    )
    for grade in (Grade.GOOD, Grade.AGAIN):
        np.testing.assert_allclose(
            _vec_next_difficulty(d, grade, w),
            [_next_difficulty(di, grade, w) for di in d],
            rtol=1e-12,
        )


# --------------------------------------------------------------------------- #
# Convergence and shape of the value function
# --------------------------------------------------------------------------- #


def test_value_iteration_converges(policy):
    assert policy.residual < policy.config.tolerance
    assert policy.sweeps < policy.config.max_sweeps
    assert np.all(np.isfinite(policy.expected_cost))


def test_solver_refuses_to_return_an_unconverged_value_function():
    """The January report's central sin was publishing numbers from a solver
    that never reached a goal. Failing loudly is cheap insurance."""
    with pytest.raises(RuntimeError, match="did not converge"):
        solve(SSPConfig(target_stability=365.0, max_sweeps=3))


def test_goal_states_cost_nothing(policy):
    assert policy.expected_cost[:, -1] == pytest.approx(0.0)
    assert policy.expected_reviews(5.0, 1e6) == 0.0
    assert math.isnan(policy.optimal_retention(5.0, 1e6))


def test_cost_is_monotone_in_stability_and_difficulty(policy):
    """More stable memories are closer to done; harder items take more reviews.
    Both are properties of the model, so a violation means a bug, not a finding."""
    assert np.all(np.diff(policy.expected_cost, axis=1) <= 1e-9)
    assert np.all(np.diff(policy.expected_cost, axis=0) >= -1e-9)


def test_cost_is_a_plausible_number_of_reviews(policy):
    """Sanity band. A model claiming that one review, or four hundred, takes a
    fresh item to a year of stability is broken in a way monotonicity misses."""
    fresh = initial_state(Grade.GOOD)
    v = policy.expected_reviews(fresh.difficulty, fresh.stability)
    assert 3.0 < v < 15.0, v


# --------------------------------------------------------------------------- #
# Admissibility of the A* heuristic
# --------------------------------------------------------------------------- #


def test_restricting_the_action_set_cannot_lower_the_cost(policy):
    """The admissibility lemma, checked over the whole grid.

    The constrained scheduler may only review in free calendar slots, so it
    chooses from a *subset* of the delays available here. Solving the same MDP
    with a deliberately narrow action set must therefore give a cost that is
    everywhere at least as large as the unrestricted V*. If this failed, using
    V* as h(n) would break A*'s optimality guarantee, which is exactly the claim
    the January version could not support.
    """
    restricted = solve(
        SSPConfig(target_stability=365.0, min_retention=0.88, max_retention=0.92, n_retentions=3)
    )
    gap = restricted.expected_cost - policy.expected_cost
    assert gap.min() >= -1e-9, f"restricted cost fell below V* by {-gap.min():.3g}"
    assert gap.mean() > 0.1, "a genuinely restricted action set should cost something"


def test_bilinear_value_function_is_calibrated_against_simulation(policy):
    """V* should predict the cost the policy actually pays.

    This is the calibration check, with an error bar. An earlier version of this
    test used 1500 trials and one seed, landed 0.36 reviews off, and looked like
    evidence that the solver was biased. It was Monte-Carlo noise: at 6000 trials
    across four seeds the agreement is inside one standard error.
    """
    for state in (initial_state(Grade.GOOD), MemoryState(2.0, 7.0), MemoryState(40.0, 4.0)):
        predicted = policy.expected_reviews(state.difficulty, state.stability)
        mean, sem = reviews_statistics(policy, state, trials=1500)
        assert abs(predicted - mean) < 4 * sem + 0.05, (
            f"{state}: V*={predicted:.3f} vs MC={mean:.3f}+/-{sem:.3f}"
        )


def test_optimistic_solve_is_a_genuine_lower_bound(policy):
    """The admissibility guarantee A* actually needs.

    The optimistic operator replaces interpolation with the enclosing cell's
    minimum, so its fixed point can only sit below the true cost. Unlike the
    bilinear value function -- which is accurate and therefore above the truth
    about half the time -- this one is safe to use as h(n).
    """
    lower = solve(SSPConfig(target_stability=365.0, interpolation="optimistic"))
    assert np.all(lower.expected_cost <= policy.expected_cost + 1e-9)
    for state in (initial_state(Grade.GOOD), MemoryState(2.0, 7.0), MemoryState(0.5, 9.0)):
        mean, sem = reviews_statistics(policy, state, trials=1500)
        bound = lower.expected_reviews(state.difficulty, state.stability)
        assert bound <= mean - 4 * sem, f"{state}: bound {bound:.3f} vs MC {mean:.3f}+/-{sem:.3f}"


def test_the_admissible_heuristic_is_loose_and_we_know_by_how_much(policy):
    """Admissibility is bought with informedness; record the price.

    The guaranteed bound sits roughly 2.4 reviews (about 30%) below the accurate
    value function on this grid. That weakens A*'s pruning, and the fix is grid
    refinement rather than quietly using the tighter-but-inadmissible estimate.
    """
    lower = solve(SSPConfig(target_stability=365.0, interpolation="optimistic"))
    gap = policy.expected_cost - lower.expected_cost
    assert gap.min() >= -1e-9
    assert 0.5 < gap.mean() < 6.0, gap.mean()


def test_the_heuristic_grade_solve_is_below_the_analysis_solve_in_every_cell(policy):
    """AUDIT.md item 22. `for_heuristic` is what AO* uses, `SSPConfig()` what the
    analysis reports. The first must lie below the second in every cell, not only
    on average, for "admissible" and "accurate" to be ordered as the documents
    say. They were not: with two finite retention grids neither containing the
    other, the smallest gap was -6.2e-06. The heuristic grid now contains the
    analysis grid, so its minimum is over a superset of actions."""
    lower = solve(SSPConfig.for_heuristic(365.0))
    assert set(np.round(policy.config.retentions(), 12)) <= set(np.round(lower.config.retentions(), 12))
    assert np.all(lower.expected_cost <= policy.expected_cost)


def test_lower_bound_refuses_to_use_an_inadmissible_value_function(policy):
    """Structural guard: the distinction between "accurate" and "admissible" is
    easy to lose six modules downstream, so it fails loudly at the call site."""
    with pytest.raises(ValueError, match="optimistic"):
        policy.reviews_lower_bound([initial_state(Grade.GOOD)])


def test_lower_bound_is_additive_over_topics():
    lower = solve(SSPConfig(target_stability=365.0, interpolation="optimistic"))
    states = [initial_state(Grade.GOOD), MemoryState(5.0, 6.0), MemoryState(50.0, 3.0)]
    total = lower.reviews_lower_bound(states)
    assert total == pytest.approx(sum(lower.expected_reviews(s.difficulty, s.stability) for s in states))
    assert total > lower.reviews_lower_bound(states[:2])


# --------------------------------------------------------------------------- #
# Does the optimal policy behave like a workload minimiser?
# --------------------------------------------------------------------------- #


def test_optimal_policy_beats_fixed_retention_policies(policy):
    """The external check on the whole solver.

    Every off-the-shelf tool reviews at a constant target retention; Anki's FSRS
    default is 0.90. If our optimal policy did not beat constant policies on
    simulated runs, the value function would be decorative.
    """
    fresh = initial_state(Grade.GOOD)
    optimal = mean_reviews_to_target(policy, fresh, trials=2000, seed=3)
    for fixed in (0.70, 0.80, 0.90, 0.95):
        constant = mean_reviews_to_target(policy, fresh, trials=2000, seed=3, fixed_retention=fixed)
        assert optimal < constant, f"optimal {optimal:.3f} not better than R={fixed} ({constant:.3f})"


def test_optimal_retention_is_below_ankis_default_and_not_extreme():
    """Mean pi* across the stability grid comes out at 0.82-0.85 for normal
    difficulties: below Anki's default of 0.90 [ref:anki-manual], which the manual
    describes as a balance of retention and workload, and far from the extremes.

    This test used to be called a check against "the band reported in the
    literature", 0.75-0.90. No source for that band could be found (AUDIT.md
    item 28). The range is kept as a sanity range chosen here, not a published one.
    """
    p = solve(SSPConfig(target_stability=365.0))
    pi = p.target_retention[:, :-1]  # drop the goal column
    for row in (2, 9, 15):  # D ~ 2.0, 5.5, 8.5
        assert 0.75 <= pi[row].mean() <= 0.90, f"D={p.difficulty_grid[row]}: {pi[row].mean():.3f}"


def test_the_hardest_items_are_reviewed_at_lower_retention():
    """A maximally difficult item gains little per review -- the (11 - D) factor --
    so the policy lets it decay further before spending a block on it."""
    p = solve(SSPConfig(target_stability=365.0))
    pi = p.target_retention[:, :-1]
    assert pi[-1].mean() < pi[9].mean() - 0.05


def test_higher_lapse_cost_raises_the_optimal_retention():
    """The cost ratio is a modelling choice with a visible consequence.

    With a wasted block priced the same as a productive one, the policy accepts
    frequent failures in exchange for long intervals. Charge more for a lapse and
    it becomes conservative. Anyone quoting an optimal retention without stating
    this ratio is quoting an incomplete number.
    """
    cheap = solve(SSPConfig(target_stability=365.0, cost_lapse=1.0))
    dear = solve(SSPConfig(target_stability=365.0, cost_lapse=4.0))
    assert dear.optimal_retention(5.0, 8.0) > cheap.optimal_retention(5.0, 8.0) + 0.05


def test_v_star_is_stable_under_grid_refinement():
    """Discretisation error must be small relative to the quantity reported.

    Doubling both grids moves V* by well under a tenth of a review. Note that the
    *argmin* is far less well determined than the value -- see the docstring of
    `MemorizationPolicy.action_values` -- which is why the optimal retention is
    reported as a band and never to three decimals.
    """
    coarse = solve(SSPConfig(target_stability=365.0, n_stability=128, n_retentions=40))
    fine = solve(SSPConfig(target_stability=365.0, n_stability=256, n_retentions=160))
    assert abs(coarse.expected_reviews(5.0, 8.0) - fine.expected_reviews(5.0, 8.0)) < 0.1


# --------------------------------------------------------------------------- #
# The bridge to the scheduling layer
# --------------------------------------------------------------------------- #


def test_stability_target_derives_from_the_deadline():
    assert stability_for_interval(45, 0.9) == pytest.approx(45.0)
    assert stability_for_interval(45, 0.95) > stability_for_interval(45, 0.9)
    assert SSPConfig.for_deadline(45, 0.9).target_stability == pytest.approx(45.0)


def test_optimal_delay_from_a_fresh_item_is_days_not_months(policy):
    fresh = initial_state(Grade.GOOD)
    delay = policy.optimal_delay(fresh.difficulty, fresh.stability)
    assert 1.0 < delay < 30.0, delay


def test_a_short_deadline_makes_the_problem_easier():
    """Sanity check on the deadline bridge: needing to remember something for a
    fortnight costs fewer blocks than needing it for a year."""
    fresh = initial_state(Grade.GOOD)
    near = solve(SSPConfig.for_deadline(14, 0.9))
    far = solve(SSPConfig.for_deadline(365, 0.9))
    assert near.expected_reviews(fresh.difficulty, fresh.stability) < far.expected_reviews(
        fresh.difficulty, fresh.stability
    )


def test_fixed_retention_cost_jumps_where_one_more_review_is_needed(policy):
    """The open question of milestone M4: why does a fixed retention of 0.85 cost
    more than both 0.80 and 0.90? Because a fixed-retention schedule needs a whole
    number of successful reviews to pass the target, 3 up to 0.84 and 4 from 0.85,
    and the cost jumps by about a block where that number steps up, while inside a
    step a higher retention means fewer lapses. See docs/METHOD.md section 2."""
    from cps.ssp import successes_to_target

    fresh = initial_state(Grade.GOOD)
    assert successes_to_target(fresh, 0.84, 365.0) == 3
    assert successes_to_target(fresh, 0.85, 365.0) == 4
    assert successes_to_target(fresh, 0.90, 365.0) == 4
    assert successes_to_target(fresh, 0.91, 365.0) == 5
    cost = {
        r: reviews_statistics(policy, fresh, trials=1500, seeds=(3, 4, 5, 6), fixed_retention=r)
        for r in (0.84, 0.85, 0.90, 0.91)
    }
    jump = cost[0.85][0] - cost[0.84][0]
    se = math.hypot(cost[0.85][1], cost[0.84][1])
    assert jump > 0.6 and jump > 4 * se, cost
    assert cost[0.90][0] < cost[0.85][0] - 4 * math.hypot(cost[0.90][1], cost[0.85][1])  # within a tooth
    assert cost[0.91][0] - cost[0.90][0] > 0.6  # the next step
