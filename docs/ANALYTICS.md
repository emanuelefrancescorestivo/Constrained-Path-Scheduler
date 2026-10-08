# Analytics that motivate, the owner's notebook, and a notes library

*Written on 2026-10-08, after the owner asked for "a light mode and more analytics:
people love feeling in control, especially of their progress, and love to see
trajectory", for a look at a page of their notebook ("Strava 4 studying"), and for a
discussion of a section where students upload notes and are rewarded when they are
well rated. The decisions this leads to are D21 to D24 in `DECISIONS.md`. Every
source below was found by web search and read as an abstract or a secondary summary,
not in full; `docs/REFERENCES.md` says so for each.*

## 1. What the evidence says about showing people their progress

**Monitoring progress helps, most when it is recorded.** A meta-analysis of 138
randomised studies (19,951 people) found that interventions that prompt people to
monitor their progress raise goal attainment (d = 0.40), that they work through more
frequent monitoring, and that the effect is larger when progress is physically
recorded or reported [ref:harkin2016]. Most of those studies were about health
behaviour, not study. For this product: a student who sees, week after week, what
they did against what they planned is doing exactly that kind of monitoring; the
diary and the reports are the recording.

**People speed up near a goal.** In a café loyalty programme and an online rating
site, effort rose as the reward came closer, and an illusion of progress (two free
stamps on a longer card) made people finish sooner [ref:kivetz2006]. For this
product: show the distance to a near goal ("2 sessions to finish the week"), which
the ring already does. Never fake progress: no bonus stamps.

**Counting can cost enjoyment.** Across six experiments, measuring an activity
(walking, reading, colouring) made people do more of it and enjoy it less, and
lowered their wish to continue [ref:etkin2016]. For this product: numbers are a
mirror the student opens, not a counter running while they study. The diary leads
with what was done (notes, photos, a sentence) and the numbers come second.

**Tangible, expected rewards undermine interest; feedback does not.** A meta-analysis
of 128 experiments found that tangible rewards given for doing, finishing or doing
well at a task lowered later free-choice interest in it, while positive verbal
feedback raised it [ref:deci1999]. The finding has been disputed for decades, but it
is the reason this product gives recognition (kudos, "I got it", a full week) and no
points that buy anything (`docs/STRATEGY.md`, D13).

**A load measure from effort and minutes exists, in sport.** Coaches score a training
session as its perceived exertion (0 to 10) times its minutes, the session-RPE
method, which tracked a heart-rate method across cycling and basketball
[ref:foster2001]. Strava's Fitness & Freshness turns daily load into a long-term and
a short-term average and tells athletes to read the trends rather than the absolute
numbers [ref:strava-ff]. For this product: "study load" (minutes × perceived effort)
is the honest form of the "score" in the owner's notebook. It describes how much
and how hard a student worked; nobody has shown it measures learning.

**Feeling that you know is not knowing.** Learners judge what they have learned
while the material is in front of them, and so feel more prepared than a later test
shows [ref:koriat2005]. Practice tests beat rereading and the other conditions they
were compared with, across a meta-analysis of many studies [ref:adesope2017], and the
best gap between study sessions grows with how long the material must be kept
[ref:cepeda2006]. For this product: "perceived progress" is a feeling worth
recording, not a measurement. The reliable measure is recall on a self-test, and the
memory model already turns sessions into a predicted recall on exam day.

## 2. What the analytics show, and what they do not

Ranked by what they give a student for the effort of reading them:

1. **Where you are going: the exam forecast.** For each exam, the predicted recall on
   exam day if the plan is followed, against today's if nothing more were done, from
   the FSRS model and the sessions reported (D24). It is the number the notebook's
   "revise today, +22 %" asks for, computed instead of invented, and labelled as a
   model's estimate with population-average memory.
2. **The trajectory: hours studied per week**, the last weeks against your own
   4-week average and your weekly limit. Strava's advice applies: the trend, not the
   total.
3. **Keeping the plan**: the share of planned sessions done, week by week.
4. **Study load**: minutes × perceived effort of the sessions you logged, per week,
   with its 4-week average (D23).
5. **Balance between courses**: hours per course over the period.
6. **When you keep your sessions**: morning, afternoon, evening, the share done.
7. **Focus**: timed sessions, and those with the focus checked.

Every chart has a table under it, its numbers in words, and works without scripts.
A range (4 weeks, 12 weeks, the semester) scopes all of them at once.

**Not shown, on purpose:**

- **A percentile against other students, or any ranking of people.** Comparison is
  what leagues sell, and what this product rejected in D16: it rewards hours, and
  with a pilot of 20 the percentiles would be noise.
- **A single opaque score.** A number made of other numbers (hours, effort, streak)
  cannot tell a student what to change; the parts can.
- **Precision the data does not have.** "84.3 %" from a model with population
  weights and guessed starting states is false precision; the forecast is rounded to
  5 % and says it is an estimate.
- **Minute-by-minute surveillance.** No screen-time graphs, no "you left the page at
  14:32": the focus check stays a line on the session.

## 3. The owner's notebook, item by item

The page reads: log a study session (30 minutes of analysis), a photo of notes or
exercises, perceived effort and perceived progress as a score, explain what you did
in simple words, then points, a streak, recognition and momentum; a network with
friends in other degrees and from other places; an AI "study smart scheduler" with
tips (study after working out because memory is boosted 10 %; eat certain foods for
more memory; your exam is in 17 days, revise today for a +22 % boost; flashcards,
puzzles, leagues, and how to measure reliably); and a calendar that updates live,
with few API calls, or a robust, flexible and efficient algorithm.

| item | status | what to do |
|---|---|---|
| log, photo, effort, progress | built (D17, D18) | — |
| "explain what you did in simple words" | built as a separate post (D19) | add an optional "In simple words" line to the session's own log, so it costs no second step (proposed) |
| score | not built | study load (minutes × effort) and its trend (D23, this step); no points |
| points | rejected (D13) | recognition instead: kudos, "I got it", milestones |
| streak | built, forgiving (D9) | — |
| recognition | kudos, comments, "I got it" | a monthly "helpful" list of notes if the notes library is built (§4) |
| momentum | not built | the 4-week trend of hours and load (this step) |
| network across degrees and universities | built (D16), Explore filters | — |
| tip: study after working out, +10 % | evidence exists, the number does not | acute exercise improved memory in a meta-analysis (a moderate effect on long-term memory, smaller on short-term memory, mixed results by timing and intensity) [ref:roig2013]; a tip without a percentage, and an option to place a session after a busy time marked "training" (proposed) |
| tip: eat foods or creatine for memory | **not recommended** | creatine's effect on memory in a meta-analysis of 8 small trials was small, varied a lot between trials and was clearer in older adults [ref:prokopidis2023]; supplement and diet advice to students as young as 15 is health advice this product should not give |
| tip: "exam in 17 days, revise today, +22 %" | **built in this step** | the exam forecast and, on each session, what it adds to exam-day recall (D24) |
| flashcards | not built | the memory model is a flashcard scheduler (FSRS); flashcards are a feature of their own (an editor, a review screen), the next big candidate |
| puzzles | not recommended | no evidence they help exam recall more than self-tests |
| leagues | rejected (D16) | a cooperative alternative: a group of friends with a shared weekly goal, nobody ranked (proposed) |
| how to measure reliably | partly | the reliable signal is recall on self-tests: after a self-test session, "how much could you recall: none, some, most, all", which maps onto FSRS's four grades and makes the forecast measured instead of assumed (proposed, the most valuable next step) |
| live calendar update, few API calls | built locally | every report replans at once on the server with no API call (the rules are local and fast); the feed changes at once, but Google Calendar re-reads subscriptions on its own schedule, often hours apart; writing to Google Calendar through its API (W10) needs a Google account and OAuth, the owner's decision |

## 4. A notes library with rewards for top-rated notes

**What it would be.** A course page where students post their own notes or summaries
(photos or a PDF), others mark them "helpful", and the most helpful ones are
recognised or rewarded.

**Why students would use it.** Studocu reported 15 million users in 2021, with access
to premium documents bought or earned by uploading [ref:the2021].

**What stands in the way.**

- **Copyright.** University and union guides found by search say that in France a
  lecture is a protected work and that university teachers keep the rights to their
  teaching material; the legal texts themselves were not read, and a lawyer must
  confirm this before a library is built. Slides, handouts and exam papers posted without consent are what
  lecturers complained about on Studocu [ref:the2021]. A student's own notes and
  summaries are a grey zone, safer when they are clearly the student's own words and
  sketches. The library would need: own work only, no lecturer material, a report
  reason "copyright", and a takedown route (the moderation of D20 already exists).
- **Academic integrity.** Graded coursework and exam answers must stay out; the
  guidelines already say so.
- **Rewards.** Money means payments, tax and fraud, and pays for volume. Points that
  buy things are a currency (rejected in D13), and expected tangible rewards lower
  interest in the activity they reward [ref:deci1999]. Recognition does not have
  that cost.

**Options.**

| option | what | cost | risk |
|---|---|---|---|
| A. none | photos stay inside sessions and explanations, as now | none | none |
| B. notes with recognition | a "notes" kind of post, by course; "helpful" marks; a course page with the month's most helpful notes (notes ranked, not people); a profile line "3 notes marked helpful" | small: posts, kudos and moderation exist | copyright reports to handle |
| C. B plus perks | when the paid tier exists, a free month for notes in a course's top three | medium | turns notes into a way to earn; gaming |
| D. money | paying authors | large: payments, tax, KYC | fraud, copyright, a marketplace to run |

**Recommendation: B**, with own-work-only rules, a copyright report reason and the
owner's review, because it uses what exists and keeps rewards as recognition.
C can wait for the paid tier and the pilot's evidence. The owner decides.

## 5. Built in this step

- **Appearance**: Automatic, Light or Dark, per plan, in Settings (D21).
- **Trends**: a Trends page and a trajectory card on Progress: the exam forecast,
  hours per week with the 4-week average and the weekly limit, the share of sessions
  kept, study load, balance between courses, when sessions are kept, focus (D22 to
  D24).
- **On each self-test**: what it adds to the predicted recall on exam day (D24).

Proposed next, for the owner to choose: recall on self-tests (§3), the notes library
(§4), "in simple words" inside the session log, flashcards, a cooperative group goal.
