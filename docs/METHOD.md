# Method: what the scheduler optimises, and what we are entitled to claim

This replaces §1.2, §3.2 and §5.2 of the January 2026 report. Those sections
describe a retention-deficit heuristic whose admissibility could not be stated,
let alone verified. See `AUDIT.md` for why.

## 1. Memorising one topic is a stochastic shortest path problem

Following Ye et al. (KDD 2022), the state of one topic is the FSRS pair
`(D, S)` — difficulty and stability. An action is the retrievability `r` at which
to schedule the next review, which fixes the delay
`t = interval_for_retention(S, r)`. Because `r` *is* the recall probability, the
transition is:

| outcome | probability | successor |
|---|---|---|
| recall | `r` | `(D_good, S_recall(S, D, r))` |
| lapse | `1 − r` | `(D_again, S_lapse(S, D, r))` |

Each review consumes one study block, so the cost is 1. The goal is absorbing:
`S ≥ S_target`. Hence

```
V*(D, S) = min_r [ 1 + r·V*(D_good, S_recall) + (1 − r)·V*(D_again, S_lapse) ]
```

The trade-off is visible in the equation. Raising `r` makes success likely but
shrinks the stability gain, which is driven by `(1 − r)`. Lowering `r` buys a
large jump at the risk of wasting the block.

**The target comes from the deadline, not from a constant.** The January code
hardcoded `target = 14.0`. At that target the problem is nearly trivial — about
1.2 reviews from a fresh item — so the policy is dominated by the goal boundary
and no spacing structure appears. `SSPConfig.for_deadline(days, retention)`
derives the target from what the student actually said: an exam date and a
tolerance for forgetting.

## 2. Solving it gives the reference optimum

Value iteration on a 19 × 128 grid over `(D, log S)` with 40 candidate retentions
converges in 240 sweeps to a residual below 1e-9, in about a second. This is
the exact optimum for the *unconstrained* problem, so claims about the
constrained search can be measured rather than asserted.

Validation, all in `tests/test_ssp.py`:

- **Calibrated.** Simulating the policy reproduces `V*` at every state tested,
  within one standard error from a fresh item (6.06 predicted, 6.13 ± 0.09
  simulated over 6000 runs).
- **Beats constant-retention policies.** Every real tool reviews at a fixed
  desired retention; Anki's FSRS default is 0.90. From a fresh item the optimal
  policy needs 6.02 ± 0.09 blocks against 7.41 ± 0.10 at a fixed 0.90, about 19%
  fewer (`python demo.py`, section 2).
- **Agrees with the literature it was never told about.** Mean `π*` comes out at
  0.82–0.85 for difficulties between 2 and 8.5, inside the 0.75–0.90 band the FSRS
  community reports for workload-minimising desired retention.
- **Sensitive to the cost model in the right direction.** Pricing a lapse at 4×
  a productive block moves the optimum from 0.75 to 0.88.

Reported honestly: `V*` is stable to under 0.1 reviews under grid refinement, but
the **argmin is much less well determined than the value**: the range of
retentions that lose less than 0.01 review against the best is 0.03 to 0.06 wide,
so the optimal retention is a band and is never quoted to three decimals.

## 3. The heuristic for the constrained search, and its proof

The constrained problem adds the calendar: reviews may only happen in free slots,
at most two blocks a day, sleep and classes blocked out. For a node holding
memory states `(D_i, S_i)` take

```
h(node) = Σ_i V*(D_i, S_i)
```

**Claim.** `h ≤ h*`, the true expected remaining blocks.

**Proof.** Three steps. (i) At every state the constrained problem offers a
subset of the unconstrained action set, since the available delays are a subset
of the positive reals; so no constrained policy beats `V*` on any single topic.
(ii) Topics do not help one another — a block spent on one is not spent on
another — so the per-topic bounds cannot be undercut jointly. (iii) Costs are
additive in blocks. Therefore the sum of per-topic optima lower-bounds the
constrained optimum. ∎

Two things this fixes relative to January. Both `g` and `h` are now counted in
**study blocks**; the old heuristic added hours to stability-days, so
admissibility was not a well-formed statement. And the bound is *proved*, so
plain A* (`w = 1`) returns a genuinely optimal constrained schedule — the
`w = 10.0` weighting that voided the old optimality claim is no longer needed.

## 4. Accurate is not the same as admissible

Bilinear interpolation of `V` is *unbiased*, which means it lies above the true
value about half the time. An overestimating `h` silently destroys A*'s
optimality guarantee, so it cannot be the heuristic — even though it is the right
thing to report.

The solver therefore has a second mode. `interpolation="optimistic"` replaces
interpolation with the minimum over the enclosing grid cell. Since `V*` is
monotone — decreasing in `S`, increasing in `D`, both verified as tests — the cell
minimum is attained at a corner and lower-bounds `V*` everywhere inside. The
optimistic Bellman operator is therefore dominated by the true one and its fixed
point is a guaranteed lower bound.

The price is informedness: the guaranteed bound sits 2.45 reviews (28%) below
the accurate value function on average across the grid, which weakens pruning. The
remedy is grid refinement, not quietly using the tighter inadmissible estimate.
`reviews_lower_bound` raises rather than accept a bilinear policy, so the
distinction cannot be lost downstream. One residual caveat is measured rather than
hidden: the heuristic configuration has its own finite action grid, and on it the
bound can sit above the analysis solve by up to 6e-06 reviews in a cell (AUDIT.md
item 22).

## 5. What this project may and may not claim

**May claim.** An exact reference optimum for unconstrained memorisation, an
admissible heuristic with a written proof and a numerical check over the whole
state grid, a policy that measurably beats the fixed-0.90 default every tool
ships with, and a scheduler whose optimality claim is either true or reported as
a bound.

**May not claim.** A large retention improvement from scheduling as such. The
objective is very flat in the middle of a plan — any placement between roughly
day 8 and day 16 of a 21-day window scores within 0.1% of optimal. That flatness
*is* the useful result: hard calendar constraints turn out to cost almost
nothing, so respecting sleep, classes and deadlines is close to free rather than
a compromise. It is a smaller claim than "+32.2% effective retention" and, unlike
that one, it survives being checked.

## 6. Receding-horizon replanning (implemented, with one open defect)

The one-shot search reaches about ten blocks, and a real horizon offers forty to a
hundred and twenty. `rolling.py` plans a short window exactly with AO*, executes
one block, observes whether the recall succeeded, and replans. An observed outcome
collapses a whole contingency branch, so this is cheaper than building the tree up
front, not a weaker substitute for it.

The window needs a value for "and then the rest happens later". `budget.py` supplies
it as `V(D, S, b)`, the expected cost of reaching the target with at most `b` more
review opportunities, solved by backward recursion. Within a window the heuristic
`h = Σᵢ V(Dᵢ, Sᵢ, remaining blocks)` is admissible, checked against exhaustive
search at every reachable state of a five-block window
(`test_budgeted_heuristic_is_admissible_across_a_whole_window`). What is **not**
claimed is global optimality: greedy-over-windows planning is a heuristic scheme.

**Open defect.** `V(D, S, b)` has no clock. Skipping a block leaves `(D, S)`
unchanged, so waiting is free while blocks are plentiful and the planner defers:
the first review lands on day 16 to 19 of 21 instead of 3 to 4, and 35 to 38% of
runs end with both topics ready. Until this is fixed the timing of a plan is
indicative and the CLI says so. See AUDIT.md item 20 and `benchmarks/replanning.py`.
