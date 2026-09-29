# Changelog

## Unreleased

The project becomes a study assistant: the product schedules with rules, and the
research planner becomes the reference behind them (AUDIT.md item 36).

### Added
- The hosted app's Week page is a calendar to drag on: the timetable, exams and
  study sessions, with busy times drawn, moved, resized and renamed in place, and the
  plan redone at every change (`service.calendar_view`, `/p/<token>/calendar.json`,
  `/p/<token>/activities`). Three days on a phone, seven on a wide screen; a tap adds
  a block on a touch screen; the editor is a sheet along the bottom on a phone. Setup
  goes through it. Tapping a session opens its report page.
- Readable labels: "Algebra 3 · CM · Salle 4" for ADE's "Algebra 3, Grp: CM .,
  Salle: Salle 4" (`service.short_title`); "Self-test: Algebra 3", "Work on: Stats
  report" in the calendar (`service.session_label`).
- The hosted app, `cps web` (`src/cps/web`, the `web` extra: FastAPI, uvicorn,
  Jinja2, python-multipart): start from a timetable link or file; check the exams
  found, add deadlines, busy times, weekly hours and days off; a Today page and a
  week, phone first; a calendar feed whose events link to a one-tap report (done,
  skipped, hard) that changes the plan at once; a privacy page and deletion. No
  accounts, cookies or scripts; secret addresses kept out of logs and referrers;
  cross-site forms refused; rate limits. `docs/ROADMAP.md` is the plan it follows.
- `store.py`: SQLite for plans (versioned updates, an event log, expiry 30 days after
  the last exam or deadline, rotating backups); `cps sweep`, `cps backup`.
- Deadlines from a learning platform's calendar (Moodle): `calendar_io.find_assignments`,
  `service.sync_deadlines`, and a link in the app's settings.
- `service.report_session`, `revise_subscription`, `subscription_expiry`,
  `start_subscription`, `today_view`, `agenda`, `setup_view`, `Refresher`;
  `feed_ics(subscription, base_url)` puts what to do and the report link in each event.
- `render.yaml` and `docs/DEPLOY.md`: a Render blueprint (Frankfurt, one instance, a
  disk) and the owner's steps to deploy it.
- The assistant (`assistant.py`, `service.make_schedule`): deadlines met
  earliest-deadline-first with at-risk warnings, a weekly hours budget, days off,
  exam practice in the last two weeks before each exam, self-testing on the taught
  material that is fading, working ahead, and free time left free. A semester in
  about a hundredth of a second. Every session says what to do.
- The web page plans with the assistant by default: a "Deadlines" table, hours a
  week and days off in the sidebar, a deadlines summary with the plan; the research
  planner is a switch. Feeds refresh with the assistant.
- `benchmarks/rule_vs_planner.py`: the research planner against one-line rules on a
  semester. `docs/PRODUCT.md`: where the product is going and what it lacks.
- A calendar can be read from its link: a university timetable's export address
  (ADE, Hyperplanning), Google Calendar's secret iCal address, `webcal://`. In the
  web page ("Paste a calendar link") and on the command line (`cps plan "https://…"`).
  Private and loopback addresses are refused, after redirects too, unless
  `CPS_ALLOW_PRIVATE_LINKS=1` says the page runs on the person's own machine.
- Plans can be published as a calendar feed that a calendar app subscribes to:
  "Publish as a calendar feed" on the page, `cps serve` to answer it. A stale feed
  reads the timetable's link again and continues the plan from now, keeping the
  sessions already behind; a dead link keeps the last plan. `service.continue_plan`,
  `service.new_subscription`, `service.refresh_subscription`, `service.feed_ics`.
- Lectures become topics: a subject's lectures in the calendar are grouped by week,
  and each week is a topic studied from the day it is taught, with its own target
  (AUDIT.md item 33, METHOD.md §7). A subject without lectures is planned as before;
  `cps plan --whole-subjects` and `make_plan(..., lectures_as_topics=False)` keep the
  old model. `benchmarks/semester.py` and `examples/sample-semester.ics` show the
  difference on a synthetic semester.
- The web page has a week calendar: drag on a day to block time for training, a
  commute, work or time off, move and resize blocks, click one to rename it or make
  it weekly or one-off. Keyboard users can do the same with the arrow keys, or edit
  the same activities as a table. The plan is shown on the same calendar.
- `service.calendar_week`, `service.calendar_week_count`, `service.subject_status`,
  and `CalendarReport.lectures`.

### Changed
- Planning is faster without changing a result (item 35): the synthetic semester
  planned with each course as one topic takes 26 seconds instead of 39, with the
  same sessions, and a replan reuses the solved value functions.

### Fixed
- A class cancelled only in its title ("COURS ANNULE") no longer blocks time
  (item 34).
- Exams are found in a real university timetable export (ADE / Hyperplanning
  style, `Course, Grp: EXAMEN ., Salle: Room`), tested on the owner's own:
  keywords match whole words, longest first (AUDIT.md item 29); the subject is the
  course name, not the whole title (item 30); cancelled classes no longer block
  time (item 31); assessments are searched for over a year, not 120 days, so the
  end of a semester is found (item 32).

## 0.3.0 (2026-09-29)

The planner's timing can be trusted, a web page and a service layer exist, and
the quality bar is enforced by CI on Ubuntu and Windows.

### Fixed
- The rolling planner procrastinated: its continuation counted remaining blocks,
  not remaining days. It now uses a value function with a clock, per subject
  (`clock.py`). First review of a weak subject: day 16 to 19 of 21 before, day 2.3
  after; both subjects ready in 35 to 38% of runs before, 90% ± 3% after
  (AUDIT.md item 20).
- State aggregation could carry a stability across its target, which also
  invalidated a published ten-day result (item 26).
- The memory model now matches FSRS-4.5 exactly: a post-lapse clamp that
  FSRS-4.5 does not have was removed (item 27). The old reference sequence
  "4, 14, 44, 125" was not FSRS-4.5's; the test is now exact against py-fsrs 2.5.1.
- The heuristic-grade SSP solve is below the analysis solve in every cell (item 22).
- `cps inspect ... | head` no longer prints a traceback (item 23).
- The exported calendar defines the time zone it uses (item 24).
- A console test assumed Linux line endings (item 25).

### Added
- Per-subject exam dates in the planner, the service, the CLI
  (`--subject "Name:S:D@2026-03-20"`) and the web page; a subject the calendar
  cannot prepare for is reported instead of scheduled.
- `cps.service`: analyse, plan, replan after a lapse or a skip, export, recall
  curve; JSON results; typed errors.
- `app.py`, a Streamlit page over the service, and hand-typed timetables
  (`calendar_io.busy_from_table`).
- Benchmarks against a fixed-0.90 scheduler and every-k scheduling, a calibration
  script, the design spike, and the py-fsrs reference script.
- `docs/REFERENCES.md`, checked, with a test; `docs/ARCHITECTURE.md`;
  `docs/WRITEUP.md`.
- CI on Ubuntu and Windows for Python 3.11 to 3.13; ruff, mypy, coverage; slow
  tests marked.

### Changed
- The CLI's horizon runs to the latest exam and includes its day.
- "Esame di Fisica" is found as the subject "Fisica", not "di Fisica".

### Withdrawn
- The claim that the optimal retention lies in "the band the FSRS literature
  reports": no source could be found (item 28).
- "19% fewer reviews than fixed retention" now comes with its context: 13% fewer
  than the best fixed retention, which is a sawtooth.

## 0.2.0

The rebuild of the January 2026 submission, imported as the baseline of this
repository: FSRS-4.5, SSP-MMC value iteration, AO* with verified admissible
heuristics, calendar ingestion and export, the CLI, and the audit.

## December 2025 prototype

The Streamlit prototype with greedy bin-packing and an A* attempt, kept in
`archive/2025-prototype/`.
