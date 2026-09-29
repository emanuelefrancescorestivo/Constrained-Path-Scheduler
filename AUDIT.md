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
lists, so at least one is wrong: the SSP-MMC paper is Ye et al. (KDD 2022)
[ref:ye2022], while Reddy et al. (KDD 2016) [ref:reddy2016] is *Unbounded Human
Learning: Optimal Scheduling for Spaced Repetition*. §3.2 attributes FSRS to "Reddy et al."; FSRS is by Jarrett
Ye. The proposal attributes `R(t) = (1 + t/9S)^-1` to Reddy et al. while the
report body correctly says Reddy used `exp(-θd/s)` — the two documents
contradict each other. The author list of [1] (Balkanski et al., NeurIPS 2023)
also needs checking against the published version. *Milestone M4:* the paper is
"Energy-Efficient Scheduling with Predictions" [ref:balkanski2023], whose author
list the proposal left as "[Authors]". All entries now live in
`docs/REFERENCES.md`, each with how it was checked, and
`tests/test_references.py` turns this item into a regression test: a
"Surname et al. (year)" that does not match an entry's first author and year fails
the build.

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
reference. *Added in milestone M4:* that "published sequence" was itself
unverified. py-fsrs 2.5.1, the FSRS-4.5 release, schedules 4, 15, 49, 146, 393,
973 days with the same defaults, and the check was only 0.6x to 1.7x wide. The
test is now exact against py-fsrs 2.5.1 (item 27).

**12. Both obvious objectives degenerate.** `argmax E[S']` runs out to ~97 days
for a 5-day item, because post-lapse stability does not fall with the delay (it
rises slightly), so the downside of waiting is capped and waiting is nearly free.
(Until M4 this item blamed a `min(·, S)` clamp, which does not bind at S = 5 and
was removed; see item 27. The ~97 days is unchanged.) `argmax (E[S'] − S)/t` collapses to t → 0,
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
calendar time before the exam.

*Status: fixed in milestone M1.* The continuation is now `clock.py`: for each
subject, the expected cost of reaching its target before its own exam, given the
days since its last review. Measured with the same script and seeds (100 seeds;
before: `docs/baselines/replanning-before.txt`, after:
`docs/baselines/replanning-after.txt`):

| | first review of Analysis | both ready | blocks |
|---|---|---|---|
| before, windows 1/2/4 | day 19 / 17 / 16, recall 0.55-0.58 | 35% / 38% / 38% (± 6%, 60 seeds) | 2.5-3.0 |
| after, windows 1/2/4 | day 1.4 / 2.3 / 2.3, recall 0.89-0.93 | 86% / 83% / 90% (± 3-4%) | 5.0-5.3 |

The strict xfail passes and lost its marker. Postponing the first review of the
S=2, D=7 topic now costs 0.48 blocks at four days and 3.0 at eight, where it cost
3.4e-09. How the design was chosen, including three candidates that failed, is in
PROCESS.md, "Milestone M1".

What the fix does not buy, measured in the same run: a greedy scheduler that
reviews each subject when its recall falls to 0.90 (the fixed desired retention of
Anki-style tools) reaches the 21-day target in 0% of runs, because its next due
date falls after the exam, yet its predicted recall *at the exam* is 0.920 against
the planner's 0.932, with 3.7 blocks against 5.0. The planner's advantage is
durability past the exam (mean stability at the exam 20.5 days against 17.0), which
is the objective it was given, not exam-day recall.

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

Not verified at the time: none of this had run on real Windows. Status: fixed, and
since milestone M0 verified by the Windows CI job (item 25).

**22. The heuristic-grade solve is not cell-wise below the analysis solve.**
`SSPConfig.for_heuristic` uses 80 retentions on [0.05, 0.999]; the analysis
configuration uses 40 on [0.55, 0.98]. Neither grid contains the other, so on a
finite action set the "lower bound" can miss a slightly better action that the
analysis grid has. Measured in `demo.py`: across the whole state grid the smallest
gap (accurate minus heuristic) is -6.2e-06 reviews, against a mean slack of 2.45
reviews (28%). It cannot change any AO* result, which is why every admissibility
test passes, but it is a real discretisation caveat in a claim the project makes
strongly.

*Status: fixed in milestone M4.* `for_heuristic` now adds every retention of the
analysis grid to its own (`SSPConfig.extra_retentions`), so its minimum is over a
superset of the analysis actions, and
`test_the_heuristic_grade_solve_is_below_the_analysis_solve_in_every_cell` checks
the order cell by cell. The smallest gap is now +0.0 and the mean slack 2.47 reviews
(28%). One consequence worth knowing: `plan.Instance` uses the heuristic-grade solve
as its terminal value as well as its heuristic, so the richer action set also moved
the one-shot optimum of the five-block instance, from 13.23170 to 13.22073, and its
expansion counts slightly (116 to 114 with the capacity heuristic). Every AO* run
still reproduces the exhaustive optimum. `clock.py` never had this caveat: it bounds
delays cell by cell rather than on a grid of points. `budget.py`, superseded, still
does.

**23. The CLI prints a traceback when its output pipe closes early.**
`cps inspect calendar.ics | head -4` raises `BrokenPipeError` with a full traceback
on Linux, because the reader left before the program finished writing. Found while
verifying the package, by a command that was only meant to shorten the output. It is
harmless but untidy in a tool that will be run from a terminal, and Windows may
behave differently (a closed pipe can surface as a different `OSError`). Not fixed.
The remedy is to treat a closed stdout as a normal exit in `cli.main`, with a test,
without catching `OSError` broadly enough to hide a failed write of `--out`.

*Status: fixed in milestone M4.* `cli.main` treats a `BrokenPipeError`, or on
Windows an `OSError` with `EINVAL`, raised while printing as a normal exit (code 0)
and points stdout at the null device so the final flush cannot raise again. The
`--out` write is caught inside `command_plan` and reported with exit code 1, so the
pipe handling can never swallow a plan that was not written. Tests: an in-process
stdout that raises, a real pipe closed before the program writes (run on Windows
by CI), and an unwritable `--out`.


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

**25. The first real Windows run: one failure, in a test.** The CI job added in
milestone M0 ran the suite on `windows-latest` for Python 3.11, 3.12 and 3.13 for
the first time. 147 tests passed and one failed on all three:
`test_ensure_utf8_output_lets_a_cp1252_stream_print_them` expected the bytes
`\xcf\x80*\n` and got `\xcf\x80*\r\n`. A `TextIOWrapper` translates `\n` to
`os.linesep` on Windows, which is what a real console stream does too. The UTF-8
encoding under test was correct; the expected value assumed Linux. Fixed in the
test by expecting `os.linesep`. The four fixes of item 21 held: the BOM, CRLF and
tzdata tests passed on Windows, and so did the redirected demo once the suite was
green. Status: fixed; Windows is now verified by CI rather than by simulation.

**26. State aggregation could carry a state across the goal.** Found in milestone
M1 while testing per-subject exams: with the Analysis exam on day 9 (target 9),
the planner reviewed at 3.33 (S from 5.0 to 8.85) and again 1.5 hours later at
recall 0.999 (8.85 to 8.97, still short). `Instance.snap` rounds stability to a 15%
multiplicative grid, and 8.85 and 8.97 both round to 9.36, past the target, so the
window search believed one cheap review finished the topic. The executed
trajectory was exact (invariant 7 held); the *decision* was made on a snapped state
on the wrong side of the goal. Fixed in `Instance.successors`: goal membership is
decided by the exact state, and a snapped state that would cross the target is
pinned to the target's edge. `test_aggregation_never_carries_a_state_across_the_goal`
covers it.

**It had also invalidated a published result.** The one-shot solver aggregates
too. `demo.py` section 4 reported the ten-day, two-topic plan at a cost of 8.83,
studying both topics ("the hard topic on days 2, 8 and 9, the easy one on day 6",
PROCESS.md). Followed with exact FSRS dynamics, that policy costs 28.60: snapped
stabilities had crossed a target the real ones had not reached. With the guard the
optimum studies only the easier topic (days 1 and 6) and its reported and real
costs agree, 17.28 and 17.29. Reproduced by `python benchmarks/aggregation_goal_crossing.py` (figures as it prints
them after the M4 model fixes, items 22 and 27; at the time the demo printed 8.84,
28.61 and 17.30).
`plan.evaluate_exact_dynamics`, which follows an aggregated policy with exact
transitions, now checks this in `test_the_aggregated_cost_is_the_real_cost_on_a_ten_day_plan`,
and the demo prints both costs. The earlier aggregation check ("within 0.1% of
exact") had compared the aggregated model's own value with the exact optimum on a
five-block instance, where it happened to hold; it now also compares the real cost.

A first hypothesis was wrong and is recorded because it was plausible: that
bilinear interpolation towards the zero stored at the target smeared "almost free"
into the states just below it. Measured, the value just below a 9-day target was
1.06 blocks either way, because successors that reach the target are zeroed by the
goal test rather than by interpolation. The grid change was reverted; the test that
checks the property (`test_just_below_the_target_is_at_least_one_more_review`)
stays. Status: fixed.

**27. A second, milder version mix: a post-lapse clamp that FSRS-4.5 does not have.**
`_stability_on_lapse` returned `min(post, S)`, "so that forgetting can never help".
Found in milestone M4 while replacing the neighbourhood check of item 11 with an
exact comparison against an independent implementation of the same version:
py-fsrs 2.5.1, the last release of the Python package implementing FSRS-4.5, by
the algorithm's author. Its post-lapse formula has no clamp, and neither does the
FSRS-4.5 formula on the algorithm's wiki ("The Algorithm", awesome-fsrs). FSRS-5
later added a *different* cap, `S / exp(w17·w18)`, so the clamp was a hybrid that
matched no published version. A trajectory with four lapses in a row exposes it:
py-fsrs gives 1.185347 at the last step, the clamped model 0.927273. Without the
clamp every value of both reference trajectories matches to 4e-14.

Fixed by removing the clamp (`memory.py` and its vectorised twin in `ssp.py`).
Measured effect: the exhaustive optimum of the five-block instance moves from
13.23196 to 13.23170, one aggregated expansion count from 10,133 to 10,136, and no
other number in `demo.py`, `benchmarks/replanning.py` or the clock calibration
changes; the clamp never binds above a stability of about two days. The bounds that
cited the clamp (`plan.best_case_reviews`, `rolling.best_case_stability`) need only
that a lapse never ends above a successful recall from the same state and delay,
which holds without it (largest ratio 0.999, at the stability floor) and is now a
test. The test that asserted the clamp was replaced by one that pins the reference
value [ref:py-fsrs-2.5.1] [ref:fsrs-wiki]. Reproduce the reference with
`benchmarks/fsrs_reference.py` in an
environment with `fsrs==2.5.1`; the project does not depend on it. Status: fixed.

**28. A corroboration that had no source.** METHOD.md, PROCESS.md, the README,
`demo.py` and a test name all said that the optimal retention the solver finds,
0.82 to 0.85, lies "inside the 0.75–0.90 band the FSRS community reports" for
workload-minimising desired retention, and presented that as independent evidence
that the model and solver are right. Found in milestone M4 while building
`docs/REFERENCES.md`: no source for the band could be found. The Anki manual gives
0.90 as the default, "a good balance of retention and workload", and says the
workload rises quickly above it [ref:anki-manual]; that is all. The claim is
withdrawn everywhere; what remains is that the optimum lies below Anki's default,
which is what a workload-minimising retention should do and is a weaker statement.
The test keeps its range as a sanity check chosen here and says so. Status: fixed.

**29. A keyword was found inside a longer one.** Found on the owner's own timetable,
exported from his university's scheduling system: every exam title reads
`Course, Grp: EXAMEN ., Salle: Room`. `find_deadlines` tried the keywords in list
order and matched substrings, so "exam" was found inside "EXAMEN" before "examen"
was tried, and stripping it left "EN" in the subject name. Fixed: keywords match
whole words, plural allowed, longest first. "Contest" no longer contains the word
"test", nor "Finalist" the word "final". Status: fixed.

**30. The subject was the whole title, room and group included.** Same timetable:
the subjects table showed "Computer Programming 3, Grp: EN ., Salle: Salle 5
Estrapade". Fixed in `calendar_io._course_name`: remove the keyword, split what
remains at field separators (comma, semicolon, pipe, a dash with spaces around it,
a colon followed by a space), and take the first field that contains a letter.
Hand-made titles ("Analysis exam", "Final - Economics", "Esame di Fisica") give the
same names as before. A course name that itself contains a comma loses its tail,
which the user sees and can correct in the subjects table. Status: fixed.

**31. A cancelled lecture still blocked its time.** The export marks a cancelled
class with `STATUS:CANCELLED` (RFC 5545) and "COURS ANNULE" in the title;
`expand_events` ignored the status. Fixed: cancelled events are skipped. Status:
fixed.

**32. Exams at the end of a semester were not found.** `service.analyse_calendar`
looked for assessments over the next 120 days. Started on 29 September, that ends
on 26 January, and the owner's exams run from 25 to 29 January: two of six were
found. Now a year (`ASSESSMENT_SEARCH_DAYS = 366`). Status: fixed.

Checked on the owner's timetable, which is not in the repository: 260 events, all
six exams found and correctly named, 246 free blocks of 60 minutes, a plan of 30
sessions reaching every target. The synthetic `ADE_STYLE` calendar in
`tests/test_calendar_io.py` reproduces the format for the tests. One consequence
left as it is and disclosed: planning six subjects over four months took 38
seconds on the development machine, because each of about 246 blocks runs a window
search over six subjects.

**33. A course was one memory item that was already known.** Found on the owner's
timetable, once the exams were read correctly: the plan put its last session on 23
November and nothing in the two months before exams on 25 to 29 January, although
lectures went on until December. The model was working as designed; the design was
wrong for a semester. Each subject was a single FSRS state, rated once by the
student at the start, so the plan only had to keep that state from decaying; it
reached each target (stability of about 120 days) by late November and stopped.
Material taught in October, November and December did not exist for it.
`benchmarks/semester.py` reproduces this on a synthetic timetable in the same
format (`examples/sample-semester.ics`).

Fixed by making the lectures in the calendar the material. `calendar_io.course_of`
reads the course from each event's title, `service` groups a subject's lectures by
week, and each week becomes a topic that appears when its last lecture ends, in the
state of something seen once and shaky (familiarity 2), with a target running from
that day to the exam; what was taught before the plan starts is one more topic,
rated by the student as before. A subject without lectures in the calendar is one
topic exactly as before, so every published result is unchanged (the
`--whole-subjects` flag and `lectures_as_topics=False` keep the old model). Two
consequences for the search, both in METHOD.md §7: each window now chooses among
the topics taught so far, which is exact, and among at most four of them, which is
not; targets are rounded down to whole weeks so that topics share value-function
solves. Stated simplifications, also shown in the app: one study block reviews a
week of one subject, and the familiarity of fresh lectures is a guess. Status:
fixed; the simplifications are disclosed.

**34. A class cancelled in its title still blocked its time.** Item 31 skipped
events with `STATUS:CANCELLED`; the owner's export also writes "COURS ANNULE" in
the title of a cancelled class, and not every export sets the status. Found while
reading the lectures for item 33. Fixed: a title containing a cancellation word
(annulé, cancelled, annullato, entfällt, abgesagt) is skipped too; "Annual review"
is not. Status: fixed.

**35. Planning a semester was slow.** Six subjects over four months took 44 seconds
on the owner's timetable and 39 on the synthetic semester of item 33, and the first
version of topics was slower still. Three changes, none of which alters a result: the search caches the hash of
its states and the memo of the waiting value keys on the exact arguments before
rounding them (a value is now computed at the rounded arguments, so a memo can be
shared or cleared without changing anything); the value-function solves use slices
instead of copies and run in threads; a table is solved only as far before the exam
as its topics are ever queried, and solved tables are kept for the next plan. The
cap of four topics per window (item 33) is the one change that trades quality for
time, and `benchmarks/semester.py --candidates 3 4 6` measures it. Measured on the
development machine, which is not the reader's: the synthetic semester with each
course as one topic, 39 seconds before and 26 after, the same 27 sessions; with a
topic per week, 38 seconds. Status: improved; the remaining time is disclosed in the
README.

**36. The optimiser's advantage over a simple rule had never been measured on a real
horizon.** The project's centre is the search: clock value functions and AO* over
windows, checked exact against exhaustive search. What it had never been compared
with, on a semester, was the simplest rule a person would write. Found while
reviewing the whole concept after items 33 to 35. On the synthetic semester
(`benchmarks/rule_vs_planner.py`), "study the taught topic you remember least" reaches
65 of 69 topics at target in 246 sessions and 0.01 seconds, against the planner's 63
in 222 sessions and about 40 seconds; the same rule restricted to recall of 0.93 or
less reaches 63 in 235 sessions, with higher predicted recall at the exams (0.978
against 0.967). The search saves about one session in twenty on the model's own
objective, which METHOD.md §5 already said is flat where plans differ. Nothing
published is wrong: the planner's results stand as stated, including its advantage
over the fixed-0.90 rule. What changes is what the product runs: the assistant
(`assistant.py`, METHOD.md §8) schedules with rules, instantly, and adds what the
planner never modelled (deadlines, a weekly budget, days off, exam practice); the
planner stays as the research reference and a switch in the app. Status: resolved by
the choice of engine; the comparison is a benchmark.
