"""
A student's own FSRS-6 parameters (DECISIONS.md D53), fitted to their answers with
NumPy alone.

FSRS-6's 21 parameters are population defaults (`memory.DEFAULT_WEIGHTS_6`). A
student whose memory differs from the population's gets intervals and forecasts
that are wrong for them, the same way every time. Here the parameters are fitted
to the student's own answers once there are enough of them (`MIN_REVIEWS`, measured
by `benchmarks/personal_fsrs.py`).

What is fitted: an answer history per item (`History`: a flashcard, or a topic from
its lecture through its graded self-tests). The first answer sets the item's state;
each later answer at least a day after the one before is a prediction the model
made (its recall then) and an outcome (recalled, unless the answer was "again").
The measure is the mean log loss over those predictions, as the open benchmark
scores models [ref:srs-benchmark]: answers on the same day update the state and are
not scored.

How: the most probable parameters under a prior centred on the population's. Each
parameter is put on a 0-to-1 scale between py-fsrs's bounds (the four initial
stabilities on a log scale, since they span 0.001 to 100 days), and the fit
minimises the summed log loss plus the squared distance from the defaults on that
scale over twice `PRIOR_SCALE` squared. With few answers the defaults win; with many,
the answers do. Minimised by Adam on central finite differences; the 43 parameter
sets a step needs are replayed together, every item at once (`_replay`); the
step is annealed so the fit settles.

Rejected: py-fsrs's optimizer (`fsrs[optimizer]`), which brings PyTorch, hundreds of
megabytes on a free server for a fit of 21 numbers (the owner, 2026-10-14);
analytic gradients (the review has three branches and five clamps, and finite
differences over 21 parameters are 43 vectorised replays a step); fitting without
a prior, or with a loose one (with a few hundred answers the fit chases noise:
`benchmarks/personal_fsrs.py --prior`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

import numpy as np

from .memory import (
    _BOUNDS_6,
    D_MAX,
    D_MIN,
    DEFAULT_WEIGHTS_6,
    S_MIN,
    Grade,
    MemoryState,
    Weights6,
    initial_state6,
    interval_for_retention6,
    retrievability6,
    review6,
)

FREE: tuple[int, ...] = tuple(range(21))  # the parameters a fit moves; the others keep the population's
PRIOR_SCALE = 0.02  # how far from the defaults, on the 0-to-1 scale, a parameter may wander cheaply (D53)
STEPS = 300  # Adam steps; the fit stops sooner when the objective stops moving
RATE = 0.01  # Adam's step, on the 0-to-1 scale
MIN_REVIEWS = 400  # scored answers before a student's own parameters are used (D53, measured)
_H = 1e-4  # finite-difference step, on the 0-to-1 scale
_R_CLIP = 1e-4  # predicted recall is kept in [1e-4, 1 - 1e-4] for the log loss, as the benchmark does

History = Sequence[tuple[float | None, Grade]]  # (days since the previous answer, None first; grade)

_LO = np.array([lo for lo, _ in _BOUNDS_6])
_HI = np.array([hi for _, hi in _BOUNDS_6])
_ULO = _LO.copy()
_UHI = _HI.copy()
_ULO[:4], _UHI[:4] = np.log(_LO[:4]), np.log(_HI[:4])  # the 0-to-1 scale's ends


def _to_unit(w: np.ndarray) -> np.ndarray:
    """Weights (..., 21) on the 0-to-1 scale."""
    x = np.array(w, dtype=float)
    x[..., :4] = np.log(np.maximum(x[..., :4], _LO[:4]))
    return (x - _ULO) / (_UHI - _ULO)


def _from_unit(z: np.ndarray) -> np.ndarray:
    w = _ULO + np.clip(z, 0.0, 1.0) * (_UHI - _ULO)
    w[..., :4] = np.exp(w[..., :4])
    # Initial stabilities must not decrease with the grade (Weights6.validate).
    w[..., 1] = np.maximum(w[..., 1], w[..., 0])
    w[..., 2] = np.maximum(w[..., 2], w[..., 1])
    w[..., 3] = np.maximum(w[..., 3], w[..., 2])
    return np.clip(w, _LO, _HI)


Mask = Sequence[
    Sequence[bool]
]  # per item and answer: is it scored? (a held-out test scores only the later ones)


@dataclass(frozen=True)
class _Batch:
    """Histories as arrays: `elapsed`, `grade` and `scored` (items, longest), grade
    0 past an item's end."""

    elapsed: np.ndarray
    grade: np.ndarray
    scored: np.ndarray

    @classmethod
    def of(cls, histories: Sequence[History], mask: Mask | None = None) -> _Batch:
        longest = max((len(h) for h in histories), default=0)
        elapsed = np.zeros((len(histories), longest))
        grade = np.zeros((len(histories), longest), dtype=np.int64)
        counted = np.zeros((len(histories), longest), dtype=bool)
        for i, history in enumerate(histories):
            for j, (days, g) in enumerate(history):
                elapsed[i, j] = 0.0 if days is None else days
                grade[i, j] = int(g)
                counted[i, j] = j > 0 and days is not None and days >= 1.0 and (mask is None or mask[i][j])
        return cls(elapsed, grade, counted)


def scored(histories: Sequence[History], mask: Mask | None = None) -> int:
    """The answers a fit learns from, or a test scores: after an item's first, at
    least a day after the one before (and in `mask`)."""
    return int(_Batch.of(histories, mask).scored.sum()) if histories else 0


def _replay(w: np.ndarray, batch: _Batch) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Replay every item under each parameter set `w` (P, 21) at once: the summed
    log loss of the scored answers (P,), and the final stability and difficulty
    (P, items). The same arithmetic as `memory.review6`, a test keeps them equal."""
    p = w.shape[0]
    n, longest = batch.grade.shape
    col = [w[:, k : k + 1] for k in range(21)]  # each (P, 1), broadcast over items
    decay = col[20]
    factor = 0.9 ** (-1.0 / decay) - 1.0
    stability = np.full((p, n), S_MIN)
    difficulty = np.full((p, n), D_MIN)
    loss = np.zeros(p)

    def d0(g: np.ndarray) -> np.ndarray:
        return col[4] - np.exp(col[5] * (g - 1.0)) + 1.0

    easy_d0 = d0(np.full(1, 4.0))
    for j in range(longest):
        g = batch.grade[:, j]
        active = g > 0
        if not active.any():
            break
        gf = np.where(active, g, 3).astype(float)  # a harmless grade where an item has ended
        if j == 0:
            s0 = np.take_along_axis(w[:, :4], np.broadcast_to(gf.astype(int) - 1, (p, n)), axis=1)
            stability = np.where(active, np.maximum(s0, S_MIN), stability)
            difficulty = np.where(active, np.clip(d0(gf), D_MIN, D_MAX), difficulty)
            continue
        e = batch.elapsed[:, j]
        same_day = e < 1.0
        s = np.maximum(stability, S_MIN)
        r = (1.0 + factor * e / s) ** -decay
        rc = np.clip(r, _R_CLIP, 1.0 - _R_CLIP)
        recalled = gf > 1.0
        counted = batch.scored[:, j]
        loss += np.where(counted, -np.where(recalled, np.log(rc), np.log(1.0 - rc)), 0.0).sum(axis=1)

        increase = np.exp(col[17] * (gf - 3.0 + col[18])) * s ** -col[19]
        increase = np.where(gf != 1.0, np.maximum(increase, 1.0), increase)
        short = np.maximum(s * increase, S_MIN)
        hard = np.where(gf == 2.0, col[15], 1.0)
        easy = np.where(gf == 4.0, col[16], 1.0)
        gain = np.exp(col[8]) * (11.0 - difficulty) * s ** -col[9] * (np.exp((1.0 - r) * col[10]) - 1.0)
        on_recall = np.maximum(s * (1.0 + gain * hard * easy), S_MIN)
        long_lapse = (
            col[11] * difficulty ** -col[12] * ((s + 1.0) ** col[13] - 1.0) * np.exp((1.0 - r) * col[14])
        )
        on_lapse = np.maximum(np.minimum(long_lapse, s / np.exp(col[17] * col[18])), S_MIN)
        s_next = np.where(same_day, short, np.where(gf == 1.0, on_lapse, on_recall))

        damped = difficulty + (10.0 - difficulty) * (-col[6] * (gf - 3.0)) / 9.0
        d_next = np.clip(col[7] * easy_d0 + (1.0 - col[7]) * damped, D_MIN, D_MAX)
        stability = np.where(active, s_next, stability)
        difficulty = np.where(active, d_next, difficulty)
    return loss, stability, difficulty


def log_loss(weights: Weights6, histories: Sequence[History], mask: Mask | None = None) -> float:
    """Mean log loss of `weights`' predictions over the scored answers (nan with none)."""
    batch = _Batch.of(histories, mask)
    count = int(batch.scored.sum())
    if count == 0:
        return math.nan
    loss, _, _ = _replay(np.array([weights.as_vector()]), batch)
    return float(loss[0]) / count


@dataclass(frozen=True)
class Fit:
    """A student's parameters, the scored answers they were fitted to, and the mean
    log loss there of theirs and of the population's (in-sample: the threshold, not
    this, says whether to trust them)."""

    weights: Weights6
    reviews: int
    loss: float
    default_loss: float


def fit(
    histories: Sequence[History],
    *,
    prior_scale: float = PRIOR_SCALE,
    steps: int = STEPS,
    start: Weights6 = DEFAULT_WEIGHTS_6,
    free: Sequence[int] = FREE,
) -> Fit:
    """The most probable FSRS-6 parameters for these answers, the prior centred on
    `start` (the population's)."""
    count = scored(histories)
    if count == 0:
        raise ValueError("no answer to fit: each item needs a second answer a day or more later")
    batch = _Batch.of(histories)
    centre = _to_unit(np.array(start.as_vector()))
    z = centre.copy()
    k = len(free)
    eye = np.zeros((k, 21))
    eye[np.arange(k), list(free)] = _H

    def objective(zs: np.ndarray) -> np.ndarray:
        data, _, _ = _replay(_from_unit(zs.copy()), batch)
        return data + ((zs - centre) ** 2).sum(axis=1) / (2.0 * prior_scale**2)

    m, v = np.zeros(21), np.zeros(21)
    moving = np.zeros(21, dtype=bool)
    moving[list(free)] = True
    previous = math.inf
    for t in range(1, steps + 1):
        values = objective(np.vstack([z, z + eye, z - eye]))
        grad = np.zeros(21)
        grad[moving] = (values[1 : k + 1] - values[k + 1 :]) / (2.0 * _H)
        m = 0.9 * m + 0.1 * grad
        v = 0.999 * v + 0.001 * grad**2
        # Adam's step does not shrink with the gradient, so it is annealed (a cosine
        # from RATE to nothing) for the fit to settle rather than circle the optimum.
        rate = RATE * 0.5 * (1.0 + math.cos(math.pi * t / steps))
        z = np.clip(z - rate * (m / (1 - 0.9**t)) / (np.sqrt(v / (1 - 0.999**t)) + 1e-8), 0.0, 1.0)
        if abs(previous - values[0]) < 1e-6 * max(1.0, abs(values[0])):
            break
        previous = values[0]
    weights = Weights6.from_vector([float(x) for x in _from_unit(z.copy())])
    weights.validate()
    return Fit(weights, count, log_loss(weights, histories), log_loss(start, histories))


# --------------------------------------------------------------------------- #
# Simulated students: answer logs made with known parameters, for the threshold's
# measurement (benchmarks/personal_fsrs.py) and the tests. Not used by the product.
# --------------------------------------------------------------------------- #

GRADES_IF_RECALLED = ((Grade.HARD, 0.15), (Grade.GOOD, 0.75), (Grade.EASY, 0.10))  # a stated guess


def student(rng: Random, spread: float = 0.3, start: Weights6 = DEFAULT_WEIGHTS_6) -> Weights6:
    """A made-up student: each parameter the population's times e^N(0, spread),
    within the bounds. How far real students are from the population is not known
    here; `spread` is a stated assumption, and the benchmark varies it."""
    w = np.array(start.as_vector()) * np.exp(np.array([rng.gauss(0.0, spread) for _ in range(21)]))
    return Weights6.from_vector([float(x) for x in _from_unit(_to_unit(np.clip(w, _LO, _HI)))])


def answers(
    truth: Weights6,
    rng: Random,
    *,
    days: int,
    new_per_day: int = 10,
    scheduler: Weights6 = DEFAULT_WEIGHTS_6,
    retention: float = 0.9,
) -> list[list[tuple[float, float | None, Grade]]]:
    """A flashcard deck studied as the product schedules it (`cards.schedule`:
    `scheduler`'s interval for `retention`, "again" back the same day), the outcome
    of each answer drawn from `truth`. Per card: (day, days since the previous
    answer or None, grade)."""
    logs: list[list[tuple[float, float | None, Grade]]] = []
    state: list[MemoryState] = []  # the scheduler's view, which decides when a card is due
    real: list[MemoryState] = []  # the truth's, which decides the outcome
    due: list[float] = []
    for day in range(days):
        for _ in range(new_per_day):
            logs.append([])
            state.append(MemoryState(S_MIN, D_MIN))  # replaced at the first answer
            real.append(MemoryState(S_MIN, D_MIN))
            due.append(float(day))
        for i in range(len(logs)):
            if due[i] > day:
                continue
            now = day + rng.random() * 0.5  # some time that day
            if not logs[i]:
                recalled = rng.random() < 0.5  # a new card: about half known already (a guess)
                grade = _draw(rng) if recalled else Grade.AGAIN
                logs[i].append((now, None, grade))
                state[i] = initial_state6(grade, scheduler)
                real[i] = initial_state6(grade, truth)
            else:
                last = logs[i][-1][0]
                elapsed = now - last
                recalled = rng.random() < retrievability6(elapsed, real[i].stability, truth)
                grade = _draw(rng) if recalled else Grade.AGAIN
                logs[i].append((now, elapsed, grade))
                state[i] = review6(state[i], elapsed, grade, scheduler)
                real[i] = review6(real[i], elapsed, grade, truth)
            if grade == Grade.AGAIN:
                # Back in the same sitting; answered "good" ten minutes later.
                again = now + 10 / (24 * 60)
                logs[i].append((again, again - now, Grade.GOOD))
                state[i] = review6(state[i], again - now, Grade.GOOD, scheduler)
                real[i] = review6(real[i], again - now, Grade.GOOD, truth)
                now = again
            due[i] = now + interval_for_retention6(state[i].stability, retention, scheduler)
    return logs


def _draw(rng: Random) -> Grade:
    x, total = rng.random(), 0.0
    for grade, p in GRADES_IF_RECALLED:
        total += p
        if x < total:
            return grade
    return Grade.GOOD


def split(
    logs: Sequence[Sequence[tuple[float, float | None, Grade]]], day: float, until: float = math.inf
) -> tuple[list[History], list[History], list[list[bool]]]:
    """Answers before `day`, to fit; every card's whole history, with only the
    answers from `day` until `until` scored (the mask), to test: a held-out test of
    the future."""
    before = [[(e, g) for at, e, g in log if at < day] for log in logs]
    whole: list[History] = [[(e, g) for _, e, g in log] for log in logs]
    mask = [[day <= at < until for at, _, _ in log] for log in logs]
    return [h for h in before if h], whole, mask
