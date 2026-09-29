#!/usr/bin/env python3
"""
Reproduces AUDIT.md item 26: state aggregation that crosses the goal.

    python benchmarks/aggregation_goal_crossing.py      # a few seconds

Solves the ten-day, two-topic instance of demo.py section 4 twice, with the
current `Instance.successors` (goal membership decided by the exact state) and
with the pre-M1 version, reconstructed below, that snapped without that guard.
For each it prints the cost AO* reports in the aggregated model and the cost of
following the same policy with exact FSRS dynamics (`plan.evaluate_exact_dynamics`).
"""

from __future__ import annotations

from dataclasses import dataclass

from cps.console import ensure_utf8_output
from cps.memory import Grade, MemoryState, retrievability, review, stability_for_interval
from cps.plan import (
    SKIP,
    Instance,
    PlanState,
    TopicState,
    capacity_heuristic,
    evaluate_exact_dynamics,
    evaluate_policy,
    solve_ao_star,
)
from cps.ssp import SSPConfig, solve
from cps.timegrid import TimeGrid

TARGET = stability_for_interval(21.0, 0.9)
TOPICS = [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))]


@dataclass(frozen=True, slots=True)
class UnguardedInstance(Instance):
    """`Instance.successors` as it was before milestone M1: snap, no goal guard."""

    def successors(self, state: PlanState, action: int):
        nxt_index = state.block_index + 1
        if action == SKIP:
            return 0.0, ((1.0, PlanState(nxt_index, state.topics)),)
        block = self.blocks[state.block_index]
        topic = state.topics[action]
        elapsed = max(block.start_day - topic.last_review_day, 0.0)
        r = retrievability(elapsed, topic.stability)
        outcomes = []
        for prob, grade in ((r, Grade.GOOD), (1.0 - r, Grade.AGAIN)):
            if prob <= 0.0:
                continue
            after = self.snap(review(topic.as_memory(), elapsed, grade, self.weights))
            topics = list(state.topics)
            topics[action] = TopicState(after.stability, after.difficulty, block.start_day)
            outcomes.append((prob, PlanState(nxt_index, tuple(topics))))
        return 1.0, tuple(outcomes)


def student_week(days: int) -> TimeGrid:
    weekdays = [d for d in range(days) if d % 7 < 5]
    return TimeGrid(days=days).block_daily(23, 7).block_daily(8, 18, days=weekdays)


def main() -> None:
    ensure_utf8_output()
    continuation = solve(SSPConfig.for_heuristic(TARGET))
    base = Instance.build(
        student_week(10),
        TOPICS,
        TARGET,
        continuation,
        max_blocks_per_day=1,
        lateness_penalty=12.0,
        stability_step=0.15,
        difficulty_step=0.5,
    )
    old = UnguardedInstance(**{f: getattr(base, f) for f in base.__dataclass_fields__})
    print(f"{'successors':<22} {'reported':>9} {'exact dynamics':>15}  plan if every recall succeeds")
    for name, instance in (("before M1 (unguarded)", old), ("now (guarded)", base)):
        solution = solve_ao_star(instance, capacity_heuristic, max_expansions=40_000)
        plan = ", ".join(f"day {b.day} {t}" for b, t in solution.trajectory(instance))
        print(
            f"{name:<22} {evaluate_policy(instance, solution):>9.2f} "
            f"{evaluate_exact_dynamics(instance, solution):>15.2f}  {plan}"
        )


if __name__ == "__main__":
    main()
