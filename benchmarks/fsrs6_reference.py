#!/usr/bin/env python3
"""
Golden values for tests/test_memory6.py, produced by py-fsrs 6.3.2 (DECISIONS.md
D52).

    pip install fsrs==6.3.2          # pure Python; the dev extra has it
    python benchmarks/fsrs6_reference.py

py-fsrs (open-spaced-repetition, MIT) is the reference implementation of FSRS-6 in
Python. It is a test dependency only (the `dev` extra): the product computes with
`cps.memory`'s own FSRS-6, and this script and the test it feeds keep the two equal.
Its optimizer extra, which would bring PyTorch, is not used (D53).

Two trajectories, through py-fsrs' public API with whole-day gaps and no learning
steps, as the product reviews a topic:

  1. a new card rated Good, then Good at every due date;
  2. a fixed sequence of grades and gaps with Hard, Easy, two lapses and a review
     on the same day as the one before it (FSRS-6's short-term update).

For each review it prints the state py-fsrs reports, the state `cps.memory`
computes from the same inputs, and the largest difference.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fsrs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cps.memory import Grade, initial_state6, review6

START = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
# (grade, days since the review before; the first is the card's first review)
MIXED = [(3, 0), (3, 0), (2, 3), (1, 10), (3, 1), (4, 7), (1, 30), (3, 2), (3, 5)]


def scheduler() -> fsrs.Scheduler:
    return fsrs.Scheduler(learning_steps=(), relearning_steps=(), enable_fuzzing=False)


def good_trajectory(reviews: int = 7) -> list[tuple[int, float, float, float, float]]:
    """(interval, py-fsrs stability, difficulty, ours) for Good at every due date."""
    s = scheduler()
    card, _ = s.review_card(fsrs.Card(), fsrs.Rating.Good, START)
    ours = initial_state6(Grade.GOOD)
    out = []
    when = START
    for _ in range(reviews):
        gap = (card.due - when).days
        when = card.due
        card, _ = s.review_card(card, fsrs.Rating.Good, when)
        ours = review6(ours, gap, Grade.GOOD)
        out.append((gap, card.stability, card.difficulty, ours.stability, ours.difficulty))
    return out


def mixed_trajectory() -> list[tuple[int, int, float, float, float, float]]:
    s = scheduler()
    card = fsrs.Card()
    when = START
    ours = None
    out = []
    for grade, gap in MIXED:
        when = when + timedelta(days=gap)
        card, _ = s.review_card(card, fsrs.Rating(grade), when)
        ours = initial_state6(Grade(grade)) if ours is None else review6(ours, gap, Grade(grade))
        out.append((grade, gap, card.stability, card.difficulty, ours.stability, ours.difficulty))
    return out


def main() -> int:
    print(f"py-fsrs {fsrs.__version__ if hasattr(fsrs, '__version__') else '6.3.2'}\n")
    worst = 0.0
    print("Good at every due date: gap, py-fsrs S and D, cps.memory S and D")
    for gap, s, d, s2, d2 in good_trajectory():
        worst = max(worst, abs(s - s2), abs(d - d2))
        print(f"  {gap:>4}  {s:12.6f} {d:9.6f}   {s2:12.6f} {d2:9.6f}")
    print("\nmixed grades: grade, gap, py-fsrs S and D, cps.memory S and D")
    for grade, gap, s, d, s2, d2 in mixed_trajectory():
        worst = max(worst, abs(s - s2), abs(d - d2))
        print(f"  {grade} {gap:>3}  {s:12.6f} {d:9.6f}   {s2:12.6f} {d2:9.6f}")
    print(f"\nlargest difference: {worst:.2e}")
    return 0 if worst < 1e-9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
