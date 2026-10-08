# Constrained-Path Scheduler (CPS)

A study planner. It reads a student's calendar export (`.ics`), finds their exams,
and works out when to study, using the FSRS memory model and AO* search over the
free calendar slots. The plan goes back out as an `.ics`.

This file is the working agreement for AI coding assistants on this repository
(Claude Code reads it). The rule behind all of it: an honest limitation is worth
more than an impressive number.

## Conventions

- Everything written to disk (code, comments, docs, commits) is English.
- Explain each non-obvious decision in the change itself: what was chosen, what
  was rejected, why.
- Deliver complete working slices. Ask the maintainer before changing scope,
  licence, naming, dependencies, accounts or credentials, or anything published.
- The maintainer works on Windows (PowerShell), Python 3.13. Do not assume bash:
  call the venv interpreter directly (`.venv\Scripts\python -m pytest`).

## Commands

    pip install -e ".[dev,app,web]"         # app = streamlit (app.py); web = the hosted app
    cps web                                  # the hosted product: pages, feeds, reports (port 8000)
    streamlit run app.py                     # the workbench
    cps serve                                # the calendar feeds the Streamlit page publishes
    pytest                                   # 419 passed, 14 deselected, 1 xfailed, about 60 s
    pytest -m "slow or not slow" --cov=cps   # all 433 + 1 xfailed, 93% coverage, about 2 min; CI runs this
    ruff check . && ruff format --check . && mypy
    python demo.py                           # recomputes every number quoted in the docs
    python benchmarks/replanning.py          # planner vs greedy-0.90 and every-k, 100 seeds
    python benchmarks/semester.py            # a synthetic semester, with and without lectures as topics
    python benchmarks/rule_vs_planner.py     # the research planner against one-line rules
    python benchmarks/figures.py             # the README's figures (docs/figures/*.svg), from the runs above
    python benchmarks/reviews.py             # competitors' App Store reviews by theme (docs/MARKET.md §4)
    cps inspect examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome
    cps plan examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome --subject "Analysis:2:7" --subject "Algebra:4:5@2026-03-27" --out plan.ics

## Map of src/cps

| module | role |
|---|---|
| `memory.py` | FSRS-4.5: forgetting curve, stability and difficulty updates, 17 named weights |
| `legacy.py` | the January 2026 model, verbatim, so its defect stays a failing test |
| `timegrid.py` | calendar as one immutable, hashable integer bitmask; local wall-clock slots |
| `calendar_io.py` | `.ics` in (RRULE, EXDATE, DST, BOM, cancellations), exam detection, course of each event, `.ics` out |
| `ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic |
| `clock.py` | `V(D, S, t)` and `W(D, S, e, t)`: cost to reach a subject's target before its exam |
| `budget.py` | `V(D, S, b)`: cost with a finite block budget. Superseded by `clock.py` (AUDIT item 20) |
| `plan.py` | AO* on the AND/OR calendar graph, exact solver, heuristics, aggregation |
| `rolling.py` | receding-horizon replanning built on `clock.py` and `plan.py`, per-topic exams, topics that appear when taught |
| `assistant.py` | the product's scheduler: deadlines (EDF), weekly budget, days off, exam practice, self-testing, free time; sessions the student moved (pins) |
| `progress.py` | the streak (studied, rest, forgiven, missed days), the week, the day grid, full weeks: pure functions of sessions and reports (DECISIONS.md D8, D9) |
| `i18n.py`, `locales/` | `_()` and `_n()` keyed by the English sentence, the language per request in a context variable, dates and numbers in each language's words (D10) |
| `service.py` | the API every front end uses; lectures become weekly topics; subscriptions, reports, Moodle deadlines, page views; typed errors; no UI imports |
| `sources.py` | a calendar from a link; refuses non-http schemes, private addresses (after redirects too), oversized answers |
| `feed.py` | WSGI feed server (`cps serve`): `/feed/<token>.ics`, background refresh, no secrets in logs |
| `store.py` | SQLite store: one JSON document per token, version compare-and-swap, event log, expiry sweep, backups |
| `web/` | the hosted app (`cps web`, FastAPI + Jinja2): setup, Today, the drag-and-drop calendar, Tasks and the new-task sheet, one-tap reports, privacy; only calls `service`; every page works without its scripts |
| `cli.py`, `console.py` | `cps inspect` / `plan` / `serve` / `web` / `sweep` / `backup`; UTF-8 output hardening |

The hosted app's front end is `web/static/`, in the manner of Apple's interface
guidelines (system font, grouped lists, translucent bars, sheets): `style.css`
(tokens, light and dark, seven course colours checked for colour-blind separation;
red is for exams and today), `calendar.js` (the calendar: Day / 3 Days / Week, draw,
drag, popovers, titles that fit, AUDIT 39), `app.js` (the plan screen: the panel is
server-rendered HTML, fetched again after each change) and `ui.js` (every page:
forms sent in the background with the page's own HTML as the answer, the new-task
sheet, Undo, the N key). Plain JavaScript modules, no build step, no framework, layout and
input only: every decision comes back from `service` as JSON or HTML. They must work
under the app's Content-Security-Policy: styles through the CSSOM (or classes, such
as the `w0`..`w100` progress widths), no inline script or style. `widgets/` (next to
`app.py`) is the Streamlit workbench's own calendar component. Check the front end in
a real browser at desktop and phone widths; the Python tests do not run JavaScript.

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

- The product runs the assistant (rules); the FSRS + AO* planner is the research
  reference and saves about one session in twenty on its own objective
  (`benchmarks/rule_vs_planner.py`, AUDIT item 36). Do not present the search as
  what makes the product better; `docs/PRODUCT.md` is the product plan.

- The planner beats a fixed-0.90 scheduler on durability past the exam, not on
  exam-day recall; say so wherever results are quoted.
- With lectures in the calendar, a topic is one week of one subject, reviewed in one
  block and starting "seen once and shaky"; each window considers at most four
  topics (METHOD.md §7). A semester takes about half a minute to plan.
- Sessions not reported count as done as planned. In `cps web` a report reaches the
  feed; in the Streamlit page it does not. Nothing is hosted yet: deployment is the
  owner's step (docs/DEPLOY.md, checkpoint C1 of docs/ROADMAP.md). Google Calendar
  refreshes a subscribed feed on its own schedule, often hours apart.
- Moodle deadlines are read from English event names only; their hours are a guess
  (2 h) until the student changes them.
- FSRS weights are population defaults; a subject's starting stability and
  difficulty are user-supplied guesses. The target and the 40-block failure penalty
  are stated choices.
