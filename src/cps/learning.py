"""
Your memory (DECISIONS.md D82): progress a student can watch grow, measured only
in what makes memory last, so that wanting the numbers to rise means studying well.

The research the app cites (docs/REFERENCES.md, checked entries): practice testing
and spaced practice are the two techniques rated of high utility in a review of ten
(Dunlosky et al. 2013); practice tests beat restudying (Adesope et al. 2017), also in
classrooms (Yang et al. 2021); the best gap between reviews grows with how long
the memory must last (Cepeda et al. 2006, 2008); left alone, most students reread
instead (Karpicke et al. 2009). So every number here rises with spaced recall and
with nothing else: hours alone, rereading or a review crammed the same day add
little or nothing.

- **Memory strength**: the median, over the topics taught so far, of FSRS's
  stability, the number of days a topic keeps 90 % predicted recall without a
  review. It grows when a topic is recalled after a gap; in FSRS a successful
  recall adds more the lower the recall it was made at, so a review crammed at once
  adds little and a well-spaced, effortful one adds most.
- **The map**: each taught topic's predicted recall today, in four levels.
- **Memory days added**: the stability the week's reviews added, summed over
  topics (a forgotten topic's loss counts against it).
- **Recall reps**: self-tests answered, exam practice done and flashcards
  answered, each a retrieval; repeated retrieval is what the testing studies
  measured (the number of repetitions moderates the effect, Yang et al. 2021).

All of it is a model's estimate with average weights (D24): said on each card.
Pure functions of plain values; `cps.service` replays the plan and feeds them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .progress import monday

# Today's predicted recall from which each level starts: FSRS's own target (90 %),
# then two steps of ten points. A stated choice, not a result.
LEVELS = ((0.90, "strong"), (0.80, "good"), (0.70, "fading"), (0.0, "review"))
WEEKS = 8  # the weeks the charts cover
# Memory strengths (days) celebrated once each when first reached: a week, two, a
# month, two, three, six.
MILESTONES = (7, 14, 30, 60, 90, 180)
SELF_TESTS = ("review", "first review")
PRACTICE = ("practice",)


@dataclass(frozen=True)
class Topic:
    course: str
    name: str  # as the plan names it: "Algebra 3 · week of 05 Oct", or the course
    recall: float  # predicted today, 0 to 1
    stability: float  # days until predicted recall falls to 90 % without a review


@dataclass(frozen=True)
class Rep:
    """One retrieval: a self-test answered, an exam practice done, a card answered."""

    day: date
    kind: str  # "self-test", "exercise" or "card"


def level(recall: float) -> str:
    return next(name for floor, name in LEVELS if recall >= floor)


def strength(stabilities: Sequence[float]) -> float | None:
    """The median stability in days, or None without a topic."""
    return median(stabilities) if stabilities else None


def topic_map(topics: Sequence[Topic]) -> dict:
    """The topics by course, in the order given, each with its level, and how many
    are at each level."""
    courses: dict[str, list[dict]] = {}
    for t in topics:
        courses.setdefault(t.course, []).append(
            {
                "name": t.name,
                "recall": round(100 * t.recall),
                "days": round(t.stability),
                "level": level(t.recall),
            }
        )
    counts = {name: 0 for _, name in LEVELS}
    for t in topics:
        counts[level(t.recall)] += 1
    return {"courses": [{"course": c, "topics": ts} for c, ts in courses.items()], "counts": counts}


def by_week(points: Sequence[tuple[date, float]], mondays: Sequence[date]) -> list[float]:
    """The sum of the values falling in each week."""
    out = []
    for first in mondays:
        out.append(sum(v for d, v in points if monday(d) == first))
    return out


def reps_by_week(reps: Sequence[Rep], mondays: Sequence[date]) -> list[dict[str, int]]:
    """Recall reps of each week, by kind and in all."""
    out = []
    for first in mondays:
        week = [r for r in reps if first <= r.day < first + timedelta(days=7)]
        counts = {kind: sum(1 for r in week if r.kind == kind) for kind in ("self-test", "exercise", "card")}
        out.append({**counts, "total": len(week)})
    return out


def growth(series: Sequence[float | None]) -> float | None:
    """How many times stronger the last week is than the first week with a value."""
    values = [v for v in series if v is not None]
    if len(values) < 2 or values[0] <= 0:
        return None
    return values[-1] / values[0]


def milestone(days: float) -> int | None:
    """The highest milestone reached, or None below the first."""
    reached = [m for m in MILESTONES if days >= m]
    return reached[-1] if reached else None
