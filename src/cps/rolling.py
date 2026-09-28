"""
Receding-horizon replanning.

The problem this solves
----------------------
`plan.solve_ao_star` reaches about ten study blocks. A real 28-day horizon with
two free evenings a day offers about fifty, and a 60-day exam period about 120.
Planning the whole thing in one solve is not going to happen, and no heuristic
fixes it: a policy for a stochastic problem is a contingency tree whose size is
exponential in the number of coin flips along a path, so inflating `h` prunes
exploration *outside* the solution graph while the graph itself keeps doubling.

So: plan a short window exactly, execute one block, observe what happened,
replan. This is not a compromise. An observed outcome collapses one whole
contingency branch immediately, so replanning is strictly cheaper than building
the tree up front, and it is what a working product has to do anyway, because a
student's actual recall is not known in advance.

What prices "and then the rest happens later"
---------------------------------------------
A window needs a value for the work left after it. Two earlier choices failed,
and both failures are worth knowing:

* the unconstrained optimum `ssp.V_opt` made the window indifferent to spending a
  block, so the planner scheduled nothing (PROCESS.md, Mistake 5);
* `budget.V(D, S, b)`, indexed by remaining *blocks*, made waiting free while
  blocks were plentiful, so the planner put the first review on day 16 to 19 of
  21 and was ready in 35 to 38% of runs (AUDIT.md item 20, PROCESS.md Mistake 11).

The continuation now comes from `clock.py`: for each subject, the expected cost of
reaching that subject's target *before that subject's exam*, given the time since
its last review. Waiting consumes calendar time, which is the resource it really
consumes, and each subject carries its own exam date and target.

Estimate for the objective, bound for the search
------------------------------------------------
The window's terminal value is part of the objective the window optimises, so it
is the *accurate* (bilinear) clock solve: an estimate of the real cost-to-go. The
admissible (optimistic) solve is used only as the AO* heuristic. Using a lower
bound as the terminal value, as the budget version did, makes every window
systematically optimistic about the future, which is itself a reason to defer.

Admissibility across the window: at a node whose next block starts at time
`tau`, `h = sum_i W_lo_i(D_i, S_i, tau - last_i, exam_i - tau)`. Each `W_lo_i` is a
lower bound on topic i's cost under *any* review times from `tau` on, which
includes the calendar-restricted ones inside the window followed by the accurate
continuation, because the optimistic table lies below the accurate one cell by
cell. Topics do not help one another, and costs add. The claim is checked against
exhaustive search at every reachable state of a window in `tests/test_rolling.py`.
What is *not* claimed is global optimality: replanning is a greedy-over-windows
scheme, and `docs/METHOD.md` says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .budget import BudgetPolicy
from .clock import ClockConfig, ClockPolicy
from .clock import solve as clock_solve
from .memory import (
    DEFAULT_WEIGHTS,
    Grade,
    MemoryState,
    Weights,
    _stability_on_recall,
    retrievability,
    review,
    stability_for_interval,
)
from .plan import (
    SKIP,
    Block,
    Heuristic,
    Instance,
    PlanState,
    TopicState,
    solve_ao_star,
)

DEFAULT_FAILURE_PENALTY = 40.0


# --------------------------------------------------------------------------- #
# Subjects and the clocked continuation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Subject:
    """One thing to study: its memory state now and when its exam is.

    `exam_day` is measured in days from midnight at the start of the horizon, the
    same clock as `Block.start_day`. The memory state is taken as of that
    midnight, with the last review at day 0.
    """

    name: str
    memory: MemoryState
    exam_day: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a subject needs a name")
        if not self.exam_day > 0:
            raise ValueError(f"the exam for {self.name!r} is not after the start of the plan")

    def target(self, retention: float) -> float:
        """Stability to reach by the exam: recall at `retention` for as long as the
        preparation lasts. See "Which goal" in `clock.py` for why not less."""
        return stability_for_interval(self.exam_day, retention)


@dataclass(frozen=True)
class DeadlineContinuation:
    """Per-subject clock solves: an estimate for the objective, a bound for `h`."""

    estimates: tuple[ClockPolicy, ...]
    bounds: tuple[ClockPolicy, ...]
    exam_days: tuple[float, ...]

    def __post_init__(self) -> None:
        if not len(self.estimates) == len(self.bounds) == len(self.exam_days):
            raise ValueError("one estimate, one bound and one exam day per subject")
        if not all(b.is_lower_bound for b in self.bounds):
            raise ValueError(
                "bounds used by a heuristic must come from ClockConfig.for_heuristic "
                "(optimistic interpolation)"
            )
        for estimate, bound in zip(self.estimates, self.bounds):
            if estimate.target_stability != bound.target_stability:
                raise ValueError("estimate and bound must share a target")

    @property
    def targets(self) -> tuple[float, ...]:
        return tuple(b.target_stability for b in self.bounds)

    @staticmethod
    def _total(policies, exams, topics: Sequence[TopicState], now: float) -> float:
        return sum(
            policy.cost_of(t.stability, t.difficulty, t.last_review_day, now, exam)
            for policy, exam, t in zip(policies, exams, topics)
        )

    def cost_at(self, topics: Sequence[TopicState], now: float) -> float:
        return self._total(self.estimates, self.exam_days, topics, now)

    def bound_at(self, topics: Sequence[TopicState], now: float) -> float:
        return self._total(self.bounds, self.exam_days, topics, now)


def solve_deadlines(
    subjects: Sequence[Subject],
    retention: float = 0.9,
    failure_penalty: float = DEFAULT_FAILURE_PENALTY,
    weights: Weights = DEFAULT_WEIGHTS,
) -> DeadlineContinuation:
    """Solve the clock value function for every subject (two solves each).

    The time step grows with the horizon, 0.25 days up to 24 days and one
    ninety-sixth of the horizon beyond, so a 60-day exam costs the same few
    seconds as a 21-day one. Subjects sharing an exam day share the solves.
    """
    cache: dict[tuple[float, str], ClockPolicy] = {}

    def policy(exam_day: float, mode: str) -> ClockPolicy:
        key = (exam_day, mode)
        if key not in cache:
            config = ClockConfig.for_exam(
                exam_day,
                retention,
                failure_penalty=failure_penalty,
                time_step=max(0.25, exam_day / 96),
                interpolation=mode,
            )
            cache[key] = clock_solve(config, weights)
        return cache[key]

    return DeadlineContinuation(
        estimates=tuple(policy(s.exam_day, "bilinear") for s in subjects),
        bounds=tuple(policy(s.exam_day, "optimistic") for s in subjects),
        exam_days=tuple(s.exam_day for s in subjects),
    )


def deadline_heuristic(continuation: DeadlineContinuation) -> Heuristic:
    """`h` at a node: the admissible clock bound, evaluated when its block starts."""

    def heuristic(instance: Instance, state: PlanState) -> float:
        now = instance.blocks[state.block_index].start_day
        return continuation.bound_at(state.topics, now)

    return heuristic


def best_case_stability(
    memory: MemoryState,
    review_days: Sequence[float],
    weights: Weights = DEFAULT_WEIGHTS,
) -> float:
    """An upper bound on the stability reachable by reviewing at some subset of
    `review_days`, starting from `memory` last reviewed at day 0.

    Every review is assumed to succeed, and difficulty is held at
    `min(D, D0(Good))`, which Good grades cannot go below (the argument in
    `plan.best_case_reviews`). The best stability after a review at each day is a
    dynamic programme over the previous review, valid because the post-recall
    stability increases with the pre-review stability at a fixed gap
    (`test_recall_stability_increases_with_stability`). If even this is below a
    subject's target, no schedule on this calendar can make it ready, and the
    planner says so instead of scheduling anyway.
    """
    floor = min(memory.difficulty, _next_difficulty_fixed_point(weights))
    days = sorted(d for d in review_days if d > 0)
    best: list[float] = []
    overall = memory.stability
    for j, day in enumerate(days):
        candidates = [(0.0, memory.stability)] + [(days[i], best[i]) for i in range(j)]
        value = max(
            _stability_on_recall(s, floor, retrievability(day - last, s), Grade.GOOD, weights)
            for last, s in candidates
            if day > last
        )
        best.append(value)
        overall = max(overall, value)
    return overall


def _next_difficulty_fixed_point(weights: Weights) -> float:
    from .memory import initial_difficulty

    return initial_difficulty(Grade.GOOD, weights)


# --------------------------------------------------------------------------- #
# Superseded: the budget continuation (AUDIT.md item 20)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BudgetedContinuation:
    """Prices leftover work at a *finite* remaining block count.

    Superseded by `DeadlineContinuation`: it has no clock, which is the defect of
    AUDIT.md item 20. Kept, with its tests, because it documents that defect and
    because removing a public class is the owner's decision.
    """

    policy: BudgetPolicy
    budget: int

    def __post_init__(self) -> None:
        if self.budget < 0:
            raise ValueError("budget must be non-negative")
        if not self.is_lower_bound:
            raise ValueError(
                "a continuation used as a heuristic must come from "
                "BudgetConfig.for_heuristic (optimistic interpolation)"
            )

    @property
    def is_lower_bound(self) -> bool:
        return self.policy.config.interpolation == "optimistic"

    def reviews_lower_bound(self, states: Iterable[MemoryState]) -> float:
        return sum(self.policy.cost_of(state, self.budget) for state in states)


def budgeted_heuristic(policy: BudgetPolicy, budget_after_window: int) -> Heuristic:
    """`h` for the superseded budget continuation. See `BudgetedContinuation`."""

    def heuristic(instance: Instance, state: PlanState) -> float:
        remaining = (len(instance.blocks) - state.block_index) + budget_after_window
        return sum(policy.cost_of(topic.as_memory(), remaining) for topic in state.topics)

    return heuristic


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScheduledSession:
    """One study block, as planned and as it turned out."""

    block: Block
    subject: str
    retrievability_at_review: float
    outcome: Grade
    stability_before: float
    stability_after: float
    target_stability: float = 0.0
    exam_day: float = 0.0

    @property
    def rationale(self) -> str:
        """Human-readable reason, for the calendar event description.

        A schedule a student does not understand is a schedule they will not
        follow, so the *why* travels with the *when* all the way into the .ics.
        """
        return (
            f"Recall was around {self.retrievability_at_review * 100:.0f}% at this point. "
            f"Stability {self.stability_before:.1f} to {self.stability_after:.1f} days, "
            f"working towards {self.target_stability:.0f} days by the exam "
            f"(day {self.exam_day:.1f} of the plan)."
        )


@dataclass(slots=True)
class RollingResult:
    sessions: list[ScheduledSession] = field(default_factory=list)
    final_topics: tuple[TopicState, ...] = ()
    subjects: tuple[str, ...] = ()
    targets: tuple[float, ...] = ()
    exam_days: tuple[float, ...] = ()
    unreachable: tuple[bool, ...] = ()
    solves: int = 0
    expansions: int = 0
    peak_expansions: int = 0
    blocks_offered: int = 0

    @property
    def blocks_used(self) -> int:
        return len(self.sessions)

    @property
    def lapses(self) -> int:
        return sum(1 for s in self.sessions if s.outcome == Grade.AGAIN)

    @property
    def ready(self) -> tuple[bool, ...]:
        """Per subject: did stability reach that subject's target before its exam."""
        return tuple(t.stability >= g for t, g in zip(self.final_topics, self.targets))

    @property
    def recall_at_exam(self) -> tuple[float, ...]:
        """Per subject: predicted probability of recall at the moment of the exam."""
        return tuple(
            retrievability(max(exam - t.last_review_day, 0.0), t.stability)
            for t, exam in zip(self.final_topics, self.exam_days)
        )

    def unready_subjects(self) -> tuple[str, ...]:
        return tuple(n for n, ok in zip(self.subjects, self.ready) if not ok)

    def unreachable_subjects(self) -> tuple[str, ...]:
        return tuple(n for n, bad in zip(self.subjects, self.unreachable) if bad)

    def first_review_day(self, subject: str) -> float | None:
        return next((s.block.start_day for s in self.sessions if s.subject == subject), None)

    def to_ics_sessions(self) -> list[tuple[int, str, str]]:
        """Shape expected by `calendar_io.plan_to_ics`."""
        return [(s.block.slot, s.subject, s.rationale) for s in self.sessions]


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def run_rolling(
    blocks: Sequence[Block],
    subjects: Sequence[Subject],
    continuation: DeadlineContinuation | None = None,
    *,
    window: int = 6,
    retention: float = 0.9,
    failure_penalty: float = DEFAULT_FAILURE_PENALTY,
    weights: Weights = DEFAULT_WEIGHTS,
    rng: np.random.Generator | None = None,
    stability_step: float = 0.15,
    difficulty_step: float = 0.5,
    max_expansions: int = 60_000,
    force_lapse_at: Sequence[int] = (),
) -> RollingResult:
    """Plan one window exactly, act, observe, repeat.

    `continuation` defaults to `solve_deadlines(subjects, retention,
    failure_penalty)`; pass one in to reuse the solves across runs.

    `rng = None` means every recall succeeds: the modal trajectory, useful for
    reproducible comparisons. Pass a generator for the stochastic version.

    `force_lapse_at` forces a lapse at the given session indices, so that the
    planner's reaction to a failure can be tested rather than hoped for.

    Note which state is snapped and which is not. Window instances aggregate
    stability onto a grid, because that is what makes the search tractable; the
    *executed* state is updated with the exact FSRS transition. Aggregation is a
    search device and must not leak into the trajectory that the student actually
    lives, or the reported stabilities would be fiction.
    """
    if window < 1:
        raise ValueError("window must be at least one block")
    if not subjects:
        raise ValueError("nothing to schedule")
    if continuation is None:
        continuation = solve_deadlines(subjects, retention, failure_penalty, weights)
    exams = tuple(s.exam_day for s in subjects)
    if continuation.exam_days != exams:
        raise ValueError("the continuation was solved for different exam dates")

    names = tuple(s.name for s in subjects)
    targets = continuation.targets
    unreachable = tuple(
        best_case_stability(s.memory, [b.start_day for b in blocks if b.start_day < s.exam_day], weights)
        < target
        for s, target in zip(subjects, targets)
    )
    # An unreachable subject is never offered as an action: its exam is treated as
    # already past, so the plan does not spend blocks on a lost cause.
    live_exams = tuple(0.0 if bad else exam for bad, exam in zip(unreachable, exams))

    state = tuple(TopicState(s.memory.stability, s.memory.difficulty, 0.0) for s in subjects)
    result = RollingResult(
        subjects=names,
        targets=targets,
        exam_days=exams,
        unreachable=unreachable,
        blocks_offered=len(blocks),
    )
    forced = set(force_lapse_at)
    heuristic = deadline_heuristic(continuation)

    for index in range(len(blocks)):
        now = blocks[index].start_day
        if not any(t.stability < g and now < e for t, g, e in zip(state, targets, live_exams)):
            break  # everything is ready, lost, or past its exam

        pane = tuple(blocks[index : index + window])
        after = index + len(pane)
        end_day = blocks[after].start_day if after < len(blocks) else max(exams)
        instance = Instance(
            topics=names,
            blocks=pane,
            initial=PlanState(0, state),
            target_stability=max(targets),
            continuation=continuation,
            lateness_penalty=0.0,
            stability_step=stability_step,
            difficulty_step=difficulty_step,
            weights=weights,
            targets=targets,
            exam_days=live_exams,
            end_day=end_day,
        )
        solution = solve_ao_star(instance, heuristic, max_expansions=max_expansions)
        result.solves += 1
        result.expansions += solution.nodes
        result.peak_expansions = max(result.peak_expansions, solution.nodes)

        action = solution.policy.get(instance.initial, SKIP)
        if action == SKIP:
            continue
        block = pane[0]
        topic = state[action]
        elapsed = max(block.start_day - topic.last_review_day, 0.0)
        recall_probability = retrievability(elapsed, topic.stability)
        if len(result.sessions) in forced:
            recalled = False
        elif rng is None:
            recalled = True
        else:
            recalled = rng.random() < recall_probability
        grade = Grade.GOOD if recalled else Grade.AGAIN
        reviewed = review(topic.as_memory(), elapsed, grade, weights)
        result.sessions.append(
            ScheduledSession(
                block=block,
                subject=names[action],
                retrievability_at_review=recall_probability,
                outcome=grade,
                stability_before=topic.stability,
                stability_after=reviewed.stability,
                target_stability=targets[action],
                exam_day=exams[action],
            )
        )
        updated = list(state)
        updated[action] = TopicState(reviewed.stability, reviewed.difficulty, block.start_day)
        state = tuple(updated)

    result.final_topics = state
    return result

