# The product: a study assistant for busy students

*Where this is going, what exists, what does not, and which decisions are the
owner's. Market facts are sourced; everything about what people would pay for is a
hypothesis to test, and is written as one.*

## In one sentence

Paste your university timetable's link, add your deadlines, and get a realistic
study plan in the calendar you already use: one that meets your deadlines, prepares
each exam, fits the hours you choose, and changes when your week does.

## Why this and not Motion

Motion auto-schedules tasks: it takes each task's duration, deadline and priority,
fits them into free time around calendar events, reschedules when something
changes, and flags tasks that will miss their deadline ([Motion help: how
auto-scheduling works](https://www.usemotion.com/help/time-management/auto-scheduling/reference-auto-scheduling/how-auto-scheduling-works-behind-the-scenes)).
Pro costs $19 a month billed monthly ([Alfred, Motion pricing 2026](https://get-alfred.ai/blog/motion-pricing)).
Reclaim does similar work with a free tier and a student discount
([Reclaim pricing](https://reclaim.ai/pricing)).

Both are built for work. A student's semester has structure they know nothing
about, and this project reads it:

| | Motion, Reclaim | this project |
|---|---|---|
| where the week comes from | you type tasks | the university timetable's link: lectures, exams, cancellations, changes |
| deadlines | yes | yes, earliest-deadline-first, at-risk warnings |
| exams | a task like any other | found in the timetable; exam practice in the last two weeks |
| learning | not modelled | each week of lectures is material to keep; self-testing when it fades |
| what a session says | the task's name | what to do: recall first, then check; a timed past paper |
| a week you can live with | working hours | a weekly study budget and days off |

Student planners (MyStudyLife, Shovel, Ahead) organise classes and deadlines;
flashcard tools (Anki, RemNote's exam scheduler) time reviews of cards. In a web
search on 2026-09-29, none of them read a university timetable to plan study time
around it; that is a search, not a proof.

## What exists today (version 0.4, unreleased)

- Reads a timetable from a file or a link (ADE, Hyperplanning, Google's secret iCal
  address), finds exams and each course's lectures, skips cancelled classes.
- A week calendar to drag in training, commutes, work and time off.
- **The assistant** (`src/cps/assistant.py`): deadlines, a weekly hours budget, days
  off, exam practice, self-testing on fading material, free time left free; a
  semester in about a hundredth of a second; says what to do in each session and
  which deadlines are at risk.
- **The hosted app** (`cps web`, `src/cps/web`): setup from a link in a few screens,
  a phone-first Today page and week, a calendar feed whose events say what to do and
  link to a one-tap report (done, skipped, hard) that changes the plan at once, a
  privacy page, deletion on request and 30 days after the last exam. Built and
  tested; not deployed yet (`docs/ROADMAP.md`, checkpoint C1; `docs/DEPLOY.md`).
- Deadlines from Moodle's calendar link, kept in step with it.
- The research planner (FSRS value functions and AO*) behind a switch in the
  Streamlit workbench, with the benchmark that shows why the product does not need
  it (AUDIT.md item 36).

## What does not exist, in the order it matters

1. **Running somewhere.** The hosted app is ready for a small host in the EU; the
   account, the payment and the privacy notice are the owner's (C1, C2).
2. **Evidence.** No student has used it. The pilot below is the next step.
3. **Writing into Google Calendar directly**, instead of a subscribed feed that
   Google refreshes on its own schedule. Needs Google's verification of the app,
   because calendar scopes are sensitive
   ([Google: sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)).
4. **Capturing tasks in words** ("stats report, due the 20th, about six hours"),
   with a language model: a provider, an API key and a cost per student.
5. **Other learning platforms**, and Moodle in languages other than English.

## What people might pay for (hypotheses)

- Never missing a deadline, with a warning early enough to act on.
- Setting up a semester in two minutes from a link, instead of typing it.
- A plan that stays right when the week changes, without redoing it.
- Evidence it works, from the pilot below, which no competitor above publishes.

Pricing is not decided. Motion's price is a ceiling for working professionals, not
a reference for students; a free core with a paid tier (direct calendar sync,
feedback-driven replanning, task capture in words) is one shape to test.

## How to find out

A pilot with 10 to 20 students at PSL through the January exams: the share of
planned sessions done, deadlines met, how often the plan was changed by hand, and
what they would pay, asked before and after. That is the evidence this project's
standards ask for, and the only kind that answers whether people would pay.

## Decisions that are the owner's

- **Hosting**: which provider, a domain, who operates it.
- **Personal data**: timetables, tasks and study history are personal data under
  the GDPR; a privacy notice, where the data lives, how it is deleted.
- **Name, brand and price.**
- **A language-model provider**, if task capture in words is built.
- **Opening it to others**, and when.

## Risks, stated

- Motion, Reclaim or a university timetable vendor could add the student half.
- Students' willingness to pay is untested.
- Timetable exports differ between universities; ADE is the one tested, on one
  real export.
- The memory model is fitted on flashcards; the assistant only uses it to rank what
  is fading, and makes no promise about stability.
