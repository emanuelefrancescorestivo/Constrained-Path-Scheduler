# Constrained-Path Scheduler (CPS)

A study planner. It reads a student's calendar export (`.ics`), finds their exams,
and works out when to study, using the FSRS memory model and AO* search over the
free calendar slots. The plan goes back out as an `.ics`.

Owner: Emanuele Restivo, PSL University, Paris. The goal is a real, publishable
project (GitHub, LinkedIn, internship applications), possibly a product later.
His philosophy is substance over appearance: an honest limitation is worth more
than an impressive number.

## Working with Emanuele

- He writes in Italian and English. Reply in the language of his message.
  Everything written to disk (code, comments, docs, commits) is English.
- He wants to understand the method. Explain each non-obvious decision: what you
  chose, what you rejected, why.
- Deliver complete working slices with the reasoning inline. Do not stop for
  approval at every step. Ask only when the decision is his: scope, license,
  naming, new dependencies, accounts or credentials, publishing.
- His machine is Windows (PowerShell), Python 3.13, VS Code. Do not assume bash.
  Call the venv interpreter directly (`.venv\Scripts\python -m pytest`), which
  works whatever shell Claude Code is using.

## Commands

    pip install -e ".[dev,app]"             # app = streamlit, for app.py
    streamlit run app.py
    pytest                                   # 213 passed, 1 xfailed, about 95 s
    python demo.py                           # recomputes every number quoted in the docs
    python benchmarks/replanning.py          # planner vs greedy-0.90 and every-k, 100 seeds
    cps inspect examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome
    cps plan examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome --subject "Analysis:2:7" --subject "Algebra:4:5@2026-03-27" --out plan.ics

## Map of src/cps

| module | role |
|---|---|
| `memory.py` | FSRS-4.5: forgetting curve, stability and difficulty updates, 17 named weights |
| `legacy.py` | the January 2026 model, verbatim, so its defect stays a failing test |
| `timegrid.py` | calendar as one immutable, hashable integer bitmask; local wall-clock slots |
| `calendar_io.py` | `.ics` in (RRULE, EXDATE, DST, BOM), exam detection, `.ics` out |
| `ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic |
| `clock.py` | `V(D, S, t)` and `W(D, S, e, t)`: cost to reach a subject's target before its exam |
| `budget.py` | `V(D, S, b)`: cost with a finite block budget. Superseded by `clock.py` (AUDIT item 20) |
| `plan.py` | AO* on the AND/OR calendar graph, exact solver, heuristics, aggregation |
| `rolling.py` | receding-horizon replanning built on `clock.py` and `plan.py`, per-subject exams |
| `service.py` | the API every front end uses; typed errors; JSON results; no UI imports |
| `cli.py`, `console.py` | `cps inspect` / `cps plan`, printing what `service` returns; UTF-8 output hardening |

## Invariants: do not weaken these to get a green build

1. **Admissible is not accurate.** Only solves made with `for_heuristic`
   (optimistic interpolation) are lower bounds and may be used as A*/AO*
   heuristics. Bilinear solves are accurate estimates. `Instance.build`,
   `BudgetedContinuation`, `DeadlineContinuation` and `reviews_lower_bound` raise on
   the wrong kind. Keep it. The converse matters too: a window's terminal value is
   part of the objective and uses the estimate, not the bound (PROCESS Mistake 13).
2. A heuristic is checked against `solve_exact(...).values` at every reachable
   state, not only at the root.
3. The `xfail(strict=True)` test is a deliberate record of a known defect.
   When a fix makes one pass, remove the marker in the same commit, update the
   counts, add an AUDIT entry. Never delete or loosen a test to get green.
4. Every number in a document is reproduced by code (`demo.py`, `benchmarks/`, or
   a test). No hand-typed results. Change behaviour, re-run, update the docs in the
   same commit.
5. Simulated comparisons carry error bars (`ssp.reviews_statistics`) and fixed seeds.
6. Pin `lateness_penalty` when comparing two instances. Its default is derived from
   the instance and is not comparable across instances.
7. Aggregation (`stability_step`, `difficulty_step`) is a search device only. The
   executed trajectory uses exact FSRS transitions, and a snapped state never
   crosses a goal the exact state has not crossed (AUDIT item 26).
8. `TimeGrid.days_from_start` is the only slot-to-days conversion. Slots follow
   local wall-clock time; DST drift of up to one hour is documented, not hidden.
9. Property tests are not enough for a parameterised model. Keep at least one
   trajectory pinned to an external reference.
10. When you find a defect, add an AUDIT.md item (what, how found, status) before
    or with the fix. Known limitations are disclosed in CLI output and docs.
11. No business logic in a UI. Streamlit, when it exists, only calls `cps.service`.
12. Never fabricate a reference, number or result. If you cannot verify it, say so.

## Windows

- Write `.ics` as bytes (icalendar emits CRLF), decode input with
  `calendar_io.decode_ics` (BOM tolerant), call `console.ensure_utf8_output()` in
  every entry point that prints.
- `tzdata` is a declared dependency because Windows ships no time-zone database.
- Most of this repository was developed on Linux. Treat any untested Windows
  behaviour as unverified, and prefer a test that fails on Linux too.

## Known defects and limits (details in AUDIT.md)

- Item 22: the heuristic-grade SSP solve can exceed the analysis solve by 6e-06 in a
  cell. (`clock.py` bounds delays cell by cell and does not have this caveat.)
- Item 23: `cps inspect ... | head` prints a BrokenPipeError traceback.
- The planner beats a fixed-0.90 scheduler on durability past the exam, not on
  exam-day recall; say so wherever results are quoted.
- FSRS weights are population defaults; a subject's starting stability and
  difficulty are user-supplied guesses. The target and the 40-block failure penalty
  are stated choices.
