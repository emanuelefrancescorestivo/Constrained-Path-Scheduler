# Every property test passed

*How a study planner's results turned out to be invalid, and how the rebuild is
arranged so that it cannot happen quietly again.*

## The bug that nothing caught

The planner models memory with FSRS, the algorithm behind Anki's modern scheduler.
FSRS describes each thing you have learned by a difficulty and a stability, the
number of days after which you would still recall it with 90% probability, and it
comes in versions: 4, 4.5, 5, 6. Each version has its own formulas and its own
fitted parameters.

When I rebuilt the memory model I took the initial-difficulty formula from FSRS-5,
`D0(g) = w4 - exp(w5·(g-1)) + 1`, and fed it the parameters of FSRS-4.5, which
were fitted for a different formula, `D0(g) = w4 - w5·(g-3)`. The initial
difficulty of an item you answered well came out at -5.5. A clamp quietly raised it
to 1, the easiest possible value. Every item looked trivially easy, and stability
exploded.

Every property test passed. Stability rose after a successful review; it rose more
after a longer gap, which is the spacing effect; it never went negative; difficulty
stayed between 1 and 10. All of those are true of the broken model too, because
they are properties of the shape of the equations, and the shape was right. The
numbers were wrong.

What caught it was printing the initial difficulty per grade, and one test that
compared a whole trajectory with an outside reference: rate an item Good at every
due date and compare the intervals with what the reference produces. The broken
model scheduled 3.7, 21.5, 102 and 414 days. The reference was nowhere near.

The lesson is now a rule in the repository: property tests are necessary and not
sufficient for a model whose parameters have named meanings. At least one trajectory
has to be pinned to an external reference.

## The reference was the weakest link

The postscript is less comfortable. The "external reference" in that test was a
sequence, 4, 14, 44, 125 days, that nobody had traced to a source, and the test
accepted anything between 0.6 and 1.7 times it. That is a neighbourhood check, good
enough to catch a factor of three and nothing smaller.

Later I replaced it with an exact comparison against py-fsrs 2.5.1, the last
release of the Python package that implements FSRS-4.5, run in its own environment
so that it is evidence and not a dependency. Two findings came out within the hour.
The sequence was not FSRS-4.5's: with the same defaults, the reference schedules 4,
15, 49, 146, 393 and 973 days. And the model had a second, milder version mix. It
clamped post-lapse stability at its pre-lapse value, "so that forgetting can never
help". FSRS-4.5 has no such clamp; FSRS-5 added a different one later. After four
lapses and a thirty-day gap the reference gives a stability of 1.185 and the
clamped model gave 0.927. Removing the clamp changed the headline optimum in the
fifth decimal and nothing else, which is exactly the size of error only an exact
reference finds. The model now matches the reference to 4e-14 on two trajectories,
one of which exercises every grade and five lapses.

## What the first version claimed

The project started as a university assignment in late 2025. The idea has survived
unchanged: spaced-repetition tools assume you are free whenever a review falls due,
calendars know when you are busy and nothing about memory, and nobody joins the two.
The January 2026 report claimed that an A* scheduler beat a greedy one by 32.2% on
"effective retention" over fifty simulated trials, and that its schedule was
optimal.

Rereading the code before writing any new code turned up three defects that mean
those numbers do not measure what the report says. The memory model had no argument
for elapsed time, so a review tomorrow and a review in three weeks had the same
effect: there was no spacing effect for a scheduler to exploit. The A* had no goal
test and reported the best state it had seen anywhere in the search tree, while the
greedy baseline was scored where it finished. And the heuristic added hours to days,
so its admissibility, the property that makes A* optimal, could not even be stated.
The calendar parser was a stub; nothing had ever been run on a real timetable.
`AUDIT.md` lists these and every defect found since, 42 in all.

## How the rebuild is arranged

The rebuild claims less, and every claim has a check that fails loudly.

**The broken model stays as a failing test.** The January memory model is kept in
the repository, and the spacing-effect test runs against it as a strict expected
failure. If it ever passes, the build breaks.

**Optimality is measured, not asserted.** Memorising one subject is a stochastic
shortest path problem: each review costs a block and succeeds or lapses with the
predicted probability. Value iteration solves it exactly, which gives a reference
optimum and a heuristic with a written admissibility proof. The proof is then
checked numerically: on a small instance with 3,906 reachable states, the search
reproduces exhaustive backward induction, and the heuristic is below the true cost
at every one of those states, not only at the start.

**Accurate and admissible are different objects.** An unbiased estimate is above
the truth about half the time, which is fine to report and fatal as a heuristic. The
solver has two modes, and the code refuses to use the accurate one where a bound is
required. A later refinement made the bound hold over continuous review times, not
just a grid of them.

**Simulations carry error bars.** An early calibration test used one seed and 1,500
runs, came out 0.36 reviews low, and looked like solver bias. With 12,000 runs it
was noise. Every simulated number now carries its standard error and a fixed seed.

**Every number in a document comes from code.** `demo.py` and the scripts in
`benchmarks/` reproduce each figure quoted anywhere, and when the model changes, the
documents change in the same commit. This rule caught a figure that had gone stale,
and later a published result that turned out to be an artefact.

## The degenerate optimum, five times

The most instructive failure repeated itself. Five times the search returned a
correct answer to the wrong question.

Maximising expected stability after one review says to wait about 97 days for an
item you would recall for 5. Maximising stability gained per day says to review
immediately. Charging leftover work at the unconstrained optimum made the scheduler
study nothing, because spending a block now never beat paying the same price later.
The fourth time was the rolling planner, which prices "and then the rest happens
later" by a value function indexed by the number of free blocks left. Skipping a
block left memory untouched and cost nothing while blocks were plentiful: the value
of one more block was 3.4e-09 with forty-two left. The planner put the first review
on day 16 to 19 of 21, and both subjects were ready in about a third of runs.

The fix was to make waiting consume what it really consumes, calendar time before
the exam. Before building it I ran the same small experiment on five candidate value
functions. Three failed in instructive ways. Without the time since the last review
the value never changes while a subject waits, so waiting is free again. With the
natural goal, "recall at the exam of at least 90%", the planner crams: one review
the night before satisfies it whether it succeeds or not. That was the fifth
degenerate optimum, and FSRS is right about it: cramming works for the exam day. A
study planner that recommends it has been asked the wrong question. The goal kept
is a stability target per subject, memory that would last as long again as the
preparation did.

With the clock, the first review of the weak subject moved from day 16 to day 2.3,
and both subjects are ready in 90% ± 3% of runs.

## What it does not beat

It would be easy to stop there. The benchmark also runs the rule Anki uses, review
whenever predicted recall falls to 0.90, and that rule never reaches the durable
target, because its next review falls after the exam. But its predicted recall on
the morning of the exam is 0.920 against the planner's 0.932, with 1.2 fewer study
blocks. The planner's advantage is memory that lasts past the exam. For exam-morning
recall alone, it is not needed, and the README says so in its first table.

Two more corrections came from the same habit of checking. State aggregation, which
merges nearly identical memory states to keep the search small, could round a
stability of 8.85 up past a target of 9; the search then paid for a review it
believed would finish the subject. The same flaw had produced a published result: a
ten-day plan reported at 8.8 blocks that costs 28.6 when followed in the real,
unrounded model. And a sentence that had been in the method document from the start,
that the optimal retention "lies inside the 0.75–0.90 band the FSRS community
reports", turned out to have no source anyone could find. It was presented as
independent corroboration. It is withdrawn.

## The plan that finished in November

The first real calendar the planner saw was my own: six courses, lectures until
December, exams at the end of January. Once its exams were read correctly, the plan
put its last session on 23 November and nothing in the two months before the exams.
Every test passed and every number in this document still held. The model did what
it was built for, keeping one rated memory per course from decaying, and it reached
each target early. A course is not one memory: its material arrives a lecture at a
time. The calendar already said so, since it lists every lecture; it now drives the
model, one topic per week of lectures, each studied from the day it is taught. On a
synthetic semester in the same format the plan goes from 27 sessions ending in
November to 222 spread over all eighteen weeks. The same lesson as the degenerate
optimum, one level up: a correct answer to the wrong question, found only by using
the thing for what it is for.

## What I would tell someone starting a project like this

Write the test the broken version fails before writing the fix. Pin a model with
named parameters to an external reference, and check where the reference came from.
When the optimum is degenerate, the objective is wrong, not the solver. Keep a bound
and an estimate in separate objects and make the wrong one raise. Put error bars on
every simulation. And keep a record of what went wrong, because the record is the
part other people can learn from.

The code, the audit and the full development record are in this repository:
`AUDIT.md`, `docs/PROCESS.md`, `docs/METHOD.md`.
