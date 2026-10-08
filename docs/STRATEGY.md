# Product and engagement strategy

*What makes a student open this every day, and what it refuses to do to get
there. The market evidence is in `docs/MARKET.md`; the decisions in `DECISIONS.md`;
the screens in `docs/DESIGN.md`. Every target below is a hypothesis for the pilot
to confirm or kill.*

## The job, and the daily question

A student hires this to **keep up with the semester without thinking about how**.
Each day that becomes one question: *what do I study today, and am I on track?*
The product is good when that question takes ten seconds to answer, and the answer
is true.

## The loop

| step | what happens | what exists |
|---|---|---|
| **cue** | a calendar event at the session's time, in the calendar the student already checks; the event says what to do | yes (the feed) |
| **action** | study, then one tap: done, hard, or skipped, from the event or from Today | yes (reports) |
| **reward** | at once: the day counts, the week's ring fills, the streak grows by one, the plan adapts; on Monday, last week in a paragraph | **this stage** (progress, streak, review) |
| **investment** | each report makes the plan fit better (a hard topic returns sooner); tasks added; a friend who will see the week | reports exist; **buddies, task capture in words: this stage** |

This is the shape of habit-forming products in general (cue, routine, reward,
investment). What is particular here is that the cue is not ours: it is the
student's own calendar, already a daily habit, and an event that says *when and
where* to act, which is what implementation intentions are (d = 0.65 across 94
tests, Gollwitzer and Sheeran 2006). Notifications of our own are deliberately absent
(D14).

## What this stage adds

1. **Progress** (D8). A Progress tab: the week's ring (sessions done of those
   planned), the streak and the best one, a calendar of the last twelve weeks, each
   exam's readiness, tasks finished, and a few milestones (first session, three-,
   seven-, fourteen- and thirty-day streaks, a full week). On Today: the streak and
   the ring, small, above the next session.
2. **A forgiving streak** (D9). Studied days count, rest days are neutral, one
   missed day a week is forgiven, a late report repairs a day, a lost streak is
   never announced.
3. **The weekly review.** Last week in numbers and one paragraph, with one
   suggestion chosen by rules from what happened (sessions skipped at the same time
   of day, a week much heavier than what was done, an exam close). Written by the
   model when AI is on (D12), by a template otherwise.
4. **Study buddies** (D11). Up to five friends, invited by code; each sees the
   other's streak, the week's ring, whether they studied today, and can cheer once a
   day.
5. **Tasks typed in words** (D12). "rapport de stats pour vendredi, 6 h" becomes a
   task in the new-task sheet, to check before saving.
6. **French** (D10). Every page in French or English; the plan remembers the choice.

## Rules we keep, because the alternatives work and we will not use them

- **No guilt.** No message about a lost streak, no sad mascot, no "you broke it".
  When a streak ends, the page shows the best streak and the next milestone.
- **No currency, no store, no loot.** Nothing to buy, earn or spend.
- **No public ranking.** Buddies see each other's own progress, not a league table.
- **Nothing rewards studying beyond the plan.** The ring fills at the planned amount;
  extra hours earn nothing; the plan's weekly limit stays the student's.
- **No rating prompts, no countdown offers, no trial that turns into a charge.**
- **Progress is never paid** (D13).
- **Every page works without its scripts**, and the server decides what it says
  (CLAUDE.md, invariant 11).

## The semester as a journey

| moment | risk | answer |
|---|---|---|
| day 0, setup | gives up before seeing a plan | two-minute setup from a link (exists); the first session's tap is the first streak day |
| weeks 1 to 3 | the novelty fades (Hamari et al. 2014 name novelty as a recurring caveat) | the weekly review shows a first full week; a buddy invited in week 1 |
| mid-semester | deadlines pile up | at-risk warnings (exist), tasks typed in words |
| the exams | stress; the plan is all exam practice | readiness per exam on Progress; rest days stay neutral |
| after the exams | churn: the reason to open it is gone | a semester summary (later), and an offer to start the next semester from the new timetable link |

## How we will know (pilot metrics, from the event log, D15)

- **North star:** students who confirmed at least one session in the last 7 days,
  as a share of students set up.
- **Habit:** the share of planned sessions confirmed; the distribution of streaks;
  day-7 and day-30 return (a `visit` on day 7 and on day 30 after setup).
- **Value:** deadlines finished on time; exams reached "on track".
- **Guardrails:** hours studied above the student's own weekly limit (should be
  zero); students who turn reporting off by never tapping; AI spend against the cap.

With 10 to 20 students there is no A/B test worth the name; the pilot reads these
numbers week by week and asks the students, before and after.

## What comes after this stage (not built)

- Web push, at most one a day, opt-in (D14).
- A semester summary, shareable as an image.
- Writing directly into Google Calendar (W10), the first paid-tier candidate.
- Course-wide anonymous signals ("14 students revised Algebra this week"), once a
  course has enough students to be anonymous.
