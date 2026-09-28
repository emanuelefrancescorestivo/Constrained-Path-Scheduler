# How this project was actually built

A development history of the Constrained-Path Scheduler, from the November 2025
proposal through the January 2026 submission to the current rebuild.

It is written as a record of what went wrong, in order, because that is the part
that was worth writing down. Most of what follows is a mistake, and roughly half
of them were mine during the rebuild rather than the original team's. Where a
mistake could be turned into a test it has been; the ones still open are pinned as
strict xfails, so none of them can come back, or stay hidden, quietly.

`AUDIT.md` is the same material organised by defect rather than by date.
`docs/METHOD.md` is the formal statement of the final method.

---

## Phase 0 — the original idea (November 2025)

The premise was good and it has survived unchanged: spaced-repetition schedulers
like FSRS assume you have unlimited free time, and calendars know your
constraints but nothing about memory. Nobody joins the two. A student is forced
to pick between a schedule that is feasible and one that is good for retention.

The proposal committed to A\* search with an FSRS-derived heuristic, a Greedy
baseline, and an unconstrained FSRS baseline, evaluated on synthetic student
profiles. The success criteria were honest and modest: "Minimum (B grade): FSRS +
Greedy working, A\* designed, test results on 20 cases, honest feasibility
report."

## Phase 1 — the January 2026 submission

The report claimed a 60-day simulation over 50 trials in which A\* beat Greedy by
+32.2% on "Effective Retention", with 100% feasibility, and stated that the
returned schedule was "not merely valid, but optimal with respect to the
constraints".

Rereading the appendix before writing any new code turned up three defects that
are not presentational. They mean the reported numbers do not measure what the
report says they measure.

**1. The memory model could not see time.** The heuristic engine was:

```python
def predict_next_stability(current_S, difficulty):
    factor = 1 + (math.exp(difficulty / 10) * 0.2)
    return current_S * factor
```

No `elapsed_days` argument. A review tomorrow at 02:00 and a review in three
weeks produce identical outcomes, so no time slot is preferable to any other and
there is nothing for a scheduler to schedule. The spacing effect — the entire
reason spaced repetition works — is absent. This is not FSRS; FSRS *is* its
forgetting curve.

**2. A\* never returned a solution.** `run_astar` had no goal test, and
`best_final_state` was updated from every popped node *before* the horizon check.
The reported result was therefore the minimum-deficit state encountered anywhere
in the search tree, including partial paths two days into a sixty-day plan.
Greedy, by contrast, was evaluated after running the whole horizon. Table 1
compares "best moment A\* ever glimpsed" against "where Greedy finished". Any
algorithm wins that comparison.

**3. The optimality claim was not merely false but ill-formed.** Two independent
reasons. Weighted A\* with `w = 10` guarantees only a factor-of-ten bound. And
`g(n)` was in units of time while `h(n)` was in stability-days, so `g + w·h` adds
hours to days and admissibility cannot even be stated.

Plus seven smaller items a reader could check: a latent `TypeError` when heap
entries tie (the tuples contain un-orderable `Task` lists), a pruning signature
that collapsed all 48 slots of a day into one state, a falsy-default bug in
`Task.copy`, an undefined `Normalized Deficit` underneath the headline metric, a
"bitmask" that was an int64 NumPy array with an O(duration) reduction, two
bibliography entries sharing one title under different authors, and 50 trials
reported without a seed, a standard deviation, or a paired comparison.

**Decision taken:** rebuild, keep the report, and add an explicit post-mortem
section rather than quietly publishing a v2. The defects are more interesting
than the original results were.

---

## Phase 2 — the memory model

### The falsification test came first

Before writing a scheduler, write the test that the old model fails. The January
heuristic is preserved verbatim in `src/cps/legacy.py` and the spacing-effect
property test runs against both models, with the legacy one marked
`xfail(strict=True)` — so the build breaks if it ever starts passing. Measured at
S = 5, D = 5: FSRS gives S′ = 6.52 at a half-day delay and 18.79 at five days;
the January model gives 6.65 either way.

Doing it in this order meant the central defect was a failing test from hour one
rather than a paragraph in a write-up.

### Mistake 1 — mixing two FSRS versions

I implemented the FSRS-5 initial-difficulty formula,
`D0(g) = w4 − exp(w5·(g−1)) + 1`, and fed it the FSRS-4.5 parameter vector.
`D0(Good)` came out at −5.5, the clamp turned it into 1.0, every item looked
trivially easy, and stability exploded.

**Every property test passed.** Monotonicity held, the spacing effect held, the
lapse clamp held. What caught it was printing `D0` per grade and then pinning one
trajectory to an external reference: py-fsrs documents that consecutive Good
ratings produce intervals near 0, 4, 14, 44, 125, 328 days. The broken version
gave 3.7, 21.5, 102, 414. The fixed version gives 3.7, 14.1, 46.9, 139.6.

FSRS-4/4.5 use the *linear* form `D0(g) = w4 − w5·(g−3)`; the exponential form
arrived in FSRS-5 with refitted `w4`/`w5`.

**Lesson, now written into the test file:** property tests are necessary and not
sufficient. A model with named-parameter semantics also needs at least one
trajectory pinned to a published reference.

### Dead end 1 — two objectives that both degenerate

With a working forgetting curve, the obvious next question is when to review one
item. Both obvious answers collapse:

- `argmax E[S′]` runs out to ~97 days for a five-day item. Post-lapse stability
  is clamped at `min(·, S)`, so forgetting costs you the gain but not your
  existing stability. Waiting is almost free, so the model says wait.
- `argmax (E[S′] − S)/t` collapses to `t → 0`. For small `t`,
  `R ≈ 1 − FACTOR·t/(2S)`, so the gain is linear in `t` and the ratio tends to a
  positive constant. The model says review immediately.

Neither is a claim about memory; both are artefacts of optimising a single review
in isolation. The 0.9 target retention everyone uses comes from a *workload*
argument, not from maximising stability. Both are now recorded in
`test_both_naive_single_review_objectives_degenerate` so nobody re-derives one
and ships the result.

### Falsified hypothesis — cramming

I then wrote a test asserting that with a free calendar the best thing for
exam-day recall is to cram, and that a capacity cap is what forces spacing. It
failed: the unconstrained optimum was already spread out, at days 4, 15 and 20
before a day-21 exam.

The real structure turned out to be sharper. If a review is allowed *at* the exam
moment, expected retention is exactly 1 and the objective is degenerate —
cramming does work for the exam, which is why students do it. Introduce any
realistic gap, even one day, and spacing wins strictly: 0.9941 against 0.9806 for
massed practice.

And a third thing fell out that nobody was looking for: the objective is very
**flat** in the middle review's position. Anywhere from day 8 to day 16 scores
within 0.1% of optimal. That flatness is the honest value proposition of the
whole project — hard calendar constraints cost almost nothing, so respecting
sleep, classes and deadlines is close to free rather than a compromise. It is a
much smaller claim than +32.2%, and unlike that one it survives checking.

---

## Phase 3 — the objective

Choosing SSP-MMC (minimise the expected number of reviews to reach a target
stability, following Ye et al., KDD 2022) turned out to solve two problems at
once, which was not the reason for choosing it.

Value iteration over a discretised `(D, log S)` grid converges in 240 sweeps to a
residual below 1e-9 in about a second, and yields:

1. **A reference optimum** for the unconstrained problem — no ILP needed, and
   exact rather than a baseline that happens to be weak.
2. **A provably admissible heuristic** for the constrained problem,
   `h = Σᵢ V*(Dᵢ, Sᵢ)`, because the constrained problem offers a subset of the
   unconstrained action set, topics cannot help each other, and costs are
   additive. Both `g` and `h` are now counted in study blocks, which is what
   makes admissibility a well-formed statement at all.

Three independent validations, in `tests/test_ssp.py` and reproduced by
`demo.py`: simulating the policy reproduces `V*` (6.06 predicted, 6.13 ± 0.09
simulated); the optimal policy needs 6.02 ± 0.09 blocks against 7.41 ± 0.10 at the
fixed 0.90 retention every tool ships with (≈19% fewer); and mean `π*` lands at 0.82–0.85 for ordinary difficulties, inside the
0.75–0.90 band the FSRS community reports. Nothing told the solver that last
number.

### Mistake 2 — `target_stability = 14.0` was inherited without justification

Straight from the January `Task` dataclass. At that target the problem is nearly
trivial: 1.2 expected reviews from a fresh item, so the policy is dominated by
the goal boundary and no spacing structure exists to find. The zigzag in `π*` at
low stability was the symptom.

Fixed by deriving the target from what a student actually says — an exam date and
a tolerance for forgetting — via `stability_for_interval`, which is just the
inverse of the forgetting curve. `SSPConfig.for_deadline(45, 0.9)` returns
`target_stability = 45.0`.

### Mistake 3 — accurate is not admissible

I wrote a test asserting `V* ≤ cost actually achieved`. It failed: 6.058 against
5.758. It looked like solver bias. With 12,000 Monte-Carlo trials instead of
1,500 the achieved cost is 6.120 ± 0.063 — the test was underpowered, not the
solver wrong. `reviews_statistics` now always returns a standard error, and the
docstring says why.

But the false alarm exposed something real. Bilinear interpolation of the value
function is *unbiased*, so it sits above the truth about half the time. An
overestimating `h` silently destroys the optimality guarantee — the January
mistake in subtler form.

So the solver has two modes. `interpolation="optimistic"` replaces interpolation
with the minimum over the enclosing grid cell; since `V*` is monotone (decreasing
in `S`, increasing in `D`, both verified as tests) that minimum is attained at a
corner and lower-bounds `V*` everywhere inside, so the Bellman operator is
dominated by the true one and its fixed point is a guaranteed bound. The price is
informedness: about 30% below the accurate value function, which weakens pruning.
`reviews_lower_bound` raises rather than accept a bilinear policy.

### Mistake 4 — a hole in the admissibility proof

The subset argument needs the constrained delays to be a subset of the
unconstrained action set. A real calendar induces retrievabilities from about
0.30 (a three-week gap on a weak memory) to 0.9998 (a review twelve hours later).
The analysis action grid spanned `[0.55, 0.98]`. Those delays sit outside it and
the proof does not hold.

`SSPConfig.for_heuristic` widens the range to `[0.05, 0.999]`, which costs
nothing because extreme retentions are never optimal. A residual caveat survives
and is documented: the grid is finite, so `V*` is a minimum over a subset rather
than the continuum, which is the wrong direction for a bound. The 30% slack from
optimistic interpolation dwarfs the action-discretisation gap (under 0.1 reviews
across a fourfold refinement), and the heuristic is checked against the exact
optimum wherever the exact optimum is computable. For a bound needing no such
argument at all, `plan.best_case_reviews` exists.

---

## Phase 4 — the constrained search

### Why AO\*, and why not A\* or LAO\*

Not A\*, because a review can fail. Choosing a study block leads to two successor
states, so the space is an AND/OR graph and the solution is a *policy* — what to
study next given what happened — not a sequence. Running plain A\* over a path
graph here means silently solving an easier problem in which memory never fails.
That is what January did.

Not LAO\*, which I had offered. LAO\* exists to handle cycles in the state graph,
and here the block index strictly increases along every edge, so the graph is
acyclic. Its cycle machinery would be weight without benefit, and the acyclicity
also means value revision can simply be ordered by block index descending.

### Mistake 5 — the objective degenerated again

First run: `h(root) = 3.232`, the exact optimum `= 3.232`, and the returned plan
was **empty**.

Charging leftover work at the unconstrained rate `V_opt` and nothing else makes
the model indifferent to *when* blocks are spent. `V_opt` is the fixed point of a
dominated Bellman operator, so spending a block inside the horizon and then
paying `V_opt` never beats simply paying `V_opt` at the end. The exam date had
entered the model only through the target stability, never as a deadline.

Fixed by charging leftover work twice: `V_opt` plus a lateness penalty per topic
that is not ready. Keeping the `V_opt` term is what preserves admissibility (the
penalty is non-negative, so it can only push the true cost up); adding the
penalty is what makes the exam a deadline. The default penalty is derived from
the instance — the number of blocks in the horizon — so that readiness dominates
block-thrift lexicographically without a tuned constant.

That is now `test_without_a_lateness_penalty_the_optimal_plan_is_to_do_nothing`.

### Mistake 6 — the first night of the horizon was never blocked

`block_daily` applied its recurring window to days 0..n−1, so a window crossing
midnight was never inherited from the day *before* the start. 00:00–07:00 on day
0 stayed free, and the scheduler was offered a study block at midnight on the
first night — with zero elapsed time since the topic's last review, a block that
costs one unit and teaches nothing.

Found because a test asserting the plan was non-empty failed. Fixed by iterating
from day −1 and letting `block` clip the negative start.

### Mistake 7 — the closed-form heuristic was inadmissible

It summed the per-topic minimum review counts. But a topic that cannot be
finished within the horizon costs one lateness penalty, not two hundred blocks.
The whole-state-space admissibility test caught it immediately: `h = 400` against
an optimum of `12.38`.

Replaced with a knapsack bound: sort the per-topic minimum review counts, take
the largest prefix that fits the remaining block budget, and minimise
`Σ_{i∈F} kᵢ + penalty·(n − |F|)` over the feasible prefixes. Exact for the
argument it makes. This is also what made the combined heuristic worth having —
`max` of the SSP bound and the knapsack bound, since both include the blocks term
and cannot be added.

The bound itself was initially useless for a separate reason: it assumed
difficulty 1 and retrievability 0, claiming a single review multiplies stability
by 88. Two tightenings that stay provable: with grades restricted to Good and
Again, difficulty is attracted to `D0(Good) = 5.16` from both sides, so
`min(D, 5.16)` is a valid floor; and no gap can exceed the days left before the
last usable block, so the spacing term is evaluated at
`R = retrievability(horizon, S)`. `h(root)` went from 3.232 to 4.000 and
expansions from 1,231 to 305.

### Mistake 8 — two implementation inefficiencies that read as an explosion

AO\* hung on a fourteen-block instance rather than merely being slow. Two causes,
both mine. `find_tip` re-walked every already-solved subgraph on each iteration,
making the search quadratic in expansions — fixed by testing the `solved` flag
first. And `backup` called `successors` again for each action just to recover the
edge cost, re-running the FSRS transition maths thousands of times for a number
that never changes — fixed by caching the cost alongside the children.

### Mistake 9 — the default penalty is not comparable across instances

The demo printed a capacity comparison in which *more* free time appeared to
raise the cost: 11.80 with one block a day, 19.30 with two. The default penalty
is derived from the number of blocks, so the two instances were optimising
different objectives. Sound within one instance, meaningless between two. It is
now pinned explicitly in both the demo and the tests.

---

## Results

Five-block instance, two topics, exam in 21 days, exhaustively solvable:

| method | value | nodes |
|---|---|---|
| exhaustive backward induction | 13.23196 | 3,906 states |
| AO\*, `h = 0` | 13.23196 | 184 expansions |
| AO\*, `h =` closed-form knapsack | 13.23196 | 118 |
| AO\*, `h =` SSP bound | 13.23196 | 152 |
| AO\*, `h =` SSP + capacity | 13.23196 | **116** |

All four reproduce the exact optimum to five decimals, and every one is verified
admissible at all 3,906 reachable states, not just at the root.

Weighted AO\*, the technique January used with `w = 10` and no bound: at `w = 1.5`
the plan is still exactly optimal from 41 expansions instead of 116, and at
`w = 3` the realised gap is 1.038× against a guaranteed 3×. The bound is stated,
the gap is measured, and `evaluate_policy` computes the plan's real cost because
the inflated root value is no longer it.

Two findings a student could act on:

- **Spacing, not slot count, is the binding constraint.** Spend every block of a
  five-day, one-block-a-day calendar on a single topic and get every recall
  right: stability reaches 17.3 against a target of 21. Unreachable, so the
  optimal plan is to study nothing — correct behaviour, not a bug. The same
  blocks per day over seven days clear it at 21.25. Consecutive daily blocks mean
  every gap is one day, retrievability at review stays near 0.98, and the gain is
  near zero. What is scarce is calendar length.
- **The plan follows per-topic feasibility.** On a seven-day horizon the harder
  topic (S = 2, D = 7) tops out at 14.68 and is abandoned; the easier one clears
  at 21.25 and gets the blocks. Stretch to ten days and the optimum studies both,
  interleaved: the hard topic on days 2, 8 and 9, the easy one on day 6, four
  blocks out of ten available.

## The scaling wall, and what did not move it

The frontier is about ten blocks. Beyond that, exhaustive expansion.

Inflating the heuristic does not help — it prunes exploration *outside* the
solution graph, and the solution graph is itself exponential in the number of
coin flips along a path. State aggregation does help, because it attacks the size
of the solution instead: snapping stability to a 15% multiplicative grid and
difficulty to half-points leaves the cost within 0.1% of exact on the instance
where both are computable, and makes two more instances tractable: ten daily
blocks go from more than 30,000 expansions to 4,835, and seven days at two blocks a
day (fourteen blocks) to 9,775 expansions in under four seconds. Fourteen days at
one block a day stays out of reach even with aggregation.

It is not enough for a realistic sixty-day horizon. The next step is
receding-horizon replanning: solve the next week exactly, execute one block,
observe the outcome, replan. That is not a compromise — an observed outcome
collapses a contingency branch immediately, so replanning is strictly cheaper
than building the full tree up front, and it is what a working product would do.

## Phase 5 — from a solver to something you can point at a calendar

### Stage 1 of the original architecture had never been built

The November proposal described three stages, and the first was "parse user input".
The January appendix held `parse_user_calendar(file_path, days=60)` with the
`icalendar` import commented out and the body replaced by a comment. So the
scheduler had only ever run on a mock timetable, and no reported result had been
tested against a real calendar. `calendar_io.py` builds that stage: an `.ics` export
in, and a plan out as another `.ics` that Google, Apple, Outlook and Notion Calendar
can all import. `.ics` was chosen over a Google Calendar integration because it
needs no OAuth, no API keys and no client verification, and it runs on a stranger's
machine with their own timetable.

Three things a real parser has to get right and a stub cannot. Recurrence: a parser
that reads `DTSTART` and stops sees one lecture where there are thirteen weeks of
them, and books study time inside every one from week two on; a test builds that
naive grid and shows it leaves Monday 09:00 free. Rounding: an 18:10 to 18:50 gym
session must block both half-hour slots it touches, because under-blocking produces
a plan the student cannot follow. Time zones: a naive `UNTIL` against a
time-zone-aware `DTSTART` makes `dateutil` raise, and real exports contain it.

### Mistake 10 — a calendar records when you are busy, not when you are available

The first end-to-end run exported a study block at 00:00 to 01:30. Nobody puts an
event called "sleep" in Google Calendar, so on real data midnight is free.
Availability is calendar occupancy plus stated preferences, and the preferences
cannot be derived from the data. `load_availability` takes a study window, default
08:00 to 22:00.

### Mistake 11 — the fourth degenerate optimum

The rolling planner needs a value for "and then the rest happens later". The obvious
choice, the unconstrained `V_opt`, had already produced an empty plan once, so
`budget.py` solves the problem with a finite block budget, `V(D, S, b)`. That fixed
the indifference at the end of a window and produced the same failure one level up:
the recursion lets a block be skipped at no cost and without ageing the memory, so
while the budget is slack the planner defers.

Measured with `benchmarks/replanning.py`: the value of one more block is 3.4e-09
with forty-two left, the first review lands on day 16 to 19 instead of 3 to 4, and
35 to 38% of runs end with both topics ready. Two natural remedies made it worse
and are reproducible with the same script: a larger window (0% ready) and a longer
stability target (0% ready). The mechanism is the same as in the three earlier
cases: minimising expected review count is a workload objective, not a deadline
objective. Open, and pinned as a strict xfail.

### Mistake 12 — everything had only ever run on Linux

Handing the project to a Windows machine turned up four defects that no test had
any way to see. An `.ics` written in text mode ends up with CR CR LF. A byte-order
mark from Notepad makes the parser raise on the first line. The demo cannot print
pi through a redirected cp1252 pipe. And `zoneinfo` needs `tzdata`, which Windows
does not ship. All four are fixed and were reproduced by simulation, not verified on
Windows, which needs a CI job.

### Two smaller admissions

One session turn was lost during the rebuild. `budget.py` and its tests were on disk
but the turn that wrote them was not in the transcript, so that module was inspected
after the fact instead of reviewed as it was written. Its tests pass and it agrees
with value iteration on an aligned grid, but its design claims deserve a fresh
reader.

And a sentence in an earlier version of this document was wrong: aggregation "takes
fourteen blocks from unsolvable to five seconds". Re-running the demo showed that
holds for one of two fourteen-block instances and not the other. Checking every
quoted number against `demo.py` is now a rule, not a one-off.

## What this project claims, and what it does not

**Claims.** An exact reference optimum for unconstrained memorisation. An
admissible heuristic with a written proof and a numerical check at every
reachable state. A policy that measurably beats the fixed-0.90 default every tool
ships with. A search whose optimality is verified against exhaustive computation,
and whose bound is stated and measured whenever it is weakened.

**Does not claim.** A large retention improvement from scheduling as such. The
objective is flat in the middle of a plan, so there is no such improvement to
claim. What can be claimed is the flatness itself: constraints turn out to be
nearly free. And a scheduler whose timing can be trusted at realistic horizons:
the rolling planner scales, but it procrastinates (AUDIT.md item 20).

## Method notes worth keeping

1. **Write the test that the broken version fails, before writing the fix.** The
   central defect became a `strict=True` xfail in the first hour instead of a
   paragraph in a post-mortem.
2. **Property tests are necessary, not sufficient.** The FSRS version mix passed
   every one. Pin at least one trajectory to a published reference.
3. **Print the numbers.** Several of the mistakes were caught by printing a
   table, not by a red test.
4. **Simulated baselines need error bars.** An underpowered Monte-Carlo run
   briefly looked like solver bias and nearly triggered an unnecessary fix.
5. **Accurate and admissible are different properties.** Keep them in separate
   objects and make the wrong one raise at the call site.
6. **When a degenerate optimum appears, the objective is wrong, not the solver.**
   It happened four times: two single-review objectives, the missing
   deadline, and a value function indexed by blocks instead of by time. Each time the search was correct and the thing being asked for was
   not what was wanted.
7. **Derive constants from the instance, or state them as choices.** The lateness
   penalty is derived. The aggregation step is a stated choice with a measured
   cost. `target_stability = 14.0` was neither, which is why it went unexamined
   for a whole submission.
