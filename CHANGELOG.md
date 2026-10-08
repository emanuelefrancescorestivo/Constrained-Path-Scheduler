# Changelog

## Unreleased

The project becomes a study assistant: the product schedules with rules, and the
research planner becomes the reference behind them (AUDIT.md item 36).

### Added
- AI, opt-in and capped (D12). A line in the new-task sheet: type the task in words
  ("stats report for Friday, about 6 h", "rapport de stats pour vendredi 18h, 6
  heures") and "Fill in" puts a name, a due date, hours and a course in the fields, to
  check before adding; without scripts, a page with the form filled in. Rules read
  it always (`ai.read_task`); with `ANTHROPIC_API_KEY` set and AI turned on in
  Settings, Claude reads it (structured outputs, low effort) and writes the weekly
  review's paragraph, kept per week and numbers. A ledger (`ai_usage`) counts every
  call's tokens and dollars; the monthly cap (`CPS_AI_MONTHLY_CAP_USD`, default 10)
  and twenty calls per student a day stop calls; any refusal, error or answer out of
  bounds gives the rules' answer. Anthropic's SDK is the optional `ai` extra.
- Moderation for the network (D20). Every post (its ··· menu) and every comment can
  be reported, with a reason; the third different person to report something hides
  it from everyone but its author until the owner reviews it at
  `/admin/<CPS_ADMIN_TOKEN>` and keeps or removes it; each decision goes to the event
  log. Anyone can block someone, from a post or a profile: neither sees the other,
  follows between them end, nobody is told; People lists the blocked, to undo.
  Guidelines and the privacy page say how it works (`service.report`, `block`,
  `moderation_view`, `moderate`; `social.report`, `review`).
- The study network itself (D16, D19). A Community tab with two feeds: Following
  (one's own shared posts and those of people one follows) and Explore (posts shared
  with everyone, across universities, filtered by university, programme, course and
  kind). A profile is a handle, a university, a programme and a line about oneself,
  with a declaration of being 15 or older; the plan's secret link stays the only
  sign-in, and nothing of the plan is shown. Follows are requests the other person
  accepts; followers can be removed. Kudos (one per person per post, given in the
  background), comments (deleted by their author or the post's), and "Explain it
  simply": an idea from one's course explained for a student of something else,
  which readers mark "I got it". Sharing needs a handle; without one a post stays
  private. "Leave the network" removes the profile, follows, kudos and comments and
  keeps the diary, private. Person pages show no follower counts. Community
  guidelines at `/guidelines`. On a phone, Community takes the Tasks tab's place
  (Tasks stays on Today and behind the + button). `service.community_view`,
  `post_view`, `person_view`, `people_view`, `save_profile`, `follow`,
  `toggle_kudos`, `add_comment`, `post_explanation`, `leave_network`.
- Focus sessions and a study diary, the first step of the study network
  (DECISIONS.md D16 to D20, which replace D11's study buddies after the owner asked
  for "a social network, like Strava"). A Focus tab: a full-screen timer for a
  course or for the plan's session, the screen kept awake where the browser allows;
  a heartbeat every 30 seconds, so that time away from the page is counted and shown
  on the session ("left the app twice (6 min)"), and a session without the script
  says its focus was not checked; a web page cannot block other apps, and native
  blocking is a later step. Then the log: what was done, perceived effort 1 to 10,
  progress 1 to 5, a note, up to four photos (made smaller and stripped of metadata
  in the browser, stripped again on the server), and who sees it (only me,
  followers, everyone). Every session goes to the diary on the Progress page, makes
  its day studied for the streak, and reports the plan's session it was started
  from. Deleting a post or a plan deletes its photos (`cps.social`,
  `service.start_focus`, `focus_beat`, `finish_focus`, `log_session`, `diary_view`).
- A reason to come back every day (docs/STRATEGY.md; DECISIONS.md D8, D9). A Progress
  tab: the week's sessions done of planned in a ring, the streak and the best one,
  the weeks since the plan began day by day, milestones, each exam's readiness. On
  Today, the ring and the streak in a strip, and on Mondays and Tuesdays last week's
  review: its numbers, a paragraph and one suggestion chosen by rules (sessions not
  reported, sessions skipped at the same time of day, a week much heavier than what
  was done, an exam close, everything done). The streak counts days with a session
  reported done or hard; days with nothing planned are neutral; one day a week
  without a report is forgiven; a late report counts on its day; a lost streak is
  never announced (`progress.py`, `service.progress_view`, `weekly_review`). Each
  plan's daily visit is logged once, for the pilot's measures (D15).
- The hosted app in French and English (D10). The language is the plan's (chosen on
  the home page, changed in Settings), else the browser's; no cookie. Every page, the
  scripts' messages, what each session says to do, the plan's warnings, the errors a
  student meets, dates and numbers, and the calendar feed's events. Sessions keep
  language-free ids, so a report survives a change of language and calendar apps
  update events instead of duplicating them. `cps.i18n` (`_()`, `_n()`, dates and
  numbers in each language's words), `cps/locales/fr.py`, `static/i18n.js`;
  `tests/test_i18n.py` fails on a sentence without French. What stays English is
  AUDIT.md item 40; the French has not been reviewed by a French speaker yet.
- The market analysis (`docs/MARKET.md`), the engagement strategy
  (`docs/STRATEGY.md`), the design system (`docs/DESIGN.md`), the decisions that
  follow from the owner's answers (`DECISIONS.md`), and `benchmarks/reviews.py`, which
  codes competitors' App Store reviews by theme (not run yet: Apple's servers were
  out of reach of the sandbox it was written in).
- Tasks the way Motion handles them: a new-task sheet on every page (the + button, or
  the N key) with one-tap due dates and durations; a Tasks page with each task's
  sessions done and the next one; a task ticked off frees its remaining sessions, with
  an Undo; tasks deleted from a menu (`service.add_task`, `set_task_done`,
  `delete_task`, `tasks_view`).
- The hosted app restyled in the manner of Apple's interface guidelines: system font,
  grey grouped backgrounds and inset lists, translucent bars, a segmented control,
  iOS-style fields, pills and sheets, a dark capsule for confirmations; the calendar
  in the manner of Apple Calendar (tinted events with a colour bar, today and the
  current time in red, Day / 3 Days / Week, ← → and T). Seven course colours checked
  for colour-blind separation in both themes. Every page still works without scripts.
- Figures in the README, drawn by `benchmarks/figures.py` from the code behind their
  numbers, as SVG in light and dark (no plotting dependency): what the memory model
  predicts for spaced self-testing, cramming and a single study session; the planner
  against the schedulers students use, with standard errors; and the search against
  one-line rules on a semester. `tests/test_figures.py` holds the README's quoted
  numbers to the code.
- The hosted app, redesigned around one screen: the calendar beside a panel with
  what is next, what to report, deadlines with progress and exams with a countdown.
  Course colours (lectures lightly, study sessions fully), a now line, light and dark
  themes, popovers on a computer and sheets on a phone, tabs at the bottom on a phone,
  an onboarding stepper, compact exam rows in Settings (`calendar.js`, `app.js`,
  `style.css`; `service.course_colours`).
- Moving a study session: drag it, or give a date and a time. It stays where it was
  put (a pin), what it studies is not planned again between its old and new time, and
  a pinned block of work counts towards its deadline (`assistant.Pin`,
  `service.move_session`, `service.unpin_session`, METHOD.md §8). A move onto the
  timetable, into the past or past a deadline is refused with the reason.
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
- A post whose second photo was refused no longer leaves its first photo on disk
  (AUDIT.md item 43).
- On a phone, a long form's Save button sat under the tab bar (AUDIT.md item 42);
  the effort scale no longer makes the log page wider than the screen.
- The timetable reader was fitted to one university's export (AUDIT.md item 41). It
  now reads exams and courses in eleven systems' and countries' shapes (ADE,
  Hyperplanning, Celcat, Outlook with course codes, Italian, German, Spanish, Dutch,
  Portuguese, US, Japanese), checked by `tests/test_generality.py`; the start page
  takes the device's time zone. CLAUDE.md gains rule 13: no heuristic fitted to one
  calendar.
- On a phone, calendar blocks no longer cut a course name mid-word or lose its last
  line: short names in narrow columns, lines clamped with an ellipsis, one line when a
  word is wider than its block (AUDIT item 39).
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
