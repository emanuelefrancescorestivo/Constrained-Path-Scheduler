"""
Tests for receding-horizon replanning.

Read `test_readiness_is_poor_because_the_continuation_has_no_clock` first. It is
marked `xfail(strict=True)`: the loop is correct window-by-window and scales to a
real calendar, but the objective it optimises inside each window cannot see the
calendar clock, so the planner procrastinates. The test asserts the property we
want and will break the build the moment it starts holding — which is how the
January defect was handled and is the only way a known gap stays known.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from cps.budget import BudgetConfig
from cps.budget import solve as budget_solve
from cps.calendar_io import load_availability, plan_to_ics
from cps.memory import Grade, MemoryState, stability_for_interval
from cps.plan import Instance, PlanState, TopicState, solve_exact, tile_free_time
from cps.rolling import BudgetedContinuation, budgeted_heuristic, run_rolling
from cps.ssp import SSPConfig
from cps.ssp import solve as ssp_solve

EXAM_DAYS = 21.0
TARGET = stability_for_interval(EXAM_DAYS, 0.9)
TOPICS = [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))]

TIMETABLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:lecture@test
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/London:20260302T090000
DTEND;TZID=Europe/London:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20260525T235959Z
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(scope="module")
def policy():
    return budget_solve(BudgetConfig.for_heuristic(TARGET))


@pytest.fixture(scope="module")
def blocks():
    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), 21, "Europe/London")
    return tile_free_time(grid, block_slots=3, max_blocks_per_day=2)


# --------------------------------------------------------------------------- #
# The continuation
# --------------------------------------------------------------------------- #


def test_continuation_refuses_a_value_function_that_is_not_a_bound():
    accurate = budget_solve(BudgetConfig(target_stability=TARGET))
    with pytest.raises(ValueError, match="optimistic"):
        BudgetedContinuation(accurate, budget=10)


def test_continuation_refuses_a_negative_budget(policy):
    with pytest.raises(ValueError, match="non-negative"):
        BudgetedContinuation(policy, budget=-1)


def test_deferring_costs_almost_nothing_when_the_budget_is_slack(policy):
    """The root cause, isolated as a property of the value function.

    One fewer block is worth 2.6e+01 when only one remains and 3.4e-09 when
    forty-two do. Since a real horizon offers about forty, the window objective
    is numerically indifferent to postponing, and indifference is decided by
    tie-breaking rather than by the model.
    """
    slack = policy.marginal_value(7.0, 2.0, 42)
    binding = policy.marginal_value(7.0, 2.0, 1)
    assert slack < 1e-6
    assert binding > 1.0
    assert binding / max(slack, 1e-300) > 1e6


def test_budgeted_heuristic_is_admissible_across_a_whole_window(policy, blocks):
    """The window search is genuinely exact; the objective is what is wrong.

    Checked at every reachable state of a five-block window against exhaustive
    backward induction, exactly as for the one-shot solver.
    """
    pane = tuple(blocks[:5])
    budget_after = len(blocks) - len(pane)
    instance = Instance(
        topics=("Analysis", "Algebra"),
        blocks=pane,
        initial=PlanState(0, tuple(TopicState(m.stability, m.difficulty, 0.0) for _, m in TOPICS)),
        target_stability=TARGET,
        continuation=BudgetedContinuation(policy, budget_after),
        lateness_penalty=0.0,
    )
    exact = solve_exact(instance)
    heuristic = budgeted_heuristic(policy, budget_after)
    for state, optimal in exact.values.items():
        if instance.is_terminal(state):
            continue
        assert heuristic(instance, state) <= optimal + 1e-9


# --------------------------------------------------------------------------- #
# The loop mechanics, which do work
# --------------------------------------------------------------------------- #


def test_it_scales_to_a_real_calendar(policy, blocks):
    """The thing replanning was built for: about forty blocks, where the one-shot
    solver stops at ten."""
    assert len(blocks) > 30
    result = run_rolling(blocks, TOPICS, TARGET, policy, window=6)
    assert result.solves > 10
    assert result.peak_expansions < 2000, result.peak_expansions
    assert result.blocks_used <= len(blocks)


def test_a_seed_makes_it_reproducible(policy, blocks):
    first = run_rolling(blocks, TOPICS, TARGET, policy, window=4, rng=np.random.default_rng(7))
    second = run_rolling(blocks, TOPICS, TARGET, policy, window=4, rng=np.random.default_rng(7))
    assert [(s.block.slot, s.subject, s.outcome) for s in first.sessions] == [
        (s.block.slot, s.subject, s.outcome) for s in second.sessions
    ]


def test_aggregation_does_not_leak_into_the_executed_trajectory(policy, blocks):
    """Snapping stability onto a grid is a search device. If it reached the
    reported trajectory the stabilities shown to the student would be fiction."""
    result = run_rolling(blocks, TOPICS, TARGET, policy, window=4, stability_step=0.3)
    assert result.sessions
    for session in result.sessions:
        on_grid = abs(session.stability_after / round(session.stability_after, 0) - 1.0) < 1e-9
        assert not on_grid or session.stability_after < 1.0


def test_a_forced_lapse_costs_extra_blocks(policy, blocks):
    """Reacting to what actually happened is the whole point of replanning."""
    clean = run_rolling(blocks, TOPICS, TARGET, policy, window=4)
    lapsed = run_rolling(blocks, TOPICS, TARGET, policy, window=4, force_lapse_at=(0,))
    assert lapsed.lapses == 1
    assert lapsed.blocks_used > clean.blocks_used


def test_it_stops_once_every_topic_is_ready(policy, blocks):
    ready = [("Analysis", MemoryState(TARGET + 5, 5.0)), ("Algebra", MemoryState(TARGET + 5, 5.0))]
    result = run_rolling(blocks, ready, TARGET, policy, window=4)
    assert result.blocks_used == 0
    assert result.solves == 0
    assert all(result.ready)


def test_a_mismatched_policy_is_refused(policy, blocks):
    with pytest.raises(ValueError, match="deadline"):
        run_rolling(blocks, TOPICS, TARGET * 2, policy, window=4)


def test_sessions_export_to_a_calendar_with_their_reason(policy, blocks):
    result = run_rolling(blocks, TOPICS, TARGET, policy, window=4)
    ics = plan_to_ics(result.to_ics_sessions(), date(2026, 3, 2), "Europe/London")
    assert "Study: " in ics
    assert "Stability" in ics.replace("\r\n ", "")
    _, events = load_availability(ics, date(2026, 3, 2), 21, "Europe/London", study_window=None)
    assert len(events) == result.blocks_used


# --------------------------------------------------------------------------- #
# The defect
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The continuation V(D, S, b) is indexed by remaining blocks, not by time "
        "remaining before the exam, so postponing a review is free whenever blocks "
        "are plentiful — the marginal value of a block at b=42 is 3.4e-09. Each "
        "window therefore defers, evaluating the deferral on the assumption of good "
        "behaviour later, and by the time the budget binds there is no calendar left "
        "to space the reviews. Measured with benchmarks/replanning.py: first review on "
        "day 16-19 of 21 instead of around day 3-4, and both topics ready in 35-38% of "
        "runs. The fix is to index "
        "the value function by days remaining as well as blocks, so that waiting "
        "consumes the resource it actually consumes."
    ),
)
def test_readiness_is_poor_because_the_continuation_has_no_clock(policy, blocks):
    """The property a study planner has to have, and does not yet have."""
    successes = 0
    trials = 12
    for seed in range(trials):
        result = run_rolling(
            blocks, TOPICS, TARGET, policy, window=2, rng=np.random.default_rng(seed)
        )
        successes += all(result.ready)
    assert successes / trials >= 0.8


def test_procrastination_is_measurable_and_currently_severe(policy, blocks):
    """Pins the current behaviour so the fix is visibly a change, not a claim.

    A topic starting at S=2 should get its first review around day 4, where
    retrievability is near the 0.82-0.85 band the SSP analysis identified. It
    currently gets it after day 12, at retrievability below 0.7.
    """
    result = run_rolling(blocks, TOPICS, TARGET, policy, window=2)
    assert result.sessions
    first = result.sessions[0]
    assert first.block.day > 12, "if this now fails, the clock fix has landed — update the xfail"
    assert first.retrievability_at_review < 0.7
