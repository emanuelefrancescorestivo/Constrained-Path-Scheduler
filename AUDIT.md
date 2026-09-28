# Audit of the January 2026 version

Findings from re-reading the final report and Appendix A before writing any new
code. Severity is about whether a reader who checks the claim would find it
false, not about how hard it is to fix.

Items 1–3 mean the reported results do not measure what the report says they
measure. They are not presentation problems.

## Blocking

**1. The memory model is blind to time.** `FSRSHeuristic.predict_next_stability(current_S, difficulty)`
(A.2) takes no elapsed time, and `calculate_deficit` is `target - stability`,
also time-independent. So a review at 02:00 tomorrow and a review in three weeks
produce identical outcomes. The spacing effect — the entire reason spaced
repetition works — is not represented, which means no time slot is preferable to
any other and the "retention" figures in Table 1 measure how many blocks got
allocated, not when. This is not FSRS; FSRS is defined by its forgetting curve.
*Status: fixed in `src/cps/memory.py`; enforced by the strict-xfail test
`test_spacing_effect_exists`.*

**2. A\* never returns a solution.** `run_astar` (A.3) has no goal test.
`best_final_state` is updated from every popped node *before* the horizon check,
so the reported result is the minimum-deficit state ever encountered anywhere in
the search tree, including partial paths two days into a sixty-day plan.
Greedy, by contrast, is evaluated after running the whole horizon. The
comparison in Table 1 is therefore between "best moment ever seen by A\*" and
"final state of Greedy". Any positive result is expected regardless of algorithm
quality.

**3. The optimality claim is false.** §5.2 states the schedule is "optimal with
respect to the constraints". Two independent reasons it cannot be: (a) §3.4.1
uses Weighted A\* with w = 10, which guarantees only that the solution is within
a factor of 10 of optimal; (b) g(n) is in units of time and h(n) is in units of
stability-days, so admissibility is not even well-defined — the comparison
`g + w·h` adds hours to days. The correct claim is bounded suboptimality, and to
state a bound you need a reference optimum, which the project does not compute.

## Serious

**4. Latent crash on ties.** The heap entries are
`(f, g, time, list[Task], set, tuple)`. `Task` is a plain `@dataclass`
(`order=False`), so if `f`, `g` and `time` all tie, `heapq` compares the task
lists and raises `TypeError`. Given that stabilities are rounded to integers
during pruning, ties are likely rather than exotic. Fix: a monotonic tie-breaker
counter, or make the state a comparable/hashable frozen type.

**5. Pruning discards most of the search space.** The visited signature is
`(time // 48, rounded stabilities)` — one bucket per *day*, i.e. 48 slots
collapse to a single state. Combined with `MAX_ITERS = 150000` over a 60-day
horizon, the search almost certainly terminates on the iteration cap rather than
on a goal, and the choice of slot *within* a day — the thing being scheduled — is
outside the state signature entirely.

**6. Falsy-default bug.** `Task.copy` uses
`new_stability if new_stability else self.stability`, so `copy(new_stability=0.0)`
silently keeps the old value. Should be `is not None`.

**7. `Normalized Deficit` is never defined.** §4.2 builds the headline metric
(Effective Retention) on it. Normalised by initial deficit? By target? By worst
case? Without the definition the number is unfalsifiable, and the +32.2%
improvement cannot be reproduced or checked.

## Presentational, but they cost credibility

**8. The "bitmask" is not a bitmask.** §3.1 claims O(1) constraint checking via
a bitmask; the code is `np.zeros(total_slots, dtype=int)` with
`np.sum(grid[i:i+d])`, which is an int64 array and an O(duration) reduction.
*Status: implemented properly in `src/cps/timegrid.py` — a single Python int, so
the claim is now true and the state is immutable and hashable for free.*

**9. Citation errors.** [3] and [5] carry the same title under different author
lists, so at least one is wrong: the SSP-MMC paper is Ye et al. (KDD 2022), while
Reddy et al. (KDD 2016) is *Unbounded Human Learning: Optimal Scheduling for
Spaced Repetition*. §3.2 attributes FSRS to "Reddy et al."; FSRS is by Jarrett
Ye. The proposal attributes `R(t) = (1 + t/9S)^-1` to Reddy et al. while the
report body correctly says Reddy used `exp(-θd/s)` — the two documents
contradict each other. The author list of [1] (Balkanski et al., NeurIPS 2023)
also needs checking against the published version.

**10. No reproducibility scaffolding.** 50 trials are reported as point estimates
with no seed, no standard deviation, no confidence interval and no paired
comparison, even though both algorithms could trivially be run on the same
generated instances. `random` is used in A.4 without being imported;
`run_greedy` and `apply_mock_schedule` do not appear anywhere in the appendix.
Also: the Improvement column mixes percentage points with relative percentages
(81.2 → 94.5 is +13.3 pp, or +16.4% relative) without saying which.

## Found while rebuilding, not in the original

**11. FSRS version mixing.** My first pass used the FSRS-5 initial-difficulty
formula `D0(g) = w4 - exp(w5·(g-1)) + 1` with the FSRS-4.5 parameter vector.
`D0(GOOD)` came out at −5.5, the clamp turned it into 1.0, every item looked
trivially easy, and stability exploded. All property tests passed. It was caught
by printing D0 per grade and by a golden test against the published interval
sequence (0, 4, 14, 44, 125, 328). Worth recording as a methodology note: for a
model with named-parameter semantics, property tests are necessary but not
sufficient — you also need at least one trajectory pinned to an external
reference.

**12. Both obvious objectives degenerate.** `argmax E[S']` runs out to ~97 days
for a 5-day item, because post-lapse stability is clamped at `min(·, S)` and
waiting is therefore nearly free. `argmax (E[S'] − S)/t` collapses to t → 0,
because the gain is linear in t for small t. Recorded in
`test_both_naive_single_review_objectives_degenerate` so the next person does not
re-derive one of them and publish the result. The objective with an interior
solution is expected retention at a deadline given a fixed block budget.

**13. `target_stability = 14.0` was never justified.** Hardcoded in `Task`
(A.1) and `FSRSHeuristic` (A.2). At that target the memorisation problem is
nearly trivial: about 1.2 expected reviews from a fresh item, so the optimal
policy is dominated by the goal boundary and no spacing structure exists to find.
Fixed by deriving the target from the deadline and the student's tolerance for
forgetting (`SSPConfig.for_deadline`).

**14. Accurate is not admissible.** Discovered while validating the new solver.
Bilinear interpolation of the value function is statistically indistinguishable
from the true cost — which makes it right for reporting and wrong for use as
A*'s `h`, since an unbiased estimate exceeds the truth about half the time and an
overestimating heuristic voids the optimality guarantee. Two solver modes now
exist, and `reviews_lower_bound` refuses the inadmissible one. Related
methodology note: the first version of the calibration test used 1500 Monte-Carlo
trials and one seed, came out 0.36 reviews low, and looked like evidence of
solver bias. It was noise. Simulated baselines need error bars.

**15. `block_daily` never blocked the first night of the horizon.** The recurring
window was applied for days 0..n-1, so a window crossing midnight was never
inherited from the day *before* the start. Consequence: 00:00–07:00 on day 0
stayed free, and the scheduler was offered a study block at midnight on the first
night — with zero elapsed time since the topic's last review, a block that costs
one unit and teaches nothing. On a five-block instance that was enough to make
"study nothing" the optimal plan. Found because a test asserting the plan was
non-empty failed. Fixed by iterating from day -1 and letting `block` clip.

**16. The closed-form heuristic was inadmissible.** It summed the per-topic
minimum review counts, but a topic that cannot be finished within the horizon
costs one lateness penalty, not two hundred blocks. Caught by the whole-state-space
admissibility test: `h = 400` against an optimum of `12.38`. Replaced with a
knapsack bound — minimise over the subset of topics that could be finished within
the remaining block budget — which is exact for the argument it makes and is also
what made the combined heuristic worth having.

**17. The default lateness penalty is not comparable across instances.** It is
derived from the number of blocks, which is a sound construction within one
instance and nonsense between two: a 7-block plan and a 14-block plan built with
the default are optimising different objectives. The demo initially printed a
capacity comparison in which more free time appeared to *raise* the cost. The
penalty must be pinned for any A/B comparison, and it now is, in both the demo
and the tests.

**18. Stage 1 of the proposed architecture was never built.** The November
proposal specified a three-stage pipeline whose first stage was "parse user
input", and the January appendix contained `parse_user_calendar(file_path,
days=60)` with `import icalendar` commented out and the body replaced by
`# ... (File reading logic with icalendar library) ...`. The scheduler therefore
only ever ran against `apply_mock_schedule()`, so no reported result was ever
tested against a real calendar. Now implemented in `src/cps/calendar_io.py`, with
RRULE expansion (a DTSTART-only parser marks the whole term after week one free
and schedules study inside every lecture), outward interval rounding, and
midnight-crossing and DST cases covered by tests.

**19. A calendar records busy time, not availability.** Found on the first
end-to-end run: the exported plan put a study block at 00:00–01:30, because a
real timetable contains lectures and a gym session and no event called "sleep".
Availability is calendar occupancy *plus* stated preferences, and the preferences
cannot be derived from the data. `load_availability` now takes a `study_window`
that defaults to 08:00–22:00.

**20. The same degeneracy, a fourth time: the continuation has no clock.**
`rolling.py` plans a short window exactly and replans after each block, which is
what makes a forty-block horizon tractable. But the continuation it uses,
`budget.V(D, S, b)`, is indexed by remaining *blocks* and not by time remaining
before the exam, and its recursion is `V(D,S,b) = min(V(D,S,b-1), <use this
block>)`: the skip branch leaves `(D, S)` unchanged. Waiting neither ages the
memory nor brings the exam closer, so while blocks are plentiful skipping is free.
The value of one more block, `V(b-1) - V(b)` for a topic at S=2, D=7, is 2.6e+01
when one is left and 3.4e-09 when forty-two are. Every window therefore defers,
evaluating the deferral on the assumption of good behaviour later, and by the time
the budget binds there is no calendar left to space the reviews.

Measured with `python benchmarks/replanning.py` (21-day horizon, 42 candidate
blocks, two topics, 60 seeds): the first review lands on day 16 to 19 at recall
0.55 to 0.58, where the SSP analysis puts it on day 3 to 4 at 0.82 to 0.85, and both
topics are ready in 35 to 38% (± 6%) of stochastic runs.

Two hypotheses tested and rejected before finding the cause, both reproducible
with the same script. Larger windows do not help: `--windows 8` gives 0% ready,
worse than window 1. A durable-retention target of 90 days instead of 21 does not
help either (`--target-days 90`): it raises the bar without adding a clock, the
first review is still on day 16 to 18, and 0% of runs reach the target.

The fix is to make waiting consume the resource it actually consumes, which is
calendar time before the exam. Not implemented. Recorded as
`test_readiness_is_poor_because_the_continuation_has_no_clock`, marked
`xfail(strict=True)`, so the build breaks when it is fixed.

The general lesson, now on its fourth instance after `argmax E[S']`,
`argmax (E[S'] - S)/t` and the `V_opt` continuation: **minimising expected review
count is a workload objective, not a deadline objective.** It is indifferent to
*when* the work happens unless the resource consumed by waiting appears in the
state. Each time the search was correct and the thing being asked for was not
what was wanted.

**21. Windows portability, found before the first Windows run.** Items 1 to 20 were
developed and tested on Linux with Python 3.12. Four defects that only Windows would
have exposed were found by inspection and reproduced by simulation before the
project was handed to a Windows user:

- The plan was written through text mode. `icalendar` already emits CRLF, and a
  Windows text-mode write translates each LF again, leaving CR CR LF, which
  stricter calendar importers reject. Fixed by writing bytes. The test forbids
  `Path.write_text` on the export path, which is effective on Linux; the true
  regression test is a Windows CI job.
- A UTF-8 byte-order mark, which Windows Notepad adds, made `Calendar.from_ical`
  raise on the first line (`ValueError: Content line could not be parsed`).
  Fixed by `calendar_io.decode_ics` (`utf-8-sig`) and a defensive strip.
- `demo.py` prints pi and a prime sign, which cp1252 cannot encode, so redirected
  output on Windows raised `UnicodeEncodeError` half-way through. Fixed by
  `console.ensure_utf8_output()`, checked by running the demo with stdout forced to
  cp1252.
- `tzdata` was only a transitive dependency, through `icalendar`. Windows has no
  IANA time-zone database, so `zoneinfo` depends on it. Declared explicitly.

Not verified: none of this has run on real Windows. Status: fixed by construction
and by simulation; a Windows job in CI is the outstanding verification.

**22. The heuristic-grade solve is not cell-wise below the analysis solve.**
`SSPConfig.for_heuristic` uses 80 retentions on [0.05, 0.999]; the analysis
configuration uses 40 on [0.55, 0.98]. Neither grid contains the other, so on a
finite action set the "lower bound" can miss a slightly better action that the
analysis grid has. Measured in `demo.py`: across the whole state grid the smallest
gap (accurate minus heuristic) is -6.2e-06 reviews, against a mean slack of 2.45
reviews (28%). It cannot change any AO* result, which is why every admissibility
test passes, but it is a real discretisation caveat in a claim the project makes
strongly. Not fixed. The remedy is to build the heuristic's action grid as a
superset of the analysis grid and add a cell-wise test of `for_heuristic` against
the accurate solve.

**23. The CLI prints a traceback when its output pipe closes early.**
`cps inspect calendar.ics | head -4` raises `BrokenPipeError` with a full traceback
on Linux, because the reader left before the program finished writing. Found while
verifying the package, by a command that was only meant to shorten the output. It is
harmless but untidy in a tool that will be run from a terminal, and Windows may
behave differently (a closed pipe can surface as a different `OSError`). Not fixed.
The remedy is to treat a closed stdout as a normal exit in `cli.main`, with a test,
without catching `OSError` broadly enough to hide a failed write of `--out`.


**24. The exported plan referenced a time zone it never defined.** Every event in
`plan.ics` carried `DTSTART;TZID=Europe/Rome:...`, but the file contained no
`VTIMEZONE` component. RFC 5545 (section 3.2.19) requires one for each TZID used.
Google and Apple Calendar resolve a bare IANA name anyway; Outlook does not reliably,
and an importer that cannot resolve the zone may treat the time as floating or UTC
and move every study block by the offset. Found in milestone M0 by reading the bytes
of the exported sample plan, which was being checked for stray `\r\r\n`. Fixed with
`Calendar.add_missing_timezones()` (icalendar 6.1 or newer, now the declared
minimum); `test_exported_plan_defines_every_time_zone_it_references` covers it. The
generated definition covers 1970 to 2038, which is icalendar's documented range.
Status: fixed; import into a real calendar app still to be confirmed by a person.
