"""
FSRS memory model for the Constrained-Path Scheduler.

Why this module exists
----------------------
The January 2026 version of this project used the following "FSRS heuristic":

    def predict_next_stability(current_S, difficulty):
        factor = 1 + (math.exp(difficulty / 10) * 0.2)
        return current_S * factor

This function has no `elapsed_days` argument. A memory model that cannot see
*when* a review happens cannot represent the spacing effect, and a scheduler
built on top of it has no reason to prefer one time slot over another. The
"retention" numbers in the January write-up therefore measured something that
was not retention.

This module replaces that function with the actual FSRS-4.5 model:

  1. a forgetting curve, R(t, S), that decays with elapsed time;
  2. a stability update that is a function of retrievability *at review time*,
     so that a well-timed review is worth more than a premature one;
  3. an expected-value form, E[S'], that trades the gain from a late review
     against the risk of having forgotten the item.

(3) is what actually gives the search problem a shape: E[S'] has an interior
maximum in elapsed time, so there exists a *best moment* to review, and the
scheduler's job is to get as close to it as hard constraints allow.

References
----------
- Forgetting curve (FSRS-4.5+): R(t, S) = (1 + FACTOR * t / S) ** DECAY
- Stability / difficulty updates and default parameters:
  open-spaced-repetition/awesome-fsrs, "The Algorithm" wiki.
- FSRS is by Jarrett Ye (open-spaced-repetition), *not* Reddy et al.;
  the January report misattributes it. See AUDIT.md, item 9.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- #
# Forgetting-curve constants (fixed in FSRS-4.5 / FSRS-5; trainable in v6+).
# --------------------------------------------------------------------------- #

DECAY: float = -0.5
FACTOR: float = 19.0 / 81.0  # == 0.9 ** (1 / DECAY) - 1, so that R(t = S) == 0.9

S_MIN: float = 0.01  # stability floor, in days
D_MIN: float = 1.0
D_MAX: float = 10.0


class Grade(IntEnum):
    """Self-reported recall outcome. AGAIN is a lapse; the rest are recalls."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Weights:
    """The 17 FSRS-4.5 parameters, with names instead of indices.

    The upstream implementations index a flat vector (`w[8]`, `w[9]`, ...).
    That is fine for a library whose formulas are already trusted, but it is a
    bad idea in a project where the whole point is that a reader can check the
    model. Naming the fields makes an off-by-one in the parameter vector a
    type-level mistake rather than a silent numerical one, and it lets
    `validate()` express what each parameter is *supposed* to do.
    """

    # Initial stability after the very first review, per grade (days).
    s_again: float
    s_hard: float
    s_good: float
    s_easy: float
    # Initial difficulty: D0(g) = d_base - d_slope * (g - 3), so d_base == D0(GOOD).
    # NOTE: FSRS-5 replaced this with the exponential form
    # D0(g) = w4 - exp(w5 * (g - 1)) + 1 *and* refitted w4/w5. Mixing the v5
    # formula with the v4.5 parameter vector collapses D0(GOOD) to -5.5, which
    # the clamp then silently turns into 1.0 -- every item looks trivially easy
    # and stability explodes. The property tests did not catch this; printing
    # D0 per grade did. See AUDIT.md, item 11.
    d_base: float
    d_slope: float
    # Difficulty update.
    d_delta: float  # sensitivity of D to (grade - 3)
    d_reversion: float  # mean-reversion weight toward D0(EASY)
    # Stability increase on successful recall.
    sinc_scale: float  # e ** sinc_scale scales the whole gain
    sinc_s_decay: float  # S ** -sinc_s_decay  -> diminishing returns
    sinc_r_gain: float  # exp((1 - R) * sinc_r_gain) -> spacing effect
    # Post-lapse stability.
    lapse_scale: float
    lapse_d_decay: float
    lapse_s_gain: float
    lapse_r_gain: float
    # Grade modifiers on recall.
    hard_penalty: float  # < 1
    easy_bonus: float  # > 1

    @classmethod
    def from_vector(cls, v: Sequence[float]) -> "Weights":
        if len(v) != 17:
            raise ValueError(f"FSRS-4.5 takes 17 parameters, got {len(v)}")
        return cls(*(float(x) for x in v))

    def validate(self) -> None:
        """Reject parameter vectors that cannot express the model's semantics.

        This is a cheap structural guard, not a goodness-of-fit test. It exists
        because a mis-ordered parameter vector still runs, still produces
        numbers, and still fills a results table -- exactly the failure mode
        that produced the January results.
        """
        if not (0 < self.s_again < self.s_hard < self.s_good < self.s_easy):
            raise ValueError("initial stabilities must be positive and increasing in grade")
        if not 0.0 <= self.d_reversion <= 1.0:
            raise ValueError("d_reversion is a convex-combination weight; must lie in [0, 1]")
        if self.sinc_s_decay <= 0:
            raise ValueError("sinc_s_decay must be > 0 for diminishing returns in S")
        if self.sinc_r_gain <= 0:
            raise ValueError("sinc_r_gain must be > 0 for the spacing effect to exist")
        if not 0 < self.hard_penalty < 1 < self.easy_bonus:
            raise ValueError("expected hard_penalty < 1 < easy_bonus")
        if self.lapse_scale <= 0 or self.lapse_r_gain <= 0:
            raise ValueError("post-lapse parameters must be positive")


# open-spaced-repetition/awesome-fsrs, "The Algorithm" wiki (FSRS-4.5 defaults).
# These are population defaults, not a fit to any individual. See AUDIT.md.
DEFAULT_WEIGHTS = Weights.from_vector(
    [
        0.4872, 1.4003, 3.7145, 13.8206,  # initial stability, grades 1..4
        5.1618, 1.2298,                    # initial difficulty
        0.8975, 0.031,                     # difficulty update
        1.6474, 0.1367, 1.0461,            # stability on recall
        2.1072, 0.0793, 0.3246, 1.587,     # stability on lapse
        0.2272, 2.8755,                    # hard penalty, easy bonus
    ]
)
DEFAULT_WEIGHTS.validate()


# --------------------------------------------------------------------------- #
# Forgetting curve
# --------------------------------------------------------------------------- #


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recalling the item after `elapsed_days` without review.

    R(0) = 1 and R(S) = 0.9 by construction: stability is *defined* as the
    interval at which recall probability has fallen to 90%.
    """
    if elapsed_days < 0:
        raise ValueError("elapsed_days must be >= 0")
    s = max(stability, S_MIN)
    return (1.0 + FACTOR * elapsed_days / s) ** DECAY


def interval_for_retention(stability: float, target_retention: float = 0.9) -> float:
    """Inverse of the forgetting curve: how long until R falls to the target.

    This is the "ideal" review delay an unconstrained FSRS scheduler would use.
    The whole point of the project is that this delay is usually not available,
    so we need a search that finds the best *reachable* slot near it.
    """
    if not 0.0 < target_retention < 1.0:
        raise ValueError("target_retention must lie in (0, 1)")
    s = max(stability, S_MIN)
    return s / FACTOR * (target_retention ** (1.0 / DECAY) - 1.0)


# --------------------------------------------------------------------------- #
# Memory state and its transition
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MemoryState:
    """DSR state of one item. Immutable, so it is safe to share across the
    thousands of search nodes an A* frontier holds simultaneously."""

    stability: float
    difficulty: float

    def __post_init__(self) -> None:
        if self.stability < S_MIN - 1e-12:
            raise ValueError(f"stability {self.stability} below floor {S_MIN}")
        if not D_MIN - 1e-9 <= self.difficulty <= D_MAX + 1e-9:
            raise ValueError(f"difficulty {self.difficulty} outside [{D_MIN}, {D_MAX}]")

    def retrievability_at(self, elapsed_days: float) -> float:
        return retrievability(elapsed_days, self.stability)

    def ideal_interval(self, target_retention: float = 0.9) -> float:
        return interval_for_retention(self.stability, target_retention)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def initial_difficulty(grade: Grade, w: Weights = DEFAULT_WEIGHTS) -> float:
    """D0(g) = d_base - d_slope * (g - 3)  (FSRS-4 / 4.5 linear form)."""
    return _clamp(w.d_base - w.d_slope * (grade - 3), D_MIN, D_MAX)


def initial_state(grade: Grade, w: Weights = DEFAULT_WEIGHTS) -> MemoryState:
    s0 = (w.s_again, w.s_hard, w.s_good, w.s_easy)[grade - 1]
    return MemoryState(max(s0, S_MIN), initial_difficulty(grade, w))


def _next_difficulty(difficulty: float, grade: Grade, w: Weights) -> float:
    """D drifts up on failure, down on success, and mean-reverts toward D0(GOOD)
    so that it cannot ratchet to 10 and stick there. (FSRS-5 reverts toward
    D0(EASY) instead; we are implementing 4.5 consistently.)"""
    d = difficulty - w.d_delta * (grade - 3)
    target = initial_difficulty(Grade.GOOD, w)
    d = w.d_reversion * target + (1.0 - w.d_reversion) * d
    return _clamp(d, D_MIN, D_MAX)


def _stability_on_recall(
    stability: float, difficulty: float, r: float, grade: Grade, w: Weights
) -> float:
    """S' = S * (1 + e^a * (11 - D) * S^-b * (e^((1-R)*c) - 1) * modifiers)

    Three things to notice, because they are the three things the January model
    could not express:

      * (e^((1-R)*c) - 1) -> 0 as R -> 1. Reviewing something you certainly
        remember teaches you nothing. This *is* the spacing effect.
      * S^-b shrinks the gain as S grows: diminishing returns.
      * (11 - D) shrinks the gain for hard items.
    """
    hard = w.hard_penalty if grade == Grade.HARD else 1.0
    easy = w.easy_bonus if grade == Grade.EASY else 1.0
    s = max(stability, S_MIN)
    gain = (
        math.exp(w.sinc_scale)
        * (11.0 - difficulty)
        * s ** (-w.sinc_s_decay)
        * (math.exp((1.0 - r) * w.sinc_r_gain) - 1.0)
        * hard
        * easy
    )
    return max(s * (1.0 + gain), S_MIN)


def _stability_on_lapse(stability: float, difficulty: float, r: float, w: Weights) -> float:
    """Post-lapse stability, exactly as FSRS-4.5 defines it.

    There is no `min(., S)` here. An earlier version clamped the result at the
    pre-lapse stability "so that forgetting can never help"; FSRS-4.5 has no such
    clamp, and at low stability after a long gap its formula does return more than
    the pre-lapse value (pinned against py-fsrs 2.5.1 in tests/test_memory.py).
    FSRS-5 later added a different cap. What the bounds in this project need is
    weaker and holds without a clamp: a lapse never ends above a successful recall
    from the same state after the same delay (AUDIT.md item 27).
    """
    s = max(stability, S_MIN)
    post = (
        w.lapse_scale
        * difficulty ** (-w.lapse_d_decay)
        * ((s + 1.0) ** w.lapse_s_gain - 1.0)
        * math.exp((1.0 - r) * w.lapse_r_gain)
    )
    return max(post, S_MIN)


def review(
    state: MemoryState,
    elapsed_days: float,
    grade: Grade,
    w: Weights = DEFAULT_WEIGHTS,
) -> MemoryState:
    """Apply one review, `elapsed_days` after the previous one."""
    r = retrievability(elapsed_days, state.stability)
    if grade == Grade.AGAIN:
        s_next = _stability_on_lapse(state.stability, state.difficulty, r, w)
    else:
        s_next = _stability_on_recall(state.stability, state.difficulty, r, grade, w)
    return MemoryState(s_next, _next_difficulty(state.difficulty, grade, w))


# --------------------------------------------------------------------------- #
# Expected-value interface -- what the scheduler actually optimises
# --------------------------------------------------------------------------- #


def expected_stability(
    state: MemoryState,
    elapsed_days: float,
    w: Weights = DEFAULT_WEIGHTS,
    recall_grade: Grade = Grade.GOOD,
) -> float:
    """E[S'] over the two outcomes of a review scheduled `elapsed_days` out.

    E[S'] = R * S'_recall + (1 - R) * S'_lapse

    Reviewing early is safe but nearly worthless (R -> 1, gain -> 0).
    Reviewing late is valuable if it works and costly if it does not.
    The trade-off produces an interior optimum: there is a genuinely best
    moment to review, which is the objective the search should chase.
    """
    r = retrievability(elapsed_days, state.stability)
    s_recall = _stability_on_recall(state.stability, state.difficulty, r, recall_grade, w)
    s_lapse = _stability_on_lapse(state.stability, state.difficulty, r, w)
    return r * s_recall + (1.0 - r) * s_lapse


def expected_gain(
    state: MemoryState,
    elapsed_days: float,
    w: Weights = DEFAULT_WEIGHTS,
    recall_grade: Grade = Grade.GOOD,
) -> float:
    """E[S'] - S. Non-negative near the optimum, negative if you review far too
    late (the expected outcome is a lapse that resets stability)."""
    return expected_stability(state, elapsed_days, w, recall_grade) - state.stability


def expected_gain_rate(
    state: MemoryState,
    elapsed_days: float,
    w: Weights = DEFAULT_WEIGHTS,
    recall_grade: Grade = Grade.GOOD,
) -> float:
    """Expected stability gained per day of waiting: (E[S'] - S) / t.

    DIAGNOSTIC ONLY -- do not use this as the scheduler's objective. Both of the
    obvious single-review objectives degenerate, and it is worth recording why,
    because picking either one would have produced another set of confident and
    meaningless numbers:

      * argmax E[S']  -> ~97 days for a 5-day item. Post-lapse stability does
        not fall with the delay (it rises slightly), so the downside of waiting
        is capped at the lapse value while the upside keeps growing. Waiting is
        nearly free, so the model says wait.
      * argmax E[S']/t -> t -> 0. For small t, R ~ 1 - FACTOR*t/(2S), so the gain
        is linear in t and the ratio tends to a positive constant. The model says
        review immediately.

    Neither is a claim about memory; both are artefacts of optimising a
    single review in isolation. The 0.9 target retention used in practice comes
    from a *workload* argument, not from maximising stability.

    The objective that actually has an interior solution is
    `expected_retention_at`: fix a deadline and a budget of study blocks, and ask
    where to put them. See its docstring.
    """
    if elapsed_days <= 0:
        raise ValueError("elapsed_days must be > 0 for a rate")
    return expected_gain(state, elapsed_days, w, recall_grade) / elapsed_days


def expected_retention_at(
    state: MemoryState,
    review_days: Sequence[float],
    exam_day: float,
    w: Weights = DEFAULT_WEIGHTS,
    recall_grade: Grade = Grade.GOOD,
) -> float:
    """P(recall at `exam_day`) given reviews placed at `review_days`.

    This is the objective the scheduler should maximise, summed over topics.
    It is a real optimisation problem because:

      * a review placed too early is wasted (R is already ~1, so the stability
        gain is ~0);
      * a review placed too late risks a lapse, and a lapse resets stability;
      * you only get a fixed number of reviews, because there are only so many
        free 1.5-hour blocks between now and the exam.

    Note carefully what this objective does *not* say. With no capacity limit it
    prefers cramming: a review five minutes before the exam gives R ~ 1. The
    spacing effect only becomes decision-relevant once a constraint (max blocks
    per day, several competing subjects, sleep) makes cramming infeasible. That
    interaction -- decay model times capacity constraint -- is where a scheduler
    earns its keep, and stating it this way is more defensible than claiming
    spacing is unconditionally optimal.

    The expectation is exact, not sampled: each review branches into recall
    (probability R) and lapse (1 - R), so the cost is 2**len(review_days).
    Keep the per-topic review budget small, or switch to the deterministic
    always-recalled trajectory for the A* heuristic.
    """
    days = list(review_days)
    if any(b < a for a, b in zip(days, days[1:])):
        raise ValueError("review_days must be non-decreasing")
    if days and (days[0] < 0 or days[-1] > exam_day):
        raise ValueError("review_days must lie in [0, exam_day]")

    def walk(st: MemoryState, last: float, idx: int, prob: float) -> float:
        if prob == 0.0:
            return 0.0
        if idx == len(days):
            return prob * retrievability(exam_day - last, st.stability)
        elapsed = days[idx] - last
        r = retrievability(elapsed, st.stability)
        recalled = walk(review(st, elapsed, recall_grade, w), days[idx], idx + 1, prob * r)
        lapsed = walk(review(st, elapsed, Grade.AGAIN, w), days[idx], idx + 1, prob * (1.0 - r))
        return recalled + lapsed

    return walk(state, 0.0, 0, 1.0)


def best_review_delay(
    state: MemoryState,
    candidates: Iterable[float],
    w: Weights = DEFAULT_WEIGHTS,
    recall_grade: Grade = Grade.GOOD,
) -> float:
    """The delay in `candidates` that maximises E[S'].

    `candidates` is deliberately a caller-supplied set rather than a continuous
    optimisation: in this project the feasible delays are exactly the free slots
    in the student's calendar, so the maximisation is already discrete.
    """
    cands = list(candidates)
    if not cands:
        raise ValueError("no candidate delays given")
    return max(cands, key=lambda t: expected_stability(state, t, w, recall_grade))


def stability_for_interval(days: float, target_retention: float = 0.9) -> float:
    """Inverse of `interval_for_retention`: the stability you need in order to
    still recall something `days` from now with probability `target_retention`.

    This is the bridge between the memory layer and the scheduling layer. A
    student does not have an intrinsic stability target; they have an exam on a
    date and a tolerance for forgetting. This converts the second into the first,
    which is what the SSP goal condition needs.

    At the default 0.9 it returns `days` exactly, since stability is defined as
    the 90% interval.
    """
    if days <= 0:
        raise ValueError("days must be > 0")
    if not 0.0 < target_retention < 1.0:
        raise ValueError("target_retention must lie in (0, 1)")
    return FACTOR * days / (target_retention ** (1.0 / DECAY) - 1.0)
