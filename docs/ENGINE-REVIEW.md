# Engine review: the nine weak spots, checked against the evidence

*13 October 2026. The owner asked for a plan "to tackle weakness by weakness after
crossvalidating the problem against the backdrop of the latest breakthroughs in
neuroscience of learning", and whether new decision-making models such as jev.ai
could streamline the engine. The same plan is kept as a shared document; this copy
is the repository's.*

All nine weak spots hold against the code, seven are backed by the literature, and
two rest on product sense more than on evidence (W5, W7). They are fixed in four
phases, smallest first. Every change must match or beat the assistant's row of
`benchmarks/rule_vs_planner.py` (174 sessions, 51 of 69 topics predicted at 90% or
more at their exam, with 15 hours a week, Sundays off and 4.5 hours of exam practice)
and `benchmarks/semester.py`, run before and after, or say what it trades and why.

## How each weakness was checked

1. **The code**: what the engine does today (`assistant.py`, `plan.py`, `service.py`).
2. **The evidence**: sources opened and read on 13 October 2026, not search snippets.
   Meta-analyses and randomised trials count for more than single lab studies; a
   press release is used only for a study's design and headline numbers, and says so.
3. **A measure**: a benchmark or a test, before and after.

Verdicts: **confirmed** (the code does it and the evidence says it costs learning),
**partly** (real, but the evidence is weak or mixed), **product** (real, but a matter
of what students will follow).

## What the science of learning says now

| Finding | Source | For the engine |
|---|---|---|
| Spacing beats massing inside single cells: four spaced pulses gave stronger, longer CREB and ERK signalling than one massed pulse, in non-neural human cell lines | [ref:kukushkin2024] | Spacing is a basic property of memory; keep it the centre. A reporter in a cell line is not a student: the principle, not a number. |
| The best gap between two sessions grows with the time to the test: about 20–40% of a one-week delay, 5–10% of a one-year delay (1,350+ people) | [ref:cepeda2008] | Gaps should stretch as the exam is further away, as FSRS's intervals already do. |
| Practice tests beat restudying and every other comparison condition | [ref:adesope2017], abstract | Keep "recall first, then check" in every session. |
| Most students reread; few test themselves; they feel they know what they do not (177 students) | [ref:karpicke2009] | A student's confidence is optimistic: trust a self-test's answer, never silence (W4). |
| Sleep actively consolidates new memories (replay during slow-wave sleep) | [ref:rasch2013] | Reviews spread over days, nights between; the cap on sessions a day stays. |
| Relearning after a night's sleep took about 3 trials instead of 6; a week later about 15 of 16 pairs were recalled instead of 11 (40 adults) | [ref:mazza2016], via the APS press release | A first self-test the day after a lecture is well founded. Small study: a direction. |
| Exercise four hours after learning improved recall two days later; straight after, it did not (72 adults) | [ref:vandongen2016], via Harvard Health | Training after study is no problem. |
| About 60% of 14,894 students were "jet-lagged" by class times, and the mismatch tracked lower grades (correlational, one university) | [ref:smarr2018] | Evening people pay for mornings (W1). The lab evidence on time of day is mixed and small ([ref:bettencourt2024], its review of the literature): a preference, not a rule. |
| A test before learning helps: classroom studies report about 12–13 points higher on later exams | [ref:pan2023], via [ref:sana2023] | An idea: an opt-in warm-up quiz before a lecture (B4). |
| Mixing problem types beat blocking them, 62 against 38 on a delayed test (787 pupils, randomised) | [ref:rohrer2020], via the What Works Clearinghouse | Exam practice should be mixed (W6). Grade-7 maths: transfer likely, unproven. |
| If-then plans raise goal attainment, d = 0.65 over 94 tests | [ref:gollwitzer2006] | A plan the student can follow beats an ambitious one (W7). |

## The weak spots

**W1 · Sessions land at the start of the day. Partly.** Free time is cut earliest
first (`plan.tile_free_time`). Change: "When do you study best?" (morning, afternoon,
evening, no preference) fills each day from that end; 30 minutes free after
training and work. Measure: the benchmarks within error bars for each preference; a
test that an evening preference lands after 17:00 when free time exists there.

**W2 · Short gaps are wasted. Confirmed.** Every block is 90 minutes. Change: quick
self-tests of 30 or 45 minutes in gaps of 30 to 89 minutes; the weekly budget in
minutes; on the commute only if the student says they can study on the way. Measure:
a semester with gaps between classes.

**W3 · It does not learn from the student. Confirmed.** FSRS-4.5 with population
weights. The open benchmark ([ref:srs-benchmark], about 350 million
reviews from 9,999 collections, without same-day reviews; log loss, lower is better):
FSRS-4.5 0.3625, FSRS-6 0.3460; FSRS-7 with default parameters 0.3620, fitted to the
person 0.3401; LSTM 0.3332. Change: (1) FSRS-6 with default weights, the reference
trajectory re-pinned (invariant 9); (2) each student's own parameters through
py-fsrs's optimizer (MIT, [ref:py-fsrs-2026]) once they have enough answers, a threshold to measure.
Needs the owner's approval of a dependency.

**W4 · Silence counts as success. Confirmed.** An unreported session is replayed as
done and recalled. Change: unreported after a day counts as unknown; the topic's
memory is not raised; the forecast says how many sessions it is unsure of; Today
keeps asking. Measure: a silent week leaves the forecast lower than a reported one.

**W5 · Every exam weighs the same. Product.** Change: an optional weight per exam,
used only to choose between topics fading at the same time; no course left without
self-tests.

**W6 · Exam practice is the same for every exam. Confirmed.** 4.5 hours in the last
14 days, one course per session. Change: 1.5 hours per three weeks taught, at least
3, changeable per exam; exams within a week alternate; each session asks to mix
question types.

**W7 · The weekly budget does not adapt. Product.** Change: after three weeks under
70% of sessions kept, offer a budget equal to what was kept, with its cost at the
exams; changed only on the student's tap.

**W8 · A topic is a week of a course. Partly.** Change: a week weighted by its hours
of lectures (over about three: two blocks or two topics); a poor self-test asks for
a longer session next time.

**W9 · Nothing has met a real semester. Confirmed.** Every number is the model's
prediction on a synthetic semester. The pilot measures sessions kept, each
self-test's answer against its predicted recall, and, with consent and a privacy
line, a two-question survey after the exams. A pilot of 10 to 20 has no control
group: counts to read, not rates to test.

## New decision-making models

**Jev** (TypeSafe AI, early access since September 2026) is a hosted model that
returns typed decisions (a choice, a score, a yes/no probability) instead of text
([ref:typesafe-jev]). TypeSafe's own list of limits for Jev 1.13, as reported by
Firecrawl ([ref:firecrawl-jev]): weak at maths
and counting, reads dates as text rather than ordered values, less accurate on
multi-step questions. The engine's decisions are exactly dates, sums of hours and
several steps; they take a hundredth of a second for a semester; and a hosted API
would send students' timetables to a third party (D6). Not for the engine. One
narrow trial is possible later, if the pilot shows the rules failing to tell online
classes from classes on campus (the limit of D42): opt-in and capped like D12, with
the owner's approval.

What would streamline the engine more: **CP-SAT** (Google OR-Tools, Apache 2.0,
[ref:or-tools]), one
model of hard limits and weighted preferences in place of a growing list of
placement rules, to be tried against the rules on the benchmarks; and FSRS's own
**optimizer** (W3). Neural memory models predict best on the benchmark but need far
more data per student than a semester gives, and cannot explain a session in a
sentence. Claude stays where language is the job (D12).

## The plan

One commit per item, one pull request per phase (the work happens on one branch);
each item with its decision entry, tests, French and Italian, docs and counts, and
the benchmarks before and after.

**Before Phase A**

- [x] Merge PR #18 (D42, D43)
- [x] This document, and a phase in `docs/ROADMAP.md`
- [x] The baseline: `benchmarks/rule_vs_planner.py` (the assistant: 174 sessions, 51 of 69 topics at 90% or more) and `benchmarks/semester.py` (222 sessions, 63 of 69 at target)

**Phase A · Rules only**

- [x] A1 · W4: unreported sessions count as unknown
- [x] A2 · W1: a preferred time of day; 30 minutes after training and work (D45: mean recall at the exams the same; evening 45 of 69 topics at 90% instead of 51)
- [x] A3 · W6: exam practice sized per course, alternating, mixed (D46: 1.5 hours per four weeks taught, not three, so a twelve-week course keeps 4.5; the benchmark unchanged)
- [x] A4 · W7: a weekly budget offered from what is kept (D47: offered on Today, taken only on a tap)
- [x] Gate A: benchmarks within today's error bars, CI green, the owner approves (PR #19, merged 14 October)

**Phase B · New session shapes**

- [x] B1 · W2: quick self-tests in gaps; the budget in minutes (D48: 45 minutes in an hour's gap, one a day, and on the way on request; 53 of 69 topics at 90% instead of 51, partly the model's)
- [ ] B2 · W5: an optional weight per exam
- [ ] B3 · W8: weeks weighted by lecture hours
- [ ] B4 · An opt-in warm-up quiz before a lecture
- [ ] Gate B: the same, plus pages tried at phone and laptop widths in three languages

**Phase C · A memory model that learns** (needs the owner's approval of `fsrs[optimizer]` and `ortools`)

- [ ] C1 · W3: FSRS-6, reference re-pinned, every number re-run
- [ ] C2 · W3: per-student parameters above a measured threshold
- [ ] C3 · CP-SAT placement trial against the rules
- [ ] Gate C: the same, plus log loss of predicted against reported recall

**Phase D · The pilot**

- [ ] D1 · W9: predicted against reported recall in `cps metrics`
- [ ] D2 · W9: the post-exam survey, with consent and a privacy line (owner's approval)
- [ ] D3 · Read the results with the owner

## Sources

Every source above is an entry of `docs/REFERENCES.md`, under the engine review,
with the page that was read for it and the date, or why it could not be read.
