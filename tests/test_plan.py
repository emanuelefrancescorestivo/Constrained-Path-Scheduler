"""
Tests for the constrained scheduler.

The two that carry the weight of the project's central claim:

`test_ao_star_returns_the_exact_optimum` — the January report asserted
optimality. Here it is checked against exhaustive backward induction.

`test_every_heuristic_is_admissible_at_every_reachable_state` — admissibility is
what makes the previous test's result a guarantee rather than a coincidence, and
it is verified at all 3,906 reachable states rather than at the root.
"""

from __future__ import annotations

import pytest

from cps.memory import Grade, MemoryState, initial_state, review, stability_for_interval
from cps.plan import (
    SKIP,
    Instance,
    PlanState,
    best_case_reviews,
    capacity_heuristic,
    closed_form_heuristic,
    evaluate_exact_dynamics,
    evaluate_policy,
    solve_ao_star,
    solve_exact,
    ssp_heuristic,
    tile_free_time,
    zero_heuristic,
)
from cps.ssp import SSPConfig, solve
from cps.timegrid import TimeGrid

EXAM_DAYS = 21.0
TARGET = stability_for_interval(EXAM_DAYS, 0.9)
HEURISTICS = (
    ("zero", zero_heuristic),
    ("closed-form", closed_form_heuristic),
    ("ssp", ssp_heuristic),
    ("capacity", capacity_heuristic),
)


@pytest.fixture(scope="module")
def continuation():
    return solve(SSPConfig.for_heuristic(TARGET))


def student_week(days: int) -> TimeGrid:
    """Sleep 23:00-07:00 every night, classes plus commute 08:00-18:00 on weekdays."""
    weekdays = [d for d in range(days) if d % 7 < 5]
    return TimeGrid(days=days).block_daily(23, 7).block_daily(8, 18, days=weekdays)


def small_instance(continuation, **kwargs) -> Instance:
    """Five blocks, two topics: 3,906 reachable states, exhaustively solvable."""
    return Instance.build(
        student_week(5),
        [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))],
        TARGET,
        continuation,
        max_blocks_per_day=1,
        **kwargs,
    )


@pytest.fixture(scope="module")
def instance(continuation):
    return small_instance(continuation)


@pytest.fixture(scope="module")
def exact(instance):
    return solve_exact(instance)


# --------------------------------------------------------------------------- #
# The calendar
# --------------------------------------------------------------------------- #


def test_blocks_are_disjoint_and_respect_the_daily_cap():
    grid = student_week(7)
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    assert blocks, "a student with evenings free should have candidate blocks"
    occupied: set[int] = set()
    per_day: dict[int, int] = {}
    for block in blocks:
        slots = set(range(block.slot, block.slot + 3))
        assert not slots & occupied, "candidate blocks must not overlap"
        assert grid.is_free(block.slot, 3), "a block was placed over a hard constraint"
        occupied |= slots
        per_day[block.day] = per_day.get(block.day, 0) + 1
    assert max(per_day.values()) <= 2


def test_block_times_are_expressed_in_days_for_the_memory_model():
    grid = student_week(7)
    blocks = tile_free_time(grid, max_blocks_per_day=1)
    assert all(b.start_day == pytest.approx(b.slot / 48) for b in blocks)
    assert all(a.start_day < b.start_day for a, b in zip(blocks, blocks[1:]))


def test_build_rejects_a_non_admissible_continuation_policy():
    """Structural guard. The continuation cost feeds straight into the heuristic,
    so an accurate-but-unbounded value function here would void the whole proof."""
    accurate = solve(SSPConfig(target_stability=TARGET))
    with pytest.raises(ValueError, match="optimistic"):
        Instance.build(student_week(5), [("A", initial_state(Grade.GOOD))], TARGET, accurate)


# --------------------------------------------------------------------------- #
# Optimality
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,heuristic", HEURISTICS)
def test_ao_star_returns_the_exact_optimum(instance, exact, name, heuristic):
    """Optimality as a measurement, not a claim.

    Exhaustive backward induction over all 3,906 reachable states gives the true
    optimal expected cost. AO* must reproduce it to floating-point tolerance with
    every admissible heuristic — a better heuristic may only change how many
    nodes are touched, never the answer.
    """
    solution = solve_ao_star(instance, heuristic)
    assert solution.value == pytest.approx(exact.value, rel=1e-12), name


def test_reported_value_equals_the_cost_of_the_returned_policy(instance, exact):
    """Unweighted, the root value and the plan's real cost must coincide. When
    they diverge — as they do under weighting — the plan's cost is what counts."""
    solution = solve_ao_star(instance, capacity_heuristic)
    assert evaluate_policy(instance, solution) == pytest.approx(solution.value, rel=1e-9)


def test_a_better_heuristic_expands_strictly_fewer_nodes(instance):
    """What the heuristic work actually bought, in the only currency that matters."""
    counts = {name: solve_ao_star(instance, h).nodes for name, h in HEURISTICS}
    assert counts["capacity"] < counts["ssp"] < counts["zero"]
    assert counts["capacity"] < 0.7 * counts["zero"]


def test_ao_star_touches_far_fewer_nodes_than_exhaustive_search(instance, exact):
    solution = solve_ao_star(instance, capacity_heuristic)
    assert solution.nodes < exact.nodes / 20


# --------------------------------------------------------------------------- #
# Admissibility
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,heuristic", HEURISTICS)
def test_every_heuristic_is_admissible_at_every_reachable_state(instance, exact, name, heuristic):
    """h <= h* everywhere, not just at the root.

    The exact solver keeps the optimal value of every state it visits, so this
    checks the property that makes AO*'s answer a guarantee. A heuristic that
    overestimates anywhere in the reachable space can steer the search past the
    optimum, and it will do so silently.
    """
    for state, optimal in exact.values.items():
        if instance.is_terminal(state):
            continue  # terminal nodes are scored by terminal_cost; h is never consulted
        assert heuristic(instance, state) <= optimal + 1e-9, (
            f"{name} overestimates at block {state.block_index}: "
            f"h={heuristic(instance, state):.4f} > h*={optimal:.4f}"
        )


def test_capacity_heuristic_dominates_the_plain_ssp_bound(instance, exact):
    """Both are admissible, so tighter is strictly better. Dominance everywhere
    is why the node count improves without risking the answer."""
    tighter = 0
    for state in exact.values:
        cap, ssp = capacity_heuristic(instance, state), ssp_heuristic(instance, state)
        assert cap >= ssp - 1e-9
        tighter += cap > ssp + 1e-9
    assert tighter > 0, "the capacity term never bound anything; it is dead weight"


def test_best_case_review_count_is_never_optimistic_enough_to_be_wrong():
    """The closed-form bound needs no discretisation argument, so it is the
    yardstick the SSP-based bound is checked against. It must stay below it."""
    for stability, difficulty in ((0.5, 9.0), (2.0, 7.0), (4.0, 5.0), (18.0, 3.0)):
        count = best_case_reviews(stability, difficulty, TARGET, horizon_days=21.0)
        assert count >= 1 if stability < TARGET else count == 0
        assert count <= 20


def test_a_shorter_horizon_can_only_raise_the_minimum_review_count():
    """Monotonicity of the bound in the time available: with less room to space
    reviews out, each one is worth less, so more of them are needed."""
    tight = best_case_reviews(2.0, 7.0, TARGET, horizon_days=2.0)
    loose = best_case_reviews(2.0, 7.0, TARGET, horizon_days=21.0)
    assert tight >= loose


# --------------------------------------------------------------------------- #
# Weighting: bounded suboptimality, done properly this time
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("weight", [1.2, 1.5, 2.0, 3.0])
def test_weighted_search_stays_inside_its_stated_bound(instance, exact, weight):
    """The January report used w = 10 and claimed optimality. The technique is
    fine; the bookkeeping was not. Here the bound is stated and checked."""
    solution = solve_ao_star(instance, capacity_heuristic, weight=weight)
    achieved = evaluate_policy(instance, solution)
    assert achieved <= weight * exact.value + 1e-9
    assert achieved >= exact.value - 1e-9


def test_the_realised_gap_is_far_smaller_than_the_bound(instance, exact):
    """Worth recording because it is the practical justification for weighting:
    a 20% guarantee buys a plan that is within a few percent in practice."""
    solution = solve_ao_star(instance, capacity_heuristic, weight=1.2)
    assert evaluate_policy(instance, solution) / exact.value < 1.10


def test_weight_below_one_is_refused(instance):
    """Deflating an admissible heuristic is not a speed knob; it is a way to lose
    the guarantee without any warning that you have."""
    with pytest.raises(ValueError, match="weight"):
        solve_ao_star(instance, capacity_heuristic, weight=0.9)


# --------------------------------------------------------------------------- #
# State aggregation
# --------------------------------------------------------------------------- #


def test_aggregation_barely_moves_the_answer(continuation, exact):
    """The only lever that shifts the scaling wall, so its cost must be measured.

    Snapping stability to a 15% multiplicative grid and difficulty to half-points
    leaves the plan's cost within a fraction of a percent of the exact optimum on
    an instance where both can be computed.
    """
    coarse = small_instance(continuation, stability_step=0.15, difficulty_step=0.5)
    solution = solve_ao_star(coarse, capacity_heuristic)
    assert abs(evaluate_policy(coarse, solution) / exact.value - 1.0) < 0.02
    # ...and followed in the real, unsnapped world, which is the cost that matters
    real = evaluate_exact_dynamics(coarse, solution)
    assert exact.value - 1e-9 <= real <= exact.value * 1.02


def test_the_aggregated_cost_is_the_real_cost_on_a_ten_day_plan(continuation):
    """AUDIT.md item 26. Before goal membership was decided by the exact state, the
    ten-day plan reported 8.83 blocks in the aggregated model and cost 28.6 when
    followed with exact dynamics: snapped stabilities crossed the target that the
    real ones had not reached. Now the two agree (17.28 and 17.29). Both versions:
    benchmarks/aggregation_goal_crossing.py."""
    coarse = Instance.build(
        student_week(10),
        [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))],
        TARGET,
        continuation,
        max_blocks_per_day=1,
        lateness_penalty=12.0,
        stability_step=0.15,
        difficulty_step=0.5,
    )
    solution = solve_ao_star(coarse, capacity_heuristic, max_expansions=40_000)
    claimed = evaluate_policy(coarse, solution)
    real = evaluate_exact_dynamics(coarse, solution)
    assert abs(real / claimed - 1.0) < 0.01, (claimed, real)


def test_aggregation_reduces_the_search(continuation, instance):
    coarse = small_instance(continuation, stability_step=0.15, difficulty_step=0.5)
    assert solve_ao_star(coarse, capacity_heuristic).nodes < solve_ao_star(
        instance, capacity_heuristic
    ).nodes


def test_snapping_is_idempotent(continuation):
    coarse = small_instance(continuation, stability_step=0.15, difficulty_step=0.5)
    once = coarse.snap(MemoryState(7.3, 6.4))
    assert coarse.snap(once) == once


# --------------------------------------------------------------------------- #
# Regressions on modelling traps that were actually fallen into
# --------------------------------------------------------------------------- #


def test_without_a_lateness_penalty_the_optimal_plan_is_to_do_nothing(continuation):
    """Regression for the degenerate objective.

    Charging leftover work at the unconstrained rate and nothing else makes the
    model indifferent to when blocks are spent: V_opt is the fixed point of a
    dominated Bellman operator, so spending a block now and paying V_opt later
    never beats just paying V_opt. The first run of the scheduler returned an
    empty plan whose cost matched the heuristic at the root to five decimals.
    """
    lazy = small_instance(continuation, lateness_penalty=0.0)
    solution = solve_ao_star(lazy, capacity_heuristic)
    assert solution.trajectory(lazy) == []
    assert solution.value == pytest.approx(ssp_heuristic(lazy, lazy.initial), rel=1e-9)


def test_spacing_not_slot_count_is_the_binding_constraint():
    """The finding that a failing test turned up, as an assertion.

    Spend every block of a five-day, one-block-a-day calendar on a single topic
    and get every recall right, and stability reaches 17.3 against a target of 21.
    The target is simply unreachable, which is why the optimal plan on that
    instance is to study nothing -- correct behaviour, not a bug. Stretch the same
    number of blocks per day over seven days and it clears the target.

    The reason is the spacing term: consecutive daily blocks mean every gap is one
    day, retrievability at review stays around 0.98, and the stability gain is
    near zero. What is scarce is calendar length, not slots.
    """
    for days, expected_reachable in ((5, False), (7, True), (10, True)):
        grid = student_week(days)
        blocks = tile_free_time(grid, max_blocks_per_day=1)
        state, last = MemoryState(4.0, 5.0), 0.0
        for block in blocks:
            state = review(state, block.start_day - last, Grade.GOOD)
            last = block.start_day
        assert (state.stability >= TARGET) is expected_reachable, (
            f"{days} days, {len(blocks)} blocks, perfect recall -> S={state.stability:.2f}"
        )


def test_the_plan_is_empty_exactly_when_the_target_is_unreachable(instance, continuation):
    """Five blocks cannot get there, so the optimum abandons both topics. Seven
    can, so it studies. The behaviour tracks feasibility rather than the penalty."""
    assert solve_ao_star(instance, capacity_heuristic).trajectory(instance) == []

    reachable = Instance.build(
        student_week(7),
        [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))],
        TARGET,
        continuation,
        max_blocks_per_day=1,
        stability_step=0.15,
        difficulty_step=0.5,
    )
    plan = solve_ao_star(reachable, capacity_heuristic).trajectory(reachable)
    assert plan, "seven daily blocks clear the target, so the plan should not be empty"
    days_used = [block.day for block, _ in plan]
    assert max(days_used) - min(days_used) >= 2, "reviews should be spread, not massed"


def test_extra_capacity_never_makes_the_plan_worse(continuation):
    """Sanity property, and the setup for the triage finding: more free evenings
    cannot increase the optimal expected cost."""
    tight = Instance.build(
        student_week(7),
        [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))],
        TARGET,
        continuation,
        max_blocks_per_day=1,
        stability_step=0.15,
        difficulty_step=0.5,
        lateness_penalty=12.0,
    )
    roomy = Instance.build(
        student_week(7),
        [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))],
        TARGET,
        continuation,
        max_blocks_per_day=2,
        stability_step=0.15,
        difficulty_step=0.5,
        lateness_penalty=12.0,
    )
    tight_cost = evaluate_policy(tight, solve_ao_star(tight, capacity_heuristic))
    roomy_cost = evaluate_policy(roomy, solve_ao_star(roomy, capacity_heuristic))
    assert roomy_cost <= tight_cost + 1e-9

    # And the finding: under the tight calendar the plan concentrates on fewer
    # subjects than it does when there is room for both.
    tight_subjects = {name for _, name in solve_ao_star(tight, capacity_heuristic).trajectory(tight)}
    roomy_subjects = {name for _, name in solve_ao_star(roomy, capacity_heuristic).trajectory(roomy)}
    assert len(tight_subjects) <= len(roomy_subjects)


def test_topics_already_at_target_are_not_offered_as_actions(instance):
    ready = PlanState(
        0,
        tuple(type(t)(TARGET + 1.0, t.difficulty, t.last_review_day) for t in instance.initial.topics),
    )
    assert instance.actions(ready) == (SKIP,)
    assert instance.is_terminal(ready)
    assert instance.terminal_cost(ready) == pytest.approx(0.0)
