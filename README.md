# Constrained-Path Scheduler

Study scheduling that respects both the science of memory and the reality of a
student's calendar. Point it at your `.ics` export, tell it when your exams are,
and it works out when to study — with every optimality claim checked against
exhaustive computation rather than asserted.

    # Linux / macOS
    python3 -m venv .venv && source .venv/bin/activate

    # Windows (PowerShell)
    python -m venv .venv
    .venv\Scripts\Activate.ps1

    pip install -e ".[dev]"

    pytest                                    # 177 passed, 1 xfailed
    python demo.py                            # every number quoted in the docs
    python benchmarks/replanning.py           # the planner against two baselines
    cps inspect examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome
    cps plan    examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome \
                --subject "Analysis:2:7" --subject "Algebra:4:5@2026-03-27" --out plan.ics

A subject is `Name:stability:difficulty`, optionally followed by `@` and its exam
date. Without a date, the date of the assessment with that name in the calendar is
used ("Analysis exam" above).

On Windows, if PowerShell refuses to run `Activate.ps1` ("running scripts is
disabled on this system"), run
`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once and try
again. Open the project folder itself in your terminal before creating the
environment; a terminal that starts in `C:\Windows\System32` cannot write there.

Then swap in your own calendar. Google Calendar: Settings, Import and export,
Export — you get a zip with one `.ics` per calendar. Apple Calendar: File,
Export. Run `inspect` on it first; if the free blocks it lists are wrong, nothing
downstream can be right.

The xfail is deliberate: it pins the January 2026 memory model's inability to
represent the spacing effect, and it is `strict=True`, so the build breaks if it
silently starts passing. A second one pinned the replanning defect until milestone
M1 fixed it (AUDIT.md item 20).

## What is here

| file | what it does |
|---|---|
| `src/cps/memory.py` | FSRS-4.5 forgetting curve and stability update |
| `src/cps/timegrid.py` | calendar as a single integer bitmask; immutable, hashable |
| `src/cps/calendar_io.py` | `.ics` in, study plan out: RRULE expansion, exam detection, export |
| `src/cps/ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic |
| `src/cps/clock.py` | the same problem against an exam date: `V(D, S, t)`, the replanning continuation |
| `src/cps/budget.py` | the same problem under a finite block budget; superseded, see AUDIT.md item 20 |
| `src/cps/console.py` | forces UTF-8 output so redirected runs cannot crash on Windows |
| `src/cps/cli.py` | `cps inspect` and `cps plan` against a real `.ics` export |
| `benchmarks/replanning.py` | the planner against greedy fixed-0.90 and every-k scheduling |
| `benchmarks/spike_clock.py` | the experiment that chose the clock design (PROCESS.md, Phase 6) |
| `benchmarks/clock_calibration.py` | the clock value against Monte Carlo, and the looseness of its bound |
| `benchmarks/aggregation_goal_crossing.py` | reproduces AUDIT.md item 26 before and after the fix |
| `src/cps/rolling.py` | receding-horizon replanning: plan a window, act, observe, replan |
| `src/cps/plan.py` | AO* over the AND/OR calendar graph, plus exhaustive reference |
| `src/cps/legacy.py` | the January 2026 model, kept so its defect stays a failing test |

## Where to start reading

`docs/PROCESS.md` — how the project was built, including every mistake and what
each one now costs to make again.

`docs/METHOD.md` — the formal method: the SSP formulation, the admissibility
proof, and an explicit list of what the project may and may not claim.

`AUDIT.md` — every defect found in the January 2026 submission, by severity.

## The short version

The first version claimed a 32.2% retention improvement and an optimal schedule.
Its memory model had no `elapsed_time` argument, so it could not represent the
spacing effect; its A* had no goal test, so it never returned a solution; its
heuristic added hours to stability-days, so admissibility could not be stated; and
its calendar parser was a stub, so nothing was ever tested against a real
timetable.

The rebuilt version claims less and checks all of it. AO* reproduces the exact
optimum from exhaustive backward induction on every instance small enough to
solve both ways, using 116 node expansions against 3,906 states, and the
heuristic is verified admissible at all 3,906 reachable states. The learned policy
needs about 19% fewer reviews than the fixed 0.90 target retention that every
spaced-repetition tool ships with, and the optimal retention it discovers — 0.82
to 0.85 — lands inside the band the FSRS literature reports without having been
told it.

Two things it will tell you that are worth knowing. Spacing, not the number of
free evenings, is the binding constraint: five daily blocks cannot build 21 days
of stability however you arrange them, because every gap is one day and the gain
per review is near zero. And the objective is remarkably flat in the middle of a
plan, which means hard constraints — sleep, lectures, deadlines — cost almost
nothing.

## Known limits

Timing. Until milestone M1 the rolling planner procrastinated: its continuation
counted remaining blocks, not remaining days, so the first review landed on day 16
to 19 of 21 and both topics were ready in 35 to 38% of runs. The continuation now
has a clock (`src/cps/clock.py`) and each subject its own exam. On the same
benchmark, 100 seeds: first review on day 2.3 at recall 0.89, both subjects ready in
90% ± 3% of runs at window 4 (86% and 83% at windows 1 and 2), 5.0 blocks.

What the planner does not beat. A scheduler that reviews whenever recall falls to
0.90, as Anki-style tools do, predicts almost the same recall *at the exam* (0.920
against 0.932) with 1.2 fewer blocks. It never reaches the durable target, because
its next review falls after the exam. The planner's advantage is memory that lasts
past the exam, which is what it was asked for; for exam-morning recall alone it is
not needed. Reviewing every day matches the planner's readiness at three times the
work. `python benchmarks/replanning.py` reproduces all of this.

What it does not know. The FSRS weights are population defaults, not fitted to
you, and each subject's starting stability and difficulty are your own guesses.
The target is a stated choice: recall at 90% for as long again as the preparation
lasted, per subject. The failure penalty (40 blocks for an unready exam) is a
choice too. The value estimate is 0.1 to 0.7 blocks optimistic against simulation.

Availability is calendar occupancy plus stated preferences. A calendar records
when you are busy, and nobody has an event called "sleep", so `load_availability`
takes a study window that defaults to 08:00–22:00. Without it the scheduler will
happily propose 00:00.
