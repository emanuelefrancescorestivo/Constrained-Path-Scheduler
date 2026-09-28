#!/usr/bin/env python3
"""
Constrained-Path Scheduler — demo.

Reproduces every number quoted in docs/PROCESS.md and docs/METHOD.md.

    python demo.py            # everything
    python demo.py --quick    # skip the exhaustive reference solve

Nothing here is hard-coded: the tables are computed on the spot, so a change to
the model or the heuristics shows up in the output immediately.
"""

from __future__ import annotations

import argparse
import time

from cps.console import ensure_utf8_output
from cps.memory import (
    Grade,
    MemoryState,
    initial_state,
    retrievability,
    review,
    stability_for_interval,
)
from cps.plan import (
    Instance,
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
from cps.ssp import SSPConfig, mean_reviews_to_target, reviews_statistics, solve
from cps.timegrid import TimeGrid

EXAM_IN_DAYS = 21.0
TARGET = stability_for_interval(EXAM_IN_DAYS, 0.9)
TOPICS = [
    ("Analysis", MemoryState(2.0, 7.0)),
    ("Algebra", MemoryState(4.0, 5.0)),
    ("Physics", MemoryState(3.0, 6.0)),
]
HEURISTICS = (
    ("h = 0", zero_heuristic),
    ("h = closed-form", closed_form_heuristic),
    ("h = SSP", ssp_heuristic),
    ("h = SSP + capacity", capacity_heuristic),
)


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def student_week(days: int) -> TimeGrid:
    """Sleep 23:00–07:00; classes and commute 08:00–18:00 on weekdays."""
    weekdays = [d for d in range(days) if d % 7 < 5]
    return TimeGrid(days=days).block_daily(23, 7).block_daily(8, 18, days=weekdays)


# --------------------------------------------------------------------------- #


def part_1_memory_model() -> None:
    rule("1. The memory model responds to timing (the January model could not)")
    state = MemoryState(stability=5.0, difficulty=5.0)
    print(f"  {'delay':>8} {'R at review':>12} {'FSRS S′':>10} {'January S′':>12}")
    for delay in (0.1, 0.5, 2.0, 5.0, 15.0, 60.0):
        fsrs = review(state, delay, Grade.GOOD).stability
        legacy = state.stability * (1 + 2.718281828 ** (state.difficulty / 10) * 0.2)
        print(
            f"  {delay:>7.1f}d {retrievability(delay, state.stability):>12.3f} "
            f"{fsrs:>10.2f} {legacy:>12.2f}"
        )
    print("\n  Interval growth from a fresh item, six consecutive Good ratings:")
    fresh, seq = initial_state(Grade.GOOD), []
    for _ in range(6):
        interval = fresh.ideal_interval()
        seq.append(round(interval, 1))
        fresh = review(fresh, interval, Grade.GOOD)
    print(f"    ours      {seq}")
    print("    reference [4, 14, 44, 125, 328]  (py-fsrs, default parameters)")


def part_2_ssp() -> None:
    rule("2. SSP-MMC: the unconstrained optimum, and the heuristic it yields")
    started = time.perf_counter()
    accurate = solve(SSPConfig(target_stability=365.0))
    elapsed = time.perf_counter() - started
    lower = solve(SSPConfig.for_heuristic(365.0))
    fresh = initial_state(Grade.GOOD)
    print(f"  value iteration: {accurate.sweeps} sweeps, residual {accurate.residual:.1e}, "
          f"{elapsed:.1f}s")
    predicted = accurate.expected_reviews(fresh.difficulty, fresh.stability)
    mean, sem = reviews_statistics(accurate, fresh, trials=1500)
    print(f"\n  V* from a fresh item      : {predicted:6.3f} reviews")
    print(f"  simulated (6000 runs)     : {mean:6.3f} ± {sem:.3f}   <- calibration")
    print(f"  guaranteed lower bound    : {lower.expected_reviews(fresh.difficulty, fresh.stability):6.3f}"
          "   <- admissible, and loose because of it")

    gap = accurate.expected_cost - lower.expected_cost
    live = accurate.expected_cost > 1e-9
    print(f"  bound vs accurate, whole grid: mean gap {gap[live].mean():.2f} reviews "
          f"({100 * (gap[live] / accurate.expected_cost[live]).mean():.0f}%), "
          f"smallest gap {gap.min():+.1e}")
    print("    a negative smallest gap means the heuristic solve, which uses its own finite")
    print("    action grid, sits above the analysis solve in some cell: the discretisation")
    print("    caveat in SSPConfig.for_heuristic, measured. See AUDIT.md item 22.")

    print("\n  Optimal policy vs a fixed target retention (4 seeds x 1500 runs, mean ± s.e.):")
    for fixed in (None, 0.95, 0.90, 0.85, 0.80, 0.70):
        label = "optimal policy" if fixed is None else f"fixed R = {fixed:.2f}"
        blocks, error = reviews_statistics(
            accurate, fresh, trials=1500, seeds=(3, 4, 5, 6), fixed_retention=fixed
        )
        print(f"    {label:18s} {blocks:6.2f} ± {error:.2f} blocks")
    print("    Fixed retention is not monotone (0.85 is worse than both 0.80 and 0.90);")
    print("    see the open question in docs/CLAUDE_CODE_PROMPT.md, milestone M4.")

    band = accurate.target_retention[:, :-1]
    ordinary = [float(band[i].mean()) for i in range(2, 16)]  # D from 2.0 to 8.5
    print(f"\n  mean π* per difficulty, D in [2, 8.5]: {min(ordinary):.3f}–{max(ordinary):.3f}")
    print("  published band for workload-minimising desired retention: 0.75–0.90")

    print("\n  The optimum moves with the cost model (D=5, S=8):")
    for lapse_cost in (1.0, 2.0, 4.0):
        priced = solve(SSPConfig(target_stability=365.0, cost_lapse=lapse_cost))
        print(f"    a lapse costs {lapse_cost:.0f}x a productive block -> "
              f"π* = {priced.optimal_retention(5.0, 8.0):.3f}")

    print("\n  How well is the optimal retention identified? Width of the range of")
    print("  retentions that lose less than 0.01 review against the best:")
    retentions = accurate.config.retentions()
    for difficulty, stability in ((5.0, 2.0), (5.0, 30.0), (8.0, 8.0), (2.0, 8.0)):
        values = accurate.action_values(difficulty, stability)
        near = retentions[values <= values.min() + 0.01]
        print(f"    D={difficulty:.0f} S={stability:>4.0f}: best {retentions[values.argmin()]:.3f}, "
              f"plateau {near.max() - near.min():.2f} wide")


def part_3_search(quick: bool) -> None:
    rule("3. AO* on the calendar, against exhaustive search")
    continuation = solve(SSPConfig.for_heuristic(TARGET))
    instance = Instance.build(
        student_week(5), TOPICS[:2], TARGET, continuation, max_blocks_per_day=1
    )
    print(f"  instance: {len(instance.blocks)} blocks, 2 topics, exam in {EXAM_IN_DAYS:.0f} days "
          f"(target S = {TARGET:.0f}), lateness penalty {instance.lateness_penalty:.0f}")

    optimum = None
    if not quick:
        started = time.perf_counter()
        exact = solve_exact(instance)
        optimum = exact.value
        print(f"\n  exhaustive backward induction: {exact.value:.5f} over {exact.nodes:,} states "
              f"in {time.perf_counter() - started:.1f}s")

    print(f"\n  {'heuristic':<20} {'h(root)':>9} {'value':>10} {'expansions':>11} {'optimal':>8}")
    for name, heuristic in HEURISTICS:
        solution = solve_ao_star(instance, heuristic)
        ok = "—" if optimum is None else str(abs(solution.value - optimum) < 1e-9)
        print(f"  {name:<20} {heuristic(instance, instance.initial):>9.3f} "
              f"{solution.value:>10.5f} {solution.nodes:>11,} {ok:>8}")

    if optimum is not None:
        print(f"\n  Weighted AO*: bounded suboptimality, stated and measured.")
        print(f"  {'w':>5} {'expansions':>11} {'plan cost':>11} {'real gap':>9} {'bound':>7}")
        for weight in (1.0, 1.2, 1.5, 2.0, 3.0):
            solution = solve_ao_star(instance, capacity_heuristic, weight=weight)
            cost = evaluate_policy(instance, solution)
            print(f"  {weight:>5.1f} {solution.nodes:>11,} {cost:>11.4f} "
                  f"{cost / optimum:>8.3f}x {weight:>6.1f}x")


def part_4_findings() -> None:
    rule("4. What the scheduler actually tells a student")
    continuation = solve(SSPConfig.for_heuristic(TARGET))

    print("  (a) Spacing, not slot count, is the binding constraint.")
    print("      All blocks on one topic, every recall successful:")
    for days in (5, 7, 10, 14):
        blocks = tile_free_time(student_week(days), max_blocks_per_day=1)
        state, last = MemoryState(4.0, 5.0), 0.0
        for block in blocks:
            state = review(state, block.start_day - last, Grade.GOOD)
            last = block.start_day
        verdict = "reaches target" if state.stability >= TARGET else "CANNOT reach target"
        print(f"        {days:>2} days, {len(blocks):>2} blocks -> S = {state.stability:6.2f}   {verdict}")

    print("\n  (b) Per-topic reachability is what drives the plan.")
    print("      All blocks spent on that one topic, every recall successful:")
    header = "        " + f"{'topic':<10}" + "".join(f"{d:>4}d" for d in (7, 10, 14, 21))
    print(header)
    for name, start_state in TOPICS[:2]:
        cells = []
        for days in (7, 10, 14, 21):
            state, last = start_state, 0.0
            for block in tile_free_time(student_week(days), max_blocks_per_day=1):
                state = review(state, block.start_day - last, Grade.GOOD)
                last = block.start_day
            cells.append(f"{state.stability:5.1f}" + ("*" if state.stability >= TARGET else " "))
        print(f"        {name:<10}" + "".join(cells))
    print(f"        (* clears the target of S = {TARGET:.0f})")

    print("\n      So on a seven-day horizon the optimum abandons the harder topic —")
    print("      not arbitrary triage, but because it is provably out of reach there.")
    print("      The lateness penalty is pinned below: its default is derived from the")
    print("      number of blocks, which is sound within one instance and meaningless")
    print("      across two, so an A/B comparison must fix it.")
    for days in (7, 10):
        instance = Instance.build(
            student_week(days), TOPICS[:2], TARGET, continuation,
            max_blocks_per_day=1, lateness_penalty=12.0,
            stability_step=0.15, difficulty_step=0.5,
        )
        solution = solve_ao_star(instance, capacity_heuristic, max_expansions=40_000)
        plan = solution.trajectory(instance)
        print(f"\n      {days}-day horizon ({len(instance.blocks)} blocks): "
              f"cost {evaluate_policy(instance, solution):.2f} "
              f"(followed with exact dynamics {evaluate_exact_dynamics(instance, solution):.2f}), "
              f"{len(plan)} blocks used")
        for block, name in plan:
            print(f"          day {block.day:>2}  {block.start_day:5.2f}d  ->  {name}")
        if not plan:
            print("          (nothing — no topic is reachable in this horizon)")


def part_5_scaling() -> None:
    rule("5. Where it stops working, and why")
    continuation = solve(SSPConfig.for_heuristic(TARGET))
    print(f"  {'days':>5} {'bl/day':>7} {'topics':>7} {'blocks':>7} {'aggreg.':>8} {'expansions':>11} {'time':>7}")
    settings = ((5, 1, 2), (7, 1, 2), (10, 1, 2), (7, 2, 2), (14, 1, 2))
    for days, per_day, count in settings:
        for step in (0.0, 0.15):
            instance = Instance.build(
                student_week(days), TOPICS[:count], TARGET, continuation,
                max_blocks_per_day=per_day,
                stability_step=step, difficulty_step=0.5 if step else 0.0,
            )
            started = time.perf_counter()
            try:
                solution = solve_ao_star(instance, capacity_heuristic, max_expansions=30_000)
                nodes = f"{solution.nodes:,}"
            except RuntimeError:
                nodes = ">30k"
            print(f"  {days:>5} {per_day:>7} {count:>7} {len(instance.blocks):>7} "
                  f"{('on' if step else 'off'):>8} {nodes:>11} {time.perf_counter() - started:>6.1f}s")
    print("\n  Inflating h prunes exploration outside the solution graph. It cannot shrink")
    print("  the graph itself, which is exponential in the number of coin flips along a")
    print("  path. State aggregation attacks that; receding-horizon replanning is next.")


def main() -> None:
    ensure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="skip the exhaustive reference solve")
    args = parser.parse_args()

    part_1_memory_model()
    part_2_ssp()
    part_3_search(args.quick)
    part_4_findings()
    part_5_scaling()
    print()


if __name__ == "__main__":
    main()
