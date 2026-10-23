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

    pip install -e ".[dev,app,web,ai]"      # app = streamlit (app.py); web = the hosted app; ai = Anthropic's SDK
    pip install -e ".[trial]"                # OR-Tools, for benchmarks/cpsat_trial.py only (D54)
    cps web                                  # the hosted product: pages, feeds, reports (port 8000)
    cps demo                                 # the hosted app on made-up students (cps.showcase), to look around
    cps demo --public                        # the public demo: a fresh student per visitor (deploy/demo/render.yaml)
    streamlit run app.py                     # the workbench
    cps serve                                # the calendar feeds the Streamlit page publishes
    docker compose -f deploy/hetzner/compose.yaml up -d --build   # the pilot on its server, behind Caddy (D75, docs/HETZNER.md)
    pytest                                   # 1139 passed, 17 deselected, 1 xfailed, about 300 s
    pytest -m "slow or not slow" --cov=cps   # all 1156 + 1 xfailed, 94% coverage, about 8 min; CI runs this
    ruff check . && ruff format --check . && mypy
    python demo.py                           # recomputes every number quoted in the docs
    python benchmarks/replanning.py          # planner vs greedy-0.90 and every-k, 100 seeds
    python benchmarks/semester.py            # a synthetic semester, with and without lectures as topics
    python benchmarks/rule_vs_planner.py     # the research planner against one-line rules
    python benchmarks/fsrs6_product.py       # the assistant with FSRS-6 and with FSRS-4.5 (D52)
    python benchmarks/fsrs6_reference.py     # memory.FSRS_6 against py-fsrs 6.3.2 (the dev extra)
    python benchmarks/personal_fsrs.py       # a student's own parameters: how many answers before they help (D53)
    python benchmarks/cpsat_trial.py         # CP-SAT placement against the rules (D54; the trial extra)
    python benchmarks/figures.py             # the README's figures (docs/figures/*.svg), from the runs above
    python benchmarks/syllabus_cost.py       # what reading a syllabus with a model would cost, per model (docs/CURRICULUM.md §3a); cards from notes by the model, at most (D84)
    python benchmarks/reviews.py             # competitors' App Store reviews by theme (docs/MARKET.md §4)
    TOKEN=<a plan> node benchmarks/page_cost.mjs   # what pages cost a slow device: CPU while open, slow frames while scrolling (D93; Node + Playwright, not a dependency)
    TOKEN=<alex> ICS=<a timetable> node benchmarks/screen_load.mjs   # what each screen asks at phone size: words, things to tap, fields, screens, tour steps; taps to a first plan (Node + Playwright)
    cps metrics --db <store>                 # the pilot's measures (DECISIONS.md D15), predicted against reported recall (D55), exams (D56), tips and weekly notes read, questions to the coach (D62, D64, D65)
    cps inspect examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome
    cps plan examples/sample-timetable.ics --from 2026-03-02 --tz Europe/Rome --subject "Analysis:2:7" --subject "Algebra:4:5@2026-03-27" --out plan.ics

## Map of src/cps

| module | role |
|---|---|
| `memory.py` | FSRS-4.5 (17 named weights, the research planner's) and FSRS-6 (21, the product's plans and flashcards, D52): forgetting curve, stability and difficulty updates; `Model` |
| `fitting.py` | D53: a student's own FSRS-6 parameters, fitted with NumPy (a prior centred on the population's, Adam on finite differences, every parameter set replayed at once); made-up students for the benchmark and tests |
| `legacy.py` | the January 2026 model, verbatim, so its defect stays a failing test |
| `timegrid.py` | calendar as one immutable, hashable integer bitmask; local wall-clock slots |
| `daylight.py` | D76: where the sun is for a plan (its time zone's main city from tzdata, NOAA's equations), folded into the day's position, 0 night to 4, behind the sky's colours |
| `calendar_io.py` | `.ics` in (RRULE, EXDATE, DST, BOM, cancellations), exam detection, course of each event, `.ics` out |
| `ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic |
| `clock.py` | `V(D, S, t)` and `W(D, S, e, t)`: cost to reach a subject's target before its exam |
| `budget.py` | `V(D, S, b)`: cost with a finite block budget. Superseded by `clock.py` (AUDIT item 20) |
| `plan.py` | AO* on the AND/OR calendar graph, exact solver, heuristics, aggregation; free time cut into blocks, and the gaps too short for one (D48) |
| `rolling.py` | receding-horizon replanning built on `clock.py` and `plan.py`, per-topic exams, topics that appear when taught |
| `assistant.py` | the product's scheduler: deadlines (EDF), weekly budget, days off, exam practice (taking turns, D46), self-testing, free time; sessions the student moved (pins) |
| `progress.py` | the streak (studied, rest, forgiven, missed days), the week, the day grid, full weeks: pure functions of sessions and reports (DECISIONS.md D8, D9) |
| `trends.py` | the trajectory: week-by-week hours, sessions kept, study load (minutes × effort), the 4-week average, courses, parts of the day: pure functions (D22, D23) |
| `insights.py` | D80: what a student's own data says, each shown once there is enough of it: starting on time, focus, when they remember best, memory against the forecast; the pace against their own limit and usual weeks, which never asks for more: pure functions |
| `learning.py` | D82: Your memory, progress only spaced recall can grow: the median stability of the taught topics over the weeks, today's map in four levels of predicted recall, the memory days the week's reviews added, recall reps: pure functions over the plan's FSRS replay |
| `syllabus.py` | D81: a course's program pasted as text, read by rules into topics (a week or a lecture named in English, French, Italian, Spanish, Portuguese, German, Dutch; bullets, numbering, headings and links dropped), and placed on the course's weeks of lectures, a list without weeks spread by lecture hours: pure functions; D83: a whole course page cut at its headings (content, outcomes, assessment, prerequisites, reading, methods, as the ECTS guide lists them), the topics from its content, the assessment's weights and the ECTS credits with their hours; D90: a whole semester's program cut into its courses (contents page, the plan's names, repeated headings; page furniture dropped), dated or numbered schedules row by row (weekday-checked dates, wrapped cells, readings, "Continuation" and days off skipped), assessments in their weeks, hours of classes, prose and group labels skipped |
| `pdftext.py` | D90: a PDF's text with pypdf (the `web` extra), within 10 MB, 80 pages and 200,000 characters; the file is not kept; why a PDF gave no text (a scan, a password, damage); D95: a PDF of notes written again without its metadata and what runs, with its text |
| `cardmaker.py` | D84: flashcards read by rules from a student's notes: questions and answers, terms and definitions, lists, words in bold or a cloze, definition sentences, in seven languages; each card picked by the student before it is kept: pure functions |
| `tips.py` | D62: the study tips, each with how strong its evidence is, its limits and its sources (only entries of docs/REFERENCES.md marked checked, with the day they were read); `service` picks the one the day calls for (an exam close, a skipped class, an evening session…, D63) or one in turn; D91: six topics, each with a line about it, How the brain learns and Motivation and reward among them |
| `weekly.py` | D64: the weekly note on the science of learning, ten studies in plain words under the tips' rules; one more each Monday from the plan's first day |
| `cards.py` | flashcards: the student's own cards, each scheduled by `memory` (FSRS-6, D52; the student's own parameters from 400 answers, D53), the review queue, 20 new a day, "Again" back in 10 minutes (D29); a card's week of lectures and a week's practice, each card once a day (D87) |
| `i18n.py`, `locales/` | `_()` and `_n()` keyed by the English sentence, the language per request in a context variable, dates and numbers in each language's words; English, French (D10), Italian (D34) |
| `ai.py` | AI, opt-in and capped (D12): a task typed in one line read by rules (English, French), the coach's answer kept to its rules (D65), flashcards written from notes on request (D84), the model through one small interface (Anthropic's SDK, the optional `ai` extra), the ledger, the monthly cap and the daily limit |
| `social.py` | the study network's tables: posts (sessions, explanations, notes), photos stripped of metadata, follows, kudos (and "helpful" marks), comments, reports, blocks; who may see what; the notes library and the month's top per course; study groups (D16 to D20, D26, D28); their invitation links (D40); profiles with a year and a country, and classmates (D39); a course's topics shared with classmates (D81); notes as text and for one study group, seen by its members while the author is one (D85); notes as PDFs, up to three a set (D95) |
| `service.py` | the API every front end uses; the sky and the garden of the streak behind a plan's pages (D73), where the sun is in the plan's day (D76); which section's tour a page shows, and the ones seen (D77); lectures become weekly topics; subscriptions, reports, Moodle deadlines, page views; passkeys and the recovery code, the next semester with the past kept for progress (D35, D36); the questions about the week and the trip to campus around each day's classes (D42); when the student studies best, a pause after training or work (D45); a weekly budget offered from what is kept (D47); a warm-up before today's lectures (D51); how an exam went, asked after it (D56); a class of the timetable skipped, once or weekly, or caught up on alone (D60), or marked as a quiz, a mock exam or a partial exam (D61); a focus session's length and rewards (D67); the insights on Progress and the one on Today (D80); Your memory on Progress (D82); the student's day: getting up, going to bed, lunch (D69); a course's topics from its pasted program, on the sessions of their week, shared with classmates (D81); Courses: each course week by week, done and to come, its workload against its ECTS credits, its cards by week, its notes and links (D86, D87, D88); a semester's program imported, pasted or as a PDF, each course offered to the plan's by name, its mock exams and readings on its weeks, its hours of classes in the workload check (D90); the tips page one topic at a time (D91); the science page's numbers, from FSRS-6 and the app's settings (D89); Today's one message (D97) and the questions that make the plan fit, one a day (D98); what a student uses, with their yes (D96); typed errors; no UI imports |
| `sources.py` | a calendar from a link; refuses non-http schemes, private addresses (after redirects too), oversized answers |
| `feed.py` | WSGI feed server (`cps serve`): `/feed/<token>.ics`, background refresh, no secrets in logs |
| `store.py` | SQLite store: one JSON document per token, version compare-and-swap, event log, expiry sweep (12 months after the last visit, D36), backups; a plan's rows of every table for its copy (`_EXPORT`, D43) |
| `inbox.py` | D43: what reaches the owner: students' feedback, and the error log (server and browser errors, one row per kind, plans' addresses cut out, kept 30 days) |
| `universities.py`, `data/` | D39: the list of universities (Hipo's, MIT, its notice in `data/`), searched by name or domain, the student's country first (from the plan's time zone) |
| `reminders.py` | D38: Web Push before each session: the server's VAPID key (made once, kept in the store), devices at known push services only, a session reminded once, sending with `pywebpush` |
| `usage.py` | D96: what a student uses, counted only with their yes: the part of the app each page's address is (39 parts), per day and how many times, on the server; a no deletes; kept 180 days; the student's own counts and the owner's totals |
| `mail.py` | D94: the only e-mail sent (a link to confirm an address, one to choose a new password), plain text through any SMTP server named by `CPS_SMTP_*`; none named, nothing sent |
| `passkeys.py`, `accounts.py` | D94: e-mail accounts (scrypt passwords under ASVS 2.1's rules, a lock after ten wrong ones, sessions and e-mailed links kept as digests, plan-less accounts swept after 30 days); D35: WebAuthn checked on the server (a small CBOR reader; ES256, Ed25519, RS256 with `cryptography`, imported lazily), a plan's passkeys, single-use challenges, the recovery code (a digest, used once) |
| `web/` | the hosted app (`cps web`, FastAPI + Jinja2): setup, Today, the drag-and-drop calendar, Tasks and the new-task sheet, one-tap reports, Focus and the diary, flashcards, the study tips (D62), the spark that brings them (`_mascot.html`, D63, D66) and waits in the top bar (`_spark.html`, D71, D97), the weekly note (D64) and the coach (D65), Progress and Trends (charts drawn as SVG on the server, `web/charts.py`), the sky's colours along the sun's day and the accent of each moment (`web/sky.py`, `/sky/<step>.css`, D76, D78), Spark's tour of Today, and each other section's line on its first visit (`_tour.html`, D77, D97), Courses (`courses.html`, `course.html`, D88), a course's topics (`course_topics.html`, D81) and the semester's program (`program.html`, D90), the science behind it (`science.html`, under You, D89, D97), You (`you.html`: progress, flashcards, tasks, focus, tips, settings, D97), what was counted and the owner's totals (`usage.html`, `admin_usage.html`, D96), cards from notes (`cards_make.html`, D84), a group's notes (D85), the app's icon (the week's ring for a day, in the sky's colours, light or dark, `web/icon.py`, D74, D76), Community (feeds, posts, people, profiles, the notes library, study groups), appearance (Automatic, Light, Dark), privacy; the account before the plan, signing in and out, a new password, a confirmed address (`signin.html`, `forgot.html`, `reset.html`, `confirm.html`; the gate in front of a plan of an account, D94); the home page's field of neurons (`web/neural.py` draws `_neural.html`, D58); only calls `service`; every page works without its scripts |
| `showcase.py` | `cps demo`'s store: five made-up classmates with posts, notes (pages drawn as PNGs without a library) and a group, and students added to them (@alex, or one per visitor with `--public`, D31) with weeks of history, a group and flashcards; the sample timetable moved by whole weeks so today is in its eighth week; the semester reported once and copied per student; made through `service`, never a test fixture for a number |
| `cli.py`, `console.py` | `cps inspect` / `plan` / `serve` / `web` / `demo` / `sweep` / `backup` / `metrics`; UTF-8 output hardening |

The hosted app's front end is `web/static/`, in the manner of Apple's interface
guidelines, under calm glass (D73: Bricolage Grotesque and Figtree shipped in `fonts/`
with their licences, a sky that follows the sun behind a plan's pages (D76), clear glass, a garden in
`garden/` that grows with the streak), with grouped lists, translucent bars, sheets, switches, the "+"
in the bar, continuous corners where the browser draws them, D37): `style.css`
(tokens, light and dark, seven course colours checked for colour-blind separation;
red is for exams and today), `calendar.js` (the calendar: Day / 3 Days / Week over the whole day (D59), and a Month of six weeks (D79), a strip that scrolls sideways a day at a time (D68), draw,
drag, popovers, titles that fit, AUDIT 39; with no timetable file, a week drawn by
hand, classes first, D32), `app.js` (the plan screen: the panel is
server-rendered HTML, fetched again after each change) and `ui.js` (every page:
forms sent in the background with the page's own HTML as the answer, the new-task
sheet, Undo, the N key, the sky and the live icon as the sun moves, asked of the server every five minutes, D76), `motion.js` (D30: numbers that count up, swaps carried over
from old values in one view transition, confetti once per achievement; page
transitions and draw-ins are CSS; the calendar slides moved sessions itself; D93:
ambient motion rests six seconds after the last tap or key, and the glass turns solid on a
device that blurs too slowly), `pwa.js` and `sw.js`
(D33: the installable app, the service worker that keeps a plan's pages for offline use and takes the server's copy of the app's files first, AUDIT 67, the
install card; with D38, the reminder shown as a notification; it also remembers the plan
last opened, for invitation links, D40; the graphite icon to install on a dark device, D74), `invite.js` (D40: "Join with my plan" on an
invitation link), `routine.js` (D70: the questions about the week one card at a time with Spark, each time drawn on a bar of the day), `tour.js` (D77: Spark's tour, the page dimmed and each step's part lit; the server says which tour and what it says), `push.js` (D38: turning
reminders on or off on a device), `passkeys.js` (D35: adding a passkey, signing in with one; the server
decides, the script hands the browser's answer back), `errors.js` (D43: loaded first on every page, it sends the scripts' errors to the owner's
error log) and `i18n.js` (the scripts' words in French and Italian).
Motion is decoration over complete pages: nothing may depend on it, and everything
stops under `prefers-reduced-motion` or the plan's Motion: Reduced. Nothing loops while a
page is read (D93): an endless animation is paused by `data-rest` or is a spinner
(`tests/test_web.py` checks), and nothing moves behind the glass, which would make every
pane blur again on every frame. Every sentence a
student reads goes through `_()` (Python, templates) or `t()` (scripts) and has a
French and an Italian entry (`cps/locales/fr.py`, `it.py`, `static/i18n.js`; `tests/test_i18n.py` checks).
Plain JavaScript modules, no build step, no framework, layout and
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
13. **General, not fitted to one calendar.** The owner's own timetable is one ADE export
    among thousands of shapes. No heuristic may be tuned to it alone: a change to how
    titles, exams, courses or times are read comes with a case in
    `tests/test_generality.py` for another system or country, and all its cases stay
    green (AUDIT item 41).

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

- Two memory models (D52): the product's plans and flashcards run FSRS-6, the research
  planner and every row of `benchmarks/rule_vs_planner.py` FSRS-4.5, so the numbers
  below that come from it are FSRS-4.5's. Scored by FSRS-6, a plan made with FSRS-6 is
  not better on every measure (`benchmarks/fsrs6_product.py`: at 15 hours a week, 44
  of 69 topics at 90% against 50, the lowest recall 0.829 against 0.792). FSRS-6 was
  chosen for its forecast, not for the plan.
- A student's own parameters (D53) are measured on made-up students only: how far real
  students are from the population, and how they grade, are assumptions in
  `fitting.py`. The gain there is small (log loss 0.0035 lower at 400 answers) and the
  plan's topics keep the population's parameters.
- CP-SAT was tried against the rules (D54) and lost: a few more topics at 90%, a much
  lower floor. The product keeps the rules and never imports OR-Tools.
- Quick self-tests (D48) count as full reviews in FSRS, which ignores a review's
  length: part of their gain in `benchmarks/rule_vs_planner.py` (53 of 69 topics at
  90% instead of 51 at 15 hours a week) is the model's, not the student's.
- When the student studies best (D45) moves which topics reach 90%: with evening
  sessions, 45 of 69 instead of 51 at 15 hours a week; mean recall at the exams is
  the same within 0.005 (`benchmarks/rule_vs_planner.py`).
- The planner beats a fixed-0.90 scheduler on durability past the exam, not on
  exam-day recall; say so wherever results are quoted.
- With lectures in the calendar, a topic is one week of one subject (a week with more
  than three hours of its lectures is split, D50), reviewed in one
  block and starting "seen once and shaky"; each window considers at most four
  topics (METHOD.md §7). A semester takes about half a minute to plan.
- Sessions not reported count as done as planned, except a self-test a day after it
  ended, which counts as unknown and gains no memory (D44). In `cps web` a report reaches the
  feed; in the Streamlit page it does not. Nothing is hosted yet: deployment is the
  owner's step (docs/HETZNER.md, checkpoint C1 of docs/ROADMAP.md; the server's files
  were run here in Docker, not on a real server). Google Calendar
  refreshes a subscribed feed on its own schedule, often hours apart.
- The exam forecast and each self-test's gain (D24) come from FSRS with population
  weights and a guessed starting state; they are estimates, rounded to 5 %, and say
  so. Study load (D23) describes work, not learning.
- Motion (D30): page transitions and the sliding tab pill need cross-document view
  transitions (Chrome, Edge, Safari 18.2+); elsewhere pages simply load. Checked in
  Chromium only.
- A course's topics (D81), a whole course page (D83) and a semester's program (D90) are
  read by rules. One real program was tried (the owner's, not in the repository; AUDIT
  item 74); the other shapes are written for the tests. Weeks count the timetable's weeks
  with lectures; a schedule's dates place topics first. A PDF is read for its text only
  (no OCR), within 10 MB and 80 pages; a PDF crafted to make pypdf work long is not
  stopped. Course names and classmates' lists match by the words as written.
- The tips on the brain and on motivation (D91) rest on abstracts; most are single
  laboratory or animal studies, and say how far they carry to a semester is not known.
- Moodle is not signed in to (D92): links, the calendar export and the program import
  only; docs/CURRICULUM.md §5a is the plan for when a university agrees.
- The workload check (D86) counts the timetable's lectures from the plan's first day and the
  plan's sessions, not hours logged alone; credits are a typical student's workload. A week
  of Courses (D88) is "reviewed" once one of its sessions is done. The phone's bar has five
  tabs and Spark is in the top bar (D97), checked in Chromium only.
- Cards from notes (D84) are read by rules from shapes notes commonly take, not from real
  students' notes; the model's cards ran against a fake provider only. Photos of notes are
  not read. A group's notes (D85) were checked in Chromium only.
- Your memory (D82) is the memory model's estimate with average weights, not a
  measurement; recall reps count what was reported. Checked in Chromium only.
- The insights (D80) rest on small numbers, said on each card; their thresholds are
  choices, not results. Starting on time counts only sessions started with the timer,
  from logs written since 2026-10-21. Checked in Chromium only.
- The tips (D62) rest on abstracts and database records, not full texts; their wording is the
  owner's to review, and a tip may cite only a checked reference (`tests/test_tips.py`).
- AI (D12), the coach (D65) included, has run only against a fake provider: no model answer
  has been tried on real sentences. With no `ANTHROPIC_API_KEY`, the rules answer, as they do whenever
  the model is off, over the cap or wrong.
- Moodle deadlines are read from English event names only; their hours are a guess
  (2 h) until the student changes them. Tasks typed in one line are read by rules in
  English and French only. The Italian (D34) was not written by a native speaker.
- Offline and install (D33) were checked in Chromium only, not on an iPhone. So was the
  calendar's sideways strip (D68), with touch emulated.
- Calm glass (D73) was checked in Chromium only, with phone sizes emulated; browsers
  without backdrop blur show the glass as a translucent fill.
- What a page costs a device (D93) was measured in headless Chromium on one machine, its
  software rendering standing in for a weak graphics chip, not on a real low-end phone or
  laptop, nor in Safari or Firefox. A page at rest takes 0 to 5 % of a core; the first six
  seconds after it opens or after a tap still take about half a core with see-through
  glass, a fifth with Solid.
- The sky follows the sun of the time zone's main city (D76): a student far from it
  (Brest under Europe/Paris) sees a sunset about half an hour early. Checked in
  Chromium only.
- Spark's tour (D77) and the accent of the moment (D78) were checked in Chromium only,
  not with a screen reader nor on a real phone; without a script there is no tour.
  The accent's hues are a choice of taste; only their contrast is measured.
- The live icon (D74) was checked in Chromium only. An installed icon stays as it was
  installed (white, or graphite if the plan or the device was dark then); Safari's
  tab shows the white PNG.
- The interface polish (D37) was checked in Chromium only; continuous corners and
  the scroll-linked top bar fall back to circular corners and a solid bar elsewhere.
- Passkeys (D35) were checked with a simulated device and Chromium's virtual
  authenticator, not on a real phone. A plan with neither a passkey nor a recovery
  code cannot be found again without its address. Passkeys belong to the host name:
  use `localhost`, not `127.0.0.1`, on a computer.
- The trip to campus (D42) is kept around every timed event of the timetable on a
  day: a personal calendar with dinners in it gets trips around dinner too, and
  online classes are not told apart from classes on campus.
- The list of universities (D39) names most in English; a typed local name and the
  list's English one do not match as classmates.
- Accounts (D94) were checked with the test client and in Chromium; no real e-mail has been
  sent (no SMTP account yet: the owner's step). An address is not the student's until it is
  confirmed. A plan without an account (the demo's students, any made before D94) still
  opens by its address, which its calendar feed carries (AUDIT 76). The blocklist of
  obvious passwords is short and written by hand.
- Notes as PDFs (D95) were tried on PDFs made by the tests and by pypdf, not on files from
  note-taking apps; they are downloaded, not shown in the site; there is no limit on a
  student's total storage.
- What a student uses (D96) is counted only for those who said yes, who may use the app
  differently from those who did not; a part used through a script alone (a fold, Spark's
  sheet, the calendar's views) is not seen. A day is a UTC day.
- The lighter app (D97, D98) was measured by `benchmarks/screen_load.mjs` in Chromium only
  (AUDIT 77 fixed how it counted folds); whether students find their way better is for the
  pilot to say. Setting up keeps its defaults unless a fold is opened: a student who trains
  every evening has those evenings planned until Today's question about the week is
  answered, from the second day. Words still to approve are listed in D97.
- Reminders (D38) need the server awake: on a host that sleeps when idle (the free
  demo), they arrive only while it is awake. A push was accepted by Google's push
  service and shown by the service worker in Chromium; not tried on a real phone.
- Plans are deleted 12 months after the last visit (D36); the feed being read does
  not count. Between two semesters, Progress waits for the new semester's first exam
  or deadline; the past sessions are kept.
- FSRS weights are population defaults; a subject's starting stability and
  difficulty are user-supplied guesses. The target and the 40-block failure penalty
  are stated choices.
