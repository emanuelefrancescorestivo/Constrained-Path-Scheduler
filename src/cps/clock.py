"""
The value function with a clock: what it costs to reach the target *by the exam*.

Why this module exists
----------------------
`budget.V(D, S, b)` priced leftover work by the number of free blocks left, and
its skip branch left `(D, S)` untouched. Waiting neither aged the memory nor
brought the exam closer, so while blocks were plentiful skipping was free, and the
rolling planner deferred every review to the last week (AUDIT.md item 20). The
resource that waiting consumes is calendar time, so time has to be in the state.

Two quantities, one table
-------------------------
Right after a review, a topic is fully described by `(D, S, t)`, with `t` the days
left before its exam. The next decision is the delay `a` to the next review, which
fixes the recall probability `r = R(a, S)`:

    V(D, S, t) = 0                                      if S >= target
               = min( penalty,
                      min over 0 < a <= t of
                          1 + r V(D_good, S_recall(a), t - a)
                            + (1 - r) V(D_again, S_lapse(a), t - a) )

`penalty` is the price of giving up on the topic, which is also what arriving at
the exam unready costs: `V(D, S, 0) = penalty` below the target. Every review
moves strictly forward in time, so the recursion is acyclic in `t` and is solved
by one backward sweep, with no convergence loop across levels.

Between reviews the state also needs the time `e` elapsed since the last one,
because the next review cannot happen in the past. Leaving `e` out is admissible
and useless: the value then does not change while a topic waits, so waiting is
free again and the planner defers forever (measured in
`benchmarks/spike_clock.py`, row "B-noelapsed": one review, then nothing). It does
not need a new dimension in the table, though. While a topic waits, `e + t` stays
constant, so the waiting value is a minimum over a suffix of the same actions:

    W(D, S, e, t) = min( penalty,
                         min over e <= a <= e + t of
                             1 + r V(D_good, S_recall(a), e + t - a)
                               + (1 - r) V(D_again, S_lapse(a), e + t - a) )

`V(D, S, t) = W(D, S, 0, t)`. `W` is computed at query time from the `V` table.

Which goal, and why not "recall at the exam"
-------------------------------------------
The natural goal is `R(t, S) >= rho` right after a review: recall at the exam is
good enough. It degenerates. Under FSRS any review shortly before the exam puts
recall near 1, so the cheapest way to satisfy it is one review the night before
(`spike_clock.py`, row "B-exam": first and only review on day 20 of 21). That is
cramming, and FSRS is right that cramming works for the exam day itself; it is not
what a study planner is for. The goal kept here is the stability target
`S >= stability_for_interval(days_to_exam, rho)`, measured from the start of the
plan: memory that would keep recall at `rho` for as long as the preparation
lasted. It implies the exam-day condition and cannot be met by one late review.
It is a stated choice, per subject, derived from that subject's exam date.

Admissibility
-------------
`V` is used as the A*/AO* heuristic and as the window continuation, so the
optimistic solve must be a lower bound on the true expected cost over continuous
delays, not only over a grid of them. Three ingredients, each relying only on the
*true* value function being monotone (checked numerically in the tests):

* **Delays in cells, not points.** For delays in `[alpha, beta]`, recall
  probability lies in `[R(beta), R(alpha)]`, the post-review stabilities are at
  most their values at `beta`, and the time left is at most `t - alpha`. Since `V`
  is decreasing in `S` and non-increasing in `t`, evaluating both successors at
  `(S(beta), t - alpha)` and taking the better endpoint of the recall probability
  (the expression is linear in it) bounds every delay in the cell. This removes
  the finite-action caveat that `ssp` and `budget` still carry (AUDIT.md item 22).
* **Cell-minimum interpolation** in `(D, log S)`, as in `ssp`, and rounding the
  time left *up* to the grid.
* **The first cell refers to its own level.** A delay in `[0, dt]` leaves time
  `t - 0` on the grid, so level `t` depends on itself. It is solved by iterating
  from zero, which increases monotonically towards the least fixed point and is a
  valid lower bound at every iterate (each is `T^n(0) <= T^n(V*) <= V*`).

The accurate ("bilinear") solve evaluates point delays and interpolates. It is an
estimate for reporting, and the heuristic entry points refuse it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .memory import (
    D_MAX,
    D_MIN,
    DEFAULT_WEIGHTS,
    Grade,
    MemoryState,
    Weights,
    retrievability,
    review,
    stability_for_interval,
)
from .ssp import (
    _interp_index,
    _vec_next_difficulty,
    _vec_stability_on_lapse,
    _vec_stability_on_recall,
)

_FACTOR = 19.0 / 81.0


def _vec_retrievability(elapsed: np.ndarray, stability: np.ndarray) -> np.ndarray:
    return (1.0 + _FACTOR * elapsed / np.maximum(stability, 0.01)) ** -0.5


@dataclass(frozen=True, slots=True)
class ClockConfig:
    """Discretisation and the one modelling choice this solver makes.

    `failure_penalty` is the cost, in study blocks, of reaching the exam with the
    topic below its target. It trades readiness against workload: at 40, a policy
    will spend one extra block to raise the probability of being ready by 2.5
    percentage points. It is a choice, not a measurement, and callers comparing
    two runs must pin it (CLAUDE.md invariant 6).

    `horizon_days` is the largest time-to-exam the table covers. Queries above it
    raise instead of clamping: `V` is non-increasing in `t`, so a clamp would
    overestimate, which is the direction that breaks admissibility.
    """

    target_stability: float
    horizon_days: float
    failure_penalty: float = 40.0
    time_step: float = 0.25
    min_stability: float = 0.05
    n_stability: int = 96
    n_difficulty: int = 19
    interpolation: str = "bilinear"
    tolerance: float = 1e-10
    max_inner_iterations: int = 10_000

    def __post_init__(self) -> None:
        if self.target_stability <= self.min_stability:
            raise ValueError("target_stability must exceed min_stability")
        if self.horizon_days <= 0 or self.time_step <= 0:
            raise ValueError("horizon_days and time_step must be positive")
        if self.failure_penalty <= 0:
            raise ValueError("failure_penalty must be positive")
        if self.interpolation not in ("bilinear", "optimistic"):
            raise ValueError(f"unknown interpolation mode {self.interpolation!r}")

    @classmethod
    def for_exam(cls, days_to_exam: float, retention: float = 0.9, **kwargs) -> "ClockConfig":
        """Target and horizon from an exam date: see "Which goal" in the module docs."""
        params = dict(
            target_stability=stability_for_interval(days_to_exam, retention),
            horizon_days=days_to_exam,
        )
        params.update(kwargs)
        return cls(**params)

    @classmethod
    def for_heuristic(cls, target_stability: float, horizon_days: float, **kwargs) -> "ClockConfig":
        """Lower-bounding variant, the only kind a search may use as `h`."""
        params = dict(
            target_stability=target_stability,
            horizon_days=horizon_days,
            interpolation="optimistic",
        )
        params.update(kwargs)
        return cls(**params)

    def time_grid(self) -> np.ndarray:
        steps = int(math.ceil(self.horizon_days / self.time_step - 1e-9))
        return np.arange(steps + 1) * self.time_step

    def stability_grid(self) -> np.ndarray:
        return np.geomspace(self.min_stability, self.target_stability, self.n_stability)

    def difficulty_grid(self) -> np.ndarray:
        return np.linspace(D_MIN, D_MAX, self.n_difficulty)


# --------------------------------------------------------------------------- #
# Interpolation helpers over (D, log S)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Lookup:
    """Where a batch of successor states falls on the (D, log S) grid."""

    flat: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    w_d: np.ndarray
    w_s: np.ndarray
    done: np.ndarray

    @classmethod
    def build(cls, d_grid, s_grid, d, s, target) -> "_Lookup":
        n_s = len(s_grid)
        i, w_d = _interp_index(d_grid, np.clip(d, D_MIN, D_MAX))
        j, w_s = _interp_index(np.log(s_grid), np.log(np.clip(s, s_grid[0], s_grid[-1])))
        flat = (i * n_s + j, i * n_s + j + 1, (i + 1) * n_s + j, (i + 1) * n_s + j + 1)
        return cls(flat=flat, w_d=w_d, w_s=w_s, done=s >= target)

    def values(self, table_flat: np.ndarray, offset, mode: str) -> np.ndarray:
        """Value of each successor, read from `table_flat` at `offset + flat`."""
        c00, c01, c10, c11 = (table_flat[offset + f] for f in self.flat)
        if mode == "optimistic":
            out = np.minimum(np.minimum(c00, c01), np.minimum(c10, c11))
        else:
            wd, ws = self.w_d, self.w_s
            out = (1 - wd) * (1 - ws) * c00 + (1 - wd) * ws * c01 + wd * (1 - ws) * c10 + wd * ws * c11
        return np.where(self.done, 0.0, out)


# --------------------------------------------------------------------------- #
# The solved table and its queries
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClockPolicy:
    """`V(D, S, t)` on a grid, plus the waiting value `W` computed on demand."""

    config: ClockConfig
    weights: Weights
    difficulty_grid: np.ndarray
    stability_grid: np.ndarray
    time_grid: np.ndarray
    expected_cost: np.ndarray  # (n_t, n_d, n_s)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_lower_bound(self) -> bool:
        return self.config.interpolation == "optimistic"

    @property
    def target_stability(self) -> float:
        return self.config.target_stability

    def _give_up(self, stability: float) -> float:
        return 0.0 if stability >= self.config.target_stability else self.config.failure_penalty

    def _check_horizon(self, days_left: float) -> None:
        if days_left > self.config.horizon_days + 1e-9:
            raise ValueError(
                f"query {days_left:.3f} days before the exam, but the table covers "
                f"{self.config.horizon_days:g}; solve with a longer horizon_days"
            )

    # -- right after a review ------------------------------------------------ #

    def post_review(self, difficulty: float, stability: float, days_left: float) -> float:
        """`V(D, S, t)`: expected cost from a topic reviewed just now."""
        return self.waiting(difficulty, stability, 0.0, days_left)

    # -- between reviews ----------------------------------------------------- #

    def _cells(self, difficulty, stability, elapsed, days_left):
        """Q-values of every candidate review time, as an array over cells."""
        cfg = self.config
        dt = cfg.time_step
        count = max(int(math.ceil(days_left / dt - 1e-9)), 1)
        m = np.arange(count)
        alpha = elapsed + m * dt
        beta = np.minimum(alpha + dt, elapsed + days_left)
        s = np.full(count, float(stability))
        d = np.full(count, float(difficulty))
        n_t = len(self.time_grid)
        per_level = self.expected_cost.shape[1] * self.expected_cost.shape[2]
        flat = self.expected_cost.ravel()
        if cfg.interpolation == "optimistic":
            r_hi = _vec_retrievability(alpha, s)
            r_lo = _vec_retrievability(beta, s)
            left = days_left - m * dt
            level = np.clip(np.ceil(left / dt - 1e-9).astype(int), 0, n_t - 1)
            r_eval = r_lo
        else:
            # Point delays e, e + dt, ..., but never less than one step after the
            # last review: the table was solved with delays of at least dt, and a
            # review at zero delay costs a block, teaches nothing, and only nudges
            # difficulty, which a simulation can then exploit forever.
            if elapsed < dt:
                alpha = np.append(alpha, dt)
                m = np.append(m, (dt - elapsed) / dt)
                s, d = np.append(s, s[0]), np.append(d, d[0])
            keep = (alpha >= dt - 1e-12) & (alpha <= elapsed + days_left + 1e-12)
            m, alpha, s, d = m[keep], alpha[keep], s[keep], d[keep]
            if not keep.any():
                return alpha, np.empty(0)
            r_eval = _vec_retrievability(alpha, s)
            left = days_left - m * dt
            level = np.clip(np.round(left / dt).astype(int), 0, n_t - 1)
        s_rec = _vec_stability_on_recall(s, d, r_eval, self.weights)
        s_lap = _vec_stability_on_lapse(s, d, r_eval, self.weights)
        d_rec = _vec_next_difficulty(d, Grade.GOOD, self.weights)
        d_lap = _vec_next_difficulty(d, Grade.AGAIN, self.weights)
        rec = _Lookup.build(self.difficulty_grid, self.stability_grid, d_rec, s_rec, cfg.target_stability)
        lap = _Lookup.build(self.difficulty_grid, self.stability_grid, d_lap, s_lap, cfg.target_stability)
        v_rec = rec.values(flat, level * per_level, cfg.interpolation)
        v_lap = lap.values(flat, level * per_level, cfg.interpolation)
        if cfg.interpolation == "optimistic":
            q = 1.0 + np.minimum(
                r_hi * v_rec + (1 - r_hi) * v_lap,
                r_lo * v_rec + (1 - r_lo) * v_lap,
            )
        else:
            q = 1.0 + r_eval * v_rec + (1 - r_eval) * v_lap
        return alpha, q

    def waiting(self, difficulty: float, stability: float, elapsed: float, days_left: float) -> float:
        """`W(D, S, e, t)`: expected cost for a topic last reviewed `elapsed` days
        ago, with `days_left` before its exam. The next review is at least now."""
        if stability >= self.config.target_stability:
            return 0.0
        if days_left <= 0:
            return self.config.failure_penalty
        self._check_horizon(days_left)
        key = (round(difficulty, 9), round(stability, 9), round(elapsed, 9), round(days_left, 9))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        _, q = self._cells(difficulty, stability, max(elapsed, 0.0), days_left)
        value = float(min(self.config.failure_penalty, q.min() if q.size else np.inf))
        if len(self._cache) < 500_000:
            self._cache[key] = value
        return value

    def best_review_time(
        self, difficulty: float, stability: float, elapsed: float, days_left: float
    ) -> float | None:
        """Elapsed time (days since the last review) at which the policy would
        review next, or None if it would rather give up. For display and for
        simulating the unconstrained policy; resolution is one time step."""
        if stability >= self.config.target_stability or days_left <= 0:
            return None
        self._check_horizon(days_left)
        alpha, q = self._cells(difficulty, stability, max(elapsed, 0.0), days_left)
        if not q.size:
            return None
        best = int(np.argmin(q))
        if q[best] >= self.config.failure_penalty:
            return None
        return float(alpha[best])

    def cost_of_postponing(
        self, difficulty: float, stability: float, elapsed: float, days_left: float, delay: float
    ) -> float:
        """What waiting `delay` more days before deciding costs, in blocks.

        The diagnostic that `budget.marginal_value` provided for the old model,
        where it was 3.4e-09 with the calendar slack. Here it is zero while the
        best review time is still ahead and grows once it has passed.
        """
        now = self.waiting(difficulty, stability, elapsed, days_left)
        later = self.waiting(difficulty, stability, elapsed + delay, days_left - delay)
        return later - now

    def cost_of(self, topic_stability: float, topic_difficulty: float, last_review_day: float,
                now: float, exam_day: float) -> float:
        """Convenience wrapper in absolute days, as the planner holds them."""
        return self.waiting(topic_difficulty, topic_stability, now - last_review_day, exam_day - now)


# --------------------------------------------------------------------------- #
# Solver
# --------------------------------------------------------------------------- #


def solve(config: ClockConfig, weights: Weights = DEFAULT_WEIGHTS) -> ClockPolicy:
    """One backward sweep over the time grid. See the module docstring."""
    weights.validate()
    t_grid = config.time_grid()
    d_grid = config.difficulty_grid()
    s_grid = config.stability_grid()
    n_t, n_d, n_s = len(t_grid), len(d_grid), len(s_grid)
    per_level = n_d * n_s
    dt = config.time_step
    target = config.target_stability
    optimistic = config.interpolation == "optimistic"
    mode = config.interpolation

    d = d_grid[None, :, None]
    s = s_grid[None, None, :]
    k = np.arange(max(n_t - 1, 1))[:, None, None]
    # Optimistic: cell k covers delays [k dt, (k+1) dt]; the successor stability is
    # taken at the right end and the time left at the left end. Accurate: the
    # point delay (k+1) dt.
    r_right = _vec_retrievability((k + 1) * dt, s) * np.ones_like(d)
    r_left = _vec_retrievability(k * dt, s) * np.ones_like(d)
    shape = r_right.shape
    s_rec = _vec_stability_on_recall(s, d, r_right, weights) * np.ones(shape)
    s_lap = _vec_stability_on_lapse(s, d, r_right, weights) * np.ones(shape)
    d_rec = _vec_next_difficulty(d, Grade.GOOD, weights) * np.ones(shape)
    d_lap = _vec_next_difficulty(d, Grade.AGAIN, weights) * np.ones(shape)
    rec = _Lookup.build(d_grid, s_grid, d_rec, s_rec, target)
    lap = _Lookup.build(d_grid, s_grid, d_lap, s_lap, target)
    at_goal = s_grid >= target
    give_up = np.where(at_goal, 0.0, config.failure_penalty)[None, :] * np.ones((n_d, 1))

    values = np.empty((n_t, n_d, n_s))
    values[0] = give_up
    flat = values.ravel()

    def q_of(cells: np.ndarray, levels: np.ndarray) -> np.ndarray:
        """Q for delay cells `cells`, reading the successor table at `levels`."""
        offset = (levels * per_level)[:, None, None]
        sub_rec = _Lookup(
            flat=tuple(f[cells] for f in rec.flat), w_d=rec.w_d[cells], w_s=rec.w_s[cells],
            done=rec.done[cells],
        )
        sub_lap = _Lookup(
            flat=tuple(f[cells] for f in lap.flat), w_d=lap.w_d[cells], w_s=lap.w_s[cells],
            done=lap.done[cells],
        )
        v_rec = sub_rec.values(flat, offset, mode)
        v_lap = sub_lap.values(flat, offset, mode)
        if optimistic:
            hi, lo = r_left[cells], r_right[cells]
            return 1.0 + np.minimum(hi * v_rec + (1 - hi) * v_lap, lo * v_rec + (1 - lo) * v_lap)
        r = r_right[cells]
        return 1.0 + r * v_rec + (1 - r) * v_lap

    for j in range(1, n_t):
        if optimistic:
            # cells 1..j-1 read strictly earlier levels; cell 0 reads level j
            known = give_up
            if j > 1:
                cells = np.arange(1, j)
                known = np.minimum(known, q_of(cells, j - cells).min(axis=0))
            current = np.zeros((n_d, n_s))
            zero = np.array([0])
            for _ in range(config.max_inner_iterations):
                values[j] = current
                nxt = np.minimum(known, q_of(zero, np.array([j]))[0])
                nxt[:, at_goal] = 0.0
                change = float(np.max(np.abs(nxt - current)))
                current = nxt
                if change < config.tolerance:
                    break
            else:
                raise RuntimeError(f"inner iteration at level {j} did not converge")
            values[j] = current
        else:
            cells = np.arange(0, j)
            best = np.minimum(give_up, q_of(cells, j - 1 - cells).min(axis=0))
            best[:, at_goal] = 0.0
            values[j] = best

    return ClockPolicy(
        config=config,
        weights=weights,
        difficulty_grid=d_grid,
        stability_grid=s_grid,
        time_grid=t_grid,
        expected_cost=values,
    )


# --------------------------------------------------------------------------- #
# Simulation of the unconstrained policy, for calibration
# --------------------------------------------------------------------------- #


def simulate(
    policy: ClockPolicy,
    initial: MemoryState,
    days_left: float,
    rng: np.random.Generator,
) -> tuple[int, bool]:
    """Follow the policy with free choice of review times. (reviews, ready)."""
    state, elapsed, left, reviews = initial, 0.0, days_left, 0
    while state.stability < policy.config.target_stability:
        at = policy.best_review_time(state.difficulty, state.stability, elapsed, left)
        if at is None or at - elapsed > left:
            return reviews, False
        wait = max(at, 1e-9) - elapsed
        left -= wait
        elapsed = max(at, 1e-9)
        r = retrievability(elapsed, state.stability)
        grade = Grade.GOOD if rng.random() < r else Grade.AGAIN
        state = review(state, elapsed, grade, policy.weights)
        reviews += 1
        elapsed = 0.0
        if left <= 0:
            break
    return reviews, state.stability >= policy.config.target_stability
