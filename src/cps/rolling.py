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
the tree up front — and it is what a working product has to do anyway, because a
student's actual recall is not known in advance.

Why this needed `budget.py` first
---------------------------------
A window needs a value for "and then the rest happens later". The obvious choice
— the unconstrained optimum `ssp.V_opt` — makes the window objective degenerate,
and it did exactly that the first time: because `V_opt` is the fixed point of a
dominated Bellman operator, spending a block inside the window and then paying
`V_opt` never beats simply paying `V_opt` at the end, so the planner scheduled
nothing. The one-shot solver worked around it with a lateness penalty at the
horizon, which is wrong here: a topic that is merely not-ready-yet at the end of
an eight-block window would be charged the full penalty for missing an exam that
is still three weeks away, and the planner would panic in every window.

`budget.V(D, S, b)` is the honest continuation. The failure penalty lives inside
it, at `b = 0`, where it belongs; and because `V` is strictly decreasing in `b`
over the binding range, skipping a block has a real cost. No lateness penalty is
applied at the window boundary — `lateness_penalty` is zero on window instances.

Admissibility across the window
-------------------------------
At a node `k` blocks into a window of `W`, with `b_after` blocks left beyond it,
the remaining budget is `(W - k) + b_after`. Take

    h(node) = Σᵢ V(Dᵢ, Sᵢ, (W - k) + b_after)

`V` satisfies `V(s, b) ≤ 1 + E[V(s', b-1)]` for every available delay, so summing
over topics and telescoping across the window gives
`h(node) ≤ E[blocks spent + Σ V(final, b_after)]`, which is exactly the window's
cost-to-go. So AO* still returns the window-optimal policy. What is *not* claimed
is global optimality over the full horizon: replanning is a greedy-over-windows
scheme, and `docs/METHOD.md` says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .budget import BudgetPolicy
from .memory import DEFAULT_WEIGHTS, Grade, MemoryState, Weights, retrievability, review
from .plan import (
    SKIP,
    Block,
    Heuristic,
    Instance,
    PlanState,
    TopicState,
    solve_ao_star,
)

# --------------------------------------------------------------------------- #
# Continuation adapter
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BudgetedContinuation:
    """Prices leftover work at a *finite* remaining block count."""

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
    """`h` that carries the shrinking budget down the window. See module docstring."""

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

    @property
    def rationale(self) -> str:
        """Human-readable reason, for the calendar event description.

        A schedule a student does not understand is a schedule they will not
        follow, so the *why* travels with the *when* all the way into the .ics.
        """
        return (
            f"Recall was around {self.retrievability_at_review * 100:.0f}% at this point, "
            f"which is where a review is worth most. Stability "
            f"{self.stability_before:.1f} to {self.stability_after:.1f} days."
        )


@dataclass(slots=True)
class RollingResult:
    sessions: list[ScheduledSession] = field(default_factory=list)
    final_topics: tuple[TopicState, ...] = ()
    subjects: tuple[str, ...] = ()
    target_stability: float = 0.0
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
        return tuple(t.stability >= self.target_stability for t in self.final_topics)

    def unready_subjects(self) -> tuple[str, ...]:
        return tuple(n for n, ok in zip(self.subjects, self.ready) if not ok)

    def to_ics_sessions(self) -> list[tuple[int, str, str]]:
        """Shape expected by `calendar_io.plan_to_ics`."""
        return [(s.block.slot, s.subject, s.rationale) for s in self.sessions]


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def run_rolling(
    blocks: Sequence[Block],
    topics: Sequence[tuple[str, MemoryState]],
    target_stability: float,
    policy: BudgetPolicy,
    window: int = 8,
    weights: Weights = DEFAULT_WEIGHTS,
    rng: np.random.Generator | None = None,
    stability_step: float = 0.15,
    difficulty_step: float = 0.5,
    max_expansions: int = 60_000,
    force_lapse_at: Sequence[int] = (),
) -> RollingResult:
    """Plan one window exactly, act, observe, repeat.

    `rng = None` means every recall succeeds — the modal trajectory, useful for
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
    if not topics:
        raise ValueError("nothing to schedule")
    if policy.config.target_stability != target_stability:
        raise ValueError(
            f"policy targets S={policy.config.target_stability:g} but the deadline "
            f"implies S={target_stability:g}; solve budget.solve for this deadline"
        )

    names = tuple(name for name, _ in topics)
    state = tuple(TopicState(m.stability, m.difficulty, 0.0) for _, m in topics)
    result = RollingResult(
        subjects=names, target_stability=target_stability, blocks_offered=len(blocks)
    )
    forced = set(force_lapse_at)

    index = 0
    while index < len(blocks):
        if all(t.stability >= target_stability for t in state):
            break

        pane = tuple(blocks[index : index + window])
        budget_after = len(blocks) - (index + len(pane))
        instance = Instance(
            topics=names,
            blocks=pane,
            initial=PlanState(0, state),
            target_stability=target_stability,
            continuation=BudgetedContinuation(policy, budget_after),
            lateness_penalty=0.0,
            stability_step=stability_step,
            difficulty_step=difficulty_step,
            weights=weights,
        )
        solution = solve_ao_star(
            instance,
            budgeted_heuristic(policy, budget_after),
            max_expansions=max_expansions,
        )
        result.solves += 1
        result.expansions += solution.nodes
        result.peak_expansions = max(result.peak_expansions, solution.nodes)

        action = solution.policy.get(instance.initial, SKIP)
        if action != SKIP:
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
            after = review(topic.as_memory(), elapsed, grade, weights)
            result.sessions.append(
                ScheduledSession(
                    block=block,
                    subject=names[action],
                    retrievability_at_review=recall_probability,
                    outcome=grade,
                    stability_before=topic.stability,
                    stability_after=after.stability,
                )
            )
            updated = list(state)
            updated[action] = TopicState(after.stability, after.difficulty, block.start_day)
            state = tuple(updated)

        index += 1

    result.final_topics = state
    return result
