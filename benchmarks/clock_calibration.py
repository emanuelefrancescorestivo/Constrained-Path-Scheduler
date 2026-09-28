#!/usr/bin/env python3
"""
Calibration and looseness of the clock value function (src/cps/clock.py).

    python benchmarks/clock_calibration.py            # about 15 s
    python benchmarks/clock_calibration.py --refine   # adds the grid-refinement table, about a minute more

Reproduces the numbers quoted in docs/METHOD.md section 6, docs/PROCESS.md
Phase 6 and tests/test_clock.py:

  * the accurate estimate, the optimistic bound and the simulated cost of the
    policy the estimate induces (free choice of review times, 2,000 runs, fixed
    seeds), with standard errors;
  * the value just below a 9-day target (PROCESS.md, Mistake 14);
  * with --refine, how little a finer (D, S) grid tightens the bound (Mistake 13).
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from cps.clock import ClockConfig, simulate, solve
from cps.console import ensure_utf8_output
from cps.memory import MemoryState

EXAM = 21.0
STATES = [MemoryState(2.0, 7.0), MemoryState(4.0, 5.0), MemoryState(8.0, 6.0), MemoryState(0.5, 8.0)]


def main() -> None:
    ensure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials", type=int, default=2000)
    parser.add_argument("--refine", action="store_true")
    args = parser.parse_args()

    accurate = solve(ClockConfig.for_exam(EXAM, 0.9))
    bound = solve(ClockConfig.for_exam(EXAM, 0.9, interpolation="optimistic"))
    penalty = accurate.config.failure_penalty

    print(f"exam in {EXAM:g} days, target {accurate.target_stability:.0f}, penalty {penalty:g}, "
          f"{args.trials} simulated runs per state")
    print(f"  {'state':<14} {'estimate':>8} {'bound':>7} {'simulated':>16} {'ready':>6}")
    for state in STATES:
        costs = []
        for seed in range(args.trials):
            reviews, ready = simulate(accurate, state, EXAM, np.random.default_rng(seed))
            costs.append(reviews + (0.0 if ready else penalty))
        costs = np.array(costs)
        se = costs.std(ddof=1) / np.sqrt(len(costs))
        print(f"  S={state.stability:<4g} D={state.difficulty:<4g} "
              f"{accurate.post_review(state.difficulty, state.stability, EXAM):>8.2f} "
              f"{bound.post_review(state.difficulty, state.stability, EXAM):>7.2f} "
              f"{costs.mean():>9.2f} ± {se:.2f} {np.mean(costs < penalty):>6.1%}")

    nine = solve(ClockConfig.for_exam(9.0, 0.9))
    print("\nvalue just below a 9-day target (D=6.9, 0.1 days since the review, 5 days left):")
    print("  " + ", ".join(f"S={9 * f:.3f}: {nine.waiting(6.9, 9 * f, 0.1, 5.0):.3f}"
                           for f in (0.95, 0.99, 0.999)))

    if args.refine:
        print("\nlooseness of the bound at S=2, D=7 against grid refinement:")
        print(f"  {'n_stability':>11} {'n_difficulty':>12} {'estimate':>8} {'bound':>7} {'seconds':>8}")
        for n_s, n_d in ((96, 19), (384, 19), (96, 91), (384, 91)):
            started = time.perf_counter()
            est = solve(ClockConfig.for_exam(EXAM, 0.9, n_stability=n_s, n_difficulty=n_d))
            low = solve(ClockConfig.for_exam(EXAM, 0.9, n_stability=n_s, n_difficulty=n_d,
                                             interpolation="optimistic"))
            print(f"  {n_s:>11} {n_d:>12} {est.post_review(7, 2, EXAM):>8.2f} "
                  f"{low.post_review(7, 2, EXAM):>7.2f} {time.perf_counter() - started:>8.1f}")


if __name__ == "__main__":
    main()
