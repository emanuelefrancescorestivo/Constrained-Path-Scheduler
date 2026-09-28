#!/usr/bin/env python3
"""
Before/after harness for the replanning defect (AUDIT.md item 20), and the
comparison of the planner against the two schedulers students actually use.

    python benchmarks/replanning.py                 # about a minute
    python benchmarks/replanning.py --seeds 200 --windows 1 2 4 6

The output before the fix is kept in docs/baselines/replanning-before.txt; the
numbers quoted in AUDIT.md, PROCESS.md, METHOD.md and the README come from this
script with its defaults.

The scenario is the one used in tests/test_rolling.py: a 21-day horizon, one
Monday lecture, two topics (Analysis at S=2, D=7 and Algebra at S=4, D=5), both
examined at the end of day 21, and a stability target of 21 days, the stability at
which recall would still be 90% if the whole preparation period elapsed again.

Three schedulers, all stopping work on a subject once it reaches its target and
none studying after its exam, all on the same calendar and the same seeds:

  planner     rolling AO* with the clock continuation (src/cps/rolling.py)
  greedy-0.90 review a subject when its recall has fallen to 0.90 or below, the
              most overdue first: the fixed desired retention of Anki-style tools
  every-k     review each subject every k days; k is chosen *after the fact* as
              the best of 1..7 on these seeds, which favours the baseline

"ready" means both subjects reached their target, reported with the binomial
standard error. The failure penalty is pinned at 40 blocks (CLAUDE.md invariant 6).
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import date
from typing import Callable, Sequence

import numpy as np

from cps.calendar_io import load_availability
from cps.console import ensure_utf8_output
from cps.memory import Grade, MemoryState, retrievability, review
from cps.plan import Block, tile_free_time
from cps.rolling import Subject, run_rolling, solve_deadlines

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
RETENTION = 0.9
PENALTY = 40.0
SUBJECTS = (
    Subject("Analysis", MemoryState(2.0, 7.0), EXAM_DAYS),
    Subject("Algebra", MemoryState(4.0, 5.0), EXAM_DAYS),
)


class Outcome:
    """What one run of any scheduler produced, in the same shape for all."""

    def __init__(self, sessions: list[tuple[float, int, bool]], final: list[tuple[MemoryState, float]],
                 targets: Sequence[float]):
        self.sessions = sessions  # (start_day, subject index, recalled)
        self.ready = all(m.stability >= g for (m, _), g in zip(final, targets))
        self.recall = float(np.mean([retrievability(EXAM_DAYS - last, m.stability) for m, last in final]))
        self.stability = float(np.mean([m.stability for m, _ in final]))
        self.lapses = sum(1 for *_, ok in sessions if not ok)
        first = [d for d, i, _ in sessions if i == 0]
        self.first_analysis = first[0] if first else math.nan


def simulate_rule(blocks: Sequence[Block], targets: Sequence[float],
                  due: Callable[[MemoryState, float, float], float | None],
                  rng: np.random.Generator | None) -> Outcome:
    """A rule-based scheduler. `due(memory, last_review_day, now)` returns an
    urgency (lower is more urgent) or None if the subject is not due."""
    state = [(s.memory, 0.0) for s in SUBJECTS]
    sessions = []
    for block in blocks:
        now = block.start_day
        candidates = []
        for i, ((memory, last), subject, target) in enumerate(zip(state, SUBJECTS, targets)):
            if memory.stability >= target or now >= subject.exam_day:
                continue
            urgency = due(memory, last, now)
            if urgency is not None:
                candidates.append((urgency, i))
        if not candidates:
            continue
        _, i = min(candidates)
        memory, last = state[i]
        r = retrievability(now - last, memory.stability)
        ok = True if rng is None else bool(rng.random() < r)
        state[i] = (review(memory, now - last, Grade.GOOD if ok else Grade.AGAIN), now)
        sessions.append((now, i, ok))
    return Outcome(sessions, state, targets)


def greedy_fixed(retention: float):
    def due(memory, last, now):
        r = retrievability(now - last, memory.stability)
        return r if r <= retention else None
    return due


def every_k(k: float):
    def due(memory, last, now):
        return last - now if now - last >= k else None
    return due


def planner(blocks, continuation, window, rng) -> Outcome:
    result = run_rolling(blocks, SUBJECTS, continuation, window=window, rng=rng)
    index = {name: i for i, name in enumerate(result.subjects)}
    sessions = [(s.block.start_day, index[s.subject], s.outcome != Grade.AGAIN) for s in result.sessions]
    final = [(t.as_memory(), t.last_review_day) for t in result.final_topics]
    return Outcome(sessions, final, result.targets)


def summarise(name: str, outcomes: list[Outcome], seconds: float) -> str:
    n = len(outcomes)
    p = sum(o.ready for o in outcomes) / n
    se = math.sqrt(p * (1 - p) / n)
    used = np.array([len(o.sessions) for o in outcomes], dtype=float)
    firsts = np.array([o.first_analysis for o in outcomes])
    first = f"day {np.nanmean(firsts):.1f}" if np.isfinite(firsts).any() else "-"
    return (f"  {name:<13} {p:>5.0%} ± {se:>3.0%}  {used.mean():>5.2f} ± {used.std(ddof=1) / math.sqrt(n):.2f}"
            f"  {np.mean([o.lapses for o in outcomes]):>6.2f}  {first:>9}"
            f"  {np.mean([o.recall for o in outcomes]):>6.3f}  {np.mean([o.stability for o in outcomes]):>6.1f}"
            f"  {seconds:>5.1f}s")


def main() -> None:
    ensure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--windows", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--penalty", type=float, default=PENALTY,
                        help="failure penalty in blocks; pinned at 40 for every quoted number")
    parser.add_argument("--baselines", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    grid, _ = load_availability(TIMETABLE, date(2026, 3, 2), int(EXAM_DAYS), "Europe/London")
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    started = time.perf_counter()
    continuation = solve_deadlines(SUBJECTS, RETENTION, args.penalty)
    targets = continuation.targets
    print(f"scenario: {int(EXAM_DAYS)} days, {len(blocks)} candidate blocks, {len(SUBJECTS)} subjects, "
          f"targets {', '.join(f'{t:.0f}' for t in targets)}, penalty {args.penalty:g}, "
          f"clock solves {time.perf_counter() - started:.1f}s")

    estimate = continuation.estimates[0]
    print("\ncost of postponing Analysis (S=2, D=7), in blocks, from the start of the plan:")
    print("  " + "  ".join(
        f"{x:g}d: {estimate.cost_of_postponing(7.0, 2.0, 0.0, EXAM_DAYS, x):+.2f}" for x in (1, 2, 4, 8, 12, 16)
    ))
    print(f"  best first review, unconstrained: day "
          f"{estimate.best_review_time(7.0, 2.0, 0.0, EXAM_DAYS):.2f}")

    print("\ndeterministic run (every recall succeeds): day, subject, recall at review")
    for window in args.windows:
        result = run_rolling(blocks, SUBJECTS, continuation, window=window)
        line = ", ".join(f"{s.block.start_day:.1f} {s.subject[:3]} {s.retrievability_at_review:.2f}"
                         for s in result.sessions)
        print(f"  window {window}: {line or 'no sessions'}")

    seeds = range(args.seeds)
    print(f"\nstochastic runs ({args.seeds} seeds), mean ± standard error:")
    print(f"  {'scheduler':<13} {'ready':>11}  {'blocks':>12}  {'lapses':>6}  {'first Ana':>9}"
          f"  {'recall':>6}  {'S exam':>6}  {'time':>6}")
    print("  recall = predicted probability of recall at the exam, S exam = stability at the")
    print("  exam in days; both are means over the two subjects")
    for window in args.windows:
        started = time.perf_counter()
        runs = [planner(blocks, continuation, window, np.random.default_rng(s)) for s in seeds]
        print(summarise(f"planner w={window}", runs, time.perf_counter() - started))

    if not args.baselines:
        return
    started = time.perf_counter()
    runs = [simulate_rule(blocks, targets, greedy_fixed(0.90), np.random.default_rng(s)) for s in seeds]
    print(summarise("greedy-0.90", runs, time.perf_counter() - started))

    scan = {}
    for k in range(1, 8):
        runs = [simulate_rule(blocks, targets, every_k(k), np.random.default_rng(s)) for s in seeds]
        scan[k] = runs
    best_k = max(scan, key=lambda k: (sum(o.ready for o in scan[k]), -np.mean([len(o.sessions) for o in scan[k]])))
    print(summarise(f"every-{best_k} (best)", scan[best_k], 0.0))
    print("  every-k readiness for k = 1..7: " + ", ".join(
        f"{k}: {sum(o.ready for o in runs) / len(runs):.0%}" for k, runs in scan.items()))


if __name__ == "__main__":
    main()
