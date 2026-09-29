"""
Budgeted memorisation: what does it cost when you only get `b` more reviews?

Why this module exists
----------------------
Two problems that looked separate turn out to be the same one.

**The product problem.** A student with an exam in sixty days and two free
evenings a day has about 120 candidate study blocks. `plan.solve_ao_star`
reaches about ten. Planning the whole horizon exactly is not going to happen.

**The modelling problem.** The obvious fix is a receding horizon: plan the next
week exactly, act, observe, replan. But that needs a value for "and then the
rest happens later", and the honest version of that value is exactly what
`ssp.solve` computes -- at which point the window objective goes degenerate. It
already did once: charging leftover work at the unconstrained rate `V_opt` made
the model indifferent to *when* blocks are spent, because `V_opt` is the fixed
point of a dominated Bellman operator, so spending a block now and paying
`V_opt` later never beats just paying `V_opt`. The returned plan was empty.

`plan.py` patched around this with a lateness penalty. That works for a single
window whose end *is* the exam, and not at all for a window in the middle of a
sixty-day run, where nothing is due yet and the penalty never fires.

The actual mistake was pretending the future is unbounded. It is not: between
now and the exam there is a finite number of free evenings, and that is the only
reason studying tonight is worth anything. So the state gains a dimension:

    V(D, S, b) = expected cost to reach the target using at most b more reviews

    V(D, S, 0) = 0                if S >= target
               = failure_penalty  otherwise
    V(D, S, b) = min( V(D, S, b-1),
                      min over r of [ 1 + r * V(D_good, S_recall, b-1)
                                        + (1-r) * V(D_again, S_lapse, b-1) ] )

`b` counts remaining *opportunities*, not reviews: at each free block you either
use it or leave it. The first branch is that choice, and it is what makes V
non-increasing in b.

Three things follow.

1. **No degeneracy.** `V(D, S, b) < V(D, S, b-1)` strictly, wherever the budget
   binds: a block spent now genuinely buys something, because there are only so
   many left. This is the property whose absence produced the empty plan, and it
   is asserted in `tests/test_budget.py`.
2. **It is a backward recursion, not a fixed-point iteration.** Solve b = 0, then
   1, then 2. No convergence loop, no tolerance to tune.
3. **It is the terminal value a receding-horizon planner needs.** At the end of a
   one-week window, the cost of a subject's memory state is `V(D, S, b)` with
   `b` = free blocks left in the calendar between the window's end and that
   subject's exam. Different subjects get different `b` and different targets,
   which is what a student with three exams on three dates actually has.

The transition maths is imported from `ssp` rather than restated, so the two
solvers cannot drift apart, and `tests/test_budget.py` checks that this backward
recursion agrees with `ssp`'s value iteration in the limit of a large budget --
two independently structured solvers, one answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .memory import D_MAX, D_MIN, DEFAULT_WEIGHTS, Grade, MemoryState, Weights
from .ssp import (
    _build_gather,
    _interp_index,
    _vec_next_difficulty,
    _vec_stability_on_lapse,
    _vec_stability_on_recall,
)


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    """Discretisation, plus the two modelling choices this solver makes.

    `failure_penalty` is the cost of arriving at the exam not ready. It is the
    same lexicographic construction as `plan.Instance.lateness_penalty`: set it
    above any achievable block count and the policy will prefer readiness over
    thrift, while still minimising blocks among policies that get ready. It is a
    choice, not a measurement, and it belongs in the open.

    `max_budget` must be large enough that the budget stops binding, because
    queries above it are clamped. `BudgetPolicy.binding_budget` reports where it
    stops mattering and `solve` refuses a configuration that clamps too early.
    """

    target_stability: float
    # 64, not 30. The value function converges geometrically in the budget at a
    # ratio of about 0.71 per level, so the change at level 30 is still 3e-3 --
    # large enough that clamping a query there would overestimate, which is the
    # one direction that breaks an admissible heuristic. At 64 the change is
    # 3e-8, and 120 levels cost under a second, so there is no reason to be tight.
    max_budget: int = 64
    failure_penalty: float = 40.0
    cost_recall: float = 1.0
    cost_lapse: float = 1.0
    min_stability: float = 0.05
    n_stability: int = 96
    n_difficulty: int = 19
    min_retention: float = 0.05
    max_retention: float = 0.999
    n_retentions: int = 60
    interpolation: str = "bilinear"

    def stability_grid(self) -> np.ndarray:
        return np.geomspace(self.min_stability, self.target_stability, self.n_stability)

    def difficulty_grid(self) -> np.ndarray:
        return np.linspace(D_MIN, D_MAX, self.n_difficulty)

    def retentions(self) -> np.ndarray:
        return np.linspace(self.min_retention, self.max_retention, self.n_retentions)

    @classmethod
    def for_heuristic(cls, target_stability: float, **kwargs) -> BudgetConfig:
        """Lower-bounding variant, for use as an admissible heuristic.

        Same two requirements as `ssp.SSPConfig.for_heuristic`: optimistic
        interpolation so the value function is a bound rather than an estimate,
        and an action range wide enough that every delay a calendar can induce is
        inside it.
        """
        params: dict[str, Any] = dict(target_stability=target_stability, interpolation="optimistic")
        params.update(kwargs)
        return cls(**params)


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """`V(D, S, b)` and the optimal target retention at each budget level."""

    config: BudgetConfig
    weights: Weights
    difficulty_grid: np.ndarray
    stability_grid: np.ndarray
    expected_cost: np.ndarray  # (max_budget + 1, n_difficulty, n_stability)
    target_retention: np.ndarray  # same shape; NaN at the goal

    # -- queries ------------------------------------------------------------- #

    def _interp(self, table: np.ndarray, difficulty: float, stability: float) -> float:
        i_arr, wd_arr = _interp_index(self.difficulty_grid, np.array([np.clip(difficulty, D_MIN, D_MAX)]))
        log_grid = np.log(self.stability_grid)
        j_arr, ws_arr = _interp_index(
            log_grid,
            np.log(np.array([np.clip(stability, self.stability_grid[0], self.stability_grid[-1])])),
        )
        i, j, wd, ws = int(i_arr[0]), int(j_arr[0]), float(wd_arr[0]), float(ws_arr[0])
        corners = (table[i, j], table[i, j + 1], table[i + 1, j], table[i + 1, j + 1])
        if self.config.interpolation == "optimistic":
            return float(min(corners))
        return float(
            (1 - wd) * (1 - ws) * corners[0]
            + (1 - wd) * ws * corners[1]
            + wd * (1 - ws) * corners[2]
            + wd * ws * corners[3]
        )

    def expected_blocks(self, difficulty: float, stability: float, budget: int) -> float:
        """Expected cost from this state with at most `budget` reviews left.

        Budgets above `max_budget` are clamped, which is safe only because the
        value function has stopped changing there -- `solve` verifies that.
        """
        if stability >= self.config.target_stability:
            return 0.0
        level = int(np.clip(budget, 0, self.config.max_budget))
        return self._interp(self.expected_cost[level], difficulty, stability)

    def optimal_retention(self, difficulty: float, stability: float, budget: int) -> float:
        if stability >= self.config.target_stability or budget <= 0:
            return float("nan")
        level = int(np.clip(budget, 1, self.config.max_budget))
        return self._interp(self.target_retention[level], difficulty, stability)

    def cost_of(self, state: MemoryState, budget: int) -> float:
        return self.expected_blocks(state.difficulty, state.stability, budget)

    # -- diagnostics --------------------------------------------------------- #

    @property
    def binding_budget(self) -> int:
        """Largest budget at which one more review still changes the answer.

        Past this point the constraint is slack and the problem is the
        unconstrained one from `ssp`. Useful for sanity-checking `max_budget`,
        and for telling a student that more free evenings would not help.
        """
        deltas = np.abs(np.diff(self.expected_cost, axis=0)).max(axis=(1, 2))
        binding = np.nonzero(deltas > 1e-6)[0]
        return int(binding[-1] + 1) if binding.size else 0

    def marginal_value(self, difficulty: float, stability: float, budget: int) -> float:
        """What one extra study block is worth from this state. Non-negative."""
        return self.expected_blocks(difficulty, stability, budget - 1) - self.expected_blocks(
            difficulty, stability, budget
        )


def solve(
    config: BudgetConfig,
    weights: Weights = DEFAULT_WEIGHTS,
    require_slack: bool = True,
    slack_tolerance: float = 1e-6,
) -> BudgetPolicy:
    """Backward recursion over the budget dimension.

    One pass per budget level, so the cost is linear in `max_budget` and there is
    no convergence criterion. Raises if the budget is still binding at the top
    level, because queries above `max_budget` are clamped and a clamp against a
    still-decreasing value function silently overestimates -- which for an
    admissible heuristic is the one direction that breaks the guarantee.
    """
    weights.validate()
    if config.interpolation not in ("bilinear", "optimistic"):
        raise ValueError(f"unknown interpolation mode {config.interpolation!r}")

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
    gather_rec = _build_gather(d_grid, s_grid, d_rec, s_rec, target, mode)
    gather_lap = _build_gather(d_grid, s_grid, d_lap, s_lap, target, mode)
    immediate = r * config.cost_recall + (1.0 - r) * config.cost_lapse
    at_goal = s_grid >= target

    values = np.empty((config.max_budget + 1, n_d, n_s))
    policy = np.full((config.max_budget + 1, n_d, n_s), np.nan)

    values[0] = config.failure_penalty
    values[0][:, at_goal] = 0.0

    for level in range(1, config.max_budget + 1):
        flat = values[level - 1].ravel()
        q = immediate + r * gather_rec(flat) + (1.0 - r) * gather_lap(flat)
        chosen = q.min(axis=0)
        best = r_grid[np.argmin(q, axis=0)]
        # Declining the opportunity is always available, and leaving it out was a
        # bug: with only "review at retention r" in the action set the recursion
        # forces a review at every level, so V(1) = 1 + (1-r)*penalty exceeds
        # V(0) = penalty whenever r < 1/penalty. Spending a block on a review
        # that will almost certainly fail is worse than not spending it. Adding
        # the skip makes V non-increasing in b by construction, which is also
        # what "at most b reviews" was supposed to mean.
        skip = values[level - 1]
        declined = skip < chosen
        chosen = np.minimum(chosen, skip)
        best[declined] = np.nan
        chosen[:, at_goal] = 0.0
        best[:, at_goal] = np.nan
        values[level] = chosen
        policy[level] = best

    solution = BudgetPolicy(
        config=config,
        weights=weights,
        difficulty_grid=d_grid,
        stability_grid=s_grid,
        expected_cost=values,
        target_retention=policy,
    )

    if require_slack:
        tail = float(np.abs(values[-1] - values[-2]).max())
        if tail > slack_tolerance:
            raise RuntimeError(
                f"budget still binds at max_budget={config.max_budget} "
                f"(top-level change {tail:.3g}); raise max_budget before querying above it"
            )
    return solution
