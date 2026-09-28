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

    pytest                                    # 148 passed, 2 xfailed
    python demo.py                            # every number quoted in the docs
    cps inspect examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome
    cps plan    examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome \
                --subject "Analysis:2:7" --out plan.ics

On Windows, if PowerShell refuses to run `Activate.ps1` ("running scripts is
disabled on this system"), run
`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once and try
again. Open the project folder itself in your terminal before creating the
environment; a terminal that starts in `C:\Windows\System32` cannot write there.

Then swap in your own calendar. Google Calendar: Settings, Import and export,
Export — you get a zip with one `.ics` per calendar. Apple Calendar: File,
Export. Run `inspect` on it first; if the free blocks it lists are wrong, nothing
downstream can be right.

The two xfails are deliberate. One pins the January 2026 memory model's inability
to represent the spacing effect; the other pins the replanning defect below. Both
are `strict=True`, so the build breaks if either silently starts passing.

## What is here

| file | what it does |
|---|---|
| `src/cps/memory.py` | FSRS-4.5 forgetting curve and stability update |
| `src/cps/timegrid.py` | calendar as a single integer bitmask; immutable, hashable |
| `src/cps/calendar_io.py` | `.ics` in, study plan out: RRULE expansion, exam detection, export |
| `src/cps/ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic |
| `src/cps/budget.py` | the same problem under a finite block budget — the replanning primitive |
| `src/cps/console.py` | forces UTF-8 output so redirected runs cannot crash on Windows |
| `src/cps/cli.py` | `cps inspect` and `cps plan` against a real `.ics` export |
| `benchmarks/replanning.py` | before/after harness for the open replanning defect |
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

The one-shot search reaches about ten study blocks (more with state aggregation);
`rolling.py` replans over short windows and handles a 42-block horizon. But the
value function it uses as a continuation is indexed by remaining blocks and not by
time remaining, so postponing a review costs 3.4e-09 when the calendar is generous.
The planner procrastinates: the first review lands on day 16 to 19 of 21 instead of
day 3 to 4, and both topics are ready in 35 to 38% of runs. Reproduce it with
`python benchmarks/replanning.py`. The fix is to make waiting consume calendar time.
It is recorded as a strict xfail, so the build breaks when it lands: see AUDIT.md
item 20.

Availability is calendar occupancy plus stated preferences. A calendar records
when you are busy, and nobody has an event called "sleep", so `load_availability`
takes a study window that defaults to 08:00–22:00. Without it the scheduler will
happily propose 00:00.
