# Roadmap to the pilot

Validated by the owner on 2026-09-29. Work items are done in order, one pull request
per item or pair, merged when CI is green. The owner is asked only at the
checkpoints marked **(owner)**. `docs/PRODUCT.md` says why; this page says what.

## Decisions

| | decision |
|---|---|
| D1 | The hosted app uses FastAPI, uvicorn and Jinja2 (plus python-multipart, which FastAPI needs for forms), in an optional `web` extra. The core does not depend on them. |
| D2 | Storage is one SQLite file on the server's disk. |
| D3 | Hosting on Render, Starter plan, EU region. Prices re-checked at sign-up. |
| D4 | The pilot runs on the provider's free subdomain; a domain comes with a name. |
| D5 | No passwords for the pilot: each student has secret links, like a calendar subscription. |
| D6 | The owner is the data controller; data stays in the EU; a "delete my data" button; deletion 30 days after the last exam; no trackers. |
| D7 | The product name can wait. |

## Phase 1: the hosted product

- [x] **W1 Storage.** SQLite for students' plans, settings and reported sessions.
- [x] **W2 Web app.** One FastAPI app for pages, feeds and feedback; security headers,
      rate limits, no secrets in logs.
- [x] **W3 One-tap feedback.** Done, skipped, struggled, from each calendar event and
      from the Today page, confirmed by a second tap; the plan changes at once.
- [x] **W4 Today page.** Phone first: what's next and what to do, the week, deadlines
      at risk, add a deadline.
- [x] **W5 Two-minute setup.** Timetable link, courses and exams found, deadlines,
      hours and days off, then the feed link and the Today link.
- [x] **W6 Privacy.** A privacy page, deletion on request and after the exams.
- [x] **W7 Deployment.** Render blueprint, health check, settings from the
      environment, daily backup, click-by-click steps.
- [x] **W8 Moodle deadlines.** A learning platform's calendar export becomes tasks.
- [x] **W9 Tests, browser run, documents.**
- [ ] **C1 (owner)** Create the Render account, connect the repository, set two
      settings, go through setup on a phone with one's own timetable.
- [ ] **C2 (owner)** Approve the privacy notice; two or three friends try it.

## Phase 1b: a reason to come back every day

Decided by the owner on 2026-10-08 (DECISIONS.md, D8 to D15; docs/STRATEGY.md).

- [x] **E1 Progress and a forgiving streak.** A Progress tab, the week's ring, the
      streak on Today (D8, D9).
- [x] **E2 The weekly review.** Last week in numbers, a paragraph and one suggestion.
- [x] **E3 French.** The app in French and English, chosen per plan (D10).
- [x] **E4a Focus sessions and the diary** (D17, D18).
- [x] **E4b The study network**: profiles, follows, feeds, kudos, comments, "explain it
      simply" (D16, D19).
- [x] **E4c Moderation**: report, hide, block, the owner's review page (D20).
- [ ] **C4 (owner)** Set `CPS_ADMIN_TOKEN` on the host and read `/admin/<token>` at
      least once a day during the pilot (docs/DEPLOY.md).
- [x] **E5 AI.** Tasks typed in words and the review's paragraph, opt-in, capped (D12).
      (owner: an Anthropic API key and a monthly cap, `CPS_AI_MONTHLY_CAP_USD`.)
- [x] **E6 Pilot metrics.** `cps metrics` from the event log (D15).
- [x] **E7 Analytics** (owner, 2026-10-08: "more analytics, people love to see
      trajectory"): docs/ANALYTICS.md; Trends with the exam forecast, hours, sessions
      kept, study load, courses, parts of the day, focus; each self-test's gain on
      exam day (D22 to D24).
- [x] **E8 Appearance**: Automatic, Light or Dark per plan (D21).
- [x] **E9 The recall question**: a self-test asks how much was recalled; FSRS's four
      grades feed the plan and the forecast (D25).
- [x] **E10 The notes library**: own notes by course, "helpful" marks, the month's
      top three per course, a line on the profile, no money (D26). Before it opens to
      the public (owner): a lawyer's view of students' notes and copyright in France.
- [x] **E11 "In simple words"** inside the session log (D27).
- [ ] **Proposed, for the owner to choose** (docs/ANALYTICS.md §3): flashcards; a
      group of friends with a shared weekly goal.
- [ ] **C3 (owner)** Run `python benchmarks/reviews.py` and keep its output in
      docs/MARKET.md §4.

## Phase 2: once the pilot runs

- [ ] **W10 Writing into Google Calendar directly.** (owner: a Google Cloud project and
      an OAuth client; later, Google's verification.)
- [ ] **W11 Deadlines typed in words.** (owner: a language-model provider, an API key,
      a monthly spending cap.)
- [ ] **W12 Payments**, only if the pilot shows people would pay. (owner: Stripe, prices,
      a legal status to invoice.)
