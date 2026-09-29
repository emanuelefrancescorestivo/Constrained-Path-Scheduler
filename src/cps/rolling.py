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

import math
import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

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
    same clock as `Block.start_day`, and so is `last_review_day`: 0 for a plan
    made from scratch, the day of the last review when replanning part-way.

    A topic taught later in the horizon, one week of a course's lectures, has
    `available_day` at the end of its last lecture: it cannot be studied before
    then, and `memory` is its state right after that lecture (`last_review_day`
    equal to `available_day`). Its preparation, and so its target, runs from
    `available_day` to the exam unless `target_days` says otherwise; the service
    rounds it to whole weeks so that topics can share value-function solves.
    """

    name: str
    memory: MemoryState
    exam_day: float
    last_review_day: float = 0.0
    available_day: float = 0.0
    target_days: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a subject needs a name")
        if not self.exam_day > 0:
            raise ValueError(f"the exam for {self.name!r} is not after the start of the plan")
        if not self.available_day < self.exam_day:
            raise ValueError(f"{self.name!r} is only taught after its exam")
        if self.target_days is not None and not self.target_days > 0:
            raise ValueError("target_days must be positive")

    @property
    def preparation_days(self) -> float:
        if self.target_days is not None:
            return self.target_days
        return self.exam_day - self.available_day

    def target(self, retention: float) -> float:
        """Stability to reach by the exam: recall at `retention` for as long as the
        preparation lasts. See "Which goal" in `clock.py` for why not less."""
        return stability_for_interval(self.preparation_days, retention)


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
        for estimate, bound in zip(self.estimates, self.bounds, strict=True):
            if estimate.target_stability != bound.target_stability:
                raise ValueError("estimate and bound must share a target")

    @property
    def targets(self) -> tuple[float, ...]:
        return tuple(b.target_stability for b in self.bounds)

    @staticmethod
    def _total(policies, exams, topics: Sequence[TopicState], now: float) -> float:
        return sum(
            policy.cost_of(t.stability, t.difficulty, t.last_review_day, now, exam)
            for policy, exam, t in zip(policies, exams, topics, strict=True)
        )

    def cost_at(self, topics: Sequence[TopicState], now: float) -> float:
        return self._total(self.estimates, self.exam_days, topics, now)

    def bound_at(self, topics: Sequence[TopicState], now: float) -> float:
        return self._total(self.bounds, self.exam_days, topics, now)

    def subset(self, indices: Sequence[int]) -> DeadlineContinuation:
        """The continuation of some of the subjects, in the order given."""
        return DeadlineContinuation(
            estimates=tuple(self.estimates[i] for i in indices),
            bounds=tuple(self.bounds[i] for i in indices),
            exam_days=tuple(self.exam_days[i] for i in indices),
        )


# Solved tables, shared between calls: a replan, or a second plan on the same
# calendar, reuses them. A solve is a pure function of its configuration and the
# weights, so sharing cannot change a result. Bounded, because a table is about
# 1.4 MB and the web page is a long-lived process.
_SOLVED: dict[tuple[ClockConfig, Weights], ClockPolicy] = {}
_SOLVED_LIMIT = 64


def _solved(config: ClockConfig, weights: Weights) -> ClockPolicy:
    key = (config, weights)
    policy = _SOLVED.get(key)
    if policy is None:
        policy = clock_solve(config, weights)
        if len(_SOLVED) >= _SOLVED_LIMIT:
            _SOLVED.pop(next(iter(_SOLVED)))
        _SOLVED[key] = policy
    return policy


def solve_deadlines(
    subjects: Sequence[Subject],
    retention: float = 0.9,
    failure_penalty: float = DEFAULT_FAILURE_PENALTY,
    weights: Weights = DEFAULT_WEIGHTS,
    horizon: float | None = None,
) -> DeadlineContinuation:
    """Solve the clock value function for every subject (two solves each).

    The time step grows with the horizon, 0.25 days up to 24 days and one
    ninety-sixth of the horizon beyond, so a 60-day exam costs the same few
    seconds as a 21-day one. Subjects sharing a configuration share the solves.

    Without `horizon`, each subject's table covers its own exam, as it always has.
    With it, every table covers `horizon` days on the same time step, so that
    subjects with different exams but the same target share one solve: that is
    what makes one topic per week of lectures affordable. A table only depends on
    the target, the time step and the penalty; its horizon is how many levels of
    the backward sweep are kept.
    """
    if horizon is not None and any(s.exam_day > horizon + 1e-9 for s in subjects):
        raise ValueError("an exam lies beyond the shared horizon")
    # With a shared horizon, a table only needs to reach as far before the exam as
    # its longest-prepared subject is ever queried: from the day it is taught. A
    # subject taught in December needs weeks of table, not the semester, and the
    # backward sweep is quadratic in its length.
    reach: dict[float, float] = {}
    for s in subjects:
        target = s.target(retention)
        reach[target] = max(reach.get(target, 0.0), s.exam_day - s.available_day)

    def config(subject: Subject, mode: str) -> ClockConfig:
        span = subject.exam_day if horizon is None else horizon
        return ClockConfig(
            target_stability=subject.target(retention),
            horizon_days=span if horizon is None else reach[subject.target(retention)],
            failure_penalty=failure_penalty,
            time_step=max(0.25, span / 96),
            interpolation=mode,
        )

    configs = {config(s, mode) for s in subjects for mode in ("bilinear", "optimistic")}
    missing = [c for c in configs if (c, weights) not in _SOLVED]
    if len(missing) > 1:
        # The backward sweeps are numpy-bound and release the GIL for much of
        # their time: about 1.6 times faster on four cores than one after another.
        with ThreadPoolExecutor(max_workers=min(len(missing), os.cpu_count() or 1)) as pool:
            for c, policy in zip(missing, pool.map(lambda c: clock_solve(c, weights), missing), strict=True):
                if len(_SOLVED) >= _SOLVED_LIMIT:
                    _SOLVED.pop(next(iter(_SOLVED)))
                _SOLVED[(c, weights)] = policy

    continuation = DeadlineContinuation(
        estimates=tuple(_solved(config(s, "bilinear"), weights) for s in subjects),
        bounds=tuple(_solved(config(s, "optimistic"), weights) for s in subjects),
        exam_days=tuple(s.exam_day for s in subjects),
    )
    # A shared table keeps its memo of waiting values between plans; drop a large
    # one so that a long-lived process does not grow without bound.
    for policy in {id(p): p for p in continuation.estimates + continuation.bounds}.values():
        if len(policy._cache) + len(policy._rounded) > 200_000:
            policy._cache.clear()
            policy._rounded.clear()
    return continuation


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
    last_review_day: float = 0.0,
    enough: float = math.inf,
) -> float:
    """An upper bound on the stability reachable by reviewing at some subset of
    `review_days`, starting from `memory` last reviewed at `last_review_day`.

    Every review is assumed to succeed, which is an upper bound because a lapse
    never ends above a recall from the same state and delay; difficulty is held at
    `min(D, D0(Good))`, which Good grades cannot go below (the argument in
    `plan.best_case_reviews`). The best stability after a review at each day is a
    dynamic programme over the previous review, valid because the post-recall
    stability increases with the pre-review stability at a fixed gap
    (`test_recall_stability_increases_with_stability`). If even this is below a
    subject's target, no schedule on this calendar can make it ready, and the
    planner says so instead of scheduling anyway.

    The programme stops as soon as it reaches `enough`: the caller only asks
    whether a target is reachable, and a topic per week of lectures makes the
    full quadratic programme too slow to run for every topic.
    """
    floor = min(memory.difficulty, _next_difficulty_fixed_point(weights))
    days = sorted(d for d in review_days if d > last_review_day)
    best: list[float] = []
    overall = memory.stability
    for j, day in enumerate(days):
        candidates = [(last_review_day, memory.stability)] + [(days[i], best[i]) for i in range(j)]
        value = max(
            _stability_on_recall(s, floor, retrievability(day - last, s), Grade.GOOD, weights)
            for last, s in candidates
            if day > last
        )
        best.append(value)
        overall = max(overall, value)
        if overall >= enough:
            break
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
        return tuple(t.stability >= g for t, g in zip(self.final_topics, self.targets, strict=True))

    @property
    def recall_at_exam(self) -> tuple[float, ...]:
        """Per subject: predicted probability of recall at the moment of the exam."""
        return tuple(
            retrievability(max(exam - t.last_review_day, 0.0), t.stability)
            for t, exam in zip(self.final_topics, self.exam_days, strict=True)
        )

    def unready_subjects(self) -> tuple[str, ...]:
        return tuple(n for n, ok in zip(self.subjects, self.ready, strict=True) if not ok)

    def unreachable_subjects(self) -> tuple[str, ...]:
        return tuple(n for n, bad in zip(self.subjects, self.unreachable, strict=True) if bad)

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
    max_candidates: int | None = None,
) -> RollingResult:
    """Plan one window exactly, act, observe, repeat.

    `continuation` defaults to `solve_deadlines(subjects, retention,
    failure_penalty)`; pass one in to reuse the solves across runs.

    `rng = None` means every recall succeeds: the modal trajectory, useful for
    reproducible comparisons. Pass a generator for the stochastic version.

    `force_lapse_at` forces a lapse at the given session indices, so that the
    planner's reaction to a failure can be tested rather than hoped for.

    `max_candidates` caps how many topics a window may choose from; see
    `_candidates`. None means no cap.

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
    available = tuple(s.available_day for s in subjects)
    unreachable = tuple(
        best_case_stability(
            s.memory,
            [b.start_day for b in blocks if s.available_day <= b.start_day < s.exam_day],
            weights,
            s.last_review_day,
            enough=target,
        )
        < target
        for s, target in zip(subjects, targets, strict=True)
    )
    # An unreachable subject is never offered as an action: its exam is treated as
    # already past, so the plan does not spend blocks on a lost cause.
    live_exams = tuple(0.0 if bad else exam for bad, exam in zip(unreachable, exams, strict=True))
    # Topics that appear during the horizon, or more topics than a window should
    # branch over, make each window a problem over the topics that can be studied
    # now: see `_candidates`. Without either, the window holds every subject, as it
    # always has, so the published results are untouched.
    restrict = max_candidates is not None or any(a > 0 for a in available)

    state = tuple(TopicState(s.memory.stability, s.memory.difficulty, s.last_review_day) for s in subjects)
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
        if not any(t.stability < g and now < e for t, g, e in zip(state, targets, live_exams, strict=True)):
            break  # everything is ready, lost, or past its exam

        pane = tuple(blocks[index : index + window])
        after = index + len(pane)
        end_day = blocks[after].start_day if after < len(blocks) else max(exams)
        if restrict:
            chosen = _candidates(
                continuation, state, targets, live_exams, available, now, end_day, max_candidates
            )
            if not chosen:
                continue  # nothing taught yet is left to study
        else:
            chosen = tuple(range(len(subjects)))
        part = continuation.subset(chosen) if restrict else continuation
        instance = Instance(
            topics=tuple(names[i] for i in chosen),
            blocks=pane,
            initial=PlanState(0, tuple(state[i] for i in chosen)),
            target_stability=max(targets[i] for i in chosen),
            continuation=part,
            lateness_penalty=0.0,
            stability_step=stability_step,
            difficulty_step=difficulty_step,
            weights=weights,
            targets=tuple(targets[i] for i in chosen),
            exam_days=tuple(live_exams[i] for i in chosen),
            end_day=end_day,
        )
        solution = solve_ao_star(
            instance, heuristic if not restrict else deadline_heuristic(part), max_expansions=max_expansions
        )
        result.solves += 1
        result.expansions += solution.nodes
        result.peak_expansions = max(result.peak_expansions, solution.nodes)

        local = solution.policy.get(instance.initial, SKIP)
        if local == SKIP:
            continue
        action = chosen[local]
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


def _candidates(
    continuation: DeadlineContinuation,
    state: Sequence[TopicState],
    targets: Sequence[float],
    exams: Sequence[float],
    available: Sequence[float],
    now: float,
    end_day: float,
    limit: int | None,
) -> tuple[int, ...]:
    """The topics a window starting at `now` chooses between.

    A topic can be studied once it has been taught, until its exam, while it is
    below its target. Leaving the others out of the window is exact, not an
    approximation: nothing inside the window can change them, so their share of
    the terminal value is the same constant under every policy.

    With more such topics than `limit`, only the `limit` most urgent are kept,
    and that is an approximation, stated here and in METHOD.md: the window is
    exact over the topics it is given. Urgency is what waiting through the window
    costs a topic, by the accurate estimate; ties, which are the common case while
    every best review time is still ahead, go to the earlier exam and then to the
    weaker memory. A window of `w` blocks studies at most `w` topics, so a limit
    at or above `w` keeps every topic the window could possibly use when the
    ranking is right.
    """
    live = [i for i, t in enumerate(state) if available[i] <= now < exams[i] and t.stability < targets[i]]
    if limit is None or len(live) <= limit:
        return tuple(live)

    def urgency(i: int) -> tuple[float, float, float]:
        t = state[i]
        policy = continuation.estimates[i]
        waiting = policy.cost_of(t.stability, t.difficulty, t.last_review_day, end_day, exams[i]) - (
            policy.cost_of(t.stability, t.difficulty, t.last_review_day, now, exams[i])
        )
        return (-waiting, exams[i], t.stability)

    return tuple(sorted(sorted(live, key=urgency)[:limit]))
