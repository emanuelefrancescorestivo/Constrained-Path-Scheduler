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
trajectory to an external reference: consecutive Good ratings were said to produce
intervals near 0, 4, 14, 44, 125, 328 days. The broken version gave 3.7, 21.5, 102,
414. The fixed version gives 3.7, 14.1, 46.9, 139.6.

*Postscript from milestone M4.* The reference was the weakest link. Nobody had
checked where "4, 14, 44, 125" came from, and the test accepted anything from 0.6
to 1.7 times it. Run through py-fsrs 2.5.1, the FSRS-4.5 release, the same defaults
give 4, 15, 49, 146, 393, 973 whole days, and our model now matches that
implementation to 4e-14 on two trajectories, one with every grade and several
lapses. That exact comparison immediately found a second, milder version mix: a
post-lapse clamp, `min(·, S)`, which FSRS-4.5 does not have (AUDIT.md item 27). It
had changed the headline optimum in the fifth decimal and nothing else, which is
the kind of error only an exact reference finds.

FSRS-4/4.5 use the *linear* form `D0(g) = w4 − w5·(g−3)`; the exponential form
arrived in FSRS-5 with refitted `w4`/`w5`.

**Lesson, now written into the test file:** property tests are necessary and not
sufficient. A model with named-parameter semantics also needs at least one
trajectory pinned to a published reference.

### Dead end 1 — two objectives that both degenerate

With a working forgetting curve, the obvious next question is when to review one
item. Both obvious answers collapse:

- `argmax E[S′]` runs out to ~97 days for a five-day item. Post-lapse stability
  does not fall with the delay (it rises slightly), so the downside of waiting is
  capped at the lapse value while the upside keeps growing. Waiting is almost
  free, so the model says wait. (This paragraph first blamed a post-lapse clamp;
  the clamp does not bind here and was removed in M4, AUDIT.md item 27.)
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
| exhaustive backward induction | 13.22073 | 3,906 states |
| AO\*, `h = 0` | 13.22073 | 184 expansions |
| AO\*, `h =` closed-form knapsack | 13.22073 | 118 |
| AO\*, `h =` SSP bound | 13.22073 | 151 |
| AO\*, `h =` SSP + capacity | 13.22073 | **114** |

(Values after milestone M4, AUDIT.md items 22 and 27; before, the optimum was
13.23196 and the last two rows needed 152 and 116 expansions.) All four reproduce
the exact optimum to five decimals, and every one is verified
admissible at all 3,906 reachable states, not just at the root.

Weighted AO\*, the technique January used with `w = 10` and no bound: at `w = 1.5`
the plan is still exactly optimal from 41 expansions instead of 114, and at
`w = 3` the realised gap is 1.039× against a guaranteed 3×. The bound is stated,
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
  at 21.25 and gets the blocks. *Corrected in milestone M1:* this paragraph used to
  say that at ten days the optimum studies both topics, interleaved, at a cost of
  8.83. That plan was an artefact of state aggregation crossing the target; followed
  with exact dynamics it costs 28.60. At ten days the optimum still abandons the
  harder topic, studies the easier one on days 1 and 6, and costs 17.29 (AUDIT.md
  item 26, `benchmarks/aggregation_goal_crossing.py`).

## The scaling wall, and what did not move it

The frontier is about ten blocks. Beyond that, exhaustive expansion.

Inflating the heuristic does not help — it prunes exploration *outside* the
solution graph, and the solution graph is itself exponential in the number of
coin flips along a path. State aggregation does help, because it attacks the size
of the solution instead: snapping stability to a 15% multiplicative grid and
difficulty to half-points leaves the cost within 0.1% of exact on the instance
where both are computable, and makes two more instances tractable: ten daily
blocks go from more than 30,000 expansions to 4,592, and seven days at two blocks a
day (fourteen blocks) to 10,058 expansions in under four seconds (counts after the
M1 fix to aggregation, AUDIT.md item 26, and the M4 fixes to the lapse formula and
the heuristic's action grid, items 27 and 22; they were 4,835 and 9,775 before). Fourteen days at
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
objective. It was pinned as a strict xfail and fixed in milestone M1 (Phase 6).

### Mistake 12 — everything had only ever run on Linux

Handing the project to a Windows machine turned up four defects that no test had
any way to see. An `.ics` written in text mode ends up with CR CR LF. A byte-order
mark from Notepad makes the parser raise on the first line. The demo cannot print
pi through a redirected cp1252 pipe. And `zoneinfo` needs `tzdata`, which Windows
does not ship. All four were fixed and reproduced by simulation. The Windows CI job
added in milestone M0 then confirmed them, and found one more failure, in a test
that expected a Linux line ending (AUDIT.md item 25).

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

## Phase 6 — milestone M1: giving the value function a clock

The brief was specific: waiting must consume calendar time, each subject must carry
its own exam, and two designs were to be spiked before either was built. A: add a
days-left dimension to the budget recursion. B: a backward sweep over time, where
the state after a review is `(D, S, t)` with `t` the days left, and the action is
the delay to the next review. Everything else was a hypothesis.

### The spike, and what it decided

`benchmarks/spike_clock.py` plugs each candidate into the same one-block lookahead
on the benchmark calendar, so the only thing that changes between rows is the value
function (100 seeds, ready means both subjects at stability 21):

| continuation | blocks | first review | ready |
|---|---|---|---|
| budget, the defect | 3.6 | day 6 | 54% ± 5% |
| A: budget plus days left | 7.3 | day 1 | 88% ± 3% |
| B without elapsed time | 1.2 | day 6 | 0% |
| B, goal "recall at the exam" | 1.0 | day 20 | 0% |
| B, stability target, elapsed time | 6.2 | day 2 | 82% ± 4% |

(The budget row differs from the benchmark's 35 to 38% because the spike uses the
accurate budget solve and a one-block lookahead; the ordering is what matters.)

**A was rejected** for being internally inconsistent rather than for its number. On
a skip it lets time run but keeps `(D, S)` unaged, and it charges a review its
delay in days but a single block, so it reviews too early and too often (recall
0.93 to 0.98 at review, 7.3 blocks). It also needs to know how many days a skipped
block consumes, which a real calendar does not answer: blocks come in clumps.

**B without elapsed time is admissible and useless.** Leaving the time since the
last review out of the state gives a valid lower bound, and a value that does not
change while a topic waits. Waiting is free again, and the planner reviewed once
and never again. This is the subtlety the brief flagged, and the answer turned out
to need no new table dimension: while a topic waits, elapsed time plus time left is
constant, so the waiting value is a minimum over a suffix of the same actions,
computed at query time from the `(D, S, t)` table.

**The goal "recall at the exam" is the fifth degenerate optimum.** It is the goal a
student would state, and FSRS says one review twelve hours before the exam meets it
whether that review succeeds or lapses. The spike's planner obliged: one review, day
20. Cramming does work for the exam day, in this model and in life; a study planner
that recommends it has been asked the wrong question. The stability target of the
old code survives, per subject: reach the stability at which recall would still be
`rho` after as long again as the preparation lasted. It implies the exam-day goal
and cannot be met by one late review. It is a choice, and it is written down as one
(`test_the_exam_day_goal_is_met_by_cramming`).

### Mistake 13 — a lower bound is the wrong terminal value

Making the optimistic clock solve admissible over *continuous* delays, not only a
grid of them, was straightforward: bound each cell of delays by its best corner.
But it is loose, 2.7 blocks at the benchmark's start state against an accurate 6.1,
mostly from cell-minimum interpolation, which gives every review a slightly easier
difficulty and a slightly higher stability than the exact one; refining the grid
four- to fivefold in both dimensions only moved it to 3.7
(`python benchmarks/clock_calibration.py --refine`). The old budget planner used such
a bound for two jobs at once: the heuristic and the window's terminal value. The
second job is part of the objective. Pricing everything after the window with a
lower bound tells every window that the future is cheaper than it is, which is one
more reason to defer. The window now ends on the accurate estimate, and only the
heuristic is a bound; the heuristic is admissible for that objective because the
bound lies below the estimate in every cell, which a test checks, along with the
usual check against exhaustive search at every reachable state of a window.

### Mistake 14 — a hypothesis about interpolation that was wrong

With the Analysis exam moved to day 9 the planner reviewed at 08:00 and again at
09:30, at recall 0.999, taking stability from 8.85 to 8.97, still short of 9. The
first explanation was that bilinear interpolation towards the zero stored at the
target made "just below the target" look almost free. It was plausible, it came
with a clean fix, and it was wrong: the value just below the 9-day target is 1.06
blocks (`benchmarks/clock_calibration.py`), and the fix did not change it. The cause was state aggregation. The window
search snaps stability to a 15% grid, and 8.85 snapped to 9.36, past the target, so
the search believed one cheap review finished the topic. Aggregation is still only a
search device, and the executed trajectory was exact, but the decision had been
made on the wrong side of the goal. Goal membership is now decided by the exact
state (AUDIT.md item 26). The wrong fix was reverted before it was committed.

### What the fix buys, and what it does not

`benchmarks/replanning.py` now puts the planner beside the two schedulers students
actually use, on the same calendar and seeds (100 seeds):

| scheduler | ready | blocks | first review | recall at exam | stability at exam |
|---|---|---|---|---|---|
| planner, window 4 | 90% ± 3% | 4.95 | day 2.3 | 0.932 | 20.5 |
| greedy, fixed recall 0.90 | 0% | 3.74 | day 2.3 | 0.920 | 17.0 |
| every day (best k of 1..7) | 93% ± 3% | 15.81 | day 1.3 | 0.943 | 21.4 |

Three readings, none of them flattering by default. Reviewing every day reaches the
target slightly more often, at three times the work. The fixed-0.90 scheduler never
reaches the 21-day target, because its next due date falls after the exam; but its
recall at the exam is almost the planner's, with a block and a quarter less. What the
planner buys is durability past the exam, which is the objective it was given, and
spacing that is timed against the exam date. A student who only cares about the exam
morning does not need it; one who needs the material next term does. The runs in
which the planner fails are ones where lapses leave too little calendar to rebuild,
and there it stops spending blocks on the subject, which is the rational thing to do
and exactly what a failure penalty says.

The brief asked for 90% of 100 seeds. The planner reaches it at window 4 (90% ± 3%);
windows 1 and 2 give 86% and 83%. A higher failure penalty does not move readiness
and costs blocks (`--penalty 100 --windows 6`: 88% ± 3%, 7.0 blocks), which suggests
the calendar, not the planner, is the limit.

## What this project claims, and what it does not

**Claims.** An exact reference optimum for unconstrained memorisation. An
admissible heuristic with a written proof and a numerical check at every
reachable state. A policy that measurably beats the fixed-0.90 default every tool
ships with. A search whose optimality is verified against exhaustive computation,
and whose bound is stated and measured whenever it is weakened.

**Does not claim.** A large retention improvement from scheduling as such. The
objective is flat in the middle of a plan, so there is no such improvement to
claim. What can be claimed is the flatness itself: constraints turn out to be
nearly free. And an advantage over a fixed-0.90 scheduler on exam-day recall: the
planner's timing is now sound (AUDIT.md item 20 is fixed), but what it buys over
that scheduler is durability past the exam, at 1.2 more blocks for two subjects.

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
   It happened five times: two single-review objectives, the missing
   deadline, a value function indexed by blocks instead of by time, and "recall at
   the exam", which is met by cramming. Each time the search was correct and the
   thing being asked for was not what was wanted.
7. **Derive constants from the instance, or state them as choices.** The lateness
   penalty is derived. The aggregation step is a stated choice with a measured
   cost. `target_stability = 14.0` was neither, which is why it went unexamined
   for a whole submission.
8. **A bound belongs in the heuristic, an estimate in the objective.** The value a
   receding-horizon window ends on is part of what it optimises. A lower bound there
   is a systematic bias towards waiting.
9. **Spike before building, with the same harness for every candidate.** Three of
   five candidate value functions failed in ways that were obvious in one table and
   would each have cost a day to discover inside the real planner.
