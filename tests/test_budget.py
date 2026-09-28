"""
Tests for the budgeted value function.

The load-bearing ones:

`test_an_extra_block_is_worth_something` — the property whose absence caused the
scheduler to return an empty plan. If a marginal block is worth zero everywhere,
there is no reason to study tonight and the planner correctly does nothing.

`test_agrees_with_value_iteration_on_a_matched_grid` — the same quantity computed
two structurally different ways: a fixed-point iteration in `ssp` and a backward
recursion over the budget here. They agree to 2e-8, which is the strongest
evidence available that neither is wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from cps.budget import BudgetConfig, BudgetPolicy
from cps.budget import solve as solve_budget
from cps.memory import MemoryState, stability_for_interval
from cps.ssp import SSPConfig
from cps.ssp import solve as solve_ssp

TARGET = stability_for_interval(21.0, 0.9)


@pytest.fixture(scope="module")
def policy() -> BudgetPolicy:
    return solve_budget(BudgetConfig(target_stability=TARGET))


# --------------------------------------------------------------------------- #
# Non-degeneracy
# --------------------------------------------------------------------------- #


def test_an_extra_block_is_worth_something(policy):
    """Why the budget dimension exists at all.

    Charging leftover work at the unconstrained rate made the model indifferent
    to when blocks are spent, and the scheduler answered by scheduling nothing.
    The future is not unbounded: there are only so many free evenings before the
    exam, and that is the whole reason tonight matters.
    """
    assert np.any(np.diff(policy.expected_cost, axis=0) < -1e-9)
    state = MemoryState(2.0, 7.0)
    assert policy.marginal_value(state.difficulty, state.stability, 2) > 1.0


def test_cost_never_rises_with_a_larger_budget(policy):
    """More opportunities cannot hurt.

    This failed on the first version: with only "review at retention r" in the
    action set the recursion forces a review at every level, so
    V(1) = 1 + (1-r)*penalty exceeded V(0) = penalty whenever r < 1/penalty.
    Declining a block had to be an action.
    """
    assert np.all(np.diff(policy.expected_cost, axis=0) <= 1e-12)


def test_marginal_value_decays_to_nothing(policy):
    """The answer a study planner actually owes a student: how many free evenings
    are enough. From a weak, hard item the second block is worth six blocks of
    expected cost and the fortieth is worth nothing."""
    state = MemoryState(2.0, 7.0)
    early = policy.marginal_value(state.difficulty, state.stability, 2)
    late = policy.marginal_value(state.difficulty, state.stability, 40)
    assert early > 1.0
    assert late < 1e-3
    assert 0 < policy.binding_budget < policy.config.max_budget


# --------------------------------------------------------------------------- #
# Boundary conditions and shape
# --------------------------------------------------------------------------- #


def test_zero_budget_costs_the_failure_penalty(policy):
    assert policy.cost_of(MemoryState(2.0, 7.0), 0) == pytest.approx(
        policy.config.failure_penalty
    )
    assert policy.cost_of(MemoryState(TARGET + 1, 7.0), 0) == 0.0


def test_goal_states_cost_nothing_at_any_budget(policy):
    for budget in (0, 1, 7, 64):
        assert policy.expected_blocks(5.0, TARGET * 2, budget) == 0.0
        assert np.isnan(policy.optimal_retention(5.0, TARGET * 2, budget))


def test_cost_is_monotone_in_stability_and_difficulty(policy):
    for level in (1, 4, 12, 64):
        table = policy.expected_cost[level]
        assert np.all(np.diff(table, axis=1) <= 1e-9), f"not decreasing in S at b={level}"
        assert np.all(np.diff(table, axis=0) >= -1e-9), f"not increasing in D at b={level}"


def test_queries_above_max_budget_are_clamped_safely(policy):
    assert policy.cost_of(MemoryState(2.0, 7.0), 10_000) == pytest.approx(
        policy.cost_of(MemoryState(2.0, 7.0), policy.config.max_budget)
    )


# --------------------------------------------------------------------------- #
# Cross-validation against the other solver
# --------------------------------------------------------------------------- #


def test_agrees_with_value_iteration_on_a_matched_grid(policy):
    """Two solvers, two structures, one answer.

    `ssp.solve` iterates a Bellman operator to a fixed point with no budget.
    This one recurses backwards over the budget in a single pass. Given a large
    budget the constraint is slack and they must agree; on identical grids they
    do, to 2e-8. On the default grids they differ by up to 6e-2, which is
    discretisation and not disagreement — hence the matched grids here.
    """
    matched = solve_ssp(
        SSPConfig(
            target_stability=TARGET,
            n_stability=policy.config.n_stability,
            n_retentions=policy.config.n_retentions,
            min_retention=policy.config.min_retention,
            max_retention=policy.config.max_retention,
            min_stability=policy.config.min_stability,
        )
    )
    for stability, difficulty in ((0.5, 9.0), (2.0, 7.0), (5.0, 5.0), (15.0, 3.0)):
        slack = policy.cost_of(MemoryState(stability, difficulty), policy.config.max_budget)
        unconstrained = matched.expected_reviews(difficulty, stability)
        assert slack == pytest.approx(unconstrained, abs=1e-6)


# --------------------------------------------------------------------------- #
# Admissibility plumbing
# --------------------------------------------------------------------------- #


def test_optimistic_mode_is_a_lower_bound_everywhere(policy):
    lower = solve_budget(BudgetConfig.for_heuristic(TARGET))
    assert np.all(lower.expected_cost <= policy.expected_cost + 1e-9)
    assert lower.config.interpolation == "optimistic"


def test_solve_refuses_a_budget_that_still_binds():
    """Clamping a query against a still-decreasing value function overestimates,
    and an overestimating heuristic voids the search's guarantee silently. The
    solver would rather fail than hand one back."""
    with pytest.raises(RuntimeError, match="still binds"):
        solve_budget(BudgetConfig(target_stability=TARGET, max_budget=20))


def test_slack_check_can_be_waived_for_diagnostics():
    coarse = solve_budget(
        BudgetConfig(target_stability=TARGET, max_budget=20), require_slack=False
    )
    assert coarse.binding_budget == coarse.config.max_budget


def test_unknown_interpolation_mode_is_rejected():
    with pytest.raises(ValueError, match="interpolation"):
        solve_budget(BudgetConfig(target_stability=TARGET, interpolation="cubic"))
