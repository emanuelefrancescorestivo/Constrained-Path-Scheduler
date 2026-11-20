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
- The owner's first pillar (D165): frictionless, easy use everywhere the app can. Fewer steps,
  no confirmation that need not be, a change saved as it is made.
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
    pytest                                   # 1519 passed, 17 deselected, 1 xfailed, about 450 s
    pytest -m "slow or not slow" --cov=cps   # all 1536 + 1 xfailed, 94% coverage, about 8 min; CI runs this
    ruff check . && ruff format --check . && mypy
    python demo.py                           # recomputes every number quoted in the docs
    python benchmarks/replanning.py          # planner vs greedy-0.90 and every-k, 100 seeds
    python benchmarks/semester.py            # a synthetic semester, with and without lectures as topics
    python benchmarks/rule_vs_planner.py     # the research planner against one-line rules
    python benchmarks/fsrs6_product.py       # the assistant with FSRS-6 and with FSRS-4.5 (D52)
    python benchmarks/fsrs6_reference.py     # memory.FSRS_6 against py-fsrs 6.3.2 (the dev extra)
    python benchmarks/personal_fsrs.py       # a student's own parameters: how many answers before they help (D53)
    python benchmarks/cpsat_trial.py         # CP-SAT placement against the rules (D54; the trial extra)
    python benchmarks/last_pass.py           # a last pass before each exam, measured and rejected (D147)
    python benchmarks/figures.py             # the README's figures (docs/figures/*.svg), from the runs above
    python benchmarks/syllabus_cost.py       # what reading a syllabus with a model would cost, per model (docs/CURRICULUM.md §3a); cards from notes by the model, at most (D84)
    python benchmarks/reviews.py             # competitors' App Store reviews by theme (docs/MARKET.md §4)
    python benchmarks/page_weight.py         # what a first visit to Today downloads, raw and with gzip; the next page's files asked again (D106)
    TOKEN=<a plan> node benchmarks/page_cost.mjs   # what pages cost a slow device: CPU while open, slow frames while scrolling (D93; Node + Playwright, not a dependency)
    TOKEN=<alex> ICS=<a timetable> node benchmarks/screen_load.mjs   # what each screen asks at phone size: words, things to tap, fields, screens, tour steps; taps to a first plan and to a first session (D107; Node + Playwright)
    WEB=<a cps web> node benchmarks/scroll_flow.mjs   # how much of each screen the three explaining pages leave empty as they are scrolled, how much they cover and how many words can be read, at laptop and phone sizes (D118, D120; Node + Playwright)
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
| `calendar_io.py` | `.ics` in (RRULE, EXDATE, DST, BOM, cancellations), exam detection, course of each event, `.ics` out; an expanded timetable kept for the same text and window (D106); a learning platform's deadlines in Moodle's own words, eight languages (D148) |
| `ssp.py` | SSP-MMC value iteration: reference optimum and admissible heuristic; the table after each sweep, on request (D112) |
| `clock.py` | `V(D, S, t)` and `W(D, S, e, t)`: cost to reach a subject's target before its exam |
| `budget.py` | `V(D, S, b)`: cost with a finite block budget. Superseded by `clock.py` (AUDIT item 20) |
| `plan.py` | AO* on the AND/OR calendar graph, exact solver, heuristics, aggregation; free time cut into blocks, and the gaps too short for one (D48); AO*'s trace, on request: each expansion, the root's value, the best plan then (D112) |
| `rolling.py` | receding-horizon replanning built on `clock.py` and `plan.py`, per-topic exams, topics that appear when taught |
| `assistant.py` | the product's scheduler: deadlines (EDF), weekly budget, days off, exam practice (taking turns, D46), self-testing, free time; sessions the student moved (pins) |
| `progress.py` | the streak (studied, rest, forgiven, missed days; repaired and paused days, D126), a pair's days in a row both studied (D127), the week, the day grid, full weeks: pure functions of sessions and reports (DECISIONS.md D8, D9) |
| `trends.py` | the trajectory: week-by-week hours, sessions kept, study load (minutes × effort), the 4-week average, courses, parts of the day: pure functions (D22, D23); the climb, the hours added up with a flag at each milestone (D100) |
| `insights.py` | D80: what a student's own data says, each shown once there is enough of it: starting on time, focus, when they remember best, memory against the forecast; the pace against their own limit and usual weeks, which never asks for more: pure functions |
| `learning.py` | D82: Your memory, progress only spaced recall can grow: the median stability of the taught topics over the weeks, today's map in four levels of predicted recall, the memory days the week's reviews added, recall reps: pure functions over the plan's FSRS replay |
| `syllabus.py` | D81: a course's program pasted as text, read by rules into topics (a week or a lecture named in English, French, Italian, Spanish, Portuguese, German, Dutch; bullets, numbering, headings and links dropped), and placed on the course's weeks of lectures, a list without weeks spread by lecture hours: pure functions; D83: a whole course page cut at its headings (content, outcomes, assessment, prerequisites, reading, methods, as the ECTS guide lists them), the topics from its content, the assessment's weights and the ECTS credits with their hours; D90: a whole semester's program cut into its courses (contents page, the plan's names, repeated headings; page furniture dropped), dated or numbered schedules row by row (weekday-checked dates, wrapped cells, readings, "Continuation" and days off skipped), assessments in their weeks, hours of classes, prose and group labels skipped |
| `pdftext.py` | D90: a PDF's text with pypdf (the `web` extra), within 10 MB, 80 pages and 200,000 characters; the file is not kept; why a PDF gave no text (a scan, a password, damage); D95: a PDF of notes written again without its metadata and what runs, with its text |
| `cardmaker.py` | D84: flashcards read by rules from a student's notes: questions and answers, terms and definitions, lists, words in bold or a cloze, definition sentences, in seven languages; each card picked by the student before it is kept: pure functions |
| `notefiles.py` | D134: a file of notes as text for `cardmaker`: PDF (pypdf), Word, PowerPoint, LibreOffice's text and slides (the standard library's zip and XML), Markdown, text; headings, items, bold and slide titles kept; DOCTYPEs, swelling zips, photos and old Office files refused; the file not kept |
| `tips.py` | D62: the study tips, each with how strong its evidence is, its limits and its sources (only entries of docs/REFERENCES.md marked checked, with the day they were read); `service` picks the one the day calls for (an exam close, a skipped class, an evening session…, D63) or one in turn; D91: six topics, each with a line about it, How the brain learns and Motivation and reward among them; D160: each tip's number for its picture, as its text gives it (`FIGURES`), the caption under its cover since D164 |
| `weekly.py` | D64: the weekly note on the science of learning, ten studies in plain words under the tips' rules; one more each Monday from the plan's first day |
| `cards.py` | flashcards: the student's own cards, each scheduled by `memory` (FSRS-6, D52; the student's own parameters from 400 answers, D53), the review queue, 20 new a day, "Again" back in 10 minutes (D29); a card's week of lectures and a week's practice, each card once a day (D87) |
| `i18n.py`, `locales/` | `_()` and `_n()` keyed by the English sentence, the language per request in a context variable, dates and numbers in each language's words; English, French (D10), Italian (D34) |
| `ai.py` | AI, opt-in and capped (D12): a task typed in one line read by rules (English, French, Italian, D148), the coach's answer kept to its rules (D65), flashcards written from notes on request (D84), and from a course's materials by Claude Haiku 5.5, each card with its topic's week (D101), then checked card by card against the notes by a second call (D138), the model through one small interface (Anthropic's SDK, the optional `ai` extra), the ledger, the monthly cap and the daily limit |
| `social.py` | the study network's tables: posts (sessions, explanations, notes), photos stripped of metadata, follows, kudos (and "helpful" marks), comments, reports, blocks; who may see what; a profile's avatar, its parts' names only (D153); the notes library and the month's top per course; study groups (D16 to D20, D26, D28); their invitation links (D40); profiles with a year and a country, and classmates (D39); a course's topics shared with classmates (D81); notes as text and for one study group, seen by its members while the author is one (D85); notes as PDFs, up to three a set (D95); a class's link, one per plan (D104); studying together, a pair asked by handle (D127); the study map's places, added by students, and who is where, followers named and others counted (D133); a session's four reactions, and Studying now: who is focusing, seen by their followers, with their yes (D108); peer checks: "It's right" on an explanation or notes, a fix suggested and accepted by the author (D141); a vouch for a session that says or shows what was done, the same mark (`VOUCHED`, `says_what_was_done`, D158) |
| `service.py` | the API every front end uses; the sky and the garden of the streak behind a plan's pages (D73), where the sun is in the plan's day (D76); which section's tour a page shows, and the ones seen (D77); lectures become weekly topics; subscriptions, reports, Moodle deadlines, page views; passkeys and the recovery code, the next semester with the past kept for progress (D35, D36); the questions about the week and the trip to campus around each day's classes (D42); when the student studies best, a pause after training or work (D45); a weekly budget offered from what is kept (D47); a warm-up before today's lectures (D51); how an exam went, asked after it (D56), shown beside its forecast on Today, Progress and the course's page, a course taken again asked about again (D105); a class of the timetable skipped, once or weekly, or caught up on alone (D60), or marked as a quiz, a mock exam or a partial exam (D61); a focus session's length and rewards (D67); the insights on Progress and the one on Today (D80); Your memory on Progress (D82); the student's day: getting up, going to bed, lunch (D69); a course's topics from its pasted program, on the sessions of their week, shared with classmates (D81); Courses: each course week by week, done and to come, its workload against its ECTS credits, its cards by week, its notes and links (D86, D87, D88); a semester's program imported, pasted or as a PDF, each course offered to the plan's by name, its mock exams and readings on its weeks, its hours of classes in the workload check (D90); the tips page one topic at a time (D91); the science page's numbers, from FSRS-6 and the app's settings (D89), and its scenes run as they are scrolled through: three topics' recall day by day, the spacing effect's landscape (how many times longer a topic holds, tested at each recall), one topic kept up against left alone, step by step (D113); Today's one message (D97) and the questions that make the plan fit, one a day (D98); a tip beside what each page is about, why here from the plan (D135); the laptop's sidebar: the month with study days and exams, the courses with their exams (D136); what a student uses, with their yes (D96); a class's link: a plan's courses with topics, taken by the class into matching courses (D104); the memory loop (D125): each self-test's chance of recall as it starts, the memory days it added, the day the topic then holds until and when it comes back, the day's close once every session planned today is reported; repair and pause (D126): a missed day's make-up session offered on Today and recorded when the timed session is saved, a pause of the streak that leaves its days free of sessions; studying together (D127): a pair's days in a row both studied, judged by each one's streak, and a group's memory days this week; the stakes and a curious start (D128): a self-test's exam and the day a recall in it keeps the topic above 90 % until, and the question a planned self-test opens with, its week's card the model gives the least chance now, answered on the log page; reminders that vary (D129): one true line in turn after the kind and length, none between going to bed and getting up; unannounced moments (D130): a recall that first takes a topic past a month, and the first week every self-test answered was recalled, said the week after; the semester recap (D131): the semester's numbers once every exam is behind, the text to share, kept at the next semester's start; the study map (D133): the places around the student's, who is at each as they may see it, a session's place chosen and left; the first day's short session, Community ten posts at a time, the usage question from the second day (D106); a new plan's first run after Strava's: who is studying, the courses, the class (a class link carried through the account), who sees the sessions, the first session's summary, reminders asked once a session is done (D107); Studying now following the focus timer, Join, and a session's reactions (D108); how the code works: demo.py's worked example solved once per process, the bits, a review's two futures, value iteration's table, the AND/OR choices, each guess's nodes, the exhaustive check (D109); the calendar's blocks, the chosen plan's score term by term, the topics' course colours (D110); each cell's tower height (D111); the sleep and classes masks' bits, the value table's towers at each of eight sweeps, AO* drawn from its trace on a disc (each node's ring and angle, when it was produced and opened, the best plan at each step, the root's value with the guess and without) (D112); the student's avatar, its parts' names (D115); How it works: the garden story's numbers from FSRS-6 (D116); each plant's roots against a month (D117); each scene's numbers at finer stops with its labels and its day, the cramming chapter, a revision that works only as often as its topic is remembered (an expected value), and the month a student plans, scored three ways against the app's rule (D121); each day of the worked week as a dial and the levels the app's rule saw each evening (`_garden_evenings`, D146); Today as home (D140): the day's list (`day_agenda`), the window on classmates (`classmates_glance`), the day's hours and the week's attention (`time_and_attention`), a tip to study and one to live well (`tips_for_today`), each course's links and the learning platform's page from its calendar link (`workspace_view`, `learning_platform`); D141: each section's tour on its first visit (`SECTION_TOURS`), the course a share form starts on (`share_course`), a reader's check and an accepted fix on a post's card and page; D144: focus from Today in one tap (`focus_quick`), the week's attention day by day and the part of the day timed sessions last longest (`time_and_attention`); D145: what is on the student's mind, each item a day or a note (`add_to_mind`, `plan_mind_item`, `cross_off`, `mind_view`), tomorrow's first three in the evening (`close_day`), a task's own steps (`add_step`, `tick_step`, `drop_step`); D147: the hours that would keep more topics at the target on exam day, a block at a time, each step the plan made again (`hours_offer`); D156: a theme for light, one for dark and an accent (`palette_of`, `set_palette`, `palette_view`); D157: the sky of each hour of today for the calendar's ribbon (`sun_hours`); D161: Settings as a dashboard, what each part is set to (`settings_overview`); D160: the number under each tip's picture (`tip_figure`); D158: a session's points, 10 for doing it and 20 for its proof, a post vouched for by another student (`session_points`, `proof_posts`, `POINTS_DONE`, `POINTS_PROOF`), each day's sum on the calendar, the diary's form on a session of the plan (`log_draft(sid=)`); D155: Today's widgets each on or off, five to start (`today_widgets`, `set_today_widgets`, `today_widgets_view`), each with its head's picture (`TODAY_PICTURES`, D165); D154: setting up's sentence and its choices (`setup_sentence`), busy times from answers (`routine_rows`), Moodle's words for its export (`MOODLE_EXPORT_WORDS`), what a platform's link would bring (`deadlines_preview`); D153: an avatar shared with the profile (`share_flag`); D162: the time the plan gives tasks this week (`task_week`), and beside the feed the student's groups and classmates not followed yet (`community_context`); D163: the student's Moodle (`moodle_site`, `moodle_home`), a course's four pages on it or its search (`moodle_course`, `MOODLE_PAGES`), an address pasted read for its site and course number only (`read_moodle_address`, `link_moodle_course`); typed errors; no UI imports |
| `sources.py` | a calendar from a link; refuses non-http schemes, private addresses (after redirects too), oversized answers |
| `feed.py` | WSGI feed server (`cps serve`): `/feed/<token>.ics`, background refresh, no secrets in logs |
| `store.py` | SQLite store (`healthy()` for `/health`, D106): one JSON document per token, version compare-and-swap, event log, expiry sweep (12 months after the last visit, D36), backups; a plan's rows of every table for its copy (`_EXPORT`, D43) |
| `inbox.py` | D43: what reaches the owner: students' feedback, and the error log (server and browser errors, one row per kind, plans' addresses cut out, kept 30 days) |
| `universities.py`, `data/` | D39: the list of universities (Hipo's, MIT, its notice in `data/`), searched by name or domain, the student's country first (from the plan's time zone); D143: the countries a flag can be chosen for, with their names in English (`countries.json`, from flag-icons) |
| `reminders.py` | D38: Web Push before each session: the server's VAPID key (made once, kept in the store), devices at known push services only, a session reminded once, sending with `pywebpush` |
| `usage.py` | D96: what a student uses, counted only with their yes: the part of the app each page's address is (40 parts), per day and how many times, on the server; a no deletes; kept 180 days; the student's own counts and the owner's totals |
| `mail.py` | D94: the only e-mail sent (a link to confirm an address, one to choose a new password), plain text through any SMTP server named by `CPS_SMTP_*`; none named, nothing sent |
| `passkeys.py`, `accounts.py` | D94: e-mail accounts (scrypt passwords under ASVS 2.1's rules, a lock after ten wrong ones, sessions and e-mailed links kept as digests, plan-less accounts swept after 30 days); D35: WebAuthn checked on the server (a small CBOR reader; ES256, Ed25519, RS256 with `cryptography`, imported lazily), a plan's passkeys, single-use challenges, the recovery code (a digest, used once) |
| `web/` | the hosted app (`cps web`, FastAPI + Jinja2): setup, Today (since D140 a page of widgets, `_panel.html`), the drag-and-drop calendar (since D140 alone, full width), Tasks and the new-task sheet, one-tap reports, Focus and the diary, flashcards, the study tips (D62), each with its cover, a small scene across the top of its card (`web/covers.py`, D164; D160's tile is gone), and one beside what a page is about (`_why.html`, D135), the laptop's sidebar (`_sidebar.html`, D136), whose head is the top bar on a laptop (D149), the spark that brings them (`_mascot.html`, D63, D66) and waits in the top bar (`_spark.html`, D71, D97), the weekly note (D64) and the coach (D65), Progress and Trends (charts drawn as SVG on the server, `web/charts.py`), the sky's colours along the sun's day and the accent of each moment (`web/sky.py`, `/sky/<step>.css`, D76, D78), Spark's tour of Today, and each other section's line on its first visit (`_tour.html`, D77, D97), Courses (`courses.html`, `course.html`, D88), a course's topics (`course_topics.html`, D81) and the semester's program (`program.html`, D90), the science behind it (`science.html`, under You, D89, D97), You (`you.html`: progress, flashcards, tasks, focus, tips, settings, D97; each with its picture, `_art.html`, D99, since D139 a Lucide icon on a soft tile, `icons.py`), the climb on Progress (`charts.climb`, D100) and the diary week by week, each session drawn (D100), the front door, `/p/<plan>/open`, Today for everyone and Today the first tab (D124; D102 had opened on the feed), the semester recap (`recap.html`, `/p/<plan>/recap`, D131), the study map (`map.html`, `/p/<plan>/map`, its own Content-Security-Policy and Permissions-Policy, D133), the memory loop's first slice (`_banked.html`: an answered self-test against the model's chance of recalling it, the memory days it added and the day it holds until, "Just answered" and "Done for today" on Today, D125), the stakes on a session's card and the focus page, the opening question while the timer runs and its answer on the log page (D128), a class's link (`/c/<code>`, `class.html`, `class_take.html`, the card on Courses, D104), Settings as six rows, each its own page, since D161 six cards with what each is set to (`settings.html` the dashboard and setting up's exams, `settings_part.html`, `_set_*.html`), each saving only its part (`forms.week_revision`…), each exam changed on its course's page, and Progress with the week, the streak, the exams and the climb above "More detail" (D103), what was counted and the owner's totals (`usage.html`, `admin_usage.html`, D96), cards from notes (`cards_make.html`, D84), a group's notes (D85), the app's icon (the week's ring for a day, in the sky's colours, light or dark, `web/icon.py`, D74, D76), Community (feeds, posts, people, profiles, the notes library, study groups), appearance (Automatic, Light, Dark), privacy; the account before the plan, signing in and out, a new password, a confirmed address (`signin.html`, `forgot.html`, `reset.html`, `confirm.html`; the gate in front of a plan of an account, D94); the home page's field of neurons (`web/neural.py` draws `_neural.html`, D58); the app's files named under a version and kept a year (`asset()`, `/static/v/<version>/`, D106); a new plan's first run (`first_run.html`, `/p/<plan>/start/<step>`) and a phone's three welcome cards on the home page (D107); Studying now atop Community's feed and on the focus page, a session's reactions (`/p/<plan>/post/<id>/react`, D108); how the code works (`how.html`, `/how`, `/p/<plan>/how`) and the science page's figures drawn as they scroll (`.scroll-fig`, D109), both in the app's look, the public pages under the dusk sky (D110), in CSS 3D: the week's three layers falling into one (since D146 a card of lights, a light per half hour), the value table's landscape, figures rising tilted (D111); four scenes that run while scrolled through (`.code-pin`, a tall track and a stage that stays in view: the week's bits set mask by mask, the value table growing sweep by sweep, AO* opening node by node, replanning's window moving on) and the technical pages' solid panels (`main.tech`, D112); the science page's three such scenes (memory fading under a day's cursor with three draining columns, the spacing effect as a landscape in 3D, a topic kept up against left alone) and How the code works under You, beside the science (D113); How it works, the plain page for everyone (`garden.html`, `/garden`, `/p/<plan>/garden`): one story in a garden scrolled like time, plants drawn in WebGL by `garden.js` over a flat drawing in SVG (`_garden.html`, D117): a glowing succulent rosette in a glass bowl when light, a plant of light when dark (D119), each pinned chapter's words beside its picture, on a laptop each chapter's plants large and labelled as they change, the day it is, a line of the chapters along the bottom, and a month the student plans that the server scores (`_garden_month_part.html`, `part=month`, D121), each day of the week a 24-hour dial and a chapter on where each evening goes, a wire from it to the topic's tube (D146), the three explaining pages on the app's plain grey without the sky or the glass (`body.reading`, `main.read`), one layout for all three, every part a row of words and figure, on a laptop each pinned scene one panel held in view, its words and all its steps on the left, the current one lit, its figure on the right (`.code-story` the track, `.code-panel`, D118, D120), the student asking (`_explain.html`), linked first under You, in the footer and atop the technical pages (D116); the student's own avatar, made under You (`avatar.html`, `_avatar.html`, a preview that follows the radios through CSS `:has`, D115); Spark's tour of each section (`_tour.html`), the share forms' course as chips (`_share_course.html`), "It's right", "Suggest a fix" and "Accept the fix" on posts (D141); Edit Today, its switches (`today_edit.html`, `/p/<plan>/today/edit`, D155), each with its widget's picture and saved as it is flipped (`data-autosave` in `ui.js`, D165); the themes and accents in Appearance (`_set_look.html`, `/p/<plan>/palette`, D156); setting up's sentence (`_setup_sentence.html`) and what a pasted platform link brings (`/p/<plan>/deadlines/preview`, `_deadlines_found.html`, D154); the avatar a person made on their posts and profile in Community (`_people.html`, D153); Tasks, Courses, Flashcards and Community as the list and its context on a wide screen (`.with-context`, D162); a course's card On Moodle, its row in the sidebar and under You, the materials on the focus screen (D163); a course's weeks one row each, its exam, credits and links folded and saved as they change, the diary's older weeks one card (D167); only calls `service`; every page works without its scripts |
| `showcase.py` | `cps demo`'s store: five made-up classmates, each with an avatar (D153), with posts, notes (pages drawn as PNGs without a library) and a group, and students added to them (@alex, or one per visitor with `--public`, D31) with weeks of history, a group and flashcards; the sample timetable moved by whole weeks so today is in its eighth week; the semester reported once and copied per student; made through `service`, never a test fixture for a number |
| `cli.py`, `console.py` | `cps inspect` / `plan` / `serve` / `web` / `demo` / `sweep` / `backup` / `metrics`; UTF-8 output hardening |

The hosted app's front end is `web/static/`, in the manner of Apple's interface
guidelines, under calm glass (D73; since D139 one face, Plus Jakarta Sans, shipped in `fonts/`
with its licence, and Lucide's icons drawn inline by `web/icons.py` from `vendor/lucide/` with theirs, a sky that follows the sun behind a plan's pages (D76), glass tinted by default (D132; See-through keeps D73's clear glass), a garden in
`garden/` that grows with the streak), with grouped lists, translucent bars, sheets, switches, the "+"
in the bar, continuous corners where the browser draws them, D37): `style.css`
(tokens, light and dark, seven course colours checked for colour-blind separation;
red is for exams and today), `calendar.js` (the calendar: Day / 3 Days / Week over the whole day (D59), and a Month of six weeks (D79), a strip that scrolls sideways a day at a time (D68), draw,
drag, popovers, titles that fit, AUDIT 39; with no timetable file, a week drawn by
hand, classes first, D32; since D157 cleaner, after Notion Calendar: the month and the week over one menu, each title first in its course's colour with its kind's mark, what is over faded, the courses as switches (the sidebar's "On the calendar" on a laptop), the day's sky beside the hours and the line of now in the hour's colour), `app.js` (the plan screen: the panel is
server-rendered HTML, fetched again after each change) and `ui.js` (every page:
forms sent in the background with the page's own HTML as the answer, the new-task
sheet (its own month and times, a course list with "Other…", D159), Undo, the N key, the sky and the live icon as the sun moves, asked of the server every five minutes, D76), `motion.js` (D30: numbers that count up, swaps carried over
from old values in one view transition, confetti once per achievement; page
transitions and draw-ins are CSS; the calendar slides moved sessions itself; D93:
ambient motion rests six seconds after the last tap or key, and the glass turns solid on a
device that blurs too slowly), `pwa.js` and `sw.js`
(D33: the installable app, the service worker that keeps a plan's pages for offline use and takes the server's copy of the app's files first, AUDIT 67, the
install card; with D38, the reminder shown as a notification; it also remembers the plan
last opened, for invitation links, D40; the graphite icon to install on a dark device, D74), `invite.js` (D40: "Join with my plan" on an
invitation link), `routine.js` (D70: the questions about the week one card at a time with Spark, each time drawn on a bar of the day), `tour.js` (D77: Spark's tour, the page dimmed and each step's part lit; the server says which tour and what it says), `push.js` (D38: turning
reminders on or off on a device), `passkeys.js` (D35: adding a passkey, signing in with one; the server
decides, the script hands the browser's answer back), `map.js` (D133: the study map, Leaflet 1.9.4 shipped in `vendor/leaflet/` with its licence, over OpenStreetMap's tiles, each sending the site's origin only), `garden.js` (D117, D119: How it works' plants in WebGL, a rosette when light and a plant of light when dark, from each scene's data, drawn only when the page scrolls or changes), `errors.js` (D43: loaded first on every page, it sends the scripts' errors to the owner's
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
- The app opens on Today for everyone again (D124; D102 had opened on the feed), checked in
  Chromium only; a home-screen app added under D102 may keep opening on the feed until it is
  added again.
- The memory loop's first slice (D125) is the model's estimate with average weights, said on
  each card: the chance of recall is shown only after the answer, so D55's calibration is not
  anchored; "against the odds" is a recall the model gave under 50 %, a threshold chosen, not
  measured; with a plan that reviews at 90 %, most answers read "as forecast", and the
  surprises come after a lapse or a missed week. Whether the loop keeps students on their plan
  is for the pilot (docs/PRODUCT.md). Checked in Chromium only.
- Studying together (D127): a pair's count and "studied today" give away each one's days
  studied, as a group of two gives away its hours; checked in Chromium only.
- The stakes and a curious start (D128) rest on two laboratory studies (money as the reward,
  trivia as the questions); the 90 % day assumes a recall graded good, with average weights.
  The opening question needs cards in the session's week. Checked in Chromium only.
- Reminders that vary (D129) rotate their lines in turn; Duolingo's study chose templates by
  results as well as freshness, so whether rotation brings sessions here is for the pilot.
  Not tried on a real phone.
- The study map (D133) loads OpenStreetMap's tiles, which see each viewer's internet address and
  the area viewed, with no guarantee of service; places are what students tapped, not checked,
  and cannot be reported yet; a count of one at a small place can tell who is there. Checked in
  Chromium only, with tiles loaded.
- Unannounced moments (D130): a month and three answers are choices; the month is the memory
  model's estimate with average weights. A week with every topic above 90 % was rejected:
  on a plan followed exactly, some topic is always below it. Checked in Chromium only.
- The semester recap (D131) counts what was reported or logged, as Progress does; its memory
  figures are the model's estimate. The share sheet was not tried (headless Chromium has
  none); the copy button was. Checked in Chromium only.
- Repair and pause (D126): the two days and the 25 minutes are choices, not measured; a pause
  moves the sessions it displaces to the days after it, which near an exam means more in fewer
  days. Checked in Chromium only.
- The full check's fixes (D106) were checked in Chromium only, at 320 to 1280px with touch
  emulated, not on a real phone nor with a screen reader. Tap areas are asked of the page
  21px above and below each control: 47 controls on 17 pages still answer over less, where
  controls sit close. The tab bar's labels are 11px under 376px (401px in French), 10px
  under 341px; the rest of the text is 12px or more. Dark mode's buttons keep 4.5:1, darker
  than Apple's blue (D78 kept 3.3).
- The first run (D107) was checked in Chromium only, at phone size with touch emulated and at
  1280px, not on a real phone; Strava's flow is described from teardowns, not tried. It costs
  taps: 10 from the home page to Today (7 before), 16 to a first session saved
  (`benchmarks/screen_load.mjs`); every step after the account can be skipped. Whether it
  brings students back on day 7 is for the pilot.
- How the code works (D109) runs demo.py's small example, whose optimum leaves the week free (five
  evenings cannot reach the target); the page says so. Its figures, and the science page's, draw
  themselves only where the browser ties animations to scrolling (Chrome, Edge, Safari 26);
  checked in Chromium only, at phone size and 1280px. Since D120 the explaining pages have no sky (D110's dusk is gone).
  The 3D (D111) is CSS only and still where motion is off; what a tilting card costs a slow
  device was not measured. The scenes that run as they are scrolled through (D112) need scroll
  timelines too (elsewhere each is complete, its steps a list); the search's disc draws the 114
  nodes AO* opened and the states its best plans reached, not every state it produced. The
  technical pages' panels are solid whatever the glass setting (AUDIT 94). The science page's
  scenes (D113) run the same way, checked in Chromium only; its landscape starts at topics that
  hold 3 days, because FSRS-6 treats a review on the same day apart.
- How it works (D116) is a likeness: the garden's water is the memory model's estimate with
  average weights, and each chapter says where its picture stops. The month to plan (D121) has three
  made-up courses; there and in the cramming chapter a revision works with the chance its recall
  gives, as an expected value (not one student's luck), and since D142 the app's plan is its
  self-test rule and a last pass before the exam, standing for the exam practice it plans (D46),
  each comparison at equal effort. Its sentences are kept under 25 words by a
  test; its wording is the owner's to review, and the Italian was not written by a native speaker.
  Its 3D is WebGL (D117), checked in Chromium only with software rendering, at phone size and three
  laptop sizes, light and dark, with motion and without, the month also without a script; not on a
  real graphics chip, a phone, Safari or Firefox. Without WebGL or a script a flat drawing shows instead. What it costs a slow device
  was not measured. The plants (D119), a rosette when light and a plant of light when dark, and their
  glow and flowers are choices of taste. The explaining pages' layout (D118, D120) was checked in Chromium only, at two laptop
  sizes, four shorter laptop windows (to fit its panels) and a phone's; on a laptop the pages are 10 to 18 %
  longer than D118's, each pinned panel holding the window for four fifths of a screen of scrolling;
  the lit step follows the scene's part, not exactly the window's middle. How it works (D121) is
  17.3 screens long at 1440 × 900 and 12.0 on a phone since D146's chapter on the evenings (15.3 and
  10.7 before it; 23.6 on a phone before D142's scenes stopped pinning there); a chapter's words still fading in as they enter leave a band empty for a moment.
- The evenings redrawn (D146): the dials, the tubes and the card of lights are the owner's choice
  from a page of four designs, drawn from the worked example's week (the same lectures every day,
  one block an evening) and three made-up topics; the tubes are FSRS-6's estimate with average
  weights, every revision a success, on a scale from 80 to 100 % that the figure says. Checked in
  Chromium only, at phone size and two laptop sizes, light and dark, with motion and without.
- The student's avatar (D115, D137, D150) has eight skin tones, twenty-one hair styles (a fade, a mullet,
  curtains, locs, cornrows among them), six eyes, five brows, five mouths, seven things on the head, six
  details (one at a time), six kinds of clothes and a background, a choice and not every person; it
  is shown on the student's own pages, in How it works and, since D153, on their posts and profile
  in Community once saved with a profile (before that, the initial on a colour). Since D143 it is
  drawn in ink, and a flag of where the student is from, which they
  choose (none until then), is a round badge on it and on their initial in Community; the
  countries' names are in English; flag-icons 7.6.0 (MIT) is shipped with its licence.
  Its preview needs CSS `:has` (elsewhere it shows the avatar as saved). Checked in Chromium only.
- Studying now and reactions (D108) were checked in Chromium only, at phone size and 1280px;
  the reactions are the device's emoji. Studying now is off until a student says yes, and seen
  only by their followers; whether Join brings more sessions is for the pilot.
- How an exam went beside its forecast (D105) sets the model's estimate (average weights, a
  guessed starting state) beside a pass, not a mark; checked in Chromium only, with a plan whose
  clock stood after its exams: no demo student has an exam behind them yet.
- A class's link (D104) matches courses by the words of their names, as a program's are; checked in
  Chromium only. Whether whole classes join this way is for the pilot.
- Lighter pages (D103) were measured by `benchmarks/screen_load.mjs` and checked in Chromium
  only; a bookmark to a part of the old Settings page (`…/settings#ai`) reaches its page only
  with the page's script, and without it lands on the list. The pilot switch for extras
  (package C3 of the review) waits for the owner's choice.
- The pictures (D99, D100) were checked in Chromium only. The climb's milestones are a
  choice; a diary session's picture is read by rules from its title's words, a compound
  word missed. Whether the climb motivates more than the weekly columns is for the pilot.
- Cards from notes by Claude (D138) are written and then checked by a second call; both ran
  against a fake provider only (no key here): no real answer has been tried on real notes. At most
  0.023 USD a file with Haiku (`benchmarks/syllabus_cost.py`). Since D151 a PDF of notes is read with its
  words spaced and its mathematics marked (`R^{n}`, `x_{i}`), drawn on cards as superscripts and
  subscripts; tried on one page made by pdfTeX (`tests/data/latex-notes.tex`), not on the owner's file nor
  other TeX engines; fractions and a sum's limits are stacked scripts, and pypdf can hand two limits as one
  piece (∑ with "ni" over "=1").
- Flashcards from a file of notes (D134) are read by rules, for free: prose gives few cards;
  tried on files written by the tests and by LibreOffice, not by Microsoft Office, Google Docs or
  note-taking apps; photos, scans, equations and tables' layout are not read. Checked in
  Chromium only.
- Cards from notes (D84) are read by rules from shapes notes commonly take, not from real
  students' notes; the model's cards, from a note or a course's materials (D101, Haiku 5.5),
  ran against a fake provider only. A classmate's notes reach the model under the reader's
  yes alone (AUDIT 81). Photos of notes are
  not read. A group's notes (D85) were checked in Chromium only.
- Your memory (D82) is the memory model's estimate with average weights, not a
  measurement; recall reps count what was reported. Checked in Chromium only.
- The insights (D80) rest on small numbers, said on each card; their thresholds are
  choices, not results. Starting on time counts only sessions started with the timer,
  from logs written since 2026-10-21. Checked in Chromium only.
- The layout pass (D149): on a laptop the top bar is the sidebar's head, the calendar has no tip
  line, Today's widgets are packed by `app.js` (a grid of 2px rows; without the script, rows as
  tall as their tallest widget). Checked in Chromium only, light and dark; the countries' list fix
  (AUDIT 109) was not tried on Windows, where it was seen. A LaTeX PDF's run-together words the
  owner met are not reproduced here (AUDIT 99).
- Hours for your exams (D147) are the memory model's estimate with average weights, if every
  session is kept; four blocks, a first week, two weeks and a week before the last exam are choices. A last pass
  before each exam was measured (`benchmarks/last_pass.py`) and rejected: on the benchmark semester
  it never fired over 3 to 21 days, and lost a topic over 28. Checked in Chromium only.
- On your mind and steps (D145) rest on two laboratory studies (Masicampo and Baumeister, 2011;
  Scullin et al., 2018), read in their abstracts; whether a list in an app does the same is not
  known. Three hours before bed, three things and forty items are choices. Checked in Chromium
  only, at phone size and 1280px, light and dark.
- Focus and attention from Today (D144): the card's three lengths, its four courses and the
  shortest length chosen are choices; the part of the day is named from four timed sessions in
  four weeks and two per part, thresholds chosen, not measured, and describes the sessions a
  student chose to time; minutes typed by hand are not drawn. Checked in Chromium only, at phone
  size and 1280px, light and dark.
- Themes and accents (D156): the themes' values are the owner's choice of a page of options, the
  accents' chosen and measured (`tests/test_palette.py`, every text at 4.5:1 or more); the flat
  themes' cards are opaque whatever the glass; an installed icon keeps the sky's colours. Checked in
  Chromium only, at phone size and 1280px, light and dark.
- Moodle within reach (D163): the four pages are Moodle 4.5's standard addresses, read in its source, not
  tried on a real university's Moodle; a course is linked by pasting its address once. On the focus screen
  the materials open in another tab, which the focus clock counts as time away (D17). Checked in Chromium
  only, at phone size and 1280px, light and dark.
- The list and its context (D162): two columns from 960px; at 1100px, beside the sidebar, the list is
  about 480px. On Community the side column comes first in the page, so a keyboard reaches Studying
  now before the feed on a laptop too; groups and classmates beside the feed show on a wide screen
  only. This week's time for tasks counts as done only sessions the plan has moved to its past.
  Checked in Chromium only, at phone size and 1000, 1100 and 1440px, light and dark.
- Settings as a dashboard (D161): each card a summary of its part; whether the app is installed is not
  known to the server. Checked in Chromium only, at two laptop sizes and phone size, light and dark.
- The tips' covers (D164, after D160's pictures) are a matter of taste, drawn by hand and checked against
  each tip's advice, not with students; fourteen of 26 tips have a number under theirs, each checked against
  the tip's text. The limits fold one tap away. Checked in Chromium only, at phone size and two laptop sizes,
  light and dark, with the default accent and amber.
- The new-task sheet's month and times (D159) were checked in Chromium only; Firefox and Safari were
  not tried and may show their own calendar button beside the app's. A phone keeps its own wheel.
- Points and proof of work (D158): a vouch is a classmate's word, not a check; two friends can
  vouch for sessions that never happened. The 10 and 20 are choices; whether points bring more
  sessions or more honest reports is for the pilot. Points are private, with no ranking. Checked in
  Chromium only, at phone size and 1440px, light and dark.
- The calendar, cleaner (D157): a title is 40 % of its course's colour (A's 55 % failed on Soft
  day); the ribbon is today's sky beside every day shown, for the time zone's main city; the kinds'
  marks are a choice; courses hidden are kept on the device only. Checked in Chromium only, at
  phone size and two laptop sizes, light and dark.
- Edit Today saves each switch as it is flipped (D165): a flip made just before leaving may be lost
  if the answer has not come back. Checked in Chromium only.
- Lighter pages (D167): a course's page is 4.6 screens at phone size instead of 5.9; Progress's diary is
  tidier, not shorter. The exam's fields are saved as they change, as on Edit Today. Checked in Chromium
  only, at phone size.
- Edit Today (D155): five widgets to start, the owner's choice from a mock-up that did not list This
  week or Flashcards; both start off, so the streak's strip is on Progress until turned on. Checked in
  Chromium only, at phone size and 1280px.
- Today as home (D140): its widgets and their order are choices, longer than D97 left Today; the
  classmates' window shows two posts; "Yours" is the hours awake less the timetable and the plan,
  meals and travel in it; attention counts sessions timed with Focus only; "Open Moodle" shows only
  when the deadlines link is a Moodle calendar export; checked in Chromium only, at phone, tablet and
  laptop sizes.
- Spark's tour in every section and peer checks (D141): each section's tour shows once, with its
  line, even if left half way; "It's right" is a reader's word, not a verification, and readers who
  share a mistake can check it. Checked in Chromium only.
- The look (D139): one face and Lucide's icons (0.460.0, ISC, shipped with the licence), the tiles'
  hues a choice of taste; checked in Chromium only, light and dark.
- The sidebar (D136) shows from 1100px and was checked in Chromium only, light and dark; a
  phone keeps the tab bar.
- The tips in context (D135) are chosen by rules from the plan, one a page; their lines tie
  research done elsewhere to the plan, and each says what it does not show. Checked in Chromium
  only.
- The tips (D62) rest on abstracts and database records, not full texts; their wording is the
  owner's to review, and a tip may cite only a checked reference (`tests/test_tips.py`).
- AI (D12), the coach (D65) included, has run only against a fake provider: no model answer
  has been tried on real sentences. With no `ANTHROPIC_API_KEY`, the rules answer, as they do whenever
  the model is off, over the cap or wrong.
- Moodle deadlines (D148) are read in eight languages, from Moodle 4.5's own strings
  (English, French, Italian, Spanish, German, Portuguese, Brazilian Portuguese, Dutch), on
  exports written for the tests, not a real university's; a site's own changed strings, and
  other languages, are not read. An event a person named "… closes" (or the same in another of
  the eight) is taken as a deadline. Their hours are a guess (2 h) until the student changes
  them. Tasks typed in one line are read by rules in English, French and Italian only. The
  Italian (D34) was not written by a native speaker.
- Offline and install (D33) were checked in Chromium only, not on an iPhone. So was the
  calendar's sideways strip (D68), with touch emulated.
- Calm glass (D73) was checked in Chromium only, with phone sizes emulated; browsers
  without backdrop blur show the glass as a translucent fill.
- Calm glass (D132): which effect made the owner feel sick was not isolated; the tint and the
  removed movements are the likely causes. Nothing on the app's pages moves with the scroll
  (`tests/test_tinted_glass.py`); the explaining pages keep their scenes with motion on.
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
  pilot to say. Words still to approve are listed in D97.
- Setting up as one sentence (D154): its choices are around where most students' days fall;
  Moodle's words are those of Moodle 4.5's English, French and Italian packs, the app's
  language, not necessarily the student's Moodle's. Without `::details-content` each value sits
  on its own line. A student who adds nothing keeps the defaults, and Today's questions about the
  week come from the second day. Checked in Chromium only, at phone size with touch and 1280px.
- Reminders (D38) need the server awake: on a host that sleeps when idle (the free
  demo), they arrive only while it is awake. A push was accepted by Google's push
  service and shown by the service worker in Chromium; not tried on a real phone.
- Plans are deleted 12 months after the last visit (D36); the feed being read does
  not count. Between two semesters, Progress waits for the new semester's first exam
  or deadline; the past sessions are kept.
- FSRS weights are population defaults; a subject's starting stability and
  difficulty are user-supplied guesses. The target and the 40-block failure penalty
  are stated choices.
