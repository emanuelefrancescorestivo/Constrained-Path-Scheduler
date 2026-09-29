# Architecture

How a calendar becomes a plan, which module does each step, and where the
guarantees sit. The mathematics is in `METHOD.md`; this page is the map.

```mermaid
flowchart TD
    ICS["calendar export (.ics)"] --> CIO
    LINK["timetable link (ADE, Google secret address)"] --> SRC["sources<br/>fetch, refuse private addresses"]
    SRC --> CIO
    ROWS["activities dragged onto the week<br/>(or typed into a table)"] --> CIO
    CIO["calendar_io<br/>expand recurrences, time zones,<br/>find exams by keyword, lectures by course"] --> GRID
    WIN["study window<br/>(hours you will study)"] --> GRID
    GRID["timegrid<br/>busy time as one integer bitmask"] --> TILE
    TILE["plan.tile_free_time<br/>free time cut into study blocks"] --> ROLL

    SPEC["subjects: exam date,<br/>familiarity or (stability, difficulty)"] --> SVC
    CIO -- "lectures" --> TOPICS["service: a topic per week of lectures,<br/>studied from the day it is taught"]
    TOPICS --> ROLL
    MEM["memory<br/>FSRS-4.5, checked against py-fsrs 2.5.1"] --> CLOCK
    MEM --> AOSTAR

    TASKS["deadlines, weekly hours, days off"] --> ASSIST
    ASSIST["assistant (the product)<br/>deadline first, exam practice, self-test what fades,<br/>work ahead, else free; milliseconds"] --> SVC
    MEM --> ASSIST
    CLOCK["clock<br/>per subject, cost to reach its target<br/>before its exam: an estimate V and a bound"] --> ROLL
    ROLL["rolling<br/>plan the next blocks exactly, take one,<br/>observe, replan"] --> AOSTAR
    AOSTAR["plan.solve_ao_star<br/>AND/OR search over the window;<br/>h = bound, terminal value = estimate"] --> ROLL

    SVC["service<br/>the only API: analyse, plan, replan,<br/>export, recall curve; typed errors"]
    CIO --> SVC
    ROLL --> SVC
    SVC --> CLI["cli: cps inspect / cps plan"]
    SVC --> APP["app.py: Streamlit page,<br/>widgets/: the week calendar"]
    SVC --> OUT["plan.ics with a reason per session"]
    SVC --> STORE["feed store<br/>one JSON file per subscription"]
    STORE --> FEED["feed: cps serve<br/>/feed/token.ics, refreshed from the link"]
    FEED -- "refresh: continue_plan from now" --> SVC
```

## The layers

**Input.** `calendar_io` turns an `.ics` (or `busy_from_table`, rows typed by
hand) into concrete busy intervals on the local wall clock, and finds assessments
by keyword ("exam", "esame", "partiel", ...) and the course of every other event
from its title. `timegrid` stores busy time as one
Python integer, one bit per half hour, so a free-time check is a mask and a compare
and a grid is immutable and hashable. `plan.tile_free_time` cuts the free time
into study blocks, earliest first, capped per day.

**Model.** `memory` is FSRS-4.5 with its 17 named parameters. Everything that
predicts recall goes through it; `ssp` has a vectorised twin that a test keeps in
step. What is being remembered is a topic: a whole subject, or, when its lectures are
in the calendar, one week of them, which exists from the day it is taught
(METHOD.md §7).

**Value.** `clock` solves, for each subject, the expected cost in study blocks of
reaching that subject's target before its own exam, as a function of difficulty,
stability, days left and days since the last review. It is solved twice: an
accurate estimate, and an optimistic lower bound that a search may use as a
heuristic. `ssp` is the unconstrained version, used for analysis and by the one-shot
solver; `budget` is the superseded block-count version (AUDIT.md item 20).

**The assistant.** `assistant` is what the product runs: each free block goes to the
first of five rules that wants it (a close deadline, exam practice, self-testing on
the fading topic, working ahead, nothing), within a weekly budget and off days. It
uses the memory model only to rank what is fading. METHOD.md §8 and
`benchmarks/rule_vs_planner.py` say why a rule and not the search.

**Search (research planner).** `plan` defines the problem (states, actions, successors, the goal) and
solves it with AO*, which returns a policy because a review can fail. `rolling`
makes it tractable on a real horizon: it plans a window of a few blocks exactly,
ending on the accurate estimate and guided by the bound, executes one block, and
replans with what happened. With topics, each window chooses among the few most
urgent topics taught so far.

**Surface.** `service` is the only thing a front end may call. It validates input,
returns frozen results with `to_dict()` in plain JSON types, turns a user's mistake
into a typed error with a sentence saying what to do, replans after a session is
reported forgotten or skipped, and shapes tables and calendar weeks for display.
`cli` prints what it returns; `app.py` lays it out, with the week calendar in
`widgets/` (plain JavaScript, no build step), which returns the activities a person
drags out as busy rows. A test fails if `app.py` imports anything from `cps` except
`service`.

**Links and feeds.** `sources` reads a calendar from a link and refuses what a server
must not fetch (other schemes, private and loopback addresses, also after a redirect,
oversized answers). A plan published from the page is a `Subscription` in a store
directory; `feed` serves it at an unguessable address, and when a calendar app reads a
stale one it reads the timetable's link again and continues the plan from now in the
background (`service.continue_plan`), keeping the sessions already behind as done.

## Where the guarantees are checked

| claim | where it is checked |
|---|---|
| the memory model is FSRS-4.5 | `tests/test_memory.py`, exact against py-fsrs 2.5.1 |
| the heuristic is admissible | `tests/test_plan.py` and `tests/test_rolling.py`, against exhaustive search at every reachable state |
| the bound is below the estimate | `tests/test_clock.py` and `tests/test_ssp.py`, cell by cell |
| estimates are calibrated | Monte Carlo with standard errors in `tests/test_clock.py`, `tests/test_ssp.py` |
| aggregation does not change the cost | `plan.evaluate_exact_dynamics`, `tests/test_plan.py` |
| the page computes nothing itself | `tests/test_app.py`, by syntax tree and by comparing tables |
| deadlines met when possible, budget and days off kept, nothing before it is taught | `tests/test_assistant.py` |
| a link cannot make the server read its own network | `tests/test_sources.py`, redirects included |
| a feed keeps what was done, survives a dead link, stays deleted, logs no address | `tests/test_feed.py` |
| a topic is never studied before it is taught | `tests/test_service.py`, `tests/test_rolling.py` |
| leaving untaught and finished topics out of a window changes nothing | `tests/test_rolling.py`, same plan as the full windows |
| every number in the documents | `demo.py` and `benchmarks/`, fixed seeds |
| every reference | `tests/test_references.py` against `docs/REFERENCES.md` |
