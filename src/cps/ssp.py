"""
SSP-MMC: minimise the expected number of reviews needed to reach a target
stability.

Formulation
-----------
Following Ye et al. (KDD 2022), memorising one topic is a Stochastic Shortest
Path problem over the memory state:

    state   (D, S)              difficulty and stability
    action  r in (0, 1)         the retrievability to review at, which fixes
                                the delay t = interval_for_retention(S, r)
    outcome success w.p. r      -> (D_good, S_recall)
            lapse   w.p. 1 - r  -> (D_again, S_lapse)
    cost    1 per review        (one 1.5-hour study block)
    goal    S >= target         absorbing, zero cost

    V*(D, S) = min_r [ 1 + r * V*(D_good, S_recall) + (1 - r) * V*(D_again, S_lapse) ]

Note that choosing the *target retrievability* rather than the delay makes the
success probability equal to the action itself, which is what makes the Bellman
operator this compact. The trade-off is visible directly in the equation:
raising r makes the review more likely to succeed but shrinks the stability
gain, because the gain term is driven by (1 - r).

Why this matters for the rest of the project
--------------------------------------------
Solving this gives two things at once, and they are the two things the January
version was missing.

1. **A reference optimum.** Value iteration converges to the true optimal
   expected cost for the unconstrained problem. No approximation, no baseline
   that happens to be weak. Claims about the constrained search can be measured
   against it rather than asserted.

2. **A provably admissible heuristic.** For the constrained problem -- reviews
   may only happen in free calendar slots, at most two per day -- take

       h(node) = sum over topics of V*(D_i, S_i)

   This is a lower bound on the true cost-to-go. Proof: the constrained problem
   offers a subset of the unconstrained action set at every state, so no
   constrained policy can do better than V* on any single topic; topics cannot
   help each other, since a block spent on one is not spent on another; and the
   costs are additive. Therefore h <= h*. See docs/METHOD.md.

   Both g and h are now counted in study blocks. The January heuristic added
   hours to stability-days, which is why its admissibility claim could not even
   be stated, let alone checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .memory import (
    D_MAX,
    D_MIN,
    DEFAULT_WEIGHTS,
    Grade,
    MemoryState,
    S_MIN,
    Weights,
    initial_difficulty,
    interval_for_retention,
    retrievability,
)

# --------------------------------------------------------------------------- #
# Vectorised transitions
# --------------------------------------------------------------------------- #
# These mirror the scalar functions in memory.py. Duplicated logic is a
# liability, so `test_ssp.py::test_vectorised_transitions_match_the_scalar_model`
# checks the two implementations agree to floating-point tolerance across the
# whole grid. If they ever drift apart, the suite fails.


def _vec_next_difficulty(d: np.ndarray, grade: Grade, w: Weights) -> np.ndarray:
    target = initial_difficulty(Grade.GOOD, w)
    nd = d - w.d_delta * (int(grade) - 3)
    nd = w.d_reversion * target + (1.0 - w.d_reversion) * nd
    return np.clip(nd, D_MIN, D_MAX)


def _vec_stability_on_recall(s: np.ndarray, d: np.ndarray, r: np.ndarray, w: Weights) -> np.ndarray:
    gain = (
        np.exp(w.sinc_scale)
        * (11.0 - d)
        * s ** (-w.sinc_s_decay)
        * (np.exp((1.0 - r) * w.sinc_r_gain) - 1.0)
    )
    return np.maximum(s * (1.0 + gain), S_MIN)


def _vec_stability_on_lapse(s: np.ndarray, d: np.ndarray, r: np.ndarray, w: Weights) -> np.ndarray:
    post = (
        w.lapse_scale
        * d ** (-w.lapse_d_decay)
        * ((s + 1.0) ** w.lapse_s_gain - 1.0)
        * np.exp((1.0 - r) * w.lapse_r_gain)
    )
    return np.maximum(post, S_MIN)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SSPConfig:
    """Discretisation and cost model.

    `cost_lapse` is separated from `cost_recall` deliberately. If both are 1 the
    model says a wasted block and a productive block are equally expensive, and
    the optimal policy drifts toward low target retention -- long intervals, big
    stability jumps, frequent failures. Charging more for a lapse (relearning
    time, or simply the frustration) pushes the optimum back up. Exposing the
    ratio makes that a visible modelling choice instead of a hidden one.
    """

    # The January code hardcoded `target: float = 14.0`. At that target the
    # problem is nearly trivial -- V* is about 1.2 reviews from a fresh state,
    # so the policy is dominated by the goal boundary and no interesting spacing
    # structure appears. 365 days is the "remember it past the exam" regime where
    # the policy is non-degenerate. For a deadline-driven target, use
    # `SSPConfig.for_deadline`.
    target_stability: float = 365.0
    min_stability: float = 0.05
    n_stability: int = 128
    n_difficulty: int = 19
    min_retention: float = 0.55
    max_retention: float = 0.98
    n_retentions: int = 40
    cost_recall: float = 1.0
    cost_lapse: float = 1.0
    # "bilinear" gives the accurate value function and is what we report.
    # "optimistic" replaces interpolation with the minimum over the enclosing
    # grid cell. Because V* is monotone (decreasing in S, increasing in D) the
    # cell minimum is attained at a corner and therefore lower-bounds V* at every
    # interior point, so the Bellman operator is dominated by the true one and its
    # fixed point is a guaranteed lower bound. That is what an admissible A*
    # heuristic requires: bilinear interpolation of a convex V overestimates, and
    # an overestimating h silently destroys A*'s optimality guarantee.
    interpolation: str = "bilinear"
    tolerance: float = 1e-9
    max_sweeps: int = 20_000
    # Retentions offered in addition to the evenly spaced grid. `for_heuristic`
    # puts the analysis grid here, so that its action set contains the analysis
    # action set and its solve is below the analysis solve cell by cell
    # (AUDIT.md item 22).
    extra_retentions: tuple[float, ...] = ()

    @classmethod
    def for_deadline(cls, days_to_exam: float, retention: float = 0.9, **kwargs) -> "SSPConfig":
        """Derive the stability target from a date and a tolerance for forgetting.

        A student has an exam on the 14th and would like a 90% chance of recall,
        not an intrinsic desire for 14 days of stability. This converts the
        former into the latter, so the goal condition of the SSP is traceable to
        something the user actually said.
        """
        from .memory import stability_for_interval

        return cls(target_stability=stability_for_interval(days_to_exam, retention), **kwargs)

    @classmethod
    def for_heuristic(cls, target_stability: float, **kwargs) -> "SSPConfig":
        """Configuration valid for use as an A*/AO* heuristic.

        Two things differ from the analysis configuration, and both are required
        rather than cosmetic.

        `interpolation="optimistic"` makes the value function a guaranteed lower
        bound instead of an accurate estimate (see the field docs).

        The action range is widened to essentially all of (0, 1). The
        admissibility proof rests on the constrained delays being a *subset* of
        the unconstrained action set, and a real calendar induces retrievabilities
        from about 0.30 (a three-week gap on a weak memory) to 0.9998 (a review
        twelve hours later). The analysis grid spans [0.55, 0.98], so those delays
        sit outside it and the subset argument fails. Widening the range costs
        nothing -- extreme retentions are simply never optimal -- and repairs the
        proof.

        A residual caveat survives: the action grid is finite, so V* is the
        minimum over a finite subset rather than the continuum, which is the wrong
        direction for a bound. The slack introduced by optimistic interpolation
        (about 30%) dwarfs the action-discretisation gap (under 0.1 reviews across
        a fourfold refinement), and `tests/test_plan.py` checks the resulting
        heuristic never exceeds the exact optimum on every instance where the
        exact optimum is computable. For a bound that needs no such argument, use
        `plan.best_case_reviews`.

        Since milestone M4 the heuristic grid also contains every retention of the
        analysis grid, so the minimum is over a superset of the analysis actions
        and the heuristic solve lies below the analysis solve in every cell, not
        only on average (AUDIT.md item 22; it was above by up to 6.2e-06).
        """
        analysis = cls(target_stability=target_stability)
        params = dict(
            target_stability=target_stability,
            interpolation="optimistic",
            min_retention=0.05,
            max_retention=0.999,
            n_retentions=80,
            extra_retentions=tuple(float(r) for r in analysis.retentions()),
        )
        params.update(kwargs)
        return cls(**params)

    def stability_grid(self) -> np.ndarray:
        return np.geomspace(self.min_stability, self.target_stability, self.n_stability)

    def difficulty_grid(self) -> np.ndarray:
        return np.linspace(D_MIN, D_MAX, self.n_difficulty)

    def retentions(self) -> np.ndarray:
        grid = np.linspace(self.min_retention, self.max_retention, self.n_retentions)
        if not self.extra_retentions:
            return grid
        return np.union1d(grid, np.asarray(self.extra_retentions, dtype=float))


# --------------------------------------------------------------------------- #
# Bilinear interpolation on (difficulty, log stability)
# --------------------------------------------------------------------------- #


def _interp_index(grid: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lower-cell indices and interpolation weights, clamped at both edges."""
    idx = np.clip(np.searchsorted(grid, values) - 1, 0, len(grid) - 2)
    lo = grid[idx]
    hi = grid[idx + 1]
    frac = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
    return idx, frac


@dataclass(frozen=True, slots=True)
class _Gather:
    """Precomputed bilinear-interpolation plan for one set of successor states.

    The transitions do not depend on the value function, so the indices and
    weights are computed once and each value-iteration sweep is a gather plus a
    weighted sum. `done` marks successors that have already reached the target,
    whose cost-to-go is zero by definition.
    """

    flat_00: np.ndarray
    flat_01: np.ndarray
    flat_10: np.ndarray
    flat_11: np.ndarray
    w_d: np.ndarray
    w_s: np.ndarray
    done: np.ndarray
    mode: str = "bilinear"

    def __call__(self, v_flat: np.ndarray) -> np.ndarray:
        if self.mode == "optimistic":
            out = np.minimum(
                np.minimum(v_flat[self.flat_00], v_flat[self.flat_01]),
                np.minimum(v_flat[self.flat_10], v_flat[self.flat_11]),
            )
            return np.where(self.done, 0.0, out)
        wd, ws = self.w_d, self.w_s
        out = (
            (1.0 - wd) * (1.0 - ws) * v_flat[self.flat_00]
            + (1.0 - wd) * ws * v_flat[self.flat_01]
            + wd * (1.0 - ws) * v_flat[self.flat_10]
            + wd * ws * v_flat[self.flat_11]
        )
        return np.where(self.done, 0.0, out)


def _build_gather(
    d_grid: np.ndarray,
    s_grid: np.ndarray,
    d_next: np.ndarray,
    s_next: np.ndarray,
    target: float,
    mode: str = "bilinear",
) -> _Gather:
    n_s = len(s_grid)
    log_grid = np.log(s_grid)
    i0, w_d = _interp_index(d_grid, d_next)
    j0, w_s = _interp_index(log_grid, np.log(np.clip(s_next, s_grid[0], s_grid[-1])))
    return _Gather(
        flat_00=i0 * n_s + j0,
        flat_01=i0 * n_s + j0 + 1,
        flat_10=(i0 + 1) * n_s + j0,
        flat_11=(i0 + 1) * n_s + j0 + 1,
        w_d=w_d,
        w_s=w_s,
        done=s_next >= target,
        mode=mode,
    )


# --------------------------------------------------------------------------- #
# Solution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MemorizationPolicy:
    """Converged value function and optimal policy for one topic."""

    config: SSPConfig
    weights: Weights
    difficulty_grid: np.ndarray
    stability_grid: np.ndarray
    expected_cost: np.ndarray  # V*(D, S), in reviews
    target_retention: np.ndarray  # pi*(D, S)
    sweeps: int
    residual: float

    # -- queries ------------------------------------------------------------- #

    def _interp(self, table: np.ndarray, difficulty: float, stability: float) -> float:
        if stability >= self.config.target_stability:
            return 0.0 if table is self.expected_cost else float(table[-1, -1])
        i, wd = _interp_index(self.difficulty_grid, np.array([np.clip(difficulty, D_MIN, D_MAX)]))
        log_grid = np.log(self.stability_grid)
        j, ws = _interp_index(
            log_grid,
            np.log(np.array([np.clip(stability, self.stability_grid[0], self.stability_grid[-1])])),
        )
        i, j, wd, ws = int(i[0]), int(j[0]), float(wd[0]), float(ws[0])
        if self.config.interpolation == "optimistic" and table is self.expected_cost:
            # Same argument as in the solver: take the cell minimum so the query
            # is a lower bound too, not just the fixed point.
            return float(min(table[i, j], table[i, j + 1], table[i + 1, j], table[i + 1, j + 1]))
        return float(
            (1 - wd) * (1 - ws) * table[i, j]
            + (1 - wd) * ws * table[i, j + 1]
            + wd * (1 - ws) * table[i + 1, j]
            + wd * ws * table[i + 1, j + 1]
        )

    def expected_reviews(self, difficulty: float, stability: float) -> float:
        """V*(D, S): expected reviews still needed to reach the target."""
        return self._interp(self.expected_cost, difficulty, stability)

    def optimal_retention(self, difficulty: float, stability: float) -> float:
        """The retrievability the optimal policy reviews at from this state.

        NaN once the target is reached: there is no action to take.
        """
        if stability >= self.config.target_stability:
            return float("nan")
        return self._interp(self.target_retention, difficulty, stability)

    def action_values(self, difficulty: float, stability: float) -> np.ndarray:
        """Q(state, r) for every candidate retention, for flatness diagnostics.

        If this curve is flat, the argmin is not identified and reporting a
        single "optimal retention" to three decimals would be false precision.
        """
        from .memory import MemoryState, review

        out = []
        for r in self.config.retentions():
            delay = interval_for_retention(stability, float(r))
            st = MemoryState(stability, difficulty)
            rec = review(st, delay, Grade.GOOD, self.weights)
            lap = review(st, delay, Grade.AGAIN, self.weights)
            cost = r * self.config.cost_recall + (1.0 - r) * self.config.cost_lapse
            out.append(
                cost
                + r * self.expected_reviews(rec.difficulty, rec.stability)
                + (1.0 - r) * self.expected_reviews(lap.difficulty, lap.stability)
            )
        return np.asarray(out)

    def optimal_delay(self, difficulty: float, stability: float) -> float:
        """Days to wait before the next review, under the optimal policy."""
        return interval_for_retention(stability, self.optimal_retention(difficulty, stability))

    @property
    def is_lower_bound(self) -> bool:
        """Whether this value function may be used as an admissible heuristic.

        Exposed as a property rather than left to callers inspecting
        `config.interpolation`, so that a budgeted or otherwise-derived
        continuation can satisfy the same contract without pretending to have an
        `SSPConfig`.
        """
        return self.config.interpolation == "optimistic"

    # -- the A* heuristic ---------------------------------------------------- #

    def reviews_lower_bound(self, states: Iterable[MemoryState]) -> float:
        """h(node) = sum of per-topic V*. Admissible; see module docstring.

        Refuses to run on a bilinearly interpolated policy. Bilinear V* turns out
        to be statistically indistinguishable from the true cost -- which makes it
        the right thing to *report*, and the wrong thing to use as h, because an
        unbiased estimate is above the truth roughly half the time and an
        overestimating heuristic silently voids A*'s optimality guarantee. The
        distinction is easy to lose track of six modules later, so it is enforced
        here rather than left to a comment.
        """
        if self.config.interpolation != "optimistic":
            raise ValueError(
                "reviews_lower_bound requires a policy solved with "
                "interpolation='optimistic'; bilinear V* is unbiased, not a bound"
            )
        return sum(self.expected_reviews(st.difficulty, st.stability) for st in states)


def solve(config: SSPConfig = SSPConfig(), weights: Weights = DEFAULT_WEIGHTS) -> MemorizationPolicy:
    """Value iteration to convergence.

    Raises rather than returning an unconverged value function: silently
    reporting numbers from a solver that never converged is the exact failure
    mode this rebuild exists to avoid.
    """
    weights.validate()
    d_grid = config.difficulty_grid()
    s_grid = config.stability_grid()
    r_grid = config.retentions()
    n_d, n_s, n_a = len(d_grid), len(s_grid), len(r_grid)
    target = config.target_stability

    r = r_grid[:, None, None]
    d = d_grid[None, :, None]
    s = s_grid[None, None, :]
    shape = (n_a, n_d, n_s)

    s_rec = np.broadcast_to(_vec_stability_on_recall(s, d, r, weights), shape)
    s_lap = np.broadcast_to(_vec_stability_on_lapse(s, d, r, weights), shape)
    d_rec = np.broadcast_to(_vec_next_difficulty(d, Grade.GOOD, weights), shape)
    d_lap = np.broadcast_to(_vec_next_difficulty(d, Grade.AGAIN, weights), shape)

    mode = config.interpolation
    if mode not in ("bilinear", "optimistic"):
        raise ValueError(f"unknown interpolation mode {mode!r}")
    gather_rec = _build_gather(d_grid, s_grid, d_rec, s_rec, target, mode)
    gather_lap = _build_gather(d_grid, s_grid, d_lap, s_lap, target, mode)

    immediate = r * config.cost_recall + (1.0 - r) * config.cost_lapse  # (n_a, 1, 1)
    at_goal = s_grid >= target

    value = np.zeros((n_d, n_s))
    residual = np.inf
    for sweep in range(1, config.max_sweeps + 1):
        flat = value.ravel()
        q = immediate + r * gather_rec(flat) + (1.0 - r) * gather_lap(flat)
        nxt = q.min(axis=0)
        nxt[:, at_goal] = 0.0
        residual = float(np.max(np.abs(nxt - value)))
        value = nxt
        if residual < config.tolerance:
            break
    else:
        raise RuntimeError(
            f"value iteration did not converge in {config.max_sweeps} sweeps "
            f"(residual {residual:.3g} > tolerance {config.tolerance:.3g})"
        )

    flat = value.ravel()
    q = immediate + r * gather_rec(flat) + (1.0 - r) * gather_lap(flat)
    policy = r_grid[np.argmin(q, axis=0)]
    # No action is defined at the goal, but leaving NaN there poisons bilinear
    # interpolation for any state in the adjacent cell. Carry the last real
    # action forward instead; `optimal_retention` returns NaN above the target.
    if at_goal.any():
        first_goal = int(np.argmax(at_goal))
        if first_goal > 0:
            policy[:, first_goal:] = policy[:, first_goal - 1][:, None]

    return MemorizationPolicy(
        config=config,
        weights=weights,
        difficulty_grid=d_grid,
        stability_grid=s_grid,
        expected_cost=value,
        target_retention=policy,
        sweeps=sweep,
        residual=residual,
    )


# --------------------------------------------------------------------------- #
# Monte-Carlo evaluation
# --------------------------------------------------------------------------- #


def simulate_reviews_to_target(
    policy: MemorizationPolicy,
    initial: MemoryState,
    rng: np.random.Generator,
    fixed_retention: float | None = None,
    max_reviews: int = 200,
) -> int:
    """Reviews actually consumed on one simulated run.

    `fixed_retention` swaps the optimal policy for a constant-retention policy,
    which is what every off-the-shelf spaced-repetition tool does (Anki's
    default desired retention is 0.9). Comparing the two is how we check that
    V* is what it claims to be rather than a number a solver printed.
    """
    from .memory import review

    state = initial
    target = policy.config.target_stability
    for n in range(1, max_reviews + 1):
        if state.stability >= target:
            return n - 1
        r = fixed_retention if fixed_retention is not None else policy.optimal_retention(
            state.difficulty, state.stability
        )
        delay = interval_for_retention(state.stability, float(r))
        recalled = rng.random() < retrievability(delay, state.stability)
        state = review(state, delay, Grade.GOOD if recalled else Grade.AGAIN, policy.weights)
    return max_reviews


def reviews_statistics(
    policy: MemorizationPolicy,
    initial: MemoryState,
    trials: int = 1500,
    seeds: Sequence[int] = (0, 1, 2, 3),
    fixed_retention: float | None = None,
) -> tuple[float, float]:
    """Mean and standard error of the realised review count.

    Returning the standard error is not decoration. A single 1500-trial run of
    this simulation put the mean 0.36 reviews below the true value, which was
    enough to fail an assertion and briefly convince me the solver was biased.
    Any comparison against a simulated baseline needs its own error bar, or it is
    just a coin flip with extra steps.
    """
    runs: list[int] = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        runs.extend(
            simulate_reviews_to_target(policy, initial, rng, fixed_retention=fixed_retention)
            for _ in range(trials)
        )
    arr = np.asarray(runs, dtype=float)
    return float(arr.mean()), float(arr.std(ddof=1) / np.sqrt(arr.size))


def mean_reviews_to_target(
    policy: MemorizationPolicy,
    initial: MemoryState,
    trials: int = 4000,
    seed: int = 0,
    fixed_retention: float | None = None,
) -> float:
    rng = np.random.default_rng(seed)
    runs = [
        simulate_reviews_to_target(policy, initial, rng, fixed_retention=fixed_retention)
        for _ in range(trials)
    ]
    return float(np.mean(runs))
