# Claude Code prompt: Constrained-Path Scheduler

*Working document. It is for the person driving Claude Code, not for readers of the
repository. Move it out of the repo before publishing (milestone M5).*

**How to use.** Open the project folder in a terminal, start Claude Code, and paste
this one line. Everything else lives in the repository, so a fresh session can
always recover the context.

> Read CLAUDE.md, then read docs/CLAUDE_CODE_PROMPT.md in full, then start with milestone M0 and work through the milestones in order.

---

## 1. Who you are working with, and what he wants

You are the engineering partner of Emanuele Restivo, a second-year AI student at PSL
University in Paris. This repository is a study planner: it reads a student's
calendar export, finds their exams, and works out when to study, using the FSRS
memory model and search over the free calendar slots. It goes on GitHub and LinkedIn
for internship applications, and might become a product later.

He wants the project to become **real**. It should work on his own calendar on his
own Windows machine. Its claims should survive a sceptical reader. And he should
understand every design decision well enough to defend it in an interview.

Success looks like this:

1. `cps plan` gives advice whose timing you would trust (milestone M1).
2. A small web UI where he loads a calendar, or types in his busy blocks by hand,
   sees when to study for each exam, and downloads the plan as `.ics` (M2, M3).
3. A repository a stranger can clone, run, test and believe (M4, M5).

Judge every decision on two axes: engineering quality (correct, readable,
reproducible) and internship signal (what it says about him to a recruiter or a
researcher). When they conflict, quality wins.

## 2. State of the repository today

Measured, not remembered.

- Python 3.11 or newer, about 3,000 lines of source and 2,000 of tests. `pytest`
  gives 147 passed, 2 xfailed in about 45 seconds on Linux.
- It was built in a Linux sandbox on Python 3.12 and **has never run on Windows**.
  His machine is Windows with Python 3.13.5. Four Windows defects were found by
  inspection and fixed by simulation (AUDIT.md item 21). Treat the first Windows
  run as an experiment, not a formality.

**What has real evidence behind it**

- The FSRS-4.5 memory model: property tests, plus a trajectory comparison against a
  published reference. That comparison is a neighbourhood check only (tolerance 0.6x
  to 1.7x), which M4 tightens.
- The SSP-MMC solver and its admissible heuristic: monotonicity over the whole grid,
  calibration against Monte Carlo with standard errors, and a policy that needs
  6.02 ± 0.09 blocks against 7.41 ± 0.10 for the fixed-0.90 default.
- AO* on the calendar: it reproduces exhaustive backward induction to 1e-12 on the
  five-block instance (3,906 states) with four different heuristics, and each
  heuristic is verified admissible at every one of those states.
- Calendar ingestion and export, including recurrence rules, exclusions, DST,
  events crossing midnight, and byte-order marks. The CLI.

**What does not**

- **The timing of a plan.** The rolling planner procrastinates (AUDIT.md item 20).
  The first review lands on day 16 to 19 of 21 instead of day 3 to 4, and only 35 to
  38% (± 6%) of runs end with both topics ready. `python benchmarks/replanning.py`
  reproduces this, and it is the before/after yardstick for M1.
- **`budget.py`.** It was written in a session turn whose transcript was lost, and
  was inspected only afterwards. Its 12 tests pass and it agrees with value
  iteration on an aligned grid, but nobody has reviewed its design claims with fresh
  eyes. Read it critically.
- Each subject's starting stability and difficulty are user-supplied guesses, and
  the FSRS weights are population defaults, not fitted to anyone.
- The reference intervals in `tests/test_memory.py` (4, 14, 44, 125 days) were taken
  from documentation. Their provenance has not been re-verified.
- One stability target is used for all subjects, derived from the earliest exam.

## 3. How to work

The durable rules are in `CLAUDE.md` and always apply. What follows governs how you
work this session.

1. **Plan, then execute.** At the start of each milestone write a half-page plan:
   files touched, tests to add, risks, and what would make you change course. Then
   proceed without waiting for approval, unless the decision is his (section 6).
2. **Test first for defects.** Write the test that demonstrates the problem, watch
   it fail for the right reason, then fix.
3. **Explain as you go.** For each non-obvious choice, record what you chose, what
   you rejected and why, in the commit message or the docs. He wants to learn the
   method, not only receive code.
4. **Keep the negative results.** If a hypothesis fails, write it up in the style of
   `docs/PROCESS.md`: the attempt, the measurement, the lesson. Those sections are
   the most valuable thing in this repository.
5. **Commit small and often**, one logical change each, conventional messages in
   English. The first commit, before any change, is the baseline.
6. **At the end of each milestone:** run the full `pytest` and `python demo.py`,
   update the README counts and AUDIT.md, PROCESS.md and METHOD.md as needed, then
   tell him in his language what changed, what surprised you, and what is still wrong.

## 4. Milestones

### M0. Baseline on Windows (small; do it first)

1. `git init` and commit the repository as it stands: "baseline: v0.2 imported from a
   Claude.ai session". `.gitignore` already excludes personal `.ics` files, which
   must never be committed apart from `examples/`.
2. Create the environment and install: `python -m venv .venv`, then
   `.venv\Scripts\python -m pip install -e ".[dev]"`, then
   `.venv\Scripts\python -m pytest`. Expect 147 passed, 2 xfailed. Anything else is a
   Windows defect: for each, write the failing test, fix it, add an AUDIT.md item.
3. Run `python demo.py > demo.txt` (redirected on purpose), `cps inspect` and
   `cps plan ... --out plan.ics` on the sample. Check the bytes of `plan.ics` for a
   stray `\r\r\n`. Then ask Emanuele to import `plan.ics` into the calendar app he
   uses and report what he sees. Nobody else can do that step.
4. Add `.github/workflows/ci.yml`: pytest on `ubuntu-latest` and `windows-latest`
   for Python 3.11, 3.12 and 3.13, plus `python demo.py --quick`. Do not push
   anything; there is no remote yet.
5. Save the output of `python benchmarks/replanning.py` to
   `docs/baselines/replanning-before.txt`. M1 is judged against it.

*Done when:* pytest is green on his machine, the CI file exists, the baseline is saved.

### M1. Give the value function a clock (the main technical task)

Read first: the docstrings of `budget.py` and `rolling.py`, AUDIT.md item 20,
PROCESS.md "Mistake 11", and `benchmarks/replanning.py`.

**Diagnosis, measured.** In `budget.py` the recursion is
`V(D, S, b) = min(V(D, S, b-1), use-this-block)`. The skip branch leaves `(D, S)`
unchanged. Waiting neither ages the memory nor brings the exam closer, so while
blocks are plentiful, skipping is free: one more block is worth 2.6e+01 at `b = 1`
and 3.4e-09 at `b = 42`. Every window defers, and when the budget finally binds
there is no calendar left to space the reviews.

**Already tried and rejected. Do not retry:** larger windows (window 8 gives 0%
ready); a longer stability target (90 days gives 0% ready); inflating the heuristic
(it prunes outside the solution graph and cannot shrink the graph itself); LAO*
(the state graph is acyclic).

**Requirement.** Waiting must consume the resource it really consumes, which is
calendar time before the exam. Each subject must also carry its own exam date, which
removes the single-target limitation.

**Two candidate designs.** Run a small spike on each, decide with evidence, and
record the decision in PROCESS.md. Everything below is a hypothesis to test, not a
result.

- **A. The original plan.** Add a `days_left` dimension to the budget recursion, so
  that skipping a block advances time and shrinks the horizon.
- **B. A backward DP over time.** The state right after a review is `(D, S, t)`,
  with `t` the days left to the exam. The goal is time-dependent: the topic is done
  when its stability already guarantees recall at the exam, that is
  `retrievability(t, S) >= rho`, or `S >= stability_for_interval(t, rho)`. The action
  is the delay `d` to the next review (`0 < d < t`), which fixes the retrievability
  `r = R(d, S)`. The cost is 1, and the outcomes are recall with probability `r` or
  lapse. The recursion is acyclic in `t`, so it is a single backward sweep with no
  convergence loop. Calendar capacity stays out of `V` and is handled by the AO*
  window; unconstrained delays are a superset of calendar delays, so `V` should be a
  lower bound. **Subtlety to resolve:** the true value also depends on the time
  elapsed since the last review, not only on the time left. Either add that dimension
  on a coarse grid, or prove that a relaxation is still a lower bound.

Whichever you choose, keep the discipline the project already has: optimistic
interpolation for anything used as a heuristic (invariant 1); admissibility of the
window heuristic checked against `solve_exact` at every reachable state, using the
pattern in `tests/test_rolling.py`; and calibration against Monte Carlo with
standard errors.

**Acceptance.** All of it measured by `benchmarks/replanning.py` with fixed seeds.

1. The strict xfail `test_readiness_is_poor_because_the_continuation_has_no_clock`
   starts passing. When it does the build will fail, which is intended: remove the
   marker, and replace `test_procrastination_is_measurable_and_currently_severe`
   (whose assertion will now break) with a positive test. For the topic at
   `S = 2, D = 7`, the first review is scheduled by day 6 at retrievability of at
   least 0.75. Tighten those thresholds once you have measured.
2. Both topics are ready in at least 90% of 100 seeds, reported with the binomial
   standard error.
3. Add two baselines to the benchmark and report all three planners side by side, with
   readiness, blocks used, lapses and first-review day: a naive "review every k days"
   scheduler, and a greedy fixed-retention-0.90 scheduler (what Anki-style tools do).
   The planner must beat both on readiness, or you report honestly that it does not.
4. Per-subject exam dates work end to end in `run_rolling` and in the CLI (for example
   `--subject "Analysis:2:7@2026-03-20"`, or taken from the detected assessments). The
   "known defect" notice in `cps plan` is removed together with the assertion in
   `tests/test_cli.py` that expects it.
5. A test covers an exam that is too close for the target to be reachable. The
   planner should say so, not schedule anyway.
6. Docs updated: AUDIT.md item 20 becomes "fixed" with before and after numbers,
   PROCESS.md gets the decision record and the dead ends, METHOD.md section 6 is
   rewritten, the README limits section and test counts are corrected.

### M2. A service layer (`src/cps/service.py`)

Everything a user-facing surface needs, in one importable place with no framework
attached. The CLI, the Streamlit UI, and any future FastAPI or React front end call
the same functions and can be swapped independently.

    analyse_calendar(ics: bytes, *, start, tz, days=None, study_window, blocks_per_day, block_minutes) -> CalendarReport
    make_plan(report, subjects: Sequence[SubjectSpec], *, retention, window, seed=None) -> PlanReport
    replan_after(plan, session_index, outcome)  # "recalled" | "lapsed" | "skipped"
    export_ics(plan) -> bytes
    recall_curve(plan, subject) -> list[tuple[float, float]]   # days, probability

Result types are frozen dataclasses with a `to_dict()` that returns plain JSON types.
A `SubjectSpec` has a name, an exam datetime, and either a 1-to-5 familiarity rating
or an explicit `(stability, difficulty)`. The mapping from the rating is a **prior**:
document it as a heuristic and never present it as a measurement.

Add `calendar_io.busy_from_table(rows)` so a user can type in weekly busy blocks
(weekday, start, end, label) plus one-off dates, and get the same `BusyEvent`s an
`.ics` would give. This is the "our own calendar" part of the original request.

*Acceptance.* The CLI is refactored onto the service with its output tests
unchanged. `tests/test_service.py` runs bytes to plan to `.ics` without importing any
UI, round-trips through JSON, is deterministic given a seed, and returns typed,
readable errors instead of tracebacks for: no free blocks, an exam in the past, an
unreachable target.

### M3. Streamlit UI (thin)

Streamlit is a constrained UI layer here, **not the product**. `app.py` imports
`streamlit` and `cps.service` and nothing else from `cps`. Add an `[app]` extra to
`pyproject.toml`.

Flow, top to bottom on one page:

1. **Input.** Upload an `.ics`, or fill in a weekly busy-block table (`st.data_editor`).
2. **Settings.** Time zone, study window, blocks per day, block length, target recall.
3. **Subjects and exams.** A table pre-filled from the detected exams, editable, with a
   familiarity rating per subject.
4. **Plan.** A week grid (busy, study, exam); a recall-probability curve per subject
   with the sessions marked, computed by the service; a session table that includes
   the rationale text; readiness and warnings; a `plan.ics` download.
5. **What if I miss a session?** Pick a session, mark it lapsed or skipped, and show
   the replanned schedule.

Show a plain, prominent box stating what the tool does not know: population-default
FSRS parameters, a self-assessed starting point, no personalisation.

*Acceptance.* `app.py` is roughly 250 lines or fewer. A test parses `app.py` with
`ast` and fails if it imports anything from `cps` other than `service`. A headless
test (Streamlit's `AppTest`; check that the installed version provides it) loads the
sample calendar and asserts that the displayed session table equals
`service.make_plan(...)` on the same inputs. Add a screenshot for the README. Do not
spend time on styling.

### M4. Quality bar

- **FSRS reference.** Verify the "4, 14, 44, 125" intervals against an independent
  implementation of the *same FSRS version* (4.5, 17 parameters). Recent releases of
  the `fsrs` package ship FSRS-5 or FSRS-6 defaults, so check the version before
  comparing. Reproduce the trajectory with integer-day intervals and pin exact values.
  If that is not possible, say in the test's docstring that it is a neighbourhood check.
- **Close AUDIT item 22.** Build `SSPConfig.for_heuristic`'s retention grid as a
  superset of the analysis grid, and add a cell-wise test that the heuristic solve
  never exceeds the accurate solve. Today the worst cell is off by 6.2e-06.
- **An open question worth an hour.** In `demo.py` section 2, fixed retention is not
  monotone: R = 0.85 needs 8.15 ± 0.11 blocks, while R = 0.80 needs 7.55 ± 0.12 and
  R = 0.90 needs 7.41 ± 0.10. Is that a real property of the model, from threshold
  and ceiling effects in the number of reviews, or a bug? Write it up either way.
- **Citation integrity as a regression test** (a standard he holds). Create
  `docs/REFERENCES.md` as the single bibliography. A test checks that every in-text
  reference resolves and that no two entries share a title under different authors.
  Verify every entry against the publisher's page. The January bibliography had two
  entries with the same title and different authors, and attributed FSRS to the wrong
  people (AUDIT.md item 9). Do not fill in authors from memory.
- **CLI polish.** `cps inspect ... | head -4` prints a `BrokenPipeError` traceback
  (AUDIT.md item 23). Treat a closed stdout as a normal exit, with a test, and check
  what Windows does when output goes to `Select-Object -First 4`.
- Lint and format with `ruff`; type-check `src/` with `mypy` or `pyright`. Fix real
  findings; do not silence blanket categories.
- Coverage report. Mark slow tests `@pytest.mark.slow` so the default run stays fast.
- CI green on Windows.

### M5. Publication package

- **README** rewritten as narrative and technical at once: why this is interesting
  (memory models that assume unlimited time, calendars that know nothing about
  memory), a 60-second demo, results with error bars, an honest limitations section,
  and pointers to AUDIT, PROCESS and METHOD.
- `docs/ARCHITECTURE.md` with one Mermaid diagram of the data flow.
- `docs/WRITEUP.md`, a blog-length technical write-up. The strongest story is the
  audit: the first version's results were invalid, here is how that was found and how
  the rebuild is structured so it cannot happen quietly. Lead with the FSRS-4.5 and
  FSRS-5 mix-up that passed every property test and was caught only by a published
  trajectory.
- **Two LinkedIn drafts in English**, 150 to 220 words each, first person, in a
  human voice, no marketing tone: one leading with the audit, one with the calendar
  tool. Do not publish anything.
- Repository hygiene: a LICENSE (ask him which), a CHANGELOG, a `v0.3.0` tag. Move this
  prompt file out of the repository.
- Final check: clone into a clean directory on Windows and follow only the README.

### Stretch (only after M1 to M3, and ask first)

- A Google Calendar adapter behind a `CalendarSource` interface (free/busy in, events
  out), with the OAuth desktop flow, tested against a fake, credentials never committed.
- A FastAPI wrapper over `service`, if he decides to pursue the product route.
- Fitting the FSRS weights from a user's own Anki review log.

## 5. Leave alone

His other projects. Accounts, billing, or anything commercial. Changing the FSRS
version. Rewriting AO*: it is verified, so improve around it.

## 6. Decisions that are his: ask, do not assume

The license. The repository name, owner, and public or private. Any dependency beyond
`streamlit` and what it brings. Anything involving credentials, API keys, or an OAuth
app. Publishing anything anywhere. Removing or renaming public functions. Which
language the LinkedIn post is in.

## 7. Definition of done

- A fresh clone and the README are enough to get a plan on his own calendar, on
  Windows, without help.
- `pytest` is green on Ubuntu and Windows in CI, and no xfail remains except the
  deliberate January-model one.
- Every number in the docs reproduces from code.
- AUDIT.md and PROCESS.md tell the truth about what was tried, including what failed.
