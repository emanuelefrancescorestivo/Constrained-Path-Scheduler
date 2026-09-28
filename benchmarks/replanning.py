#!/usr/bin/env python3
"""
Baseline harness for the replanning defect (AUDIT.md item 20).

This is the yardstick for fixing it. Run it before touching anything, keep the
output, run it again afterwards: the fix has worked when the numbers below move,
not when someone says it has.

    python benchmarks/replanning.py                 # windows 1, 2, 4  (about 30 s)
    python benchmarks/replanning.py --windows 1 2 4 8 --seeds 100

The scenario is the one used in tests/test_rolling.py: a 21-day horizon, one
Monday lecture, two topics, an exam at day 21 and a stability target derived from
it. Two topics are enough to show the defect and small enough to run in seconds.

What to look at, and what a fixed planner should look like:

  * "value of the b-th block": today a block is worth ~1e-8 while the budget is
    slack, so the planner is indifferent to waiting. After the fix, waiting has to
    cost something well before the budget binds.
  * "first review": for a topic that starts at stability 2, retrievability decays
    to roughly 0.82-0.85 (the optimal-retention band from the SSP analysis) after
    3.5-4.5 days. The first review should land near there, not on day 13-19.
  * "ready": the share of stochastic runs in which every topic reaches the target.
    This is the number that matters to a student.
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import date

import numpy as np

from cps.budget import BudgetConfig
from cps.budget import solve as budget_solve
from cps.calendar_io import load_availability
from cps.console import ensure_utf8_output
from cps.memory import MemoryState, stability_for_interval
from cps.plan import tile_free_time
from cps.rolling import run_rolling

# Identical to the timetable in tests/test_rolling.py. Kept as a literal so that
# the benchmark does not import from the test suite.
TIMETABLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//benchmark//EN
BEGIN:VEVENT
UID:lecture@benchmark
SUMMARY:Analysis lecture
DTSTART;TZID=Europe/London:20260302T090000
DTEND;TZID=Europe/London:20260302T110000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20260525T235959Z
END:VEVENT
END:VCALENDAR
"""

EXAM_DAYS = 21.0
TOPICS = [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))]


def main() -> None:
    ensure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--seeds", type=int, default=60)
    parser.add_argument(
        "--target-days", type=float, default=EXAM_DAYS,
        help="the stability target is stability_for_interval(this, 0.9); the default is "
             "the exam date. 90 reproduces the 'durable retention' hypothesis that was "
             "tried and rejected in AUDIT.md item 20.",
    )
    args = parser.parse_args()

    target = stability_for_interval(args.target_days, 0.9)
    policy = budget_solve(BudgetConfig.for_heuristic(target))
    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), int(EXAM_DAYS), "Europe/London")
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    print(f"scenario: {int(EXAM_DAYS)} days, {len(blocks)} candidate blocks, "
          f"{len(TOPICS)} topics, target stability {target:.0f}")

    print("\nvalue of the b-th block, V(b-1) - V(b), topic at S=2, D=7:")
    for budget in (1, 5, 10, 20, len(blocks)):
        print(f"  b = {budget:>2}: {policy.marginal_value(7.0, 2.0, budget):.2e}")

    print("\ndeterministic run (every recall succeeds):")
    for window in args.windows:
        result = run_rolling(blocks, TOPICS, target, policy, window=window)
        first = ", ".join(
            f"day {s.block.day} {s.subject} (R={s.retrievability_at_review:.2f})"
            for s in result.sessions
        )
        print(f"  window {window}: {first or 'no sessions'}")

    print(f"\nstochastic runs ({args.seeds} seeds):")
    print(f"  {'window':>6} {'blocks':>7} {'lapses':>7} {'first review':>13} {'ready':>14} {'time':>7}")
    for window in args.windows:
        started = time.perf_counter()
        used, lapses, firsts, successes = [], [], [], 0
        for seed in range(args.seeds):
            result = run_rolling(
                blocks, TOPICS, target, policy, window=window, rng=np.random.default_rng(seed)
            )
            used.append(result.blocks_used)
            lapses.append(result.lapses)
            successes += all(result.ready)
            if result.sessions:
                firsts.append(result.sessions[0].block.day)
        p = successes / args.seeds
        se = math.sqrt(p * (1 - p) / args.seeds)
        first = f"day {np.mean(firsts):.1f}" if firsts else "-"
        print(f"  {window:>6} {np.mean(used):>7.2f} {np.mean(lapses):>7.2f} {first:>13} "
              f"{p:>8.0%} ± {se:.0%} {time.perf_counter() - started:>6.1f}s")


if __name__ == "__main__":
    main()
