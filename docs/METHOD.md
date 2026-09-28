# Method: what the scheduler optimises, and what we are entitled to claim

This replaces §1.2, §3.2 and §5.2 of the January 2026 report. Those sections
describe a retention-deficit heuristic whose admissibility could not be stated,
let alone verified. See `AUDIT.md` for why.

## 1. Memorising one topic is a stochastic shortest path problem

Following Ye et al. (KDD 2022) [ref:ye2022], the state of one topic is the FSRS pair
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
  desired retention; Anki's FSRS default is 0.90 [ref:anki-manual]. From a fresh item the optimal
  policy needs 6.02 ± 0.09 blocks against 7.41 ± 0.10 at a fixed 0.90, about 19%
  fewer, and 13% fewer than the best fixed retention (`python demo.py`, section 2;
  see the sawtooth below).
- **Lands below Anki's default, as it should.** Mean `π*` comes out at 0.82–0.85
  for difficulties between 2 and 8.5, below the 0.90 default of Anki's FSRS
  [ref:anki-manual], which the manual presents as a balance between retention and
  workload rather than a workload minimum. (This bullet used to say the result
  lay inside a "0.75–0.90 band the FSRS community reports"; no source for that
  band could be found and the claim is withdrawn, AUDIT.md item 28.)
- **Sensitive to the cost model in the right direction.** Pricing a lapse at 4×
  a productive block moves the optimum from 0.75 to 0.88.

**Fixed retention is a sawtooth, and 0.90 sits in a good spot on it.** The
comparison above has a trap. The cost of a fixed retention is not a smooth curve:
it needs 8.15 ± 0.11 blocks at 0.85, more than at both 0.80 (7.55 ± 0.12) and 0.90
(7.41 ± 0.10). This was an open question and it is a real property of the model,
not a bug. A schedule at fixed retention `R` reaches the target, if every review
succeeds, after a whole number of reviews, 3 up to `R = 0.84` and 4 from 0.85 to
0.90 (`ssp.successes_to_target`). Each time that number steps up, the cost jumps by
about one block (7.11 to 8.15 between 0.84 and 0.85, 7.41 to 8.55 between 0.90 and
0.91). Within a tooth the cost mostly falls as `R` rises, because lapses become
rarer while the review count stays put. So "19% fewer blocks than fixed 0.90" is
true, and 0.90 happens to be the cheap end of its tooth; against the best fixed
retention on a 0.01 grid, 0.83 at 6.94 ± 0.11, chosen in hindsight, the optimal
policy needs 13% fewer. Both figures come from `python demo.py`, section 2; the
steps are pinned by `test_fixed_retention_cost_jumps_where_one_more_review_is_needed`.
The optimal policy does not have the problem, because it chooses a retention per
state and can place the last review so that it just clears the target.

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

The price is informedness: the guaranteed bound sits 2.47 reviews (28%) below
the accurate value function on average across the grid, which weakens pruning. The
remedy is grid refinement, not quietly using the tighter inadmissible estimate.
`reviews_lower_bound` raises rather than accept a bilinear policy, so the
distinction cannot be lost downstream. The heuristic configuration's action grid
contains the analysis grid, so the bound lies below the analysis solve in every
cell, which a test checks (until milestone M4 it could sit 6e-06 above it in a
cell, AUDIT.md item 22). It is still a minimum over a finite action set; the clock
solver of section 6 removes that caveat by bounding delays cell by cell.

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

## 6. Receding-horizon replanning with a clock

The one-shot search reaches about ten blocks, and a real horizon offers forty to a
hundred and twenty. `rolling.py` plans a short window exactly with AO*, executes
one block, observes whether the recall succeeded, and replans. An observed outcome
collapses a whole contingency branch, so this is cheaper than building the tree up
front, not a weaker substitute for it. What is **not** claimed is global
optimality: greedy-over-windows planning is a heuristic scheme.

**The continuation has a clock.** A window needs a value for the work left after
it, and that value must charge for waiting, because the resource waiting consumes
is calendar time before the exam. `clock.py` solves, per subject, the expected cost
of reaching the subject's target before its own exam. Right after a review the
state is `(D, S, t)`, `t` the days left; the action is the delay `a` to the next
review, which fixes the recall probability `r = R(a, S)`:

```
V(D, S, t) = 0                                              if S >= S_target
           = min( P,  min over 0 < a <= t of
                  1 + r V(D_good, S_recall(a), t - a) + (1 - r) V(D_again, S_lapse(a), t - a) )
```

`P` is the price of giving up, which equals the price of arriving unready,
`V(D, S, 0) = P` below the target; it is 40 blocks, a stated choice. Every review
moves forward in time, so this is one backward sweep with no convergence loop.
Between reviews the state also needs the elapsed time `e`, because the next review
cannot be in the past. It needs no new table dimension: while a topic waits,
`e + t` is constant, so the waiting value `W(D, S, e, t)` is the same minimum
restricted to `a >= e`, computed at query time.

**The target is a stability, per subject.** `S_target = stability_for_interval(T,
rho)`, with `T` the days from the start of the plan to that subject's exam: recall
`rho` for as long again as the preparation lasted. The natural alternative, recall
at least `rho` at the exam, is met by a single review the night before and was
rejected for that reason (PROCESS.md, Phase 6).

**Estimate in the objective, bound in the heuristic.** The window's terminal value
is `Σᵢ W_accurate`, an estimate of the real cost-to-go. The AO* heuristic at a node
whose block starts at `τ` is `h = Σᵢ W_optimistic(Dᵢ, Sᵢ, τ − lastᵢ, examᵢ − τ)`.

**Claim.** `h` is admissible for the window objective.

**Argument.** (i) `W_optimistic` lower-bounds the true cost over continuous review
times: delays are bounded cell by cell (successor stability at the cell's right
end, time left at its left end, the better endpoint of the recall probability, on
which the expression is linear), with cell-minimum interpolation in `(D, log S)` and
time left rounded up; each step needs only that the true value is monotone in `D`,
`S` and `t`, which the solved tables show. The first delay cell refers to its own
time level and is solved by iterating from zero, which is a lower bound at every
iterate. (ii) The optimistic table lies below the accurate one in every cell, and
the optimistic query below the accurate query everywhere, so bounding the true
continuation also bounds the accurate one. (iii) Topics do not help one another and
costs add. Checked, not assumed: against exhaustive backward induction at every
reachable state of a five-block window at the start and in the middle of the plan,
and cell by cell and at 300 random off-grid queries.

**Calibration** (`python benchmarks/clock_calibration.py`). Simulating the policy
the accurate table induces, with free choice of review times, gives 6.85 ± 0.28 blocks from `S = 2, D = 7` against a predicted
6.14, and 11.99 ± 0.28 from `S = 0.5, D = 8` against 11.88. The estimate is
somewhat optimistic, by 0.1 to 0.7 blocks, and the bound (2.70 and 4.28) is far
below. The bound is loose, which costs search effort, not decision quality, since
decisions are made on the estimate.

**What it achieves** (`python benchmarks/replanning.py`, 100 seeds). The first
review of a weak topic moves from day 16 to 19 of 21 to day 2.3, at recall 0.89, and
both subjects reach their targets in 90% ± 3% of runs at window 4 (86% and 83% at
windows 1 and 2), against 35 to 38% before. Against the schedulers students use:
reviewing every day reaches 93% ± 3% at three times the blocks; a greedy scheduler
at the fixed recall 0.90 of Anki-style tools never reaches the target, because its
next review falls after the exam, but predicts almost the same recall at the exam
(0.920 against 0.932) with 1.2 fewer blocks. The planner's measurable advantage is
durability past the exam, not exam-day recall, and this document does not claim
otherwise.

**Unreachable exams.** Before planning, `best_case_stability` bounds the stability a
subject could reach on the actual free blocks if every review succeeded, holding
difficulty at `min(D, D0(Good))`. If that is below the target the subject is
reported and gets no blocks. The dynamic programme behind it relies on post-recall
stability increasing with pre-review stability at a fixed gap, which is tested.
