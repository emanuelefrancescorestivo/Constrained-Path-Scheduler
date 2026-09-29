#!/usr/bin/env python3
"""
Golden values for tests/test_memory.py, produced by an independent FSRS-4.5.

    python -m venv /tmp/fsrs45 && /tmp/fsrs45/bin/pip install fsrs==2.5.1
    PYTHONPATH=src /tmp/fsrs45/bin/python benchmarks/fsrs_reference.py

py-fsrs 2.5.1 (open-spaced-repetition, author Jarrett Ye) is the last release of
the Python package that implements FSRS-4.5: 17 parameters, linear initial
difficulty, mean reversion towards D0(Good), the power forgetting curve with
DECAY = -0.5. Release 3.0 moved to FSRS-5. It is deliberately *not* a dependency
of this project: it is run once, in its own environment, and the numbers it prints
are pinned in the tests, with this script as their provenance.

Two trajectories, both driven through py-fsrs' public API with whole-day gaps,
exactly as a user of that library would produce them:

  1. a new card rated Good, then Good at every due date: the interval sequence
     the old test compared with "4, 14, 44, 125" only to within 0.6x to 1.7x;
  2. a fixed sequence of grades and gaps that includes lapses, Hard and Easy.

For each review it prints the state py-fsrs reports and the state `cps.memory`
computes from the same inputs, and the largest difference.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fsrs  # py-fsrs 2.5.1, see above

from cps.memory import Grade, initial_state, review

assert fsrs.FSRS().p.w[4] == 5.1618, "this script needs fsrs==2.5.1 (FSRS-4.5 defaults)"

START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
GRADES = {1: fsrs.Rating.Again, 2: fsrs.Rating.Hard, 3: fsrs.Rating.Good, 4: fsrs.Rating.Easy}
# (grade, days after the previous review) after the first rating; chosen once,
# by hand, to cover every grade and a range of gaps. Not tuned to any outcome.
MIXED = [
    (3, 3),
    (1, 9),
    (3, 1),
    (2, 2),
    (3, 5),
    (4, 7),
    (1, 40),
    (3, 2),
    (3, 6),
    (2, 15),
    (4, 30),
    # four lapses in a row drive stability low, then a long gap: the last lapse
    # ends *above* the stability before it, which FSRS-4.5 allows (AUDIT item 27)
    (1, 3),
    (1, 25),
    (1, 1),
    (1, 30),
]


def first_review(grade: int):
    """A new card rated `grade`, then graduated by a second rating the same day.

    In py-fsrs a new card rated Good goes to the Learning state for ten minutes;
    a same-day rating leaves stability and difficulty as the first rating set them.
    So after graduation the state is S0(grade), D0(grade), as in `initial_state`.
    """
    scheduler = fsrs.FSRS()
    card, _ = scheduler.review_card(fsrs.Card(), GRADES[grade], START)
    now = START
    if card.state != fsrs.State.Review:
        now = START + timedelta(minutes=10)
        card, _ = scheduler.review_card(card, fsrs.Rating.Good, now)
    return scheduler, card, now


def main() -> None:
    scheduler, card, now = first_review(3)
    ours = initial_state(Grade.GOOD)
    print("1. Good every time, reviewed when due (whole days):")
    print(f"   {'interval':>8}  {'py-fsrs S':>12} {'py-fsrs D':>10}  {'ours S':>12} {'ours D':>10}")
    worst = abs(card.stability - ours.stability) + abs(card.difficulty - ours.difficulty)
    for _ in range(6):
        interval = card.scheduled_days
        now += timedelta(days=interval)
        card, _ = scheduler.review_card(card, fsrs.Rating.Good, now)
        ours = review(ours, float(interval), Grade.GOOD)
        worst = max(worst, abs(card.stability - ours.stability), abs(card.difficulty - ours.difficulty))
        print(
            f"   {interval:>8}  {card.stability:>12.6f} {card.difficulty:>10.6f}  "
            f"{ours.stability:>12.6f} {ours.difficulty:>10.6f}"
        )
    print(f"   next interval {card.scheduled_days}; largest difference {worst:.2e}")

    scheduler, card, now = first_review(3)
    ours = initial_state(Grade.GOOD)
    print("\n2. mixed grades and gaps:")
    print(f"   {'grade':>5} {'gap':>4}  {'py-fsrs S':>12} {'py-fsrs D':>10}  {'ours S':>12} {'ours D':>10}")
    worst = 0.0
    for grade, gap in MIXED:
        now += timedelta(days=gap)
        card, _ = scheduler.review_card(card, GRADES[grade], now)
        if card.state == fsrs.State.Relearning:
            # py-fsrs relearns a lapse with a same-day step, which leaves S and D as
            # the lapse set them. Taking it at the same instant keeps the next gap a
            # whole number of days; ten minutes later, py-fsrs would count the next
            # gap as one day short (its elapsed days are whole days, rounded down).
            card, _ = scheduler.review_card(card, fsrs.Rating.Good, now)
        ours = review(ours, float(gap), Grade(grade))
        worst = max(worst, abs(card.stability - ours.stability), abs(card.difficulty - ours.difficulty))
        print(
            f"   {grade:>5} {gap:>4}  {card.stability:>12.6f} {card.difficulty:>10.6f}  "
            f"{ours.stability:>12.6f} {ours.difficulty:>10.6f}"
        )
    print(f"   largest difference {worst:.2e}")


if __name__ == "__main__":
    main()
